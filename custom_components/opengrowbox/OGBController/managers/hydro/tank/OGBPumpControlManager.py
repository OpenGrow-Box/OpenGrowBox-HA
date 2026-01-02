"""
OpenGrowBox Pump Control Manager

Handles pump activation, dosing calculations, and pump control operations
for the tank feeding system.

Responsibilities:
- Pump activation and timing control
- Dose time calculations based on calibration
- Multi-pump coordination and sequencing
- Pump status monitoring and safety controls
"""

import asyncio
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Union

_LOGGER = logging.getLogger(__name__)


class PumpType(Enum):
    """Feed pump device names"""

    NUTRIENT_A = "switch.feedpump_a"  # Veg nutrient
    NUTRIENT_B = "switch.feedpump_b"  # Flower nutrient
    NUTRIENT_C = "switch.feedpump_c"  # Micro nutrient
    WATER = "switch.feedpump_w"  # Water pump
    PH_DOWN = "switch.feedpump_ph_down"  # pH down pump
    PH_UP = "switch.feedpump_ph_up"  # pH up pump


class OGBPumpControlManager:
    """
    Pump control manager for nutrient delivery systems.

    Handles pump activation, dosing calculations, and coordinated pump operations.
    """

    def __init__(self, room: str, data_store, event_manager, hass, calibration_manager):
        """
        Initialize pump control manager.

        Args:
            room: Room identifier
            data_store: Data store instance
            event_manager: Event manager instance
            hass: Home Assistant instance
            calibration_manager: Calibration manager instance
        """
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager
        self.hass = hass
        self.calibration_manager = calibration_manager

        # Pump control settings
        self.max_pump_runtime = 300  # 5 minutes maximum per pump
        self.pump_safety_delay = 2  # 2 seconds between pump operations
        self.max_concurrent_pumps = 2  # Maximum pumps running simultaneously

        # Pump status tracking
        self.active_pumps = set()
        self.pump_lock = asyncio.Lock()

    def calculate_dose_time(
        self, ml_amount: float, pump_type: Union[str, PumpType]
    ) -> float:
        """
        Calculate pump run time for a specific dose amount.

        Args:
            ml_amount: Amount to dose in ml
            pump_type: Pump type identifier

        Returns:
            Run time in seconds
        """
        try:
            # Convert pump_type to string if enum
            pump_entity = (
                pump_type.value if isinstance(pump_type, PumpType) else pump_type
            )

            # Get calibration factor
            calibration_factor = self.calibration_manager.get_pump_calibration_factor(
                pump_entity
            )

            if calibration_factor <= 0:
                _LOGGER.warning(
                    f"{self.room} - No valid calibration for {pump_entity}, using default"
                )
                calibration_factor = 1.0

            # Calculate time: ml / (ml/s) = seconds
            run_time = ml_amount / calibration_factor

            # Apply safety limits
            run_time = max(
                0.5, min(run_time, self.max_pump_runtime)
            )  # Min 0.5s, max 5min

            _LOGGER.debug(
                f"{self.room} - Calculated {run_time:.1f}s run time for {ml_amount:.1f}ml with {pump_entity}"
            )

            return run_time

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error calculating dose time: {e}")
            return 1.0  # Safe fallback

    def calculate_nutrient_dose(
        self, nutrient_ml_per_liter: float, reservoir_volume: float = 50.0
    ) -> float:
        """
        Calculate nutrient dose for reservoir volume.

        Args:
            nutrient_ml_per_liter: Desired ml per liter
            reservoir_volume: Reservoir volume in liters

        Returns:
            Total dose in ml
        """
        return nutrient_ml_per_liter * reservoir_volume

    async def activate_pump(
        self,
        pump_type: Union[str, PumpType],
        run_time: float,
        dose_ml: float,
        is_emergency: bool = False,
    ) -> bool:
        """
        Activate a single pump for specified duration.

        Args:
            pump_type: Pump to activate
            run_time: Run time in seconds
            dose_ml: Expected dose amount in ml
            is_emergency: Whether this is emergency operation

        Returns:
            True if successful, False otherwise
        """
        async with self.pump_lock:
            pump_entity = None
            try:
                # Convert pump_type to string
                pump_entity = (
                    pump_type.value if isinstance(pump_type, PumpType) else pump_type
                )

                # Check if pump is already active
                if pump_entity in self.active_pumps:
                    _LOGGER.warning(f"{self.room} - Pump {pump_entity} already active")
                    return False

                # Check concurrent pump limit
                if len(self.active_pumps) >= self.max_concurrent_pumps:
                    _LOGGER.warning(
                        f"{self.room} - Too many concurrent pumps ({len(self.active_pumps)})"
                    )
                    return False

                # Validate run time
                if run_time <= 0 or run_time > self.max_pump_runtime:
                    _LOGGER.error(
                        f"{self.room} - Invalid run time for {pump_entity}: {run_time}s"
                    )
                    return False

                # Add to active pumps
                self.active_pumps.add(pump_entity)

                _LOGGER.info(
                    f"{self.room} - Activating {pump_entity} for {run_time:.1f}s ({dose_ml:.1f}ml)"
                )

                # Turn on pump
                await self._turn_on_pump(pump_entity)

                # Wait for run time
                await asyncio.sleep(run_time)

                # Turn off pump
                await self._turn_off_pump(pump_entity)

                # Remove from active pumps
                self.active_pumps.discard(pump_entity)

                # Log completion
                await self._log_pump_completion(
                    pump_entity, run_time, dose_ml, is_emergency
                )

                return True

            except Exception as e:
                # Cleanup on error
                if pump_entity and pump_entity in self.active_pumps:
                    try:
                        await self._turn_off_pump(pump_entity)
                    except:
                        pass
                    self.active_pumps.discard(pump_entity)

                pump_name = pump_entity or str(pump_type)
                _LOGGER.error(f"{self.room} - Error activating pump {pump_name}: {e}")
                return False

    async def activate_pump2(
        self, pump_type: PumpType, run_time: float, dose_ml: float
    ) -> bool:
        """
        Alternative pump activation method (legacy compatibility).

        Args:
            pump_type: Pump type enum
            run_time: Run time in seconds
            dose_ml: Dose amount in ml

        Returns:
            True if successful
        """
        return await self.activate_pump(pump_type, run_time, dose_ml)

    async def dose_ph_down(
        self, target_ph: float, current_ph: Optional[float] = None
    ) -> bool:
        """
        Dose pH down to reach target.

        Args:
            target_ph: Target pH value
            current_ph: Current pH value (optional)

        Returns:
            True if dosing successful
        """
        try:
            if current_ph is None:
                current_ph = self.data_store.getDeep("Hydro.ph_current")

            if current_ph is None or current_ph <= target_ph:
                return False

            # Calculate dose amount (rough estimation)
            ph_difference = current_ph - target_ph
            dose_ml = ph_difference * 2.0  # 2ml per pH unit (rough estimate)

            run_time = self.calculate_dose_time(dose_ml, PumpType.PH_DOWN)

            return await self.activate_pump(
                PumpType.PH_DOWN, run_time, dose_ml, is_emergency=True
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error dosing pH down: {e}")
            return False

    async def dose_ph_up(
        self, target_ph: float, current_ph: Optional[float] = None
    ) -> bool:
        """
        Dose pH up to reach target.

        Args:
            target_ph: Target pH value
            current_ph: Current pH value (optional)

        Returns:
            True if dosing successful
        """
        try:
            if current_ph is None:
                current_ph = self.data_store.getDeep("Hydro.ph_current")

            if current_ph is None or current_ph >= target_ph:
                return False

            # Calculate dose amount (rough estimation)
            ph_difference = target_ph - current_ph
            dose_ml = ph_difference * 1.0  # 1ml per pH unit (rough estimate)

            run_time = self.calculate_dose_time(dose_ml, PumpType.PH_UP)

            return await self.activate_pump(
                PumpType.PH_UP, run_time, dose_ml, is_emergency=True
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error dosing pH up: {e}")
            return False

    async def dose_nutrients(self, nutrient_doses: Dict[str, float]) -> bool:
        """
        Dose multiple nutrients in sequence.

        Args:
            nutrient_doses: Dictionary of nutrient types to ml amounts

        Returns:
            True if all dosing successful
        """
        try:
            pump_mapping = {
                "A": PumpType.NUTRIENT_A,
                "B": PumpType.NUTRIENT_B,
                "C": PumpType.NUTRIENT_C,
                "micro": PumpType.NUTRIENT_C,
            }

            all_success = True

            for nutrient_type, ml_amount in nutrient_doses.items():
                pump_type = pump_mapping.get(nutrient_type)
                if pump_type:
                    run_time = self.calculate_dose_time(ml_amount, pump_type)
                    success = await self.activate_pump(pump_type, run_time, ml_amount)
                    all_success = all_success and success

                    # Small delay between nutrients
                    await asyncio.sleep(self.pump_safety_delay)
                else:
                    _LOGGER.warning(
                        f"{self.room} - Unknown nutrient type: {nutrient_type}"
                    )
                    all_success = False

            return all_success

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error dosing nutrients: {e}")
            return False

    async def dilute_ec(
        self,
        target_ec: float,
        current_ec: Optional[float] = None,
        reservoir_volume: float = 50.0,
    ) -> bool:
        """
        Dilute EC by adding water.

        Args:
            target_ec: Target EC value
            current_ec: Current EC value (optional)
            reservoir_volume: Reservoir volume in liters

        Returns:
            True if dilution successful
        """
        try:
            if current_ec is None:
                current_ec = self.data_store.getDeep("Hydro.ec_current")

            if current_ec is None or current_ec <= target_ec:
                return False

            # Calculate water needed to reach target EC
            # Rough estimation: EC dilution follows linear relationship
            dilution_ratio = current_ec / target_ec
            water_to_add = reservoir_volume * (dilution_ratio - 1)

            # Convert to ml and ensure reasonable bounds
            water_ml = max(10, min(water_to_add * 1000, 5000))  # 10ml to 5L

            run_time = self.calculate_dose_time(water_ml, PumpType.WATER)

            _LOGGER.info(
                f"{self.room} - Diluting EC: adding {water_ml:.0f}ml water to reach target {target_ec:.2f}"
            )

            return await self.activate_pump(PumpType.WATER, run_time, water_ml)

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error diluting EC: {e}")
            return False

    async def emergency_stop_all_pumps(self):
        """
        Emergency stop all active pumps.
        """
        try:
            pumps_to_stop = list(self.active_pumps)
            self.active_pumps.clear()

            for pump_entity in pumps_to_stop:
                try:
                    await self._turn_off_pump(pump_entity)
                    _LOGGER.warning(f"{self.room} - Emergency stopped {pump_entity}")
                except Exception as e:
                    _LOGGER.error(
                        f"{self.room} - Failed to emergency stop {pump_entity}: {e}"
                    )

            if pumps_to_stop:
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "HYDROLOG",
                        "Message": f"Emergency stopped {len(pumps_to_stop)} pumps",
                    },
                )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in emergency pump stop: {e}")

    async def _turn_on_pump(self, pump_entity: str):
        """
        Turn on a pump via Home Assistant.

        Args:
            pump_entity: Pump entity ID
        """
        try:
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": pump_entity}
            )
        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to turn on {pump_entity}: {e}")
            raise

    async def _turn_off_pump(self, pump_entity: str):
        """
        Turn off a pump via Home Assistant.

        Args:
            pump_entity: Pump entity ID
        """
        try:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": pump_entity}
            )
        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to turn off {pump_entity}: {e}")
            raise

    def get_active_pumps(self) -> List[str]:
        """
        Get list of currently active pumps.

        Returns:
            List of active pump entity IDs
        """
        return list(self.active_pumps)

    def is_pump_available(self, pump_type: Union[str, PumpType]) -> bool:
        """
        Check if a pump is available (not currently active).

        Args:
            pump_type: Pump to check

        Returns:
            True if pump is available
        """
        pump_entity = pump_type.value if isinstance(pump_type, PumpType) else pump_type
        return pump_entity not in self.active_pumps

    async def _log_pump_completion(
        self, pump_entity: str, run_time: float, dose_ml: float, is_emergency: bool
    ):
        """
        Log pump operation completion.

        Args:
            pump_entity: Pump entity ID
            run_time: Run time in seconds
            dose_ml: Dose amount in ml
            is_emergency: Whether this was emergency operation
        """
        try:
            operation_type = "Emergency" if is_emergency else "Scheduled"

            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "HYDROLOG",
                    "Message": f"{operation_type} pump operation: {pump_entity} ran {run_time:.1f}s, dosed {dose_ml:.1f}ml",
                },
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error logging pump completion: {e}")

    def get_pump_status(self) -> Dict[str, Any]:
        """
        Get comprehensive pump status information.

        Returns:
            Dictionary with pump status details
        """
        return {
            "active_pumps": list(self.active_pumps),
            "available_pumps": [
                pump.value for pump in PumpType if pump.value not in self.active_pumps
            ],
            "max_concurrent": self.max_concurrent_pumps,
            "safety_delay": self.pump_safety_delay,
            "max_runtime": self.max_pump_runtime,
            "pump_calibrations": {
                pump.value: {
                    "calibrated": self.calibration_manager.get_pump_calibration_factor(
                        pump.value
                    )
                    != 1.0,
                    "factor": self.calibration_manager.get_pump_calibration_factor(
                        pump.value
                    ),
                }
                for pump in PumpType
            },
        }
