"""
OpenGrowBox Medium Device Binding Manager

Handles device binding logic, condition evaluation, and triggering
for grow medium device management.

Responsibilities:
- Device binding creation and management
- Condition evaluation for device triggers
- Device action triggering and coordination
- Binding state management and validation
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class DeviceAction(Enum):
    """Actions that can be performed on devices"""

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    SET_LEVEL = "set_level"


class DeviceBinding:
    """
    Represents a binding between a sensor condition and device action.

    A binding defines when a device should be triggered based on sensor conditions.
    """

    def __init__(
        self,
        device_id: str,
        device_action: DeviceAction,
        conditions: Dict[str, Any],
        enabled: bool = True,
        priority: int = 0,
        cooldown_seconds: int = 0,
    ):
        """
        Initialize device binding.

        Args:
            device_id: Device entity ID to control
            device_action: Action to perform on the device
            conditions: Conditions that must be met to trigger
            enabled: Whether the binding is enabled
            priority: Priority level for conflicting actions
            cooldown_seconds: Minimum time between triggers
        """
        self.device_id = device_id
        self.device_action = device_action
        self.conditions = conditions
        self.enabled = enabled
        self.priority = priority
        self.cooldown_seconds = cooldown_seconds

        # Runtime state
        self.last_triggered: Optional[datetime] = None
        self.trigger_count = 0

    def can_trigger(self, sensor_values: Dict[str, Any]) -> bool:
        """
        Check if binding conditions are met for triggering.

        Args:
            sensor_values: Current sensor values

        Returns:
            True if conditions are met and binding can trigger
        """
        if not self.enabled:
            return False

        # Check cooldown
        if self._is_in_cooldown():
            return False

        # Evaluate conditions
        return self._evaluate_conditions(sensor_values)

    def _is_in_cooldown(self) -> bool:
        """
        Check if binding is currently in cooldown period.

        Returns:
            True if in cooldown
        """
        if not self.last_triggered or self.cooldown_seconds <= 0:
            return False

        time_since_trigger = (datetime.now() - self.last_triggered).total_seconds()
        return time_since_trigger < self.cooldown_seconds

    def _evaluate_conditions(self, sensor_values: Dict[str, Any]) -> bool:
        """
        Evaluate if sensor conditions are met.

        Args:
            sensor_values: Current sensor values

        Returns:
            True if all conditions are met
        """
        for condition_key, condition_value in self.conditions.items():
            if condition_key not in sensor_values:
                return False

            sensor_value = sensor_values[condition_key]

            if not self._check_condition(condition_key, sensor_value, condition_value):
                return False

        return True

    def _check_condition(
        self, condition_key: str, sensor_value: Any, condition_value: Any
    ) -> bool:
        """
        Check a single condition against sensor value.

        Args:
            condition_key: Condition identifier
            sensor_value: Current sensor value
            condition_value: Required condition value

        Returns:
            True if condition is met
        """
        try:
            # Handle different condition types
            if isinstance(condition_value, dict):
                # Range conditions
                if "min" in condition_value and sensor_value < condition_value["min"]:
                    return False
                if "max" in condition_value and sensor_value > condition_value["max"]:
                    return False
                return True

            elif (
                isinstance(condition_value, (list, tuple)) and len(condition_value) == 2
            ):
                # Min/max tuple
                min_val, max_val = condition_value
                return min_val <= sensor_value <= max_val

            else:
                # Direct comparison
                return sensor_value == condition_value

        except (TypeError, ValueError) as e:
            _LOGGER.error(f"Error evaluating condition {condition_key}: {e}")
            return False

    async def trigger(self, sensor_values: Dict[str, Any], hass=None) -> bool:
        """
        Trigger the device action.

        Args:
            sensor_values: Current sensor values (for logging)
            hass: Home Assistant instance

        Returns:
            True if trigger successful
        """
        try:
            if not hass:
                _LOGGER.warning(f"Cannot trigger {self.device_id}: No HA instance")
                return False

            # Perform the action
            success = await self._perform_action(hass)

            if success:
                self.last_triggered = datetime.now()
                self.trigger_count += 1

                _LOGGER.info(
                    f"Triggered {self.device_id} with action {self.device_action.value}"
                )

            return success

        except Exception as e:
            _LOGGER.error(f"Error triggering {self.device_id}: {e}")
            return False

    async def _perform_action(self, hass) -> bool:
        """
        Perform the actual device action.

        Args:
            hass: Home Assistant instance

        Returns:
            True if action performed successfully
        """
        try:
            domain, entity_name = self.device_id.split(".", 1)

            if self.device_action == DeviceAction.TURN_ON:
                if domain == "switch":
                    await hass.services.async_call(
                        "switch", "turn_on", {"entity_id": self.device_id}
                    )
                elif domain == "light":
                    await hass.services.async_call(
                        "light", "turn_on", {"entity_id": self.device_id}
                    )

            elif self.device_action == DeviceAction.TURN_OFF:
                if domain == "switch":
                    await hass.services.async_call(
                        "switch", "turn_off", {"entity_id": self.device_id}
                    )
                elif domain == "light":
                    await hass.services.async_call(
                        "light", "turn_off", {"entity_id": self.device_id}
                    )

            elif self.device_action == DeviceAction.SET_LEVEL:
                if domain == "light":
                    level = self.conditions.get("level", 50)
                    await hass.services.async_call(
                        "light",
                        "turn_on",
                        {"entity_id": self.device_id, "brightness_pct": level},
                    )

            return True

        except Exception as e:
            _LOGGER.error(
                f"Error performing action {self.device_action.value} on {self.device_id}: {e}"
            )
            return False

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert binding to dictionary for serialization.

        Returns:
            Dictionary representation
        """
        return {
            "device_id": self.device_id,
            "device_action": self.device_action.value,
            "conditions": self.conditions,
            "enabled": self.enabled,
            "priority": self.priority,
            "cooldown_seconds": self.cooldown_seconds,
            "last_triggered": (
                self.last_triggered.isoformat() if self.last_triggered else None
            ),
            "trigger_count": self.trigger_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeviceBinding":
        """
        Create binding from dictionary.

        Args:
            data: Dictionary with binding data

        Returns:
            DeviceBinding instance
        """
        binding = cls(
            device_id=data["device_id"],
            device_action=DeviceAction(data["device_action"]),
            conditions=data["conditions"],
            enabled=data.get("enabled", True),
            priority=data.get("priority", 0),
            cooldown_seconds=data.get("cooldown_seconds", 0),
        )

        # Restore runtime state
        binding.trigger_count = data.get("trigger_count", 0)
        if "last_triggered" in data and data["last_triggered"]:
            binding.last_triggered = datetime.fromisoformat(data["last_triggered"])

        return binding


class OGBMediumDeviceBindingManager:
    """
    Device binding manager for grow medium device control.

    Handles device binding creation, condition evaluation, and coordinated
    device triggering for automated medium management.
    """

    def __init__(self, room: str, data_store, event_manager, hass=None):
        """
        Initialize device binding manager.

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

        # Device bindings
        self.device_bindings: Dict[str, DeviceBinding] = {}
        self.binding_priorities: Dict[str, int] = {}

    def bind_device(
        self,
        device_id: str,
        device_action: DeviceAction,
        conditions: Dict[str, Any],
        priority: int = 0,
        cooldown_seconds: int = 0,
    ) -> bool:
        """
        Bind a device to sensor conditions.

        Args:
            device_id: Device entity ID
            device_action: Action to perform
            conditions: Trigger conditions
            priority: Binding priority
            cooldown_seconds: Cooldown between triggers

        Returns:
            True if binding created successfully
        """
        try:
            # Check for existing binding
            if device_id in self.device_bindings:
                _LOGGER.warning(
                    f"{self.room} - Device {device_id} already bound, replacing"
                )

            # Create new binding
            binding = DeviceBinding(
                device_id=device_id,
                device_action=device_action,
                conditions=conditions,
                priority=priority,
                cooldown_seconds=cooldown_seconds,
            )

            self.device_bindings[device_id] = binding
            self.binding_priorities[device_id] = priority

            _LOGGER.info(
                f"{self.room} - Bound device {device_id} with action {device_action.value}"
            )
            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error binding device {device_id}: {e}")
            return False

    def unbind_device(self, device_id: str) -> None:
        """
        Remove device binding.

        Args:
            device_id: Device entity ID to unbind
        """
        if device_id in self.device_bindings:
            del self.device_bindings[device_id]
            del self.binding_priorities[device_id]
            _LOGGER.info(f"{self.room} - Unbound device {device_id}")

    def enable_device(self, device_id: str) -> None:
        """
        Enable a device binding.

        Args:
            device_id: Device entity ID
        """
        if device_id in self.device_bindings:
            self.device_bindings[device_id].enabled = True
            _LOGGER.info(f"{self.room} - Enabled device binding for {device_id}")

    def disable_device(self, device_id: str) -> None:
        """
        Disable a device binding.

        Args:
            device_id: Device entity ID
        """
        if device_id in self.device_bindings:
            self.device_bindings[device_id].enabled = False
            _LOGGER.info(f"{self.room} - Disabled device binding for {device_id}")

    async def update_sensor_readings(self, sensor_values: Dict[str, Any]) -> List[str]:
        """
        Update sensor readings and evaluate device triggers.

        Args:
            sensor_values: Current sensor values

        Returns:
            List of triggered device IDs
        """
        triggered_devices = []

        try:
            # Evaluate all bindings
            candidates = []

            for device_id, binding in self.device_bindings.items():
                if binding.can_trigger(sensor_values):
                    candidates.append((device_id, binding))

            # Sort by priority (higher priority first)
            candidates.sort(key=lambda x: x[1].priority, reverse=True)

            # Trigger devices (avoid conflicts)
            triggered_actions = set()

            for device_id, binding in candidates:
                # Check for conflicting actions
                action_key = f"{binding.device_id}_{binding.device_action.value}"

                if action_key not in triggered_actions:
                    # Trigger the device
                    if self.hass:
                        success = await binding.trigger(sensor_values, self.hass)
                        if success:
                            triggered_devices.append(device_id)
                            triggered_actions.add(action_key)

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error updating sensor readings: {e}")

        return triggered_devices

    def _evaluate_conditions(self) -> None:
        """
        Evaluate binding conditions (placeholder for complex logic).
        """
        # This method would contain complex condition evaluation logic
        # For now, it's handled in the DeviceBinding class
        pass

    def _trigger_condition(self, condition: str, value: Any) -> List[str]:
        """
        Trigger devices based on specific condition.

        Args:
            condition: Condition identifier
            value: Condition value

        Returns:
            List of triggered device IDs
        """
        triggered = []

        try:
            for device_id, binding in self.device_bindings.items():
                if condition in binding.conditions:
                    condition_req = binding.conditions[condition]

                    # Simple condition check
                    if isinstance(condition_req, dict):
                        if "min" in condition_req and value >= condition_req["min"]:
                            triggered.append(device_id)
                        elif "max" in condition_req and value <= condition_req["max"]:
                            triggered.append(device_id)
                    elif value == condition_req:
                        triggered.append(device_id)

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error triggering condition {condition}: {e}")

        return triggered

    def get_device_bindings(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all device bindings.

        Returns:
            Dictionary of device bindings
        """
        return {
            device_id: binding.to_dict()
            for device_id, binding in self.device_bindings.items()
        }

    def get_binding_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a specific device binding.

        Args:
            device_id: Device entity ID

        Returns:
            Binding status dictionary or None
        """
        binding = self.device_bindings.get(device_id)
        if not binding:
            return None

        return {
            "device_id": device_id,
            "enabled": binding.enabled,
            "priority": binding.priority,
            "last_triggered": (
                binding.last_triggered.isoformat() if binding.last_triggered else None
            ),
            "trigger_count": binding.trigger_count,
            "cooldown_remaining": self._get_cooldown_remaining(device_id),
            "conditions": binding.conditions,
        }

    def _get_cooldown_remaining(self, device_id: str) -> float:
        """
        Get remaining cooldown time for a device.

        Args:
            device_id: Device entity ID

        Returns:
            Remaining cooldown in seconds
        """
        binding = self.device_bindings.get(device_id)
        if not binding or not binding.last_triggered:
            return 0.0

        elapsed = (datetime.now() - binding.last_triggered).total_seconds()
        remaining = max(0, binding.cooldown_seconds - elapsed)

        return remaining

    def clear_all_bindings(self) -> None:
        """
        Clear all device bindings.
        """
        self.device_bindings.clear()
        self.binding_priorities.clear()
        _LOGGER.info(f"{self.room} - Cleared all device bindings")

    def get_triggered_devices_count(self) -> Dict[str, int]:
        """
        Get trigger statistics for all devices.

        Returns:
            Dictionary with trigger counts
        """
        stats = {}
        for device_id, binding in self.device_bindings.items():
            stats[device_id] = binding.trigger_count

        return stats

    def validate_binding(self, device_id: str) -> Dict[str, Any]:
        """
        Validate a device binding configuration.

        Args:
            device_id: Device entity ID

        Returns:
            Validation result dictionary
        """
        binding = self.device_bindings.get(device_id)
        if not binding:
            return {"valid": False, "error": "Binding not found"}

        validation = {"valid": True, "warnings": [], "errors": []}

        # Validate conditions
        if not binding.conditions:
            validation["errors"].append("No conditions specified")

        # Validate device exists (if HA available)
        if self.hass and not self.hass.states.get(device_id):
            validation["warnings"].append("Device not found in Home Assistant")

        # Validate cooldown
        if binding.cooldown_seconds < 0:
            validation["errors"].append("Invalid cooldown time")

        if validation["errors"]:
            validation["valid"] = False

        return validation
