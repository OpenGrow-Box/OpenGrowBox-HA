"""
OpenGrowBox Emergency Actions Module

Handles emergency situations and critical failure responses.
Provides immediate action overrides when environmental conditions
become dangerously out of range.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class OGBEmergencyActions:
    """
    Emergency action handling for OpenGrowBox.

    Monitors for critical environmental conditions and provides
    immediate response actions that bypass normal dampening rules.

    Emergency conditions handled:
    - Critical overheating
    - Critical cold exposure
    - Immediate condensation risk
    - Critical humidity levels
    """

    def __init__(self, ogb: "OpenGrowBox"):
        """
        Initialize emergency actions.

        Args:
            ogb: Reference to the parent OpenGrowBox instance
        """
        self.ogb = ogb
        self._emergency_mode = False

    def check_emergency_conditions(self, tent_data: Dict[str, Any]) -> List[str]:
        """
        Check for emergency conditions that require immediate action.

        Args:
            tent_data: Current tent environmental data

        Returns:
            List of emergency condition identifiers
        """
        emergency_conditions = []

        # Critical overheating (above max temperature)
        if tent_data["temperature"] > tent_data["maxTemp"]:
            emergency_conditions.append("critical_overheat")

        # Critical cold exposure (below min temperature)
        if tent_data["temperature"] < tent_data["minTemp"]:
            emergency_conditions.append("critical_cold")

        # Immediate condensation risk (dewpoint too close to temperature)
        if tent_data["dewpoint"] >= tent_data["temperature"] - 0.5:
            emergency_conditions.append("immediate_condensation_risk")

        # Critical humidity levels
        if tent_data.get("humidity", 0) > 85:
            emergency_conditions.append("critical_humidity")

        # Critical O2 levels (for closed environment mode)
        o2_level = tent_data.get("o2")
        if o2_level is not None and o2_level < 19.0:  # Below 19% O2
            emergency_conditions.append("critical_o2_low")

        return emergency_conditions

    def activate_emergency_mode(self, emergency_conditions: List[str]):
        """
        Activate emergency mode, clearing all cooldowns for immediate action.

        Args:
            emergency_conditions: List of active emergency conditions
        """
        if not emergency_conditions:
            return

        _LOGGER.warning(
            f"{self.ogb.room}: EMERGENCY MODE ACTIVATED - Conditions: {emergency_conditions}"
        )

        # Set emergency mode flag
        self._emergency_mode = True

        # Clear all device cooldowns to allow immediate action
        self._clear_all_cooldowns()

        # Schedule emergency mode deactivation
        asyncio.create_task(self._deactivate_emergency_mode())

    async def _deactivate_emergency_mode(self):
        """
        Deactivate emergency mode after a delay to allow actions to complete.
        """
        await asyncio.sleep(5)  # 5 seconds
        self._emergency_mode = False
        _LOGGER.info(f"{self.ogb.room}: Emergency mode deactivated")

    def _clear_all_cooldowns(self):
        """
        Clear all device cooldowns during emergency situations.
        """
        now = datetime.now()

        # Access the action manager's cooldown history
        if hasattr(self.ogb, "actionManager") and hasattr(
            self.ogb.actionManager, "actionHistory"
        ):
            for capability in self.ogb.actionManager.actionHistory:
                self.ogb.actionManager.actionHistory[capability]["cooldown_until"] = now

            _LOGGER.warning(
                f"{self.ogb.room}: All device cooldowns cleared for emergency response"
            )

    def select_critical_emergency_action(
        self, action_map: List, emergency_conditions: List[str]
    ):
        """
        Select the most critical action needed for current emergency conditions.

        Args:
            action_map: List of available actions
            emergency_conditions: Current emergency conditions

        Returns:
            Most critical action to execute, or None
        """
        if not action_map or not emergency_conditions:
            return None

        # Priority mapping for emergency conditions to device capabilities
        emergency_priority = {
            "critical_overheat": ["canCool", "canExhaust", "canVentilate"],
            "critical_cold": ["canHeat"],
            "immediate_condensation_risk": [
                "canDehumidify",
                "canExhaust",
                "canVentilate",
            ],
            "critical_humidity": ["canDehumidify", "canExhaust"],
            "critical_o2_low": ["canVentilate", "canIntake", "canExhaust"],  # Force ventilation for O2
        }

        # Find highest priority action for current emergencies
        for condition in emergency_conditions:
            priority_capabilities = emergency_priority.get(condition, [])
            for capability in priority_capabilities:
                for action in action_map:
                    if (
                        hasattr(action, "capability")
                        and action.capability == capability
                        and hasattr(action, "action")
                        and action.action in ["Increase", "Reduce"]
                    ):
                        _LOGGER.critical(
                            f"{self.ogb.room}: Emergency override for {capability} - {action.action}"
                        )
                        return action

        # Fallback: Return first available action
        return action_map[0] if action_map else None

    async def execute_emergency_actions(
        self, tent_data: Dict[str, Any], action_map: List
    ):
        """
        Execute emergency actions when conditions are critical.

        Args:
            tent_data: Current environmental data
            action_map: Available actions to choose from

        Returns:
            Critical action to execute, or None
        """
        emergency_conditions = self.check_emergency_conditions(tent_data)

        if emergency_conditions:
            # Activate emergency mode
            self.activate_emergency_mode(emergency_conditions)

            # Select most critical action
            critical_action = self.select_critical_emergency_action(
                action_map, emergency_conditions
            )

            if critical_action:
                _LOGGER.critical(
                    f"{self.ogb.room}: Executing emergency action: {critical_action.capability} - {critical_action.action}"
                )
                return critical_action

        return None

    def is_emergency_active(self) -> bool:
        """
        Check if emergency mode is currently active.

        Returns:
            True if emergency mode is active
        """
        return self._emergency_mode

    def get_emergency_status(self) -> Dict[str, Any]:
        """
        Get current emergency status information.

        Returns:
            Dictionary with emergency status
        """
        tent_data = self.ogb.dataStore.get("tentData") or {}
        emergency_conditions = self.check_emergency_conditions(tent_data)

        return {
            "room": self.ogb.room,
            "emergency_mode_active": self._emergency_mode,
            "current_emergency_conditions": emergency_conditions,
            "temperature": tent_data.get("temperature"),
            "max_temp": tent_data.get("maxTemp"),
            "min_temp": tent_data.get("minTemp"),
            "humidity": tent_data.get("humidity"),
            "dewpoint": tent_data.get("dewpoint"),
            "o2_level": tent_data.get("o2"),
            "last_check": datetime.now().isoformat(),
        }

    async def handle_emergency_shutdown(self):
        """
        Handle emergency shutdown procedures.
        """
        _LOGGER.warning(f"{self.ogb.room}: Emergency shutdown initiated")

        # Ensure emergency mode is deactivated
        self._emergency_mode = False

        # Log emergency shutdown
        await self.ogb.eventManager.emit(
            "emergency_shutdown",
            {
                "room": self.ogb.room,
                "timestamp": datetime.now().isoformat(),
                "reason": "emergency_shutdown_called",
            },
        )
