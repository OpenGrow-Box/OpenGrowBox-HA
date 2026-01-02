"""
OpenGrowBox Feed Calibration Manager

Handles pump calibration procedures, validation, and calibration data management
for the tank feeding system.

Responsibilities:
- Pump calibration routines and timing
- Calibration data storage and retrieval
- Calibration validation and status checking
- Daily calibration automation
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class PumpCalibration:
    """
    Represents calibration data for a single pump.
    """

    def __init__(self, pump_type: str):
        self.pump_type = pump_type
        self.calibration_factor = 1.0  # ml per second
        self.last_calibration = None
        self.is_calibrated = False
        self.test_volume = 10.0  # ml for calibration test
        self.measured_time = 0.0  # seconds measured
        self.accuracy_score = 0.0  # percentage accuracy

    def calculate_adjustment(self) -> float:
        """
        Calculate flow rate adjustment factor.

        Returns:
            Adjustment factor for dosing calculations
        """
        if not self.is_calibrated or self.calibration_factor <= 0:
            return 1.0  # No adjustment if not calibrated

        return self.calibration_factor

    def update_calibration(self, measured_time: float, target_volume: float = 10.0):
        """
        Update calibration data after a calibration run.

        Args:
            measured_time: Time in seconds to dispense target volume
            target_volume: Target volume in ml
        """
        if measured_time > 0:
            self.calibration_factor = target_volume / measured_time  # ml per second
            self.last_calibration = datetime.now()
            self.is_calibrated = True
            self.accuracy_score = min(
                100.0, (target_volume / (measured_time * self.calibration_factor)) * 100
            )
            self.measured_time = measured_time

            _LOGGER.info(
                f"Pump {self.pump_type} calibrated: {self.calibration_factor:.2f} ml/s, accuracy: {self.accuracy_score:.1f}%"
            )

    def is_calibration_valid(self) -> bool:
        """
        Check if calibration is still valid (not too old).

        Returns:
            True if calibration is valid
        """
        if not self.last_calibration:
            return False

        # Calibration valid for 30 days
        max_age = timedelta(days=30)
        return (datetime.now() - self.last_calibration) < max_age

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert calibration data to dictionary for storage.

        Returns:
            Dictionary representation
        """
        return {
            "pump_type": self.pump_type,
            "calibration_factor": self.calibration_factor,
            "last_calibration": (
                self.last_calibration.isoformat() if self.last_calibration else None
            ),
            "is_calibrated": self.is_calibrated,
            "test_volume": self.test_volume,
            "measured_time": self.measured_time,
            "accuracy_score": self.accuracy_score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PumpCalibration":
        """
        Create PumpCalibration from dictionary data.

        Args:
            data: Dictionary with calibration data

        Returns:
            PumpCalibration instance
        """
        calibration = cls(data["pump_type"])
        calibration.calibration_factor = data.get("calibration_factor", 1.0)
        calibration.is_calibrated = data.get("is_calibrated", False)
        calibration.test_volume = data.get("test_volume", 10.0)
        calibration.measured_time = data.get("measured_time", 0.0)
        calibration.accuracy_score = data.get("accuracy_score", 0.0)

        last_cal_str = data.get("last_calibration")
        if last_cal_str:
            try:
                calibration.last_calibration = datetime.fromisoformat(last_cal_str)
            except ValueError:
                _LOGGER.warning(
                    f"Invalid calibration date for {calibration.pump_type}: {last_cal_str}"
                )

        return calibration


class OGBFeedCalibrationManager:
    """
    Calibration manager for feed pumps and system validation.

    Handles pump calibration procedures, daily calibration automation,
    and calibration data management.
    """

    def __init__(self, room: str, data_store, event_manager, hass):
        """
        Initialize calibration manager.

        Args:
            room: Room identifier
            data_store: Data store instance
            event_manager: Event manager instance
            hass: Home Assistant instance
        """
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager
        self.hass = hass

        # Calibration settings
        self.calibration_test_volume = 10.0  # ml
        self.calibration_timeout = 60  # seconds
        self.daily_calibration_hour = 2  # 2 AM
        self.max_calibration_attempts = 3

        # Pump calibrations
        self.pump_calibrations = {}
        self._daily_calibration_task = None

        # Initialize pump calibrations
        self._initialize_pump_calibrations()

    def _initialize_pump_calibrations(self):
        """
        Initialize calibration data for all pump types.
        """
        pump_types = [
            "switch.feedpump_a",
            "switch.feedpump_b",
            "switch.feedpump_c",
            "switch.feedpump_w",
        ]

        for pump_type in pump_types:
            self.pump_calibrations[pump_type] = PumpCalibration(pump_type)

    async def load_calibration_data(self):
        """
        Load calibration data from dataStore.
        """
        try:
            stored_data = self.data_store.getDeep("Hydro.Calibration.Pumps")
            if stored_data:
                for pump_data in stored_data.values():
                    if isinstance(pump_data, dict) and "pump_type" in pump_data:
                        calibration = PumpCalibration.from_dict(pump_data)
                        self.pump_calibrations[calibration.pump_type] = calibration

            _LOGGER.info(
                f"{self.room} - Loaded calibration data for {len(self.pump_calibrations)} pumps"
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error loading calibration data: {e}")

    async def save_calibration_data(self):
        """
        Save calibration data to dataStore.
        """
        try:
            calibration_data = {}
            for pump_type, calibration in self.pump_calibrations.items():
                calibration_data[pump_type] = calibration.to_dict()

            self.data_store.setDeep("Hydro.Calibration.Pumps", calibration_data)
            _LOGGER.debug(
                f"{self.room} - Saved calibration data for {len(calibration_data)} pumps"
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error saving calibration data: {e}")

    async def start_pump_calibration(self, pump_type: str) -> bool:
        """
        Start calibration procedure for a specific pump.

        Args:
            pump_type: Pump entity ID to calibrate

        Returns:
            True if calibration started successfully
        """
        try:
            if pump_type not in self.pump_calibrations:
                _LOGGER.error(
                    f"{self.room} - Unknown pump type for calibration: {pump_type}"
                )
                return False

            calibration = self.pump_calibrations[pump_type]

            # Run calibration test
            success = await self._run_pump_calibration_test(pump_type, calibration)

            if success:
                await self.save_calibration_data()
                await self._log_calibration_success(pump_type, calibration)
            else:
                await self._log_calibration_failure(pump_type)

            return success

        except Exception as e:
            _LOGGER.error(
                f"{self.room} - Error starting pump calibration for {pump_type}: {e}"
            )
            return False

    async def _run_pump_calibration_test(
        self, pump_type: str, calibration: PumpCalibration
    ) -> bool:
        """
        Run the actual calibration test for a pump.

        Args:
            pump_type: Pump entity ID
            calibration: PumpCalibration instance

        Returns:
            True if calibration successful
        """
        try:
            # Calculate expected time for test volume
            expected_time = calibration.test_volume / calibration.calibration_factor

            # Ensure reasonable bounds
            expected_time = max(1.0, min(expected_time, 30.0))

            _LOGGER.info(
                f"{self.room} - Starting calibration test for {pump_type}: {calibration.test_volume}ml in ~{expected_time:.1f}s"
            )

            # Record start time
            start_time = datetime.now()

            # Activate pump for expected time
            success = await self._activate_pump_for_calibration(
                pump_type, expected_time
            )

            if not success:
                return False

            # Measure actual time taken
            end_time = datetime.now()
            measured_time = (end_time - start_time).total_seconds()

            # Update calibration with measured data
            calibration.update_calibration(measured_time, calibration.test_volume)

            _LOGGER.info(
                f"{self.room} - Calibration test completed for {pump_type}: measured {measured_time:.2f}s"
            )
            return True

        except Exception as e:
            _LOGGER.error(
                f"{self.room} - Error in calibration test for {pump_type}: {e}"
            )
            return False

    async def _activate_pump_for_calibration(
        self, pump_type: str, duration: float
    ) -> bool:
        """
        Activate pump for calibration timing.

        Args:
            pump_type: Pump entity ID
            duration: Duration in seconds

        Returns:
            True if activation successful
        """
        try:
            # Turn on pump
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": pump_type}
            )

            # Wait for duration
            await asyncio.sleep(duration)

            # Turn off pump
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": pump_type}
            )

            return True

        except Exception as e:
            _LOGGER.error(
                f"{self.room} - Error activating pump {pump_type} for calibration: {e}"
            )
            return False

    async def start_daily_calibration(self):
        """
        Start daily calibration automation.
        """
        try:
            if self._daily_calibration_task and not self._daily_calibration_task.done():
                _LOGGER.warning(f"{self.room} - Daily calibration already running")
                return

            self._daily_calibration_task = asyncio.create_task(
                self._daily_calibration_loop()
            )
            _LOGGER.info(f"{self.room} - Daily calibration automation started")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error starting daily calibration: {e}")

    async def _daily_calibration_loop(self):
        """
        Main loop for daily calibration automation.
        """
        try:
            while True:
                # Calculate time until next calibration (2 AM)
                now = datetime.now()
                next_calibration = now.replace(
                    hour=self.daily_calibration_hour, minute=0, second=0, microsecond=0
                )

                if next_calibration <= now:
                    next_calibration += timedelta(days=1)

                wait_seconds = (next_calibration - now).total_seconds()

                _LOGGER.debug(
                    f"{self.room} - Next daily calibration in {wait_seconds:.0f} seconds"
                )

                # Wait until calibration time
                await asyncio.sleep(wait_seconds)

                # Run daily calibration
                await self._run_daily_calibration()

        except asyncio.CancelledError:
            _LOGGER.info(f"{self.room} - Daily calibration loop cancelled")
        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in daily calibration loop: {e}")

    async def _run_daily_calibration(self):
        """
        Run daily calibration check and recalibration if needed.
        """
        try:
            _LOGGER.info(f"{self.room} - Running daily calibration check")

            recalibrated_count = 0

            for pump_type, calibration in self.pump_calibrations.items():
                # Check if calibration is still valid
                if not calibration.is_calibration_valid():
                    _LOGGER.info(
                        f"{self.room} - Recalibrating {pump_type} (calibration expired)"
                    )

                    success = await self.start_pump_calibration(pump_type)
                    if success:
                        recalibrated_count += 1
                    else:
                        _LOGGER.warning(
                            f"{self.room} - Failed to recalibrate {pump_type}"
                        )

            if recalibrated_count > 0:
                await self.save_calibration_data()
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "HYDROLOG",
                        "Message": f"Daily calibration completed: recalibrated {recalibrated_count} pumps",
                    },
                )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in daily calibration: {e}")

    async def validate_all_calibrations(self) -> bool:
        """
        Validate that all pumps have valid calibrations.

        Returns:
            True if all calibrations are valid
        """
        invalid_calibrations = []

        for pump_type, calibration in self.pump_calibrations.items():
            if not calibration.is_calibrated:
                invalid_calibrations.append(f"{pump_type} (not calibrated)")
            elif not calibration.is_calibration_valid():
                invalid_calibrations.append(f"{pump_type} (expired)")
            elif calibration.accuracy_score < 80.0:
                invalid_calibrations.append(
                    f"{pump_type} (low accuracy: {calibration.accuracy_score:.1f}%)"
                )

        if invalid_calibrations:
            _LOGGER.warning(
                f"{self.room} - Invalid calibrations: {', '.join(invalid_calibrations)}"
            )
            return False

        _LOGGER.debug(f"{self.room} - All pump calibrations are valid")
        return True

    def get_calibration_status(self) -> Dict[str, Any]:
        """
        Get comprehensive calibration status.

        Returns:
            Dictionary with calibration status information
        """
        status = {
            "total_pumps": len(self.pump_calibrations),
            "calibrated_pumps": 0,
            "valid_calibrations": 0,
            "daily_calibration_active": self._daily_calibration_task is not None
            and not self._daily_calibration_task.done(),
            "pump_details": {},
        }

        for pump_type, calibration in self.pump_calibrations.items():
            pump_status = {
                "is_calibrated": calibration.is_calibrated,
                "is_valid": calibration.is_calibration_valid(),
                "accuracy_score": calibration.accuracy_score,
                "last_calibration": (
                    calibration.last_calibration.isoformat()
                    if calibration.last_calibration
                    else None
                ),
                "calibration_factor": calibration.calibration_factor,
            }

            status["pump_details"][pump_type] = pump_status

            if calibration.is_calibrated:
                status["calibrated_pumps"] += 1
                if calibration.is_calibration_valid():
                    status["valid_calibrations"] += 1

        return status

    def get_pump_calibration_factor(self, pump_type: str) -> float:
        """
        Get calibration factor for a specific pump.

        Args:
            pump_type: Pump entity ID

        Returns:
            Calibration factor (ml/s), defaults to 1.0 if not calibrated
        """
        calibration = self.pump_calibrations.get(pump_type)
        if calibration:
            return calibration.calculate_adjustment()
        return 1.0

    async def stop_daily_calibration(self):
        """
        Stop daily calibration automation.
        """
        try:
            if self._daily_calibration_task and not self._daily_calibration_task.done():
                self._daily_calibration_task.cancel()
                try:
                    await self._daily_calibration_task
                except asyncio.CancelledError:
                    pass

            _LOGGER.info(f"{self.room} - Daily calibration stopped")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error stopping daily calibration: {e}")

    async def _log_calibration_success(
        self, pump_type: str, calibration: PumpCalibration
    ):
        """
        Log successful calibration event.

        Args:
            pump_type: Pump entity ID
            calibration: PumpCalibration instance
        """
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": f"Pump {pump_type} calibrated successfully: {calibration.calibration_factor:.2f} ml/s ({calibration.accuracy_score:.1f}% accuracy)",
            },
        )

    async def _log_calibration_failure(self, pump_type: str):
        """
        Log failed calibration event.

        Args:
            pump_type: Pump entity ID
        """
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": f"Pump {pump_type} calibration failed",
            },
        )
