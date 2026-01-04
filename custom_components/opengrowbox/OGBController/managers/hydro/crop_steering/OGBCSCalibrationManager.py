"""
OpenGrowBox Crop Steering Calibration Manager

Handles VWC calibration procedures and sensor validation for the Crop Steering system.

Responsibilities:
- VWC max/min calibration cycles
- Sensor stabilization monitoring
- Calibration data collection and averaging
- Calibration state management
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBCSCalibrationManager:
    """
    Calibration manager for Crop Steering.

    Handles all VWC calibration procedures including max/min calibration cycles,
    sensor stabilization monitoring, and calibration data management.
    """

    def __init__(self, room: str, data_store, event_manager, advanced_sensor, hass=None):
        """
        Initialize calibration manager.

        Args:
            room: Room identifier
            data_store: Data store instance
            event_manager: Event manager instance
            advanced_sensor: Advanced sensor processing instance
            hass: Home Assistant instance for updating number entities
        """
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager
        self.advanced_sensor = advanced_sensor
        self.hass = hass

        # Calibration settings
        self.calibration_readings = []
        self.calibration_threshold = 2
        self.stability_tolerance = 0.5
        self.max_irrigation_attempts = 5
        self.block_check_interval = 120

        # Calibration tasks
        self._calibration_task = None

    async def _update_number_entity(self, parameter: str, phase: str, value: float):
        """
        Update a HA number entity with calibrated value.
        
        Args:
            parameter: Parameter name (e.g., 'VWCMax', 'VWCMin')
            phase: Phase identifier (e.g., 'p1', 'p2')
            value: The calibrated value to set
        """
        if not self.hass:
            _LOGGER.debug(f"{self.room} - Cannot update number entity: hass not available")
            return
        
        try:
            # Entity naming: OGB_CropSteering_P1_VWC_Max_{room} -> number.ogb_cropsteering_p1_vwc_max_{room}
            # Map parameter names to entity format
            param_map = {
                "VWCMax": "vwc_max",
                "VWCMin": "vwc_min",
                "VWCTarget": "vwc_target",
            }
            param_name = param_map.get(parameter, parameter.lower())
            entity_id = f"number.ogb_cropsteering_{phase}_{param_name}_{self.room.lower()}"
            
            await self.hass.services.async_call(
                domain="number",
                service="set_value",
                service_data={"entity_id": entity_id, "value": float(value)},
                blocking=True,
            )
            
            _LOGGER.info(
                f"{self.room} - Updated number entity {entity_id} to {value:.1f}"
            )
            
        except Exception as e:
            _LOGGER.warning(f"{self.room} - Failed to update number entity for {parameter}.{phase}: {e}")

    async def handle_vwc_calibration_command(self, command_data: Dict[str, Any]):
        """
        Handle incoming VWC calibration commands.

        Args:
            command_data: Calibration command data with 'action' or 'command' key
        """
        # Support both 'action' and 'command' keys for flexibility
        action = command_data.get("action") or command_data.get("command", "")
        action = action.lower() if action else ""
        phase = command_data.get("phase", "p1")

        if action == "start_max":
            await self.start_vwc_max_calibration(phase)
        elif action == "start_min":
            await self.start_vwc_min_calibration(phase)
        elif action == "stop":
            await self.stop_vwc_calibration()
        else:
            _LOGGER.warning(f"{self.room} - Unknown calibration action: {action}")

    async def start_vwc_max_calibration(self, phase: str = "p1"):
        """
        Start VWC maximum calibration procedure.

        Args:
            phase: Phase to calibrate for (p1, p2, p3)
        """
        try:
            if self._calibration_task and not self._calibration_task.done():
                _LOGGER.warning(f"{self.room} - Calibration already in progress")
                return

            _LOGGER.info(
                f"{self.room} - Starting VWC max calibration for phase {phase}"
            )

            # Cancel any existing calibration
            await self.stop_vwc_calibration()

            # Start new calibration task
            self._calibration_task = asyncio.create_task(
                self._vwc_max_calibration_cycle(phase)
            )

            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"VWC Max Calibration started for phase {phase.upper()}",
                },
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error starting VWC max calibration: {e}")

    async def _vwc_max_calibration_cycle(self, phase: str):
        """
        Execute VWC maximum calibration cycle.

        Args:
            phase: Phase identifier
        """
        try:
            _LOGGER.info(
                f"{self.room} - Executing VWC max calibration cycle for phase {phase}"
            )

            # Step 1: Fully saturate the medium
            saturation_duration = 180  # 3 minutes initial saturation
            irrigation_attempts = 0

            while irrigation_attempts < self.max_irrigation_attempts:
                # Irrigate to saturate
                success = await self._irrigate_for_calibration(saturation_duration)
                if not success:
                    _LOGGER.error(
                        f"{self.room} - Irrigation failed during max calibration"
                    )
                    return

                # Wait for stabilization
                await asyncio.sleep(60)  # Wait 1 minute

                # Check if VWC has stabilized
                if await self._wait_for_vwc_stabilization(timeout=300):
                    break

                irrigation_attempts += 1
                saturation_duration += 60  # Add 1 minute each attempt

            if irrigation_attempts >= self.max_irrigation_attempts:
                _LOGGER.warning(
                    f"{self.room} - Max calibration failed to stabilize after {irrigation_attempts} attempts"
                )
                return

            # Step 2: Collect stabilized readings
            max_vwc = await self._collect_calibration_readings("max", phase)

            if max_vwc is not None:
                # Store the calibrated max value in DataStore
                self.data_store.setDeep(
                    f"CropSteering.Calibration.{phase}.VWCMax", max_vwc
                )
                self.data_store.setDeep(
                    f"CropSteering.Calibration.{phase}.timestamp",
                    datetime.now().isoformat()
                )
                
                # Update the Number entity so user sees the new value in UI
                await self._update_number_entity("VWCMax", phase, max_vwc)
                
                # Persist calibration to disk
                await self.event_manager.emit("SaveState", {"source": "CropSteeringCalibration"})

                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"VWC Max Calibration completed: {max_vwc:.1f}% for phase {phase.upper()}",
                    },
                )

                _LOGGER.info(
                    f"{self.room} - VWC max calibration completed: {max_vwc:.1f}% for phase {phase}"
                )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in VWC max calibration cycle: {e}")
        finally:
            self._calibration_task = None

    async def start_vwc_min_calibration(self, phase: str = "p1"):
        """
        Start VWC minimum calibration procedure.

        Args:
            phase: Phase to calibrate for (p1, p2, p3)
        """
        try:
            if self._calibration_task and not self._calibration_task.done():
                _LOGGER.warning(f"{self.room} - Calibration already in progress")
                return

            _LOGGER.info(
                f"{self.room} - Starting VWC min calibration for phase {phase}"
            )

            # Cancel any existing calibration
            await self.stop_vwc_calibration()

            # Start new calibration task
            self._calibration_task = asyncio.create_task(
                self._vwc_min_calibration_cycle(
                    phase, dry_back_duration=7200
                )  # 2 hours
            )

            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"VWC Min Calibration started for phase {phase.upper()}",
                },
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error starting VWC min calibration: {e}")

    async def _vwc_min_calibration_cycle(self, phase: str, dry_back_duration: int):
        """
        Execute VWC minimum calibration cycle.

        Args:
            phase: Phase identifier
            dry_back_duration: Duration to wait for dryback in seconds
        """
        try:
            _LOGGER.info(
                f"{self.room} - Executing VWC min calibration cycle for phase {phase}"
            )

            # Wait for the specified dryback duration
            await asyncio.sleep(dry_back_duration)

            # Check if VWC has stabilized at minimum
            if not await self._wait_for_vwc_stabilization(
                timeout=600
            ):  # 10 minutes timeout
                _LOGGER.warning(
                    f"{self.room} - VWC did not stabilize during min calibration"
                )
                return

            # Collect stabilized readings
            min_vwc = await self._collect_calibration_readings("min", phase)

            if min_vwc is not None:
                # Store the calibrated min value in DataStore
                self.data_store.setDeep(
                    f"CropSteering.Calibration.{phase}.VWCMin", min_vwc
                )
                self.data_store.setDeep(
                    f"CropSteering.Calibration.{phase}.timestamp",
                    datetime.now().isoformat()
                )
                
                # Update the Number entity so user sees the new value in UI
                await self._update_number_entity("VWCMin", phase, min_vwc)
                
                # Persist calibration to disk
                await self.event_manager.emit("SaveState", {"source": "CropSteeringCalibration"})

                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"VWC Min Calibration completed: {min_vwc:.1f}% for phase {phase.upper()}",
                    },
                )

                _LOGGER.info(
                    f"{self.room} - VWC min calibration completed: {min_vwc:.1f}% for phase {phase}"
                )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in VWC min calibration cycle: {e}")
        finally:
            self._calibration_task = None

    async def _wait_for_vwc_stabilization(
        self, timeout: int = 300, check_interval: int = 10
    ) -> bool:
        """
        Wait for VWC readings to stabilize.

        Args:
            timeout: Maximum time to wait in seconds
            check_interval: Interval between stability checks in seconds

        Returns:
            True if stabilization achieved, False otherwise
        """
        try:
            start_time = datetime.now()
            stable_readings = []

            while (datetime.now() - start_time).seconds < timeout:
                # Get current VWC reading
                current_vwc = self._get_current_vwc_reading()

                if current_vwc is None:
                    await asyncio.sleep(check_interval)
                    continue

                stable_readings.append(current_vwc)

                # Keep only last 6 readings (1 minute worth)
                if len(stable_readings) > 6:
                    stable_readings.pop(0)

                # Check stability if we have enough readings
                if len(stable_readings) >= 6:
                    min_reading = min(stable_readings)
                    max_reading = max(stable_readings)
                    variation = max_reading - min_reading

                    if variation <= self.stability_tolerance:
                        _LOGGER.debug(
                            f"{self.room} - VWC stabilized: {min_reading:.1f}% - {max_reading:.1f}% (variation: {variation:.1f}%)"
                        )
                        return True

                await asyncio.sleep(check_interval)

            _LOGGER.warning(f"{self.room} - VWC stabilization timeout after {timeout}s")
            return False

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error waiting for VWC stabilization: {e}")
            return False

    async def _collect_calibration_readings(
        self, calibration_type: str, phase: str
    ) -> Optional[float]:
        """
        Collect and average calibration readings.

        Args:
            calibration_type: "max" or "min"
            phase: Phase identifier

        Returns:
            Averaged calibration value or None if failed
        """
        try:
            readings = []

            # Collect readings over 2 minutes
            for _ in range(12):  # 12 readings * 10s = 2 minutes
                vwc = self._get_current_vwc_reading()
                if vwc is not None:
                    readings.append(vwc)
                await asyncio.sleep(10)

            if not readings:
                _LOGGER.error(
                    f"{self.room} - No VWC readings collected during {calibration_type} calibration"
                )
                return None

            # Calculate average
            avg_vwc = sum(readings) / len(readings)

            # Store calibration data
            calibration_data = {
                "type": calibration_type,
                "phase": phase,
                "value": avg_vwc,
                "readings": readings,
                "timestamp": datetime.now().isoformat(),
                "sensor_count": len(readings),
            }

            self.data_store.setDeep(
                f"CropSteering.Calibration.{phase}.{calibration_type.capitalize()}",
                calibration_data,
            )

            _LOGGER.info(
                f"{self.room} - Collected {len(readings)} {calibration_type} calibration readings, average: {avg_vwc:.1f}%"
            )

            return avg_vwc

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error collecting calibration readings: {e}")
            return None

    async def stop_vwc_calibration(self):
        """
        Stop any ongoing VWC calibration procedure.
        """
        try:
            if self._calibration_task and not self._calibration_task.done():
                self._calibration_task.cancel()
                try:
                    await self._calibration_task
                except asyncio.CancelledError:
                    pass

            self._calibration_task = None

            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": "VWC Calibration stopped",
                },
            )

            _LOGGER.info(f"{self.room} - VWC calibration stopped")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error stopping VWC calibration: {e}")

    async def _irrigate_for_calibration(self, duration: int) -> bool:
        """
        Perform irrigation specifically for calibration purposes.

        Args:
            duration: Irrigation duration in seconds

        Returns:
            True if irrigation successful
        """
        try:
            # Use the irrigation manager to perform irrigation
            # This is a placeholder - would need to import and use OGBCSIrrigationManager
            _LOGGER.debug(f"{self.room} - Calibration irrigation: {duration}s")

            # For now, just simulate successful irrigation
            await asyncio.sleep(duration)
            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in calibration irrigation: {e}")
            return False

    def _get_current_vwc_reading(self) -> Optional[float]:
        """
        Get current VWC reading from sensors.

        Returns:
            Current VWC value or None if unavailable
        """
        try:
            # Get VWC readings from advanced sensor
            vwc_data = self.advanced_sensor.getSensorValue("vwc", "soil")
            if vwc_data and len(vwc_data) > 0:
                return vwc_data[0].get("value")

            return None

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error getting VWC reading: {e}")
            return None

    def get_calibration_status(self) -> Dict[str, Any]:
        """
        Get current calibration status.

        Returns:
            Dictionary with calibration status information
        """
        return {
            "in_progress": self._calibration_task is not None
            and not self._calibration_task.done(),
            "readings_collected": len(self.calibration_readings),
            "last_calibration": self.data_store.getDeep(
                "CropSteering.Calibration.LastRun"
            ),
            "phases_calibrated": self._get_calibrated_phases(),
        }

    def _get_calibrated_phases(self) -> List[str]:
        """
        Get list of phases that have been calibrated.

        Returns:
            List of calibrated phase identifiers
        """
        calibrated = []
        for phase in ["p1", "p2", "p3"]:
            if self.data_store.getDeep(
                f"CropSteering.Calibration.{phase}.Max"
            ) or self.data_store.getDeep(f"CropSteering.Calibration.{phase}.Min"):
                calibrated.append(phase)
        return calibrated

    def validate_calibration_data(self, phase: str) -> bool:
        """
        Validate that calibration data exists and is reasonable for a phase.

        Args:
            phase: Phase identifier

        Returns:
            True if calibration data is valid
        """
        max_data = self.data_store.getDeep(f"CropSteering.Calibration.{phase}.Max")
        min_data = self.data_store.getDeep(f"CropSteering.Calibration.{phase}.Min")

        if not max_data or not min_data:
            return False

        max_vwc = max_data.get("value")
        min_vwc = min_data.get("value")

        if max_vwc is None or min_vwc is None:
            return False

        # Basic validation: max should be higher than min
        if max_vwc <= min_vwc:
            _LOGGER.warning(
                f"{self.room} - Invalid calibration: max {max_vwc} <= min {min_vwc} for phase {phase}"
            )
            return False

        # Max should be reasonable (40-90%)
        if not (40 <= max_vwc <= 90):
            _LOGGER.warning(
                f"{self.room} - Unreasonable max VWC: {max_vwc}% for phase {phase}"
            )
            return False

        # Min should be reasonable (20-60%)
        if not (20 <= min_vwc <= 60):
            _LOGGER.warning(
                f"{self.room} - Unreasonable min VWC: {min_vwc}% for phase {phase}"
            )
            return False

        return True
