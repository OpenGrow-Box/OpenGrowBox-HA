"""
OpenGrowBox Closed Environment Manager

Orchestrates closed environment control with ambient-aware temperature and humidity optimization.
Provides VPD-perfection-like control but specifically designed for sealed grow chambers.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..logic.ClosedControlLogic import ClosedControlLogic
from ..managers.OGBActionManager import OGBActionManager
from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class ClosedEnvironmentManager:
    """
    Manager for closed environment control with ambient-enhanced optimization.

    Provides sophisticated control similar to VPD perfection but optimized for
    sealed chambers using ambient data for intelligent temperature and humidity management.
    """

    def __init__(self, data_store, event_manager, room, hass, action_manager=None):
        """
        Initialize the closed environment manager.

        Args:
            data_store: Reference to the data store
            event_manager: Reference to the event manager
            room: Room identifier
            hass: Home Assistant instance
            action_manager: Reference to the action manager (optional)
        """
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.hass = hass
        self.action_manager = action_manager

        # Control logic engine
        self.control_logic = ClosedControlLogic(data_store, room)

        # Control parameters (stateless - no background loop)
        # Load from controlOptionData (like co2ppm, weights, etc.)
        self.ambient_influence_strength = self.data_store.getDeep(
            "controlOptionData.closedEnvironment.ambientInfluenceStrength", 0.3
        )  # 30% ambient influence by default

        _LOGGER.info(f"Closed Environment Manager initialized for {room}")

    async def execute_cycle(self):
        """
        Execute a complete closed environment control cycle (stateless).
        Called once per cycle when Closed Environment mode is active.
        """
        # Check if we're in Closed Environment mode
        tent_mode = self.data_store.get("tentMode")
        if tent_mode != "Closed Environment":
            _LOGGER.debug(f"{self.room}: Not in Closed Environment mode (current: {tent_mode}), skipping")
            return

        # Get current conditions
        capabilities = self.data_store.getDeep("capabilities")
        if not capabilities:
            _LOGGER.warning(f"No device capabilities available for {self.room}")
            return

        # Calculate optimal targets using ambient-enhanced logic
        temp_target = await self.control_logic.calculate_optimal_temperature_target()
        humidity_target = await self.control_logic.calculate_optimal_humidity_target()

        # Create action publications and process through ActionManager (for Premium API compatibility)
        action_map = []
        if temp_target is not None:
            action_map.extend(await self._create_temperature_actions(capabilities, temp_target))
        if humidity_target is not None:
            action_map.extend(await self._create_humidity_actions(capabilities, humidity_target))

        if action_map:
            # Use ActionManager to process actions
            if self.action_manager:
                await self.action_manager.checkLimitsAndPublicate(action_map)
            else:
                _LOGGER.warning(f"{self.room}: action_manager not available, actions skipped")
            _LOGGER.debug(f"Closed environment actions processed: {len(action_map)} actions")

        # Handle CO2, O2, and air recirculation in closed environment
        await self.event_manager.emit("maintain_co2", capabilities)
        await self.event_manager.emit("monitor_o2_safety", capabilities)
        await self.event_manager.emit("optimize_air_recirculation", capabilities)

        _LOGGER.debug(f"Closed environment cycle completed for {self.room}")

    async def _create_temperature_actions(self, capabilities: Dict[str, Any], target_temp: float) -> List[Any]:
        """
        Create temperature control actions based on ambient-enhanced targets.
        Returns list of OGBActionPublication objects for ActionManager processing.
        """
        current_temp = self.data_store.getDeep("tentData.temperature")
        actions = []

        if current_temp is None:
            return actions

        temp_delta = target_temp - current_temp

        if abs(temp_delta) > 1.0:  # 1°C tolerance
            action_type = "Increase" if temp_delta > 0 else "Reduce"
            actions.append(OGBActionPublication(
                capability="canHeat",
                action=action_type,
                Name=self.room,
                message=f"Closed Environment: {action_type} heating (delta: {temp_delta:.1f}°C)",
                priority="medium"
            ))
            _LOGGER.debug(f"Temperature control: {action_type} heating (delta: {temp_delta:.1f}°C)")

        return actions

    async def _create_humidity_actions(self, capabilities: Dict[str, Any], target_humidity: float) -> List[Any]:
        """
        Create humidity control actions based on ambient-enhanced targets.
        Returns list of OGBActionPublication objects for ActionManager processing.
        """
        current_humidity = self.data_store.getDeep("tentData.humidity")
        actions = []

        if current_humidity is None:
            return actions

        humidity_delta = target_humidity - current_humidity

        if abs(humidity_delta) > 3.0:  # 3% RH tolerance
            if humidity_delta > 0:
                actions.append(OGBActionPublication(
                    capability="canHumidify",
                    action="Increase",
                    Name=self.room,
                    message=f"Closed Environment: Increase humidification (delta: {humidity_delta:.1f}%)",
                    priority="medium"
                ))
                _LOGGER.debug(f"Humidity control: Increase humidification (delta: {humidity_delta:.1f}%)")
            else:
                actions.append(OGBActionPublication(
                    capability="canDehumidify",
                    action="Increase",
                    Name=self.room,
                    message=f"Closed Environment: Increase dehumidification (delta: {humidity_delta:.1f}%)",
                    priority="medium"
                ))
                _LOGGER.debug(f"Humidity control: Increase dehumidification (delta: {humidity_delta:.1f}%)")

        return actions

    def get_control_status(self) -> Dict[str, Any]:
        """
        Get current closed environment control status (stateless).

        Returns:
            Dictionary with control status information
        """
        return {
            "room": self.room,
            "mode": self.data_store.get("tentMode"),
            "ambient_influence_strength": self.ambient_influence_strength,
            "current_targets": {
                "temperature": self.data_store.getDeep("targets.temperature"),
                "humidity": self.data_store.getDeep("targets.humidity"),
            },
            "ambient_conditions": {
                "temperature": self.data_store.getDeep("tentData.AmbientTemp"),
                "humidity": self.data_store.getDeep("tentData.AmbientHum"),
            }
        }

    def set_ambient_influence_strength(self, strength: float):
        """
        Set the strength of ambient influence on control decisions.

        Args:
            strength: Influence strength (0.0 to 1.0)
        """
        self.ambient_influence_strength = max(0.0, min(1.0, strength))
        # Persist to data_store for stateless operation (in controlOptionData like other settings)
        self.data_store.setDeep("controlOptionData.closedEnvironment.ambientInfluenceStrength", self.ambient_influence_strength)
        _LOGGER.info(f"Ambient influence strength set to {self.ambient_influence_strength} for {self.room}")

    async def emergency_stop(self):
        """
        Emergency stop of all closed environment control (stateless - just logs warning).
        """
        _LOGGER.warning(f"Emergency stop noted for closed environment control in {self.room} (stateless mode)")