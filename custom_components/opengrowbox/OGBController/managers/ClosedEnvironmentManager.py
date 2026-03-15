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
        self._sync_ambient_influence()

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

        self.ambient_influence_strength = self.data_store.getDeep(
            "controlOptionData.closedEnvironment.ambientInfluenceStrength", self.ambient_influence_strength
        )
        self._sync_ambient_influence()

        # Preferred path: delegate full cycle to ClosedActions so Closed mode
        # uses its own NoVPD/safety logic without affecting other modes.
        if self.action_manager:
            _LOGGER.debug(
                f"{self.room}: Closed Environment using delegated ClosedActions path; fallback temp/humidity path disabled"
            )
            await self.event_manager.emit("closed_environment_cycle", capabilities)
            _LOGGER.debug(f"Closed environment cycle delegated to ClosedActions for {self.room}")
            return

        # Fallback path if action manager is not ready yet
        temp_target = await self.control_logic.calculate_optimal_temperature_target()
        humidity_target = await self.control_logic.calculate_optimal_humidity_target()

        action_map = []
        if temp_target is not None:
            action_map.extend(await self._create_temperature_actions(capabilities, temp_target))
        if humidity_target is not None:
            action_map.extend(await self._create_humidity_actions(capabilities, humidity_target))

        if action_map:
            _LOGGER.warning(f"{self.room}: Closed Environment fallback executed without action_manager")
            await self._publish_fallback_actions(action_map)
        
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

        try:
            current_temp = float(current_temp)
            target_temp = float(target_temp)
        except (TypeError, ValueError):
            _LOGGER.warning(f"{self.room}: Invalid temperature values for Closed Environment")
            return actions

        temp_delta = target_temp - current_temp

        if abs(temp_delta) > 1.0:  # 1°C tolerance
            if temp_delta > 0 and capabilities.get("canHeat", {}).get("state", False):
                actions.append(OGBActionPublication(
                    capability="canHeat",
                    action="Increase",
                    Name=self.room,
                    message=f"Closed Environment: Increase heating (delta: {temp_delta:.1f}°C)",
                    priority="medium"
                ))
                _LOGGER.debug(f"Temperature control: Increase heating (delta: {temp_delta:.1f}°C)")
            elif temp_delta > 0 and capabilities.get("canClimate", {}).get("state", False):
                actions.append(OGBActionPublication(
                    capability="canClimate",
                    action="Increase",
                    Name=self.room,
                    message=f"Closed Environment: Climate heat support (delta: {temp_delta:.1f}°C)",
                    priority="medium"
                ))
                _LOGGER.debug(f"Temperature control: Climate heat support (delta: {temp_delta:.1f}°C)")
            elif temp_delta < 0 and capabilities.get("canCool", {}).get("state", False):
                actions.append(OGBActionPublication(
                    capability="canCool",
                    action="Increase",
                    Name=self.room,
                    message=f"Closed Environment: Increase cooling (delta: {abs(temp_delta):.1f}°C)",
                    priority="medium"
                ))
                _LOGGER.debug(f"Temperature control: Increase cooling (delta: {abs(temp_delta):.1f}°C)")
            elif temp_delta < 0 and capabilities.get("canClimate", {}).get("state", False):
                actions.append(OGBActionPublication(
                    capability="canClimate",
                    action="Reduce",
                    Name=self.room,
                    message=f"Closed Environment: Climate cool support (delta: {abs(temp_delta):.1f}°C)",
                    priority="medium"
                ))
                _LOGGER.debug(f"Temperature control: Climate cool support (delta: {abs(temp_delta):.1f}°C)")

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

        try:
            current_humidity = float(current_humidity)
            target_humidity = float(target_humidity)
        except (TypeError, ValueError):
            _LOGGER.warning(f"{self.room}: Invalid humidity values for Closed Environment")
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
                if capabilities.get("canDehumidify", {}).get("state", False):
                    actions.append(OGBActionPublication(
                        capability="canDehumidify",
                        action="Increase",
                        Name=self.room,
                        message=f"Closed Environment: Increase dehumidification (delta: {humidity_delta:.1f}%)",
                        priority="medium"
                    ))
                    _LOGGER.debug(f"Humidity control: Increase dehumidification (delta: {humidity_delta:.1f}%)")
                elif capabilities.get("canClimate", {}).get("state", False):
                    actions.append(OGBActionPublication(
                        capability="canClimate",
                        action="Increase",
                        Name=self.room,
                        message=f"Closed Environment: Climate dry support (delta: {humidity_delta:.1f}%)",
                        priority="medium"
                    ))
                    _LOGGER.debug(f"Humidity control: Climate dry support (delta: {humidity_delta:.1f}%)")

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
                "temperature": self._get_tentdata_midpoint_target("minTemp", "maxTemp")
                or self._get_stage_midpoint_target("minTemp", "maxTemp"),
                "humidity": self._get_tentdata_midpoint_target("minHumidity", "maxHumidity")
                or self._get_stage_midpoint_target("minHumidity", "maxHumidity"),
            },
            "ambient_conditions": {
                "temperature": self.data_store.getDeep("tentData.AmbientTemp"),
                "humidity": self.data_store.getDeep("tentData.AmbientHum"),
            }
        }

    async def _publish_fallback_actions(self, action_map: List[Any]):
        """Best-effort action publishing when action manager is unavailable."""
        for action in action_map:
            capability = getattr(action, "capability", None)
            action_type = getattr(action, "action", None)
            if not capability or not action_type:
                continue

            try:
                if capability == "canHeat":
                    await self.event_manager.emit(f"{action_type} Heater", action_type)
                elif capability == "canCool":
                    await self.event_manager.emit(f"{action_type} Cooler", action_type)
                elif capability == "canHumidify":
                    await self.event_manager.emit(f"{action_type} Humidifier", action_type)
                elif capability == "canDehumidify":
                    await self.event_manager.emit(f"{action_type} Dehumidifier", action_type)
                elif capability == "canClimate":
                    await self.event_manager.emit(f"{action_type} Climate", action_type)
            except Exception as e:
                _LOGGER.error(f"{self.room}: Fallback closed action failed for {capability} {action_type}: {e}")

    def _sync_ambient_influence(self):
        """Apply persisted ambient influence to closed control logic."""
        try:
            influence = float(self.ambient_influence_strength)
        except (TypeError, ValueError):
            influence = 0.3

        self.control_logic.set_ambient_influence(
            temp_influence=influence,
            humidity_influence=influence,
        )

    def _get_stage_midpoint_target(self, min_key: str, max_key: str) -> Optional[float]:
        """Return plant-stage midpoint for status/fallback reporting."""
        plant_stage = self.data_store.get("plantStage")
        if not plant_stage:
            return None

        stage_data = self.data_store.getDeep(f"plantStages.{plant_stage}") or {}
        min_value = stage_data.get(min_key)
        max_value = stage_data.get(max_key)
        if min_value is None or max_value is None:
            return None

        try:
            return (float(min_value) + float(max_value)) / 2
        except (TypeError, ValueError):
            return None

    def _get_tentdata_midpoint_target(self, min_key: str, max_key: str) -> Optional[float]:
        """Return midpoint of active room min/max values from tentData."""
        min_value = self.data_store.getDeep(f"tentData.{min_key}")
        max_value = self.data_store.getDeep(f"tentData.{max_key}")
        if min_value is None or max_value is None:
            return None

        try:
            return (float(min_value) + float(max_value)) / 2
        except (TypeError, ValueError):
            return None

    def set_ambient_influence_strength(self, strength: float):
        """
        Set the strength of ambient influence on control decisions.

        Args:
            strength: Influence strength (0.0 to 1.0)
        """
        self.ambient_influence_strength = max(0.0, min(1.0, strength))
        # Persist to data_store for stateless operation (in controlOptionData like other settings)
        self.data_store.setDeep("controlOptionData.closedEnvironment.ambientInfluenceStrength", self.ambient_influence_strength)
        self._sync_ambient_influence()
        _LOGGER.info(f"Ambient influence strength set to {self.ambient_influence_strength} for {self.room}")

    async def emergency_stop(self):
        """
        Emergency stop of all closed environment control (stateless - just logs warning).
        """
        _LOGGER.warning(f"Emergency stop noted for closed environment control in {self.room} (stateless mode)")
