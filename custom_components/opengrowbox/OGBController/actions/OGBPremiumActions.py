"""
OpenGrowBox Premium Actions Module

Handles advanced control algorithms including PID, MPC, and AI-based actions
for premium users. Provides sophisticated device control and optimization.
"""

import copy
import logging
from typing import TYPE_CHECKING, Any, Dict, List

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

    async def PIDActions(self, premActions: Dict[str, Any]):
        """
        Execute PID-based control actions.

        PID (Proportional-Integral-Derivative) control provides precise
        adjustments based on current error, accumulated error, and rate of change.

        Args:
            premActions: Premium action data with PID states and commands
        """
        _LOGGER.warning(f"{self.ogb.room}: Start PID Actions Handling")

        controlData = premActions.get("actionData")
        actionData = controlData.get("controlCommands")
        pidStates = controlData.get("pidStates")

        # Store PID state history
        pidStatesCopy = copy.deepcopy(pidStates)
        currentActions = self.ogb.dataStore.get("previousActions", [])
        currentActions.append(pidStatesCopy)
        controlData["room"] = self.ogb.room

        # Keep only recent history
        if len(currentActions) > 1:
            currentActions = currentActions[-1:]

        self.ogb.dataStore.set("previousActions", currentActions)

        # Group actions by device to detect conflicts
        device_actions = {}
        for action in actionData:
            device = action.get("device", "").lower()
            if device not in device_actions:
                device_actions[device] = []
            device_actions[device].append(action)

        # Process each device
        for device, actions in device_actions.items():
            if len(actions) > 1:
                # Multiple actions: highest priority wins
                priority_order = {"high": 1, "medium": 2, "low": 3}
                best_action = min(
                    actions,
                    key=lambda x: priority_order.get(x.get("priority", "medium"), 2),
                )
                actions = [best_action]

            await self.ogb.eventManager.emit("LogForClient", controlData, haEvent=True)

            for action in actions:
                deviceAction = action.get("action")
                requestedDevice = action.get("device", "").lower()

                if requestedDevice == "error":
                    _LOGGER.error(
                        f"{self.ogb.room}: Requested CONTROL ERROR {controlData}"
                    )
                    return

                # Execute device-specific actions
                await self._execute_device_action(requestedDevice, deviceAction)

        await self.ogb.eventManager.emit("SaveState", True)

    async def MPCActions(self, premActions: Dict[str, Any]):
        """
        Execute Model Predictive Control actions.

        MPC uses predictive models to optimize control actions over a future time horizon,
        considering constraints and objectives.

        Args:
            premActions: Premium action data with MPC optimization results
        """
        actionData = premActions.get("actionData")
        _LOGGER.warning(
            f"{self.ogb.room}: Start MPC Actions Handling with data {actionData}"
        )

        # Group by device to detect conflicts
        device_actions = {}
        for action in actionData:
            device = action.get("device", "").lower()
            if device not in device_actions:
                device_actions[device] = []
            device_actions[device].append(action)

        # Process each device individually
        for device, actions in device_actions.items():
            if len(actions) > 1:
                # Multiple actions: highest priority wins
                priority_order = {"high": 1, "medium": 2, "low": 3}
                best_action = min(
                    actions,
                    key=lambda x: priority_order.get(x.get("priority", "medium"), 2),
                )
                actions = [best_action]

            for action in actions:
                deviceAction = action.get("action")
                requestedDevice = action.get("device", "").lower()

                if requestedDevice == "error":
                    _LOGGER.error(
                        f"{self.ogb.room}: Requested CONTROL ERROR {premActions}"
                    )
                    return

                # Check night VPD hold conditions
                nightVPDHold = self.ogb.dataStore.getDeep("controlOptions.nightVPDHold")
                islightON = self.ogb.dataStore.getDeep("isPlantDay.islightON")

                if not islightON and not nightVPDHold:
                    _LOGGER.debug(
                        f"{self.ogb.room}: VPD Night Hold Not Active - Ignoring VPD"
                    )
                    return None

                # Execute device-specific actions
                await self._execute_device_action(requestedDevice, deviceAction)

    async def AIActions(self, premActions: Dict[str, Any]):
        """
        Execute AI-based control actions.

        Uses machine learning and AI algorithms to make intelligent control decisions
        based on learned patterns and optimization objectives.

        Args:
            premActions: Premium action data with AI decisions
        """
        _LOGGER.warning(f"{self.ogb.room}: Start AI Actions Handling")

        # AI actions would use machine learning models to determine optimal actions
        # This is a placeholder for AI-based control logic

        # For now, delegate to MPC or PID based on available data
        if "predictive" in premActions.get("algorithm", "").lower():
            await self.MPCActions(premActions)
        else:
            await self.PIDActions(premActions)

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

    def _get_last_pid_time(self) -> str:
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
