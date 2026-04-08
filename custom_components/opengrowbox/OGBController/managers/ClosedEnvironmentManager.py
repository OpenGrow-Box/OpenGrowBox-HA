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

        _LOGGER.warning(f"{self.room}: Closed Environment action_manager not ready, skipping cycle")

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
