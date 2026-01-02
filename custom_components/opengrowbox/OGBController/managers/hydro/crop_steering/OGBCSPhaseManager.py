"""
OpenGrowBox Crop Steering Phase Manager

Handles phase logic, transitions, and cycle management for the Crop Steering system.

Responsibilities:
- Phase detection and transitions (P0-P3)
- Automatic/manual cycle coordination
- Phase timing and triggers
- Mode parsing and validation
"""

import logging
from enum import Enum
from typing import Any, Dict, Optional

from .OGBCSConfigurationManager import CSMode

_LOGGER = logging.getLogger(__name__)


class OGBCSPhaseManager:
    """
    Phase manager for Crop Steering.

    Handles phase transitions, cycle management, and automatic/manual mode coordination.
    """

    def __init__(self, data_store, room: str, event_manager=None):
        """
        Initialize phase manager.

        Args:
            data_store: Data store instance
            room: Room identifier
            event_manager: Event manager instance (optional)
        """
        self.data_store = data_store
        self.room = room
        self.event_manager = event_manager

    def parse_mode(self, crop_mode: str) -> "CSMode":
        """
        Parse mode string into CSMode enum.

        Args:
            crop_mode: Mode string from configuration

        Returns:
            CSMode enum value
        """
        mode_map = {
            "Disabled": "DISABLED",
            "Config": "CONFIG",
            "Automatic": "AUTOMATIC",
            "Manual-p0": "MANUAL_P0",
            "Manual-p1": "MANUAL_P1",
            "Manual-p2": "MANUAL_P2",
            "Manual-p3": "MANUAL_P3",
        }

        mode_key = mode_map.get(crop_mode, "DISABLED")
        return getattr(CSMode, mode_key, CSMode.DISABLED)

    async def determine_initial_phase(
        self, mode: "CSMode", sensor_data: Dict[str, Any]
    ) -> str:
        """
        Determine initial phase based on mode and sensor data.

        Args:
            mode: Current CSMode
            sensor_data: Current sensor readings

        Returns:
            Initial phase identifier (p0, p1, p2, p3)
        """
        if mode == CSMode.AUTOMATIC:
            # Determine based on light status and VWC
            is_light_on = self._is_light_on()
            vwc = sensor_data.get("vwc")

            if not is_light_on:
                # Night time - start with P3 (dryback)
                return "p3"
            else:
                # Day time - check VWC level
                if vwc is None:
                    return "p0"  # Monitoring if no VWC data
                elif vwc < 60:
                    return "p1"  # Saturation needed
                else:
                    return "p2"  # Maintenance

        elif mode in [
            CSMode.MANUAL_P0,
            CSMode.MANUAL_P1,
            CSMode.MANUAL_P2,
            CSMode.MANUAL_P3,
        ]:
            # Manual mode - extract phase from mode
            return mode.value.split("_")[-1].lower()

        else:
            # Disabled/Config - no active phase
            return "p0"

    def get_manual_phase_settings(self, phase: str) -> Dict[str, Any]:
        """
        Get manual phase settings from dataStore.

        Args:
            phase: Phase identifier

        Returns:
            Manual phase settings dictionary
        """
        manual_settings = self.data_store.getDeep(
            f"CropSteering.Manual.{phase.upper()}"
        )

        if not manual_settings:
            _LOGGER.warning(f"{self.room} - No manual settings found for phase {phase}")
            return {}

        return {
            "VWCTarget": manual_settings.get("VWCTarget", 60.0),
            "VWCMin": manual_settings.get("VWCMin", 55.0),
            "VWCMax": manual_settings.get("VWCMax", 70.0),
            "ECTarget": manual_settings.get("ECTarget", 2.0),
            "MinEC": manual_settings.get("MinEC", 1.8),
            "MaxEC": manual_settings.get("MaxEC", 2.4),
            "irrigation_duration": manual_settings.get("IrrigationDuration", 30),
            "irrigation_interval": manual_settings.get("IrrigationInterval", 1800),
        }

    async def handle_phase_transition(
        self, current_phase: str, target_phase: str, reason: str
    ) -> bool:
        """
        Handle phase transitions with logging and notifications.

        Args:
            current_phase: Current phase identifier
            target_phase: Target phase identifier
            reason: Reason for transition

        Returns:
            True if transition successful
        """
        if current_phase == target_phase:
            return True

        # Log phase change
        await self._log_phase_change(current_phase, target_phase, reason)

        # Update stored phase
        self.data_store.setDeep("CropSteering.CurrentPhase", target_phase)

        # Emit event for other components
        await self.event_manager.emit(
            "CSPhaseChanged",
            {
                "room": self.room,
                "from_phase": current_phase,
                "to_phase": target_phase,
                "reason": reason,
                "timestamp": self._get_timestamp(),
            },
        )

        _LOGGER.info(
            f"{self.room} - Phase transition: {current_phase} → {target_phase} ({reason})"
        )

        return True

    def should_transition_phase(
        self, current_phase: str, sensor_data: Dict[str, Any], light_status: bool
    ) -> Optional[str]:
        """
        Determine if phase transition is needed based on conditions.

        Args:
            current_phase: Current phase
            sensor_data: Current sensor data
            light_status: Whether lights are on

        Returns:
            Target phase if transition needed, None otherwise
        """
        vwc = sensor_data.get("vwc")

        if current_phase == "p0":
            # P0 transitions to P1 when VWC drops below minimum
            if vwc is not None and vwc <= 55.0:  # VWCMin from p0 preset
                return "p1"

        elif current_phase == "p1":
            # P1 transitions to P2 when target VWC reached
            if vwc is not None and vwc >= 68.0:  # VWCMax from p1 preset
                return "p2"

        elif current_phase == "p2":
            # P2 transitions to P3 when lights turn off
            if not light_status:
                return "p3"

        elif current_phase == "p3":
            # P3 transitions to P2 when lights turn on
            if light_status:
                return "p2"

        return None

    async def execute_automatic_cycle(
        self, phase: str, config: Dict[str, Any], sensor_data: Dict[str, Any]
    ) -> bool:
        """
        Execute automatic cycle for given phase.

        Args:
            phase: Phase identifier
            config: Phase configuration
            sensor_data: Current sensor data

        Returns:
            True if cycle executed successfully
        """
        try:
            if phase == "p0":
                return await self._handle_phase_p0_auto(sensor_data, config)
            elif phase == "p1":
                return await self._handle_phase_p1_auto(sensor_data, config)
            elif phase == "p2":
                return await self._handle_phase_p2_auto(sensor_data, config)
            elif phase == "p3":
                return await self._handle_phase_p3_auto(sensor_data, config)
            else:
                _LOGGER.error(
                    f"{self.room} - Unknown phase for automatic cycle: {phase}"
                )
                return False

        except Exception as e:
            _LOGGER.error(
                f"{self.room} - Error in automatic cycle for phase {phase}: {e}"
            )
            return False

    async def execute_manual_cycle(
        self, phase: str, config: Dict[str, Any], sensor_data: Dict[str, Any]
    ) -> bool:
        """
        Execute manual cycle for given phase.

        Args:
            phase: Phase identifier
            config: Phase configuration
            sensor_data: Current sensor data

        Returns:
            True if cycle executed successfully
        """
        try:
            # Manual cycles follow similar logic to automatic but with different triggers
            return await self.execute_automatic_cycle(phase, config, sensor_data)

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in manual cycle for phase {phase}: {e}")
            return False

    async def _handle_phase_p0_auto(
        self, sensor_data: Dict[str, Any], config: Dict[str, Any]
    ) -> bool:
        """
        Handle automatic P0 phase (Monitoring).

        Waits for VWC to drop below minimum threshold.
        """
        vwc = sensor_data.get("vwc")

        if vwc is None:
            _LOGGER.warning(f"{self.room} - P0: No VWC data available")
            return False

        vwc_min = config.get("VWCMin", 55.0)

        if vwc <= vwc_min:
            _LOGGER.info(
                f"{self.room} - P0: VWC {vwc:.1f}% below minimum {vwc_min:.1f}%, ready for P1"
            )
            return True
        else:
            _LOGGER.debug(
                f"{self.room} - P0: Monitoring - VWC {vwc:.1f}%, waiting for ≤{vwc_min:.1f}%"
            )
            return False

    async def _handle_phase_p1_auto(
        self, sensor_data: Dict[str, Any], config: Dict[str, Any]
    ) -> bool:
        """
        Handle automatic P1 phase (Saturation).

        Performs irrigation cycles until target VWC is reached.
        """
        # This would contain the saturation logic
        # For now, return placeholder
        _LOGGER.debug(f"{self.room} - P1: Saturation cycle (placeholder)")
        return True

    async def _handle_phase_p2_auto(
        self, sensor_data: Dict[str, Any], config: Dict[str, Any]
    ) -> bool:
        """
        Handle automatic P2 phase (Day Maintenance).

        Maintains VWC levels during light hours.
        """
        # This would contain the maintenance logic
        # For now, return placeholder
        _LOGGER.debug(f"{self.room} - P2: Maintenance cycle (placeholder)")
        return True

    async def _handle_phase_p3_auto(
        self, sensor_data: Dict[str, Any], config: Dict[str, Any]
    ) -> bool:
        """
        Handle automatic P3 phase (Night Dryback).

        Allows controlled dryback during dark hours.
        """
        # This would contain the dryback logic
        # For now, return placeholder
        _LOGGER.debug(f"{self.room} - P3: Dryback cycle (placeholder)")
        return True

    def _is_light_on(self) -> bool:
        """
        Check if lights are currently on.

        Returns:
            True if lights are on, False otherwise
        """
        light_status = self.data_store.getDeep("isPlantDay.lightOn")
        return bool(light_status)

    def _get_timestamp(self) -> str:
        """
        Get current timestamp string.

        Returns:
            ISO format timestamp
        """
        from datetime import datetime

        return datetime.now().isoformat()

    async def _log_phase_change(self, from_phase: str, to_phase: str, reason: str):
        """
        Log phase change event.

        Args:
            from_phase: Previous phase
            to_phase: New phase
            reason: Reason for change
        """
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"Phase changed: {from_phase.upper()} → {to_phase.upper()} ({reason})",
                "Timestamp": self._get_timestamp(),
            },
        )
