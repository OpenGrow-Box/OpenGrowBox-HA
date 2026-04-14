"""
OpenGrowBox Dampening Actions Module

Handles action filtering, conflict resolution, and dampening logic.
Provides intelligent action prioritization and prevents device wear
through cooldown management and conflict resolution.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from ..managers.OGBActionManager import OGBActionManager

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class OGBDampeningActions:
    """
    Action dampening and conflict resolution for OpenGrowBox.

    Provides intelligent action filtering based on:
    - Device cooldowns and wear prevention
    - Environmental condition prioritization
    - Action conflict resolution
    - Emergency override handling
    """

    def __init__(self, ogb: "OpenGrowBox"):
        """
        Initialize dampening actions.

        Args:
            ogb: Reference to the parent OpenGrowBox instance
        """
        self.ogb = ogb
        self.action_manager: OGBActionManager = ogb.actionManager

    async def process_actions_with_dampening(self, action_map: List) -> List:
        """
        Process actions with full dampening logic including weights and conflicts.

        Args:
            action_map: Initial list of actions to process

        Returns:
            Final list of actions to execute after dampening
        """
        _LOGGER.debug(
            f"{self.ogb.room}: Processing {len(action_map)} actions with dampening"
        )

        # Get control settings
        own_weights = self.ogb.dataStore.getDeep("controlOptions.ownWeights")
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        night_vpd_hold = self.ogb.dataStore.getDeep("controlOptions.nightVPDHold")
        is_light_on = self.ogb.dataStore.getDeep("isPlantDay.islightON")

        # Check night hold conditions
        if not is_light_on and not night_vpd_hold:
            _LOGGER.debug(f"{self.ogb.room}: VPD Night Hold Not Active - Ignoring VPD")
            await self._night_hold_fallback(action_map)
            return []

        # Calculate weights and deviations
        temp_weight, hum_weight = self._calculate_weights(own_weights)
        (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
         tempPercentage, humPercentage, weight_message) = self._calculate_deviations(
            temp_weight, hum_weight
        )

        # Emit VPD Perfection diagnostics with REAL deviations for display
        current_vpd = self.ogb.dataStore.getDeep("vpd.current")
        target_vpd = self.ogb.dataStore.getDeep("vpd.perfection")
        if target_vpd is None:
            target_vpd = self.ogb.dataStore.getDeep("vpd.targeted")

        target_min = self.ogb.dataStore.getDeep("vpd.perfectMin")
        if target_min is None:
            target_min = self.ogb.dataStore.getDeep("vpd.targetedMin")

        target_max = self.ogb.dataStore.getDeep("vpd.perfectMax")
        if target_max is None:
            target_max = self.ogb.dataStore.getDeep("vpd.targetedMax")

        tent_data = self.ogb.dataStore.get("tentData")

        await self.ogb.eventManager.emit(
            "LogForClient",
            {
                "Name": self.ogb.room,
                "message": "VPD Perfection chain started",
                "vpdCurrent": current_vpd,
                "vpdTarget": target_vpd,
                "vpdTargetMin": target_min,
                "vpdTargetMax": target_max,
                "incomingActions": len(action_map),
                # Use REAL deviations for display
                "tempDeviation": real_temp_dev,
                "humDeviation": real_hum_dev,
                "tempPercentage": tempPercentage,
                "humPercentage": humPercentage,
                "tempWeight": temp_weight,
                "humWeight": hum_weight,
                "weightMessage": weight_message,
                "tempCurrent": tent_data.get("temperature"),
                "humCurrent": tent_data.get("humidity"),
                "tempMin": tent_data.get("minTemp"),
                "tempMax": tent_data.get("maxTemp"),
                "humMin": tent_data.get("minHumidity"),
                "humMax": tent_data.get("maxHumidity"),
            },
            haEvent=True,
            debug_type="DEBUG",
        )

        # Publish weight information with REAL deviations
        await self._publish_weight_info(
            weight_message, real_temp_dev, real_hum_dev, tempPercentage, humPercentage,
            temp_weight, hum_weight
        )

        # Check for emergency conditions
        emergency_conditions = self.action_manager._getEmergencyOverride(tent_data)
        if emergency_conditions:
            await self.action_manager._clearCooldownForEmergency(emergency_conditions)

        # Get capabilities and determine optimal devices
        caps = self.ogb.dataStore.get("capabilities")
        # Use WEIGHTED deviations for VPD status determination (for action prioritization)
        vpd_status = self._determine_vpd_status(
            weighted_temp_dev, weighted_hum_dev, tent_data
        )
        optimal_devices = self._get_optimal_devices(vpd_status)

        # Enhance action map with additional actions based on conditions
        enhanced_action_map = self._enhance_action_map(
            action_map,
            weighted_temp_dev,
            weighted_hum_dev,
            tent_data,
            caps,
            vpd_light_control,
            is_light_on,
            optimal_devices,
            vpd_status,  # Add VPD status for context-aware enhancement
        )

        # Apply dampening filter
        dampened_actions, blocked_actions = (
            await self.action_manager._filterActionsByDampening(
                enhanced_action_map, weighted_temp_dev, weighted_hum_dev
            )
        )

        # Handle empty action list after dampening
        if not dampened_actions:
            await self._handle_blocked_actions(
                enhanced_action_map, emergency_conditions, temp_deviation, hum_deviation
            )
            await self.ogb.eventManager.emit(
                "LogForClient",
                {
                    "Name": self.ogb.room,
                    "message": "VPD Perfection chain: all actions blocked by dampening/cooldown",
                    "incomingActions": len(action_map),
                    "enhancedActions": len(enhanced_action_map),
                    "tempDeviation": temp_deviation,
                    "humDeviation": hum_deviation,
                    "vpdCurrent": current_vpd,
                    "vpdTarget": target_vpd,
                    "vpdTargetMin": target_min,
                    "vpdTargetMax": target_max,
                },
                haEvent=True,
                debug_type="WARNING",
            )
            return []

        # Resolve conflicts between actions
        final_actions = self._resolve_action_conflicts(dampened_actions)

        _LOGGER.info(
            f"{self.ogb.room}: Executing {len(final_actions)} of {len(enhanced_action_map)} actions "
            f"(VPD status: {vpd_status}, blocked: {len(enhanced_action_map) - len(dampened_actions)})"
        )
        await self.ogb.eventManager.emit(
            "LogForClient",
            {
                "Name": self.ogb.room,
                "message": "VPD Perfection chain: executing filtered actions",
                "incomingActions": len(action_map),
                "enhancedActions": len(enhanced_action_map),
                "filteredActions": len(dampened_actions),
                "finalActions": len(final_actions),
                "vpdStatus": vpd_status,
                "tempDeviation": temp_deviation,
                "humDeviation": hum_deviation,
                "vpdCurrent": current_vpd,
                "vpdTarget": target_vpd,
                "vpdTargetMin": target_min,
                "vpdTargetMax": target_max,
            },
            haEvent=True,
            debug_type="INFO",
        )

        # Log detailed action information
        if final_actions:
            action_summary = ", ".join([f"{a.capability}:{a.action}" for a in final_actions])
            _LOGGER.debug(f"{self.ogb.room}: Actions: {action_summary}")
        else:
            _LOGGER.warning(f"{self.ogb.room}: No actions to execute after conflict resolution")

        # Execute actions
        await self._execute_actions(final_actions)

        return final_actions

    async def process_core_vpd_logic(
        self,
        action_map: List,
        temp_deviation: float,
        hum_deviation: float,
        tent_data: Dict[str, Any],
    ) -> List:
        """
        Process Core VPD Logic (ALWAYS active).

        This includes:
        - Buffer Zones (prevent oscillation near limits)
        - VPD Context enhancements (priorities based on VPD status)
        - Deviations-based actions (intelligent additional actions)
        - Conflict resolution (resolve contradictory actions)

        NOTE: Cooldown filtering is NOT included here - that's handled separately
              in process_dampening_features().

        Args:
            action_map: Initial list of actions
            temp_deviation: Weighted temperature deviation
            hum_deviation: Weighted humidity deviation
            tent_data: Current tent data

        Returns:
            Enhanced and resolved actions (without cooldown filter)
        """
        _LOGGER.debug(
            f"{self.ogb.room}: Processing {len(action_map)} actions with Core VPD Logic (buffer zones, VPD context, conflicts)"
        )

        if not action_map:
            return []

        # Get control settings
        own_weights = self.ogb.dataStore.getDeep("controlOptions.ownWeights")
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self.ogb.dataStore.getDeep("isPlantDay.islightON")

        # Calculate weights and deviations
        temp_weight, hum_weight = self._calculate_weights(own_weights)
        (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
         tempPercentage, humPercentage, weight_message) = self._calculate_deviations(
            temp_weight, hum_weight
        )

        # Get capabilities and determine VPD status
        caps = self.ogb.dataStore.get("capabilities")
        vpd_status = self._determine_vpd_status(
            weighted_temp_dev, weighted_hum_dev, tent_data
        )
        optimal_devices = self._get_optimal_devices(vpd_status)

        # Step 1: Enhance action map with buffer zones and VPD context
        enhanced_action_map = self._enhance_action_map(
            action_map,
            weighted_temp_dev,
            weighted_hum_dev,
            tent_data,
            caps,
            vpd_light_control,
            is_light_on,
            optimal_devices,
            vpd_status,
        )

        # Step 2: Resolve conflicts
        final_actions = self._resolve_action_conflicts(enhanced_action_map)

        _LOGGER.info(
            f"{self.ogb.room}: Core VPD Logic: {len(action_map)} → {len(enhanced_action_map)} (enhanced) → "
            f"{len(final_actions)} (resolved)"
        )

        # Emit log for debugging
        await self.ogb.eventManager.emit(
            "LogForClient",
            {
                "Name": self.ogb.room,
                "message": "Core VPD Logic: actions enhanced and conflicts resolved",
                "incomingActions": len(action_map),
                "enhancedActions": len(enhanced_action_map),
                "finalActions": len(final_actions),
                "vpdStatus": vpd_status,
                "tempDeviation": real_temp_dev,
                "humDeviation": real_hum_dev,
                "tempPercentage": tempPercentage,
                "humPercentage": humPercentage,
            },
            haEvent=True,
            debug_type="DEBUG",
        )

        return final_actions

    async def process_dampening_features(
        self,
        action_map: List,
        temp_deviation: float,
        hum_deviation: float,
        tent_data: Dict[str, Any],
    ) -> Tuple[List, List]:
        """
        Process Dampening Features (only when vpdDeviceDampening is enabled).

        This includes:
        - Cooldown filtering (prevent rapid repetition)
        - Repeat cooldown (prevent immediate same action)
        - Emergency override (bypass cooldown in critical conditions)

        Args:
            action_map: List of actions after core VPD logic
            temp_deviation: Weighted temperature deviation
            hum_deviation: Weighted humidity deviation
            tent_data: Current tent data

        Returns:
            Tuple of (filtered_actions, blocked_actions)
        """
        _LOGGER.debug(
            f"{self.ogb.room}: Processing {len(action_map)} actions with Dampening Features (cooldown, emergency override)"
        )

        # Check for emergency conditions
        emergency_conditions = self.action_manager._getEmergencyOverride(tent_data)
        if emergency_conditions:
            await self.action_manager._clearCooldownForEmergency(emergency_conditions)

        # Apply cooldown filtering
        dampened_actions, blocked_actions = (
            await self.action_manager._filterActionsByDampening(
                action_map, temp_deviation, hum_deviation
            )
        )

        # Resolve conflicts after dampening
        final_actions = self._resolve_action_conflicts(dampened_actions)

        _LOGGER.info(
            f"{self.ogb.room}: Dampening Features: {len(action_map)} → {len(dampened_actions)} "
            f"(filtered, {len(blocked_actions)} blocked) → {len(final_actions)} (resolved)"
        )

        return final_actions, blocked_actions

    async def process_actions_basic(self, action_map: List, temp_deviation: float, hum_deviation: float) -> List:
        """
        Process VPD Perfection actions with weighted deviations from ActionManager.

        NOTE: This method is DEPRECATED. Use process_core_vpd_logic() and
              process_dampening_features() separately for cleaner separation.

        Args:
            action_map: Initial list of actions to process
            temp_deviation: Weighted temperature deviation (calculated by ActionManager)
            hum_deviation: Weighted humidity deviation (calculated by ActionManager)

        Returns:
            Final list of actions to execute
        """
        _LOGGER.warning(
            f"{self.ogb.room}: process_actions_basic() is deprecated - use process_core_vpd_logic() and process_dampening_features()"
        )

        tent_data = self.ogb.dataStore.get("tentData")

        if not action_map:
            await self.ogb.eventManager.emit(
                "LogForClient",
                {
                    "Name": self.ogb.room,
                    "message": "VPD Perfection chain: no actions to process",
                    "tempDeviation": temp_deviation,
                    "humDeviation": hum_deviation,
                },
                haEvent=True,
                debug_type="DEBUG",
            )
            return []

        # Apply buffer zones to prevent oscillation
        buffered_actions = self._apply_buffer_zones(action_map, tent_data)

        # Resolve conflicts between actions
        resolved_actions = self._resolve_action_conflicts(buffered_actions)

        if not resolved_actions:
            await self.ogb.eventManager.emit(
                "LogForClient",
                {
                    "Name": self.ogb.room,
                    "message": "VPD Perfection chain: no actions after buffer/conflict resolution",
                    "incomingActions": len(action_map),
                    "tempDeviation": temp_deviation,
                    "humDeviation": hum_deviation,
                },
                haEvent=True,
                debug_type="DEBUG",
            )
            return []

        # Emergency handling
        emergency_conditions = self.action_manager._getEmergencyOverride(tent_data)
        if emergency_conditions:
            await self.action_manager._clearCooldownForEmergency(emergency_conditions)

        # Apply dampening (cooldown) filter
        filtered_actions, _ = await self.action_manager._filterActionsByDampening(
            resolved_actions, temp_deviation, hum_deviation
        )

        if not filtered_actions:
            await self._handle_blocked_actions(
                resolved_actions, emergency_conditions, temp_deviation, hum_deviation
            )
            current_vpd = tent_data.get("vpd", 0)
            target_vpd = self.ogb.dataStore.getDeep("vpd.perfection")
            await self.ogb.eventManager.emit(
                "LogForClient",
                {
                    "Name": self.ogb.room,
                    "message": "VPD Perfection chain: all actions blocked by dampening/cooldown",
                    "incomingActions": len(action_map),
                    "resolvedActions": len(resolved_actions),
                    "tempDeviation": temp_deviation,
                    "humDeviation": hum_deviation,
                    "vpdCurrent": current_vpd,
                    "vpdTarget": target_vpd,
                },
                haEvent=True,
                debug_type="WARNING",
            )
            return []

        current_vpd = tent_data.get("vpd", 0)
        target_vpd = self.ogb.dataStore.getDeep("vpd.perfection")
        await self.ogb.eventManager.emit(
            "LogForClient",
            {
                "Name": self.ogb.room,
                "message": "VPD Perfection chain: executing filtered actions",
                "incomingActions": len(action_map),
                "resolvedActions": len(resolved_actions),
                "filteredActions": len(filtered_actions),
                "tempDeviation": temp_deviation,
                "humDeviation": hum_deviation,
                "vpdCurrent": current_vpd,
                "vpdTarget": target_vpd,
            },
            haEvent=True,
            debug_type="INFO",
        )

        await self._execute_actions(filtered_actions)
        return filtered_actions

    async def process_actions_target_basic(self, action_map: List, temp_deviation: float, hum_deviation: float) -> List:
        """
        Process VPD Target actions with weighted deviations from ActionManager.

        Args:
            action_map: Initial list of actions to process
            temp_deviation: Weighted temperature deviation (calculated by ActionManager)
            hum_deviation: Weighted humidity deviation (calculated by ActionManager)

        Returns:
            Final list of actions to execute
        """
        _LOGGER.debug(
            f"{self.ogb.room}: Processing {len(action_map)} VPD Target actions (central weighted deviations)"
        )

        tent_data = self.ogb.dataStore.get("tentData")

        if not action_map:
            await self.ogb.eventManager.emit(
                "LogForClient",
                {
                    "Name": self.ogb.room,
                    "message": "VPD Target chain: no actions to process",
                    "tempDeviation": temp_deviation,
                    "humDeviation": hum_deviation,
                },
                haEvent=True,
                debug_type="DEBUG",
            )
            return []

        # Apply buffer zones to prevent oscillation
        buffered_actions = self._apply_buffer_zones(action_map, tent_data)

        # Resolve conflicts between actions
        resolved_actions = self._resolve_action_conflicts(buffered_actions)

        if not resolved_actions:
            await self.ogb.eventManager.emit(
                "LogForClient",
                {
                    "Name": self.ogb.room,
                    "message": "VPD Target chain: no actions after buffer/conflict resolution",
                    "incomingActions": len(action_map),
                    "tempDeviation": temp_deviation,
                    "humDeviation": hum_deviation,
                },
                haEvent=True,
                debug_type="DEBUG",
            )
            return []

        # Emergency handling
        emergency_conditions = self.action_manager._getEmergencyOverride(tent_data)
        if emergency_conditions:
            await self.action_manager._clearCooldownForEmergency(emergency_conditions)

        # Apply dampening (cooldown) filter
        filtered_actions, _ = await self.action_manager._filterActionsByDampening(
            resolved_actions, temp_deviation, hum_deviation
        )

        if not filtered_actions:
            await self._handle_blocked_actions(
                resolved_actions, emergency_conditions, temp_deviation, hum_deviation
            )
            current_vpd = tent_data.get("vpd", 0)
            target_vpd = self.ogb.dataStore.getDeep("vpd.targeted")
            await self.ogb.eventManager.emit(
                "LogForClient",
                {
                    "Name": self.ogb.room,
                    "message": "VPD Target chain: all actions blocked by dampening/cooldown",
                    "incomingActions": len(action_map),
                    "resolvedActions": len(resolved_actions),
                    "tempDeviation": temp_deviation,
                    "humDeviation": hum_deviation,
                    "vpdCurrent": current_vpd,
                    "vpdTarget": target_vpd,
                },
                haEvent=True,
                debug_type="WARNING",
            )
            return []

        current_vpd = tent_data.get("vpd", 0)
        target_vpd = self.ogb.dataStore.getDeep("vpd.targeted")
        await self.ogb.eventManager.emit(
            "LogForClient",
            {
                "Name": self.ogb.room,
                "message": "VPD Target chain: executing filtered actions",
                "incomingActions": len(action_map),
                "resolvedActions": len(resolved_actions),
                "filteredActions": len(filtered_actions),
                "tempDeviation": temp_deviation,
                "humDeviation": hum_deviation,
                "vpdCurrent": current_vpd,
                "vpdTarget": target_vpd,
            },
            haEvent=True,
            debug_type="INFO",
        )

        await self._execute_actions(filtered_actions)
        return filtered_actions

    def _calculate_weights(self, own_weights: bool) -> Tuple[float, float]:
        """
        Calculate temperature and humidity weights.

        Args:
            own_weights: Whether to use custom weights

        Returns:
            Tuple of (temp_weight, hum_weight)
        """
        if own_weights:
            temp_weight = self.ogb.dataStore.getDeep("controlOptionData.weights.temp")
            hum_weight = self.ogb.dataStore.getDeep("controlOptionData.weights.hum")
        else:
            # Use plant-stage specific weighting
            plant_stage = self.ogb.dataStore.get("plantStage") or "MidVeg"
            base_weight = self.ogb.dataStore.getDeep("controlOptionData.weights.defaultValue") or 1.0

            stage_weights = self._calculate_plant_stage_weights(plant_stage, base_weight)
            temp_weight = stage_weights["temp"]
            hum_weight = stage_weights["hum"]

        return temp_weight, hum_weight

    def _calculate_deviations(
        self, temp_weight: float, hum_weight: float
    ) -> Tuple[float, float, float, float, float, float, str]:
        """
        Calculate real and weighted temperature and humidity deviations.

        Args:
            temp_weight: Temperature weight factor
            hum_weight: Humidity weight factor

        Returns:
            Tuple of (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
                     tempPercentage, humPercentage, weight_message)
            - real_temp_dev: Real absolute deviation (for display)
            - real_hum_dev: Real absolute deviation (for display)
            - weighted_temp_dev: Weighted deviation (for action prioritization)
            - weighted_hum_dev: Weighted deviation (for action prioritization)
            - tempPercentage: Deviation as percentage of range (0-100%)
            - humPercentage: Deviation as percentage of range (0-100%)
            - weightMessage: Description of deviation status
        """
        tent_data = self.ogb.dataStore.get("tentData")

        # 1. Calculate REAL deviations (for display - absolute, not weighted)
        real_temp_dev = 0.0
        real_hum_dev = 0.0
        weight_message = ""

        # Calculate temperature deviation
        if tent_data["temperature"] > tent_data["maxTemp"]:
            real_temp_dev = round(tent_data["temperature"] - tent_data["maxTemp"], 2)
            weight_message = f"Temp Too High: +{real_temp_dev}°C"
        elif tent_data["temperature"] < tent_data["minTemp"]:
            real_temp_dev = round(tent_data["temperature"] - tent_data["minTemp"], 2)
            weight_message = f"Temp Too Low: {real_temp_dev}°C"

        # Calculate humidity deviation
        if tent_data["humidity"] > tent_data["maxHumidity"]:
            real_hum_dev = round(tent_data["humidity"] - tent_data["maxHumidity"], 2)
            if weight_message:
                weight_message += f", Humidity Too High: +{real_hum_dev}%"
            else:
                weight_message = f"Humidity Too High: +{real_hum_dev}%"
        elif tent_data["humidity"] < tent_data["minHumidity"]:
            real_hum_dev = round(tent_data["humidity"] - tent_data["minHumidity"], 2)
            if weight_message:
                weight_message += f", Humidity Too Low: {real_hum_dev}%"
            else:
                weight_message = f"Humidity Too Low: {real_hum_dev}%"

        # 2. Calculate WEIGHTED deviations (for action prioritization)
        weighted_temp_dev = round(real_temp_dev * temp_weight, 2)
        weighted_hum_dev = round(real_hum_dev * hum_weight, 2)

        # 3. Calculate percentage of range (for better context)
        temp_range = max(1.0, abs(tent_data["maxTemp"] - tent_data["minTemp"]))
        hum_range = max(1.0, abs(tent_data["maxHumidity"] - tent_data["minHumidity"]))

        tempPercentage = round((abs(real_temp_dev) / temp_range) * 100, 1) if real_temp_dev != 0 else 0.0
        humPercentage = round((abs(real_hum_dev) / hum_range) * 100, 1) if real_hum_dev != 0 else 0.0

        return (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
                tempPercentage, humPercentage, weight_message)

    def _calculate_basic_deviations(
        self, tent_data: Dict[str, Any]
    ) -> Tuple[float, float]:
        """
        Calculate basic temperature and humidity deviations.

        Args:
            tent_data: Current tent environmental data

        Returns:
            Tuple of (temp_deviation, hum_deviation)
        """
        temp_deviation = 0
        hum_deviation = 0

        if tent_data["temperature"] > tent_data["maxTemp"]:
            temp_deviation = tent_data["temperature"] - tent_data["maxTemp"]
        elif tent_data["temperature"] < tent_data["minTemp"]:
            temp_deviation = tent_data["temperature"] - tent_data["minTemp"]

        if tent_data["humidity"] > tent_data["maxHumidity"]:
            hum_deviation = tent_data["humidity"] - tent_data["maxHumidity"]
        elif tent_data["humidity"] < tent_data["minHumidity"]:
            hum_deviation = tent_data["humidity"] - tent_data["minHumidity"]

        return temp_deviation, hum_deviation

    async def _publish_weight_info(
        self,
        weight_message: str,
        temp_deviation: float,
        hum_deviation: float,
        tempPercentage: float,
        humPercentage: float,
        temp_weight: float,
        hum_weight: float,
    ):
        """
        Publish weight and deviation information.

        Args:
            weight_message: Description of weight calculations
            temp_deviation: Real temperature deviation (for display)
            hum_deviation: Real humidity deviation (for display)
            tempPercentage: Temperature deviation as percentage of range
            humPercentage: Humidity deviation as percentage of range
            temp_weight: Temperature weight
            hum_weight: Humidity weight
        """

        from ..data.OGBDataClasses.OGBPublications import OGBWeightPublication
        weight_publication = OGBWeightPublication(
            Name=self.ogb.room,
            message=weight_message,
            tempDeviation=temp_deviation,
            humDeviation=hum_deviation,
            tempPercentage=tempPercentage,
            humPercentage=humPercentage,
            tempWeight=temp_weight,
            humWeight=hum_weight,
        )
        await self.ogb.eventManager.emit(
            "LogForClient", weight_publication, haEvent=True
        )

    def _determine_vpd_status(
        self, temp_deviation: float, hum_deviation: float, tent_data: Dict[str, Any]
    ) -> str:
        """
        Determine VPD status based on VPD deviation only (not temp/hum).

        In VPD Perfection mode, the status should be based on VPD deviation,
        not on weighted temperature/humidity deviations.

        Args:
            temp_deviation: Temperature deviation (not used, kept for compatibility)
            hum_deviation: Humidity deviation (not used, kept for compatibility)
            tent_data: Current tent environmental data

        Returns:
            VPD status: "low", "medium", "high", or "critical" based on VPD deviation
        """
        if not tent_data:
            return "low"

        # Get current VPD and target
        current_vpd = tent_data.get("vpd", 0)
        target_vpd = tent_data.get("targetVpd", 0)
        vpd_deviation = abs(current_vpd - target_vpd)

        # Determine status based on VPD deviation only
        # These thresholds are chosen based on typical VPD ranges:
        # - ±0.1 kPa: Very close to target (low)
        # - ±0.3 kPa: Acceptable range (medium)
        # - ±0.5 kPa: Needs correction (high)
        # - >±0.5 kPa: Significant deviation (critical)
        if vpd_deviation <= 0.1:
            return "low"       # ±0.1 kPa - Very close to target
        elif vpd_deviation <= 0.3:
            return "medium"    # 0.1-0.3 kPa - Acceptable range
        elif vpd_deviation <= 0.5:
            return "high"      # 0.3-0.5 kPa - Needs correction
        else:
            return "critical"  # >0.5 kPa - Significant deviation

    def _get_optimal_devices(self, vpd_status: str) -> List[str]:
        """
        Get list of optimal devices based on VPD status and environmental conditions.

        Different VPD statuses prioritize different devices:
        - Critical: Fast-acting devices for immediate response
        - High: Powerful devices for significant correction
        - Medium: Balanced devices for moderate adjustment
        - Low: Gentle devices for fine-tuning

        Args:
            vpd_status: Current VPD status ("low", "medium", "high", "critical")

        Returns:
            List of optimal device capabilities for the current status
        """
        # Device prioritization based on VPD status
        device_priority = {
            "critical": [
                "canExhaust",      # Fastest air movement
                "canVentilate",    # Immediate ventilation
                "canCool",         # Emergency cooling
                "canDehumidify",   # Rapid humidity reduction
                "canHeat",         # Emergency heating
            ],
            "high": [
                "canExhaust",      # Strong air movement
                "canDehumidify",   # Significant humidity control
                "canHumidify",     # Significant humidity control
                "canCool",         # Temperature control
                "canHeat",         # Temperature control
            ],
            "medium": [
                "canIntake",       # Moderate air exchange
                "canExhaust",      # Moderate air movement
                "canHumidify",     # Humidity adjustment
                "canDehumidify",   # Humidity adjustment
            ],
            "low": [
                "canVentilate",    # Gentle air movement
                "canIntake",       # Gentle air exchange
                "canClimate",      # Precise climate control
                "canCO2",          # CO2 optimization
            ]
        }

        # Get prioritized devices for this status
        optimal_devices = device_priority.get(vpd_status, [])

        # Add common devices that are always available
        base_devices = ["canLight"] if vpd_status == "low" else []

        return optimal_devices + base_devices

    def _calculate_plant_stage_weights(self, plant_stage: str, base_weight: float) -> Dict[str, float]:
        """
        Calculate plant-stage specific weight adjustments.

        Different growth phases have different environmental priorities:
        - Germination/EarlyVeg: Prioritize temperature stability for root development
        - Mid/Late Flower: Prioritize humidity control for bud development
        - Other stages: Balanced approach for optimal growth

        Args:
            plant_stage: Current plant growth stage
            base_weight: Base weight factor

        Returns:
            Dictionary with temp_weight and hum_weight
        """
        weights = {"temp": base_weight, "hum": base_weight}

        # Plant-stage specific adjustments (matching monolithic logic)
        if plant_stage in ["Germination", "EarlyVeg"]:
            # Early stages: Higher temperature priority for root establishment
            weights["temp"] = base_weight * 1.3
            weights["hum"] = base_weight * 0.9

        elif plant_stage in ["MidFlower", "LateFlower"]:
            # Flower stages: Higher humidity priority for bud development
            weights["temp"] = base_weight * 1.0
            weights["hum"] = base_weight * 1.25

        elif plant_stage in ["MidVeg", "LateVeg"]:
            # Vegetative growth: Slightly balanced with temp emphasis
            weights["temp"] = base_weight * 1.1
            weights["hum"] = base_weight * 1.1

        # Default: use base weights (balanced growth)

        return weights

    def _calculate_adaptive_cooldown(self, capability: str, deviation: float) -> float:
        """
        Calculate adaptive cooldown based on deviation severity.

        Larger deviations get longer cooldowns to allow time for effect.
        Smaller deviations get shorter cooldowns for more responsive control.

        Args:
            capability: Device capability being controlled
            deviation: Current deviation from target

        Returns:
            Cooldown time in minutes
        """
        if hasattr(self.action_manager, 'cooldown_manager'):
            base_cooldown = self.action_manager.cooldown_manager.cooldowns.get(capability, 2.0)
            return self.action_manager.cooldown_manager._calculate_adaptive_dampening(base_cooldown, deviation)
        else:
            return 2.0

    def _apply_buffer_zones(self, action_map: List, tent_data: Dict[str, Any]) -> List:
        """
        Apply buffer zones to prevent oscillation near temperature/humidity limits.

        Buffer zones prevent devices from activating too close to limits, which
        can cause rapid on/off cycling as conditions fluctuate slightly.

        Args:
            action_map: List of actions to filter
            tent_data: Current environmental data

        Returns:
            Filtered action list with buffer zones applied
        """
        if not tent_data:
            return action_map

        current_temp = tent_data.get("temperature", 0)
        max_temp = tent_data.get("maxTemp", 28)
        min_temp = tent_data.get("minTemp", 17)
        current_humidity = tent_data.get("humidity", 55)
        max_humidity = tent_data.get("maxHumidity", 80)
        min_humidity = tent_data.get("minHumidity", 40)

        HEATER_BUFFER = float(self.ogb.dataStore.getDeep("controlOptionData.buffers.heaterBuffer") or 2.0)
        COOLER_BUFFER = float(self.ogb.dataStore.getDeep("controlOptionData.buffers.coolerBuffer") or 2.0)
        HUMIDIFIER_BUFFER = float(self.ogb.dataStore.getDeep("controlOptionData.buffers.humidifierBuffer") or 5.0)
        DEHUMIDIFIER_BUFFER = float(self.ogb.dataStore.getDeep("controlOptionData.buffers.dehumidifierBuffer") or 5.0)

        # Increase actions: prevent starting device too close to the opposite limit
        # These buffers make sense - don't start devices near their opposite limits
        heater_increase_cutoff     = max_temp - HEATER_BUFFER        # don't start heater near max
        cooler_increase_cutoff     = min_temp + COOLER_BUFFER        # don't start cooler near min
        humidifier_increase_cutoff = max_humidity - HUMIDIFIER_BUFFER    # don't start humidifier near max
        dehumidifier_increase_cutoff = min_humidity + DEHUMIDIFIER_BUFFER  # don't start dehumidifier near min

        # REDUCE actions: NO buffers - always allow stopping devices when needed!
        # The opposite capability will handle stopping if limits are exceeded
        # Reduce buffer removal - these were preventing device shutdown when needed

        filtered_actions = []

        for action in action_map:
            capability = getattr(action, 'capability', '')
            action_type = getattr(action, 'action', '')

            if capability == "canHeat":
                if action_type == "Increase":
                    if current_temp >= heater_increase_cutoff:
                        _LOGGER.warning(
                            f"{self.ogb.room}: BLOCKED heater increase - temp {current_temp}°C >= cutoff {heater_increase_cutoff}°C "
                            f"(max_temp={max_temp}°C, buffer={HEATER_BUFFER}°C)"
                        )
                        continue
                # Reduce: Always allow - no buffer needed

            elif capability == "canCool":
                if action_type == "Increase":
                    if current_temp <= cooler_increase_cutoff:
                        _LOGGER.warning(
                            f"{self.ogb.room}: BLOCKED cooler increase - temp {current_temp}°C <= cutoff {cooler_increase_cutoff}°C "
                            f"(min_temp={min_temp}°C, buffer={COOLER_BUFFER}°C)"
                        )
                        continue
                elif action_type == "Reduce":
                    # Safety check: Don't reduce cooling when temp is critically high
                    if current_temp >= max_temp - 1.0:
                        _LOGGER.warning(
                            f"{self.ogb.room}: BLOCKED cooler reduce - temp {current_temp}°C >= {max_temp - 1.0}°C "
                            f"(too close to max {max_temp}°C)"
                        )
                        continue

            elif capability == "canHumidify":
                if action_type == "Increase":
                    if current_humidity >= humidifier_increase_cutoff:
                        _LOGGER.warning(
                            f"{self.ogb.room}: BLOCKED humidifier increase - humidity {current_humidity}% >= cutoff {humidifier_increase_cutoff}% "
                            f"(max_humidity={max_humidity}%, buffer={HUMIDIFIER_BUFFER}%)"
                        )
                        continue
                # Reduce: Always allow - no buffer needed

            elif capability == "canDehumidify":
                if action_type == "Increase":
                    if current_humidity <= dehumidifier_increase_cutoff:
                        _LOGGER.warning(
                            f"{self.ogb.room}: BLOCKED dehumidifier increase - humidity {current_humidity}% <= cutoff {dehumidifier_increase_cutoff}% "
                            f"(min_humidity={min_humidity}%, buffer={DEHUMIDIFIER_BUFFER}%)"
                        )
                        continue
                # Reduce: Always allow - no buffer needed!

            filtered_actions.append(action)

        if len(filtered_actions) < len(action_map):
            blocked_count = len(action_map) - len(filtered_actions)
            _LOGGER.info(
                f"{self.ogb.room}: Buffer zones blocked {blocked_count} actions to prevent oscillation "
                f"(temp: {current_temp}°C, humidity: {current_humidity}%)"
            )

        return filtered_actions


    def _add_vpd_context_enhancements(self, actions, vpd_status, temp_dev, hum_dev, tent_data):
        """
        Add VPD-status aware enhancements to actions.

        Different VPD statuses require different enhancement strategies:
        - Critical: Immediate, aggressive corrections
        - High: Strong, prioritized corrections
        - Medium: Balanced, moderate corrections
        - Low: Gentle, minimal corrections

        Args:
            actions: Current action list
            vpd_status: Current VPD status
            temp_dev: Temperature deviation
            hum_dev: Humidity deviation
            tent_data: Environmental data

        Returns:
            Enhanced action list with VPD context
        """
        enhanced = list(actions)

        # VPD-status specific enhancements
        if vpd_status == "critical":
            # Add immediate correction actions
            enhanced.extend(self._create_critical_vpd_actions(temp_dev, hum_dev))

        elif vpd_status == "high":
            # Add strong correction actions
            enhanced.extend(self._create_high_priority_vpd_actions(temp_dev, hum_dev))

        elif vpd_status == "medium":
            # Add balanced correction actions
            enhanced.extend(self._create_balanced_vpd_actions(temp_dev, hum_dev))

        # Low status: minimal enhancements (already handled by base actions)

        return enhanced

    def _add_deviation_actions_with_context(self, actions, temp_dev, hum_dev, vpd_status):
        """
        Add deviation-based actions with VPD status context.

        Considers VPD status when deciding what deviation corrections to apply.

        Args:
            actions: Current action list
            temp_dev: Temperature deviation
            hum_dev: Humidity deviation
            vpd_status: Current VPD status

        Returns:
            Action list with context-aware deviation corrections
        """
        enhanced = list(actions)

        # Temperature deviation handling based on VPD status
        if abs(temp_dev) > 1.0:  # Significant temperature deviation
            if vpd_status in ["critical", "high"]:
                # Urgent temperature correction
                temp_actions = self._create_temperature_correction_actions(temp_dev, priority="high", vpd_status=vpd_status)
                enhanced.extend(temp_actions)

        # Humidity deviation handling - ALWAYS correct if humidity exceeds max
        if hum_dev > 0:  # Any positive deviation = humidity too high
            if vpd_status in ["critical", "high"]:
                # Urgent humidity correction
                hum_actions = self._create_humidity_correction_actions(hum_dev, priority="high")
                enhanced.extend(hum_actions)
            elif vpd_status in ["medium", "low"]:
                # Standard humidity correction
                hum_actions = self._create_humidity_correction_actions(hum_dev, priority="medium")
                enhanced.extend(hum_actions)

        return enhanced

    def _create_critical_vpd_actions(self, temp_dev, hum_dev):
        """Create immediate actions for critical VPD situations.
        
        Only creates actions for capabilities that actually exist.
        """
        actions = []
        caps = self.ogb.dataStore.get("capabilities") or {}

        # Import locally to avoid circular imports
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

        # Temperature emergency actions - only if we have cooling capability
        if temp_dev > 2.0 and caps.get("canCool", {}).get("state"):
            actions.append(OGBActionPublication(
                capability="canCool",
                action="Increase",
                Name=self.ogb.room,
                message="Critical VPD: Emergency cooling",
                priority="emergency"
            ))

        # Humidity emergency actions - ALWAYS correct if humidity exceeds max
        if hum_dev > 0 and caps.get("canDehumidify", {}).get("state"):
            actions.append(OGBActionPublication(
                capability="canDehumidify",
                action="Increase",
                Name=self.ogb.room,
                message="Critical VPD: Emergency dehumidification",
                priority="emergency"
            ))

        return actions

    def _create_high_priority_vpd_actions(self, temp_dev, hum_dev):
        """Create strong correction actions for high VPD situations.
        
        Only creates actions for capabilities that actually exist.
        """
        actions = []
        caps = self.ogb.dataStore.get("capabilities") or {}

        # Import locally to avoid circular imports
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

        # Temperature correction - only if we have cooling capability
        if temp_dev > 1.5 and caps.get("canCool", {}).get("state"):
            actions.append(OGBActionPublication(
                capability="canCool",
                action="Increase",
                Name=self.ogb.room,
                message="High VPD: Temperature correction",
                priority="high"
            ))

        # Humidity correction - ALWAYS correct if humidity exceeds max
        if hum_dev > 0 and caps.get("canDehumidify", {}).get("state"):
            actions.append(OGBActionPublication(
                capability="canDehumidify",
                action="Increase",
                Name=self.ogb.room,
                message="High VPD: Humidity correction",
                priority="high"
            ))

        return actions

    def _create_balanced_vpd_actions(self, temp_dev, hum_dev):
        """Create moderate correction actions for medium VPD situations.
        
        Only creates actions for capabilities that actually exist.
        """
        actions = []
        caps = self.ogb.dataStore.get("capabilities") or {}

        # Import locally to avoid circular imports
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

        # Gentle temperature correction - check capability exists first
        if abs(temp_dev) > 1.0:
            if temp_dev < 0 and caps.get("canHeat", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canHeat",
                    action="Increase",
                    Name=self.ogb.room,
                    message="Medium VPD: Balanced temperature adjustment",
                    priority="medium"
                ))
            elif temp_dev > 0 and caps.get("canCool", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canCool",
                    action="Increase",
                    Name=self.ogb.room,
                    message="Medium VPD: Balanced temperature adjustment",
                    priority="medium"
                ))

        # Humidity correction - ALWAYS correct based on deviation
        if hum_dev > 0:  # Any positive deviation = humidity too high
            if caps.get("canDehumidify", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canDehumidify",
                    action="Increase",
                    Name=self.ogb.room,
                    message="Medium VPD: Balanced humidity adjustment",
                    priority="medium"
                ))
        elif hum_dev < 0:  # Any negative deviation = humidity too low
            if caps.get("canHumidify", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canHumidify",
                    action="Increase",
                    Name=self.ogb.room,
                    message="Medium VPD: Balanced humidity adjustment",
                    priority="medium"
                ))

        return actions

    def _create_temperature_correction_actions(self, temp_dev, priority="medium", vpd_status="medium"):
        """Create temperature correction actions.
        
        Only creates actions for capabilities that actually exist.
        Respects VPD status to avoid conflicting actions (e.g., heating when VPD is too high).
        
        Args:
            temp_dev: Temperature deviation from target
            priority: Action priority (low, medium, high, emergency)
            vpd_status: Current VPD status (low, medium, high, critical) to prevent conflicting actions
        """
        actions = []
        caps = self.ogb.dataStore.get("capabilities") or {}

        # Import locally to avoid circular imports
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

        if temp_dev > 0:  # Too hot - need cooling
            if caps.get("canCool", {}).get("state"):
                # Cooling is generally safe for all VPD states (reduces both temp and humidity)
                actions.append(OGBActionPublication(
                    capability="canCool",
                    action="Increase",
                    Name=self.ogb.room,
                    message=f"Temperature correction (deviation: {temp_dev:.1f}°C)",
                    priority=priority
                ))
        else:  # Too cold - need heating
            if caps.get("canHeat", {}).get("state"):
                # CRITICAL FIX: Never heat when VPD is already too high!
                # Heating increases temperature which increases VPD further
                if vpd_status not in ["high", "critical"]:
                    actions.append(OGBActionPublication(
                        capability="canHeat",
                        action="Increase",
                        Name=self.ogb.room,
                        message=f"Temperature correction (deviation: {temp_dev:.1f}°C)",
                        priority=priority
                    ))
                else:
                    _LOGGER.debug(
                        f"{self.ogb.room}: Skipping heater increase - VPD is {vpd_status} "
                        f"(temp deviation: {temp_dev:.1f}°C). Heating would worsen VPD."
                    )

        return actions

    def _create_humidity_correction_actions(self, hum_dev, priority="medium"):
        """Create humidity correction actions.
        
        Only creates actions for capabilities that actually exist.
        """
        actions = []
        caps = self.ogb.dataStore.get("capabilities") or {}

        # Import locally to avoid circular imports
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

        if hum_dev > 0:  # Too humid - need dehumidification
            if caps.get("canDehumidify", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canDehumidify",
                    action="Increase",
                    Name=self.ogb.room,
                    message=f"Humidity correction (deviation: {hum_dev:.1f}%)",
                    priority=priority
                ))
        else:  # Too dry - need humidification
            if caps.get("canHumidify", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canHumidify",
                    action="Increase",
                    Name=self.ogb.room,
                    message=f"Humidity correction (deviation: {hum_dev:.1f}%)",
                    priority=priority
                ))

        return actions

    def _enhance_action_map(
        self,
        base_action_map: List,
        temp_deviation: float,
        hum_deviation: float,
        tent_data: Dict[str, Any],
        caps: Dict[str, Any],
        vpd_light_control: bool,
        is_light_on: bool,
        optimal_devices: List[str],
        vpd_status: str,  # Add VPD status parameter
    ) -> List:
        """
        Enhance action map with additional actions based on conditions.

        Args:
            base_action_map: Initial action map
            temp_deviation: Temperature deviation
            hum_deviation: Humidity deviation
            tent_data: Current tent data
            caps: Device capabilities
            vpd_light_control: Whether VPD controls lights
            is_light_on: Whether lights are currently on
            optimal_devices: List of optimal device capabilities
            vpd_status: Current VPD status ("low", "medium", "high", "critical")

        Returns:
            Enhanced action map
        """
        enhanced_actions = list(base_action_map)  # Copy original actions

        # Add VPD-status aware enhancements
        enhanced_actions = self._add_vpd_context_enhancements(
            enhanced_actions, vpd_status, temp_deviation, hum_deviation, tent_data
        )

        # Add deviation-based actions with VPD context
        enhanced_actions = self._add_deviation_actions_with_context(
            enhanced_actions, temp_deviation, hum_deviation, vpd_status
        )

        # Apply buffer zones LAST to filter ALL actions (including newly added ones)
        # This prevents oscillation near limits and ensures absolute limits are respected
        enhanced_actions = self._apply_buffer_zones(enhanced_actions, tent_data)

        # Add emergency actions
        # Add CO2 actions
        # Priority: Emergency (highest) > VPD Context > Deviation > Base > CO2 (lowest)

        return enhanced_actions

    def _resolve_action_conflicts(self, action_map: List) -> List:
        """
        Resolve conflicts between actions.

        Delegates to ActionManager's _remove_conflicting_actions() which
        correctly checks for cross-capability conflicts like
        canHumidify vs canDehumidify, canHeat vs canCool, etc.

        Args:
            action_map: List of actions that may conflict

        Returns:
            Resolved action list with conflicts removed
        """
        return self.action_manager._remove_conflicting_actions(action_map)

    async def _execute_actions(self, action_map: List):
        """Execute the final list of actions."""
        await self.action_manager.publicationActionHandler(action_map)

    async def _night_hold_fallback(self, action_map: List):
        """
        Handle night hold fallback for VPD actions (power-saving night mode).

        When lights are off and night VPD hold is NOT active, this performs:
        1. Climate devices (heating, cooling, humidity, lighting, CO2) reduced to minimum
        2. Ventilation devices actively controlled to prevent mold:
           - Exhaust/Ventilation/Window: Increased for air exchange
           - Intake: Adjusted based on outside conditions
        
        Args:
            action_map: Actions to process during night hold
        """
        _LOGGER.debug(f"{self.ogb.room}: Night Hold Power-Saving Mode - Managing ventilation for mold prevention")

        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        # Climate devices that should be minimized at night to save power
        climate_caps = {"canHeat", "canCool", "canHumidify", "canDehumidify", "canCO2", "canLight", "canClimate"}
        
        # Ventilation devices - controlled based on conditions
        ventilation_caps = {"canExhaust", "canVentilate"}
        
        final_actions = []
        
        # 1. Reduce all climate devices to minimum
        for action in action_map:
            cap = getattr(action, 'capability', None)
            if cap in climate_caps:
                final_actions.append(OGBActionPublication(
                    capability=cap,
                    action="Reduce",
                    Name=self.ogb.room,
                    message="NightHold: Climate device reduced for power saving",
                    priority="low"
                ))
        
        # 2. Get capabilities to check what ventilation devices are available
        caps = self.ogb.dataStore.get("capabilities") or {}
        
        # 3. Always increase Exhaust and Ventilation for air exchange (prevent mold)
        if caps.get("canExhaust", {}).get("state", False):
            final_actions.append(OGBActionPublication(
                capability="canExhaust",
                action="Increase",
                Name=self.ogb.room,
                message="NightHold: Exhaust increased for air exchange (mold prevention)",
                priority="medium"
            ))
        
        if caps.get("canVentilate", {}).get("state", False):
            final_actions.append(OGBActionPublication(
                capability="canVentilate",
                action="Increase",
                Name=self.ogb.room,
                message="NightHold: Ventilation increased for air circulation (mold prevention)",
                priority="medium"
            ))
        
        # 4. Window - treat like ventilation for air exchange
        if caps.get("canWindow", {}).get("state", False):
            final_actions.append(OGBActionPublication(
                capability="canWindow",
                action="Increase",
                Name=self.ogb.room,
                message="NightHold: Window opened for air exchange (mold prevention)",
                priority="medium"
            ))
        
        # 5. Intake - check BOTH outside temperature AND humidity
        if caps.get("canIntake", {}).get("state", False):
            outside_temp = self.ogb.dataStore.getDeep("tentData.AmbientTemp")
            outside_hum = self.ogb.dataStore.getDeep("tentData.AmbientHumidity")
            min_temp = self.ogb.dataStore.getDeep("tentData.minTemp")
            max_humidity = self.ogb.dataStore.getDeep("tentData.maxHumidity")
            
            try:
                outside_temp = float(outside_temp) if outside_temp is not None else None
                outside_hum = float(outside_hum) if outside_hum is not None else None
                min_temp = float(min_temp) if min_temp is not None else 18.0
                max_humidity = float(max_humidity) if max_humidity is not None else 65.0
            except (ValueError, TypeError):
                outside_temp = None
                outside_hum = None
                min_temp = 18.0
                max_humidity = 65.0
            
            # Rule 1: NO intake if outside humidity is too high (MOLD RISK!)
            if outside_hum is not None and outside_hum >= (max_humidity + 10):
                _LOGGER.debug(f"{self.ogb.room}: NightHold: Intake blocked - outside humidity {outside_hum}% too high")
                final_actions.append(OGBActionPublication(
                    capability="canIntake",
                    action="Reduce",
                    Name=self.ogb.room,
                    message=f"NightHold: Intake reduced (outside RH {outside_hum}% too high - mold risk)",
                    priority="low"
                ))
            # Rule 2: Only intake if temperature is acceptable
            elif outside_temp is not None and outside_temp >= (min_temp - 3):
                final_actions.append(OGBActionPublication(
                    capability="canIntake",
                    action="Increase",
                    Name=self.ogb.room,
                    message=f"NightHold: Intake increased (outside {outside_temp}°C, RH {outside_hum}%)",
                    priority="medium"
                ))
            else:
                # Outside too cold
                final_actions.append(OGBActionPublication(
                    capability="canIntake",
                    action="Reduce",
                    Name=self.ogb.room,
                    message=f"NightHold: Intake reduced (outside {outside_temp}°C too cold)",
                    priority="low"
                ))
        
        # Emit summary log
        climate_count = len([a for a in final_actions if getattr(a, 'capability', '') in climate_caps])
        vent_count = len([a for a in final_actions if getattr(a, 'capability', '') in ventilation_caps])
        
        await self.ogb.eventManager.emit(
            "LogForClient",
            {
                "Name": self.ogb.room,
                "NightVPDHold": "NotActive Power-Saving Mode",
                "message": f"Night hold: {climate_count} climate devices reduced, {vent_count} ventilation devices managed",
                "climateDevices": climate_count,
                "ventilationDevices": vent_count
            },
            haEvent=True,
            debug_type="INFO"
        )

        if final_actions:
            _LOGGER.info(
                f"{self.ogb.room}: Night Hold executing {len(final_actions)} actions - "
                f"Climate minimized, Ventilation active for mold prevention"
            )
            await self._execute_actions(final_actions)
        else:
            _LOGGER.debug(f"{self.ogb.room}: Night hold - no actions to execute")

    def _create_reduced_action(self, original_action):
        """
        Create a reduced version of an action for night hold fallback.

        Args:
            original_action: The original action to reduce

        Returns:
            A new action with "Reduce" action type
        """
        # Import here to avoid circular imports
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

        return OGBActionPublication(
            capability=original_action.capability,
            action="Reduce",
            Name=getattr(original_action, 'Name', self.ogb.room),
            message="VPD-NightHold Device Reduction",
            priority="low",
        )

    async def _handle_blocked_actions(
        self,
        enhanced_actions: List,
        emergency_conditions: List[str],
        temp_deviation: float,
        hum_deviation: float,
    ):
        """
        Handle case where all actions are blocked by dampening.

        Args:
            enhanced_actions: All available actions
            emergency_conditions: Current emergency conditions
            temp_deviation: Temperature deviation
            hum_deviation: Humidity deviation
        """
        _LOGGER.warning(
            f"{self.ogb.room}: All {len(enhanced_actions)} actions blocked by dampening!"
        )

        # Log blocked actions
        await self.ogb.eventManager.emit(
            "LogForClient",
            {
                "Name": self.ogb.room,
                "message": "All Actions Blocked by Device Dampening - Device Cooldowns",
                "blocked_actions": len(enhanced_actions),
                "emergency_conditions": emergency_conditions,
            },
            haEvent=True,
        )

        # Allow critical emergency action if in emergency
        if emergency_conditions:
            _LOGGER.critical(
                f"{self.ogb.room}: 🚨 EMERGENCY OVERRIDE - All actions blocked but emergency detected! "
                f"Forcing critical action. Conditions: {emergency_conditions}"
            )
            critical_action = self.action_manager._selectCriticalEmergencyAction(
                enhanced_actions, emergency_conditions
            )
            if critical_action:
                await self._execute_actions([critical_action])
                # Register the emergency action
                deviation = max(abs(temp_deviation), abs(hum_deviation))
                self.action_manager._registerAction(
                    critical_action.capability, critical_action.action, deviation
                )

    def get_dampening_status(self) -> Dict[str, Any]:
        """
        Get current dampening status.

        Returns:
            Dictionary with dampening statistics
        """
        active_cooldowns = 0
        emergency_mode = False
        if hasattr(self.action_manager, 'cooldown_manager'):
            status = self.action_manager.cooldown_manager.get_status()
            active_cooldowns = status["active_count"]
            emergency_mode = status["emergency_mode"]

        return {
            "room": self.ogb.room,
            "dampening_enabled": True,  # Always enabled in this module
            "active_cooldowns": active_cooldowns,
            "emergency_mode": emergency_mode,
        }
