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

    def __init__(self, data_store, event_manager, room, hass):
        """
        Initialize the closed environment manager.

        Args:
            data_store: Reference to the data store
            event_manager: Reference to the event manager
            room: Room identifier
            hass: Home Assistant instance
        """
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.hass = hass

        # Control logic engine
        self.control_logic = ClosedControlLogic(data_store, room)

        # Control parameters
        self.control_active = False
        self.ambient_influence_strength = 0.3  # 30% ambient influence by default
        self.update_interval = 60  # seconds

        # Control state
        self.last_control_time = None
        self.control_task: Optional[asyncio.Task] = None

        # Register event handlers
        self.event_manager.on("closed_environment_cycle", self._handle_control_cycle)
        self.event_manager.on("ambient_data_updated", self._handle_ambient_update)

        _LOGGER.info(f"Closed Environment Manager initialized for {room}")

    async def start_control(self):
        """
        Start the closed environment control loop.
        """
        if self.control_active:
            return

        self.control_active = True
        self.control_task = asyncio.create_task(self._control_loop())
        _LOGGER.info(f"Closed environment control started for {self.room}")

    async def stop_control(self):
        """
        Stop the closed environment control loop.
        """
        self.control_active = False

        if self.control_task:
            self.control_task.cancel()
            try:
                await self.control_task
            except asyncio.CancelledError:
                pass

        _LOGGER.info(f"Closed environment control stopped for {self.room}")

    async def _control_loop(self):
        """
        Main control loop for closed environment management.
        Runs similar to VPD perfection but with ambient-enhanced logic.
        """
        while self.control_active:
            try:
                await self._execute_control_cycle()
                await asyncio.sleep(self.update_interval)

            except Exception as e:
                _LOGGER.error(f"Error in closed environment control loop for {self.room}: {e}")
                await asyncio.sleep(30)  # Back off on errors

    async def _execute_control_cycle(self):
        """
        Execute a complete closed environment control cycle.
        """
        # Get current conditions
        capabilities = self.data_store.getDeep("capabilities")
        if not capabilities:
            _LOGGER.warning(f"No device capabilities available for {self.room}")
            return

        # Calculate optimal targets using ambient-enhanced logic
        temp_target = await self.control_logic.calculate_optimal_temperature_target()
        humidity_target = await self.control_logic.calculate_optimal_humidity_target()

        # Store targets for reference (using standard datastore paths)
        self.data_store.setDeep("targets.temperature", temp_target)
        self.data_store.setDeep("targets.humidity", humidity_target)

        # Create action publications and process through ActionManager (for Premium API compatibility)
        action_map = []
        if temp_target is not None:
            action_map.extend(await self._create_temperature_actions(capabilities, temp_target))
        if humidity_target is not None:
            action_map.extend(await self._create_humidity_actions(capabilities, humidity_target))

        if action_map:
            # Use ActionManager to process actions (creates Premium API output)
            await self.event_manager.emit("checkLimitsAndPublicate", action_map)
            _LOGGER.debug(f"Closed environment actions processed: {len(action_map)} actions")

        # Handle CO2, O2, and air recirculation in closed environment
        await self.event_manager.emit("maintain_co2", capabilities)
        await self.event_manager.emit("monitor_o2_safety", capabilities)
        await self.event_manager.emit("optimize_air_recirculation", capabilities)

        # Update control timestamp
        self.last_control_time = datetime.now()

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

    async def _handle_control_cycle(self, capabilities: Dict[str, Any]):
        """
        Handle manual control cycle requests.
        """
        await self._execute_control_cycle()

    async def _handle_ambient_update(self, data: Dict[str, Any]):
        """
        Handle ambient data updates for immediate control adjustments.
        """
        _LOGGER.debug(f"Ambient data updated for {self.room}, triggering control cycle")
        await self._execute_control_cycle()

    def get_control_status(self) -> Dict[str, Any]:
        """
        Get current closed environment control status.

        Returns:
            Dictionary with control status information
        """
        return {
            "room": self.room,
            "control_active": self.control_active,
            "ambient_influence_strength": self.ambient_influence_strength,
            "update_interval": self.update_interval,
            "last_control_time": self.last_control_time.isoformat() if self.last_control_time else None,
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
        _LOGGER.info(f"Ambient influence strength set to {self.ambient_influence_strength} for {self.room}")

    async def emergency_stop(self):
        """
        Emergency stop of all closed environment control.
        """
        await self.stop_control()
        _LOGGER.warning(f"Emergency stop initiated for closed environment control in {self.room}")