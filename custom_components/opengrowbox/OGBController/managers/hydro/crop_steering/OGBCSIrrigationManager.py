"""
OpenGrowBox Crop Steering Irrigation Manager

Handles irrigation control, dripper management, and EC adjustments
for the Crop Steering system.

Responsibilities:
- Irrigation timing and duration calculations
- Dripper control and activation
- EC adjustment for dryback and nutrient management
- Emergency irrigation handling
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBCSIrrigationManager:
    """
    Irrigation manager for Crop Steering.

    Handles all irrigation-related operations including timing, dripper control,
    and EC adjustments for optimal plant nutrition.
    """

    def __init__(self, room: str, data_store, event_manager, hass):
        """
        Initialize irrigation manager.

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

        # Irrigation settings
        self.max_irrigation_attempts = 5
        self.irrigation_safety_timeout = 300  # 5 minutes
        self.emergency_irrigation_count = 0

    def get_drippers(self) -> List[Dict[str, Any]]:
        """
        Get list of available dripper devices.

        Returns:
            List of dripper device configurations
        """
        devices = self.data_store.get("devices") or []
        drippers = []

        for device in devices:
            if isinstance(device, dict) and device.get("deviceType") in [
                "Pump",
                "Valve",
            ]:
                drippers.append(device)
            elif hasattr(device, "device_type") and getattr(
                device, "device_type", None
            ) in ["Pump", "Valve"]:
                drippers.append(
                    {
                        "name": getattr(device, "device_name", str(device)),
                        "entity_id": (
                            getattr(device, "switches", [{}])[0].get("entity_id")
                            if getattr(device, "switches", [])
                            else None
                        ),
                        "device_type": getattr(device, "device_type", "Unknown"),
                    }
                )

        return drippers

    async def irrigate(self, duration: int = 30, is_emergency: bool = False) -> bool:
        """
        Perform irrigation cycle.

        Args:
            duration: Irrigation duration in seconds
            is_emergency: Whether this is an emergency irrigation

        Returns:
            True if irrigation successful, False otherwise
        """
        try:
            drippers = self.get_drippers()

            if not drippers:
                _LOGGER.error(f"{self.room} - No drippers available for irrigation")
                return False

            # Validate duration
            if duration <= 0 or duration > 600:  # Max 10 minutes
                _LOGGER.error(f"{self.room} - Invalid irrigation duration: {duration}s")
                return False

            _LOGGER.info(
                f"{self.room} - Starting irrigation: {duration}s with {len(drippers)} drippers"
            )

            # Start irrigation
            success = await self._activate_drippers(drippers, duration)

            if success:
                self.emergency_irrigation_count = 0  # Reset emergency counter
                await self._log_irrigation_success(
                    duration, len(drippers), is_emergency
                )
            else:
                await self._log_irrigation_failure(
                    duration, len(drippers), is_emergency
                )

            return success

        except Exception as e:
            _LOGGER.error(f"{self.room} - Irrigation error: {e}")
            await self._emergency_stop()
            return False

    async def _activate_drippers(
        self, drippers: List[Dict[str, Any]], duration: int
    ) -> bool:
        """
        Activate dripper devices for specified duration.

        Args:
            drippers: List of dripper devices
            duration: Duration in seconds

        Returns:
            True if activation successful
        """
        activated_entities = []

        try:
            # Turn on all drippers
            for dripper in drippers:
                entity_id = dripper.get("entity_id")
                if entity_id:
                    await self._turn_on_dripper(entity_id)
                    activated_entities.append(entity_id)

            if not activated_entities:
                _LOGGER.error(f"{self.room} - No dripper entities could be activated")
                return False

            # Wait for irrigation duration
            await asyncio.sleep(duration)

            # Turn off all drippers
            for entity_id in activated_entities:
                await self._turn_off_dripper(entity_id)

            _LOGGER.debug(
                f"{self.room} - Irrigation completed: {len(activated_entities)} drippers for {duration}s"
            )
            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error during dripper activation: {e}")
            # Emergency stop any activated drippers
            for entity_id in activated_entities:
                try:
                    await self._turn_off_dripper(entity_id)
                except:
                    pass  # Best effort cleanup
            return False

    async def _turn_on_dripper(self, entity_id: str):
        """
        Turn on a single dripper.

        Args:
            entity_id: Entity ID to turn on
        """
        try:
            domain, entity_name = entity_id.split(".", 1)

            if domain == "switch":
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": entity_id}
                )
            elif domain == "valve":
                await self.hass.services.async_call(
                    "valve", "open_valve", {"entity_id": entity_id}
                )
            else:
                _LOGGER.warning(f"{self.room} - Unsupported dripper domain: {domain}")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to turn on dripper {entity_id}: {e}")
            raise

    async def _turn_off_dripper(self, entity_id: str):
        """
        Turn off a single dripper.

        Args:
            entity_id: Entity ID to turn off
        """
        try:
            domain, entity_name = entity_id.split(".", 1)

            if domain == "switch":
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": entity_id}
                )
            elif domain == "valve":
                await self.hass.services.async_call(
                    "valve", "close_valve", {"entity_id": entity_id}
                )
            else:
                _LOGGER.warning(f"{self.room} - Unsupported dripper domain: {domain}")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to turn off dripper {entity_id}: {e}")
            raise

    async def adjust_ec_for_dryback(
        self, target_ec: float, increase: bool = True, step: float = 0.1
    ) -> bool:
        """
        Adjust EC levels to prepare for or recover from dryback.

        Args:
            target_ec: Target EC value
            increase: Whether to increase (True) or decrease (False) EC
            step: Adjustment step size

        Returns:
            True if adjustment successful
        """
        try:
            current_ec = self._get_current_ec()

            if current_ec is None:
                _LOGGER.warning(
                    f"{self.room} - Cannot adjust EC: no current EC reading"
                )
                return False

            if abs(current_ec - target_ec) < 0.05:  # Within tolerance
                _LOGGER.debug(f"{self.room} - EC already at target: {current_ec:.2f}")
                return True

            # Determine adjustment direction and amount
            if increase and current_ec < target_ec:
                adjustment = min(step, target_ec - current_ec)
                return await self._adjust_ec_to_target(target_ec, increase=True)
            elif not increase and current_ec > target_ec:
                adjustment = min(step, current_ec - target_ec)
                return await self._adjust_ec_to_target(target_ec, increase=False)
            else:
                _LOGGER.debug(
                    f"{self.room} - No EC adjustment needed: current={current_ec:.2f}, target={target_ec:.2f}"
                )
                return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error adjusting EC for dryback: {e}")
            return False

    async def _adjust_ec_to_target(
        self, target_ec: float, increase: bool = True
    ) -> bool:
        """
        Adjust EC to reach target value.

        Args:
            target_ec: Target EC value
            increase: Whether to increase EC

        Returns:
            True if adjustment successful
        """
        try:
            # This would interface with nutrient dosing system
            # For now, log the adjustment needed
            direction = "increase" if increase else "decrease"
            _LOGGER.info(
                f"{self.room} - EC adjustment needed: {direction} to {target_ec:.2f}"
            )

            # Placeholder for actual EC adjustment logic
            # Would integrate with tank feed manager or nutrient dosing system

            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error adjusting EC to target: {e}")
            return False

    async def _emergency_stop(self):
        """
        Emergency stop all irrigation activity.
        """
        try:
            _LOGGER.warning(f"{self.room} - Emergency stop triggered")

            drippers = self.get_drippers()
            for dripper in drippers:
                entity_id = dripper.get("entity_id")
                if entity_id:
                    try:
                        await self._turn_off_dripper(entity_id)
                    except Exception as e:
                        _LOGGER.error(
                            f"{self.room} - Failed to stop dripper {entity_id}: {e}"
                        )

            # Reset emergency counter
            self.emergency_irrigation_count = 0

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in emergency stop: {e}")

    async def turn_off_all_drippers(self):
        """
        Turn off all dripper devices.
        """
        try:
            drippers = self.get_drippers()
            for dripper in drippers:
                entity_id = dripper.get("entity_id")
                if entity_id:
                    await self._turn_off_dripper(entity_id)

            _LOGGER.info(f"{self.room} - All drippers turned off")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error turning off all drippers: {e}")

    def _get_current_ec(self) -> Optional[float]:
        """
        Get current EC reading from dataStore.

        Returns:
            Current EC value or None if unavailable
        """
        return self.data_store.getDeep("Hydro.ec_current")

    def calculate_irrigation_duration(
        self, vwc_deficit: float, dripper_flow_rate: float = 2.0
    ) -> int:
        """
        Calculate irrigation duration based on VWC deficit.

        Args:
            vwc_deficit: VWC percentage deficit
            dripper_flow_rate: Flow rate in ml/min per dripper

        Returns:
            Irrigation duration in seconds
        """
        if vwc_deficit <= 0:
            return 0

        # Rough calculation: 1% VWC deficit ≈ 20ml water per liter medium
        # Adjust based on dripper count and flow rate
        drippers = self.get_drippers()
        total_flow_rate = len(drippers) * dripper_flow_rate  # ml/min

        # Convert to seconds and ensure reasonable bounds
        duration_seconds = max(
            15, min(300, int((vwc_deficit * 20 * 60) / total_flow_rate))
        )

        return duration_seconds

    def should_trigger_emergency_irrigation(
        self, vwc: float, vwc_min: float, last_irrigation_hours: float
    ) -> bool:
        """
        Determine if emergency irrigation should be triggered.

        Args:
            vwc: Current VWC percentage
            vwc_min: Minimum VWC threshold
            last_irrigation_hours: Hours since last irrigation

        Returns:
            True if emergency irrigation needed
        """
        if vwc is None:
            return False

        # Emergency if VWC is critically low
        emergency_threshold = vwc_min * 0.85  # 85% of minimum

        if vwc <= emergency_threshold:
            self.emergency_irrigation_count += 1
            return self.emergency_irrigation_count <= 2  # Max 2 emergency shots

        return False

    async def _log_irrigation_success(
        self, duration: int, dripper_count: int, is_emergency: bool
    ):
        """
        Log successful irrigation event.

        Args:
            duration: Irrigation duration in seconds
            dripper_count: Number of drippers used
            is_emergency: Whether this was emergency irrigation
        """
        irrigation_type = "Emergency" if is_emergency else "Scheduled"

        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"{irrigation_type} irrigation completed: {duration}s with {dripper_count} drippers",
            },
        )

    async def _log_irrigation_failure(
        self, duration: int, dripper_count: int, is_emergency: bool
    ):
        """
        Log failed irrigation event.

        Args:
            duration: Intended irrigation duration in seconds
            dripper_count: Number of drippers that should have been used
            is_emergency: Whether this was emergency irrigation
        """
        irrigation_type = "Emergency" if is_emergency else "Scheduled"

        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"{irrigation_type} irrigation FAILED: {duration}s planned with {dripper_count} drippers",
            },
        )
