"""
OpenGrowBox Premium Actions Module

Handles advanced control algorithms including PID, MPC, and AI-based actions
for premium users. Provides sophisticated device control and optimization.
"""

import copy
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class OGBPremiumActions:
    """
    Premium action handling for advanced control algorithms.

    Provides sophisticated device control through:
    - PID (Proportional-Integral-Derivative) control
    - MPC (Model Predictive Control) optimization
    - AI-based decision making and learning
    """

    def __init__(self, ogb: "OpenGrowBox"):
        """
        Initialize premium actions.

        Args:
            ogb: Reference to the parent OpenGrowBox instance
        """
        self.ogb = ogb

    def _extract_control_payload(self, prem_actions: Dict[str, Any], controller_type: str) -> Dict[str, Any]:
        """Normalize incoming premium controller payload to a consistent structure."""
        action_data = prem_actions.get("actionData")

        # Some senders pass actionData as list directly (legacy)
        if isinstance(action_data, list):
            control_data = {
                "controlCommands": action_data,
                "controllerType": controller_type,
            }
        elif isinstance(action_data, dict):
            control_data = copy.deepcopy(action_data)
        else:
            control_data = {}

        # Compatibility aliases
        if not isinstance(control_data.get("controlCommands"), list):
            if isinstance(control_data.get("actions"), list):
                control_data["controlCommands"] = control_data.get("actions")
            else:
                control_data["controlCommands"] = []

        control_data["controllerType"] = (
            str(control_data.get("controllerType") or controller_type).upper()
        )
        control_data["room"] = self.ogb.room
        return control_data

    async def _execute_controller_actions(self, prem_actions: Dict[str, Any], controller_type: str):
        """Shared executor for PID/MPC/AI action payloads."""
        control_data = self._extract_control_payload(prem_actions, controller_type)
        action_data = control_data.get("controlCommands", [])

        if not isinstance(action_data, list):
            _LOGGER.error(
                f"{self.ogb.room}: Invalid {controller_type} controlCommands type: {type(action_data)}"
            )
            return

        _LOGGER.info(
            f"{self.ogb.room}: {controller_type} action payload received - commands={len(action_data)}"
        )

        if len(action_data) == 0:
            await self.ogb.eventManager.emit(
                "LogForClient",
                {
                    "Name": self.ogb.room,
                    "Type": controller_type,
                    "Message": f"{controller_type} cycle completed - no actions required",
                    "ControllerType": controller_type,
                },
                haEvent=True,
                debug_type="INFO",
            )
            await self.ogb.eventManager.emit("SaveState", True)
            return

        import time

        current_time = time.time()
        previous_actions = self.ogb.dataStore.get("previousActions") or []

        action_set = {
            "actions": [],
            "timestamp": current_time,
            "room": self.ogb.room,
            "controllerType": controller_type,
            "pidStates": copy.deepcopy(control_data.get("pidStates")) if control_data.get("pidStates") else None,
            "controllerStates": copy.deepcopy(control_data.get("controllerStates")) if control_data.get("controllerStates") else None,
        }

        for action in action_data:
            device = str(action.get("device", "")).lower()
            action_set["actions"].append(
                {
                    "device": device,
                    "action": action.get("action", "Eval"),
                    "priority": action.get("priority", "medium") or "medium",
                    "reason": action.get("reason", "") or f"{controller_type} control: {device}",
                    "timestamp": current_time,
                    "controllerType": controller_type,
                    "capability": f"can{device.capitalize()}",
                }
            )

        if action_set["actions"]:
            previous_actions.append(action_set)

        self.ogb.dataStore.set("previousActions", previous_actions[-5:])

        # Group by device and resolve conflicts (highest priority wins)
        device_actions: Dict[str, List[Dict[str, Any]]] = {}
        for action in action_data:
            device = str(action.get("device", "")).lower()
            device_actions.setdefault(device, []).append(action)

        await self.ogb.eventManager.emit("LogForClient", control_data, haEvent=True, debug_type="DEBUG")

        priority_order = {"high": 1, "medium": 2, "low": 3}
        for _, actions in device_actions.items():
            if len(actions) > 1:
                best_action = min(
                    actions,
                    key=lambda x: priority_order.get(x.get("priority", "medium"), 2),
                )
                actions = [best_action]

            for action in actions:
                device_action = str(action.get("action") or "Eval")
                requested_device = str(action.get("device", "")).lower()

                if requested_device == "error":
                    _LOGGER.error(f"{self.ogb.room}: Requested CONTROL ERROR {control_data}")
                    return

                await self._execute_device_action(requested_device, device_action)

        await self.ogb.eventManager.emit("SaveState", True)

    async def PIDActions(self, premActions: Dict[str, Any]):
        """
        Execute PID-based control actions.

        PID (Proportional-Integral-Derivative) control provides precise
        adjustments based on current error, accumulated error, and rate of change.

        Args:
            premActions: Premium action data with PID states and commands
        """
        _LOGGER.warning(f"{self.ogb.room}: Start PID Actions Handling")
        await self._execute_controller_actions(premActions, "PID")

    async def MPCActions(self, premActions: Dict[str, Any]):
        """
        Execute Model Predictive Control actions.

        MPC uses predictive models to optimize control actions over a future time horizon,
        considering constraints and objectives.

        Args:
            premActions: Premium action data with MPC optimization results
        """
        _LOGGER.warning(f"{self.ogb.room}: Start MPC Actions Handling")
        await self._execute_controller_actions(premActions, "MPC")

    async def AIActions(self, premActions: Dict[str, Any]):
        """
        Execute AI-based control actions.

        Uses machine learning and AI algorithms to make intelligent control decisions
        based on learned patterns and optimization objectives.

        Args:
            premActions: Premium action data with AI decisions
        """
        _LOGGER.warning(f"{self.ogb.room}: Start AI Actions Handling")
        await self._execute_controller_actions(premActions, "AI")

    async def _execute_device_action(self, device: str, action: str):
        """
        Execute action on a specific device.

        Args:
            device: Device name (exhaust, intake, ventilate, etc.)
            action: Action to perform (Increase, Reduce, etc.)
        """
        # Map device names to capability events
        device_mappings = {
            "exhaust": f"{action.capitalize()} Exhaust",
            "intake": f"{action.capitalize()} Intake",
            "ventilate": f"{action.capitalize()} Ventilation",
            "humidify": f"{action.capitalize()} Humidifier",
            "dehumidify": f"{action.capitalize()} Dehumidifier",
            "heat": f"{action.capitalize()} Heater",
            "cool": f"{action.capitalize()} Cooler",
            "climate": f"{action.capitalize()} Climate",
            "co2": f"{action.capitalize()} CO2",
            "light": f"{action.capitalize()} Light",
        }

        event_name = device_mappings.get(device)
        if event_name:
            await self.ogb.eventManager.emit(event_name, action)
            _LOGGER.warning(f"{self.ogb.room}: {action.capitalize()} {device}.")
        else:
            _LOGGER.error(
                f"{self.ogb.room}: Unknown device '{device}' for action '{action}'"
            )

    def get_pid_status(self) -> Dict[str, Any]:
        """
        Get current PID control status.

        Returns:
            Dictionary with PID control information
        """
        return {
            "room": self.ogb.room,
            "pid_enabled": True,  # Premium feature
            "previous_actions": self.ogb.dataStore.get("previousActions", []),
            "last_pid_update": self._get_last_pid_time(),
        }

    def get_mpc_status(self) -> Dict[str, Any]:
        """
        Get current MPC control status.

        Returns:
            Dictionary with MPC control information
        """
        return {
            "room": self.ogb.room,
            "mpc_enabled": True,  # Premium feature
            "prediction_horizon": 24,  # 24-hour prediction window
            "optimization_active": True,
        }

    def get_ai_status(self) -> Dict[str, Any]:
        """
        Get current AI control status.

        Returns:
            Dictionary with AI control information
        """
        return {
            "room": self.ogb.room,
            "ai_enabled": True,  # Premium feature
            "learning_active": self.ogb.dataStore.getDeep(
                "controlOptions.aiLearning", False
            ),
            "model_version": "1.0",  # Placeholder
            "last_training": None,  # Would track model training
        }

    def _get_last_pid_time(self) -> Optional[str]:
        """
        Get timestamp of last PID action.

        Returns:
            ISO timestamp string or None
        """
        previous_actions = self.ogb.dataStore.get("previousActions", [])
        if previous_actions:
            return previous_actions[-1].get("timestamp")
        return None

    async def reset_pid_history(self):
        """
        Reset PID action history for fresh start.
        """
        self.ogb.dataStore.set("previousActions", [])
        _LOGGER.info(f"{self.ogb.room}: PID action history reset")

    async def update_pid_parameters(self, parameters: Dict[str, Any]):
        """
        Update PID control parameters.

        Args:
            parameters: Dictionary with PID tuning parameters
        """
        # Store PID parameters in dataStore
        self.ogb.dataStore.setDeep("control.pid.parameters", parameters)
        _LOGGER.info(f"{self.ogb.room}: PID parameters updated: {parameters}")

    async def update_mpc_horizon(self, horizon: int):
        """
        Update MPC prediction horizon.

        Args:
            horizon: Prediction horizon in hours
        """
        # Store MPC horizon
        self.ogb.dataStore.setDeep("control.mpc.horizon", horizon)
        _LOGGER.info(f"{self.ogb.room}: MPC prediction horizon set to {horizon} hours")

    async def train_ai_model(self):
        """
        Trigger AI model training (premium feature).

        This would initiate background training of ML models
        using historical data and performance metrics.
        """
        _LOGGER.info(f"{self.ogb.room}: AI model training initiated")

        # Emit training start event
        await self.ogb.eventManager.emit(
            "ai_training_started",
            {
                "room": self.ogb.room,
                "timestamp": None,  # Would be current timestamp
            },
        )

        # In a real implementation, this would start a background training task
        # For now, just log that training was requested
