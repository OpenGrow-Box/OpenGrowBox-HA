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
        temp_deviation, hum_deviation, weight_message = self._calculate_deviations(
            temp_weight, hum_weight
        )

        # Publish weight information
        await self._publish_weight_info(
            weight_message, temp_deviation, hum_deviation, temp_weight, hum_weight
        )

        # Check for emergency conditions
        tent_data = self.ogb.dataStore.get("tentData")
        emergency_conditions = self.action_manager._getEmergencyOverride(tent_data)
        if emergency_conditions:
            self.action_manager._clearCooldownForEmergency(emergency_conditions)

        # Get capabilities and determine optimal devices
        caps = self.ogb.dataStore.get("capabilities")
        vpd_status = self._determine_vpd_status(
            temp_deviation, hum_deviation, tent_data
        )
        optimal_devices = self._get_optimal_devices(vpd_status)

        # Enhance action map with additional actions based on conditions
        enhanced_action_map = self._enhance_action_map(
            action_map,
            temp_deviation,
            hum_deviation,
            tent_data,
            caps,
            vpd_light_control,
            is_light_on,
            optimal_devices,
            vpd_status,  # Add VPD status for context-aware enhancement
        )

        # Apply dampening filter
        dampened_actions, blocked_actions = (
            self.action_manager._filterActionsByDampening(
                enhanced_action_map, temp_deviation, hum_deviation
            )
        )

        # Handle empty action list after dampening
        if not dampened_actions:
            await self._handle_blocked_actions(
                enhanced_action_map, emergency_conditions, temp_deviation, hum_deviation
            )
            return []

        # Resolve conflicts between actions
        final_actions = self._resolve_action_conflicts(dampened_actions)

        _LOGGER.info(
            f"{self.ogb.room}: Executing {len(final_actions)} of {len(enhanced_action_map)} actions "
            f"(VPD status: {vpd_status}, blocked: {len(enhanced_action_map) - len(dampened_actions)})"
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

    async def process_actions_basic(self, action_map: List) -> List:
        """
        Process actions without dampening (basic mode).

        Args:
            action_map: List of actions to process

        Returns:
            Final list of actions to execute
        """
        _LOGGER.debug(
            f"{self.ogb.room}: Processing {len(action_map)} actions (basic mode)"
        )

        # Get basic control settings
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        night_vpd_hold = self.ogb.dataStore.getDeep("controlOptions.nightVPDHold")
        is_light_on = self.ogb.dataStore.getDeep("isPlantDay.islightON")

        # Check night hold conditions
        if not is_light_on and not night_vpd_hold:
            _LOGGER.debug(f"{self.ogb.room}: VPD Night Hold Not Active - Ignoring VPD")
            await self._night_hold_fallback(action_map)
            return []

        # Calculate basic deviations (no weights)
        tent_data = self.ogb.dataStore.get("tentData")
        temp_deviation, hum_deviation = self._calculate_basic_deviations(tent_data)

        # Get capabilities and enhance action map
        caps = self.ogb.dataStore.get("capabilities")
        vpd_status = self._determine_vpd_status(
            temp_deviation, hum_deviation, tent_data
        )
        optimal_devices = self._get_optimal_devices(vpd_status)

        enhanced_action_map = self._enhance_action_map(
            action_map,
            temp_deviation,
            hum_deviation,
            tent_data,
            caps,
            vpd_light_control,
            is_light_on,
            optimal_devices,
            vpd_status,  # Add VPD status for context-aware enhancement
        )

        # Resolve conflicts
        final_actions = self._resolve_action_conflicts(enhanced_action_map)

        # Execute actions
        await self._execute_actions(final_actions)

        return final_actions

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
    ) -> Tuple[float, float, str]:
        """
        Calculate temperature and humidity deviations with weights.

        Args:
            temp_weight: Temperature weight factor
            hum_weight: Humidity weight factor

        Returns:
            Tuple of (temp_deviation, hum_deviation, weight_message)
        """
        tent_data = self.ogb.dataStore.get("tentData")
        temp_deviation = 0
        hum_deviation = 0
        weight_message = ""

        # Calculate temperature deviation
        if tent_data["temperature"] > tent_data["maxTemp"]:
            temp_deviation = round(
                (tent_data["temperature"] - tent_data["maxTemp"]) * temp_weight, 2
            )
            weight_message = f"Temp Too High: Deviation {temp_deviation}"
        elif tent_data["temperature"] < tent_data["minTemp"]:
            temp_deviation = round(
                (tent_data["temperature"] - tent_data["minTemp"]) * temp_weight, 2
            )
            weight_message = f"Temp Too Low: Deviation {temp_deviation}"

        # Calculate humidity deviation
        if tent_data["humidity"] > tent_data["maxHumidity"]:
            hum_deviation = round(
                (tent_data["humidity"] - tent_data["maxHumidity"]) * hum_weight, 2
            )
            weight_message = f"Humidity Too High: Deviation {hum_deviation}"
        elif tent_data["humidity"] < tent_data["minHumidity"]:
            hum_deviation = round(
                (tent_data["humidity"] - tent_data["minHumidity"]) * hum_weight, 2
            )
            weight_message = f"Humidity Too Low: Deviation {hum_deviation}"

        return temp_deviation, hum_deviation, weight_message

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
        temp_weight: float,
        hum_weight: float,
    ):
        """
        Publish weight and deviation information.

        Args:
            weight_message: Description of weight calculations
            temp_deviation: Temperature deviation
            hum_deviation: Humidity deviation
            temp_weight: Temperature weight
            hum_weight: Humidity weight
        """

        from ..data.OGBDataClasses.OGBPublications import OGBWeightPublication
        weight_publication = OGBWeightPublication(
            Name=self.ogb.room,
            message=weight_message,
            tempDeviation=temp_deviation,
            humDeviation=hum_deviation,
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
        Determine comprehensive VPD status based on environmental conditions.

        This method assesses the overall environmental stress level to determine
        which devices should be prioritized for VPD control.

        Args:
            temp_deviation: Temperature deviation from target
            hum_deviation: Humidity deviation from target
            tent_data: Current tent environmental data

        Returns:
            VPD status: "low", "medium", "high", or "critical"
        """
        if not tent_data:
            return "low"

        # Get current VPD and target
        current_vpd = tent_data.get("vpd", 0)
        target_vpd = tent_data.get("targetVpd", 0)
        vpd_deviation = abs(current_vpd - target_vpd)

        # Calculate environmental stress factors
        temp_factor = abs(temp_deviation) / max(abs(tent_data.get("maxTemp", 30) - tent_data.get("minTemp", 15)), 1)
        hum_factor = abs(hum_deviation) / max(abs(tent_data.get("maxHumidity", 80) - tent_data.get("minHumidity", 40)), 1)

        # Combined environmental stress
        combined_stress = (temp_factor + hum_factor) / 2

        # VPD deviation factor
        vpd_factor = min(vpd_deviation / 2.0, 1.0)  # Cap at 1.0 for VPD deviations > 2.0

        # Overall stress assessment
        total_stress = (combined_stress + vpd_factor) / 2

        # Determine status based on stress level
        if total_stress > 0.4:
            return "critical"  # Major environmental stress
        elif total_stress > 0.25:
            return "high"      # Significant environmental stress
        elif total_stress > 0.15:
            return "medium"    # Moderate environmental stress
        else:
            return "low"       # Minimal environmental stress

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
        Smaller deviations get shorter cooldowns to allow more responsive control.

        Args:
            capability: Device capability being controlled
            deviation: Current deviation from target

        Returns:
            Cooldown time in minutes
        """
        # Get base cooldown from action manager or use defaults
        if hasattr(self.action_manager, 'defaultCooldownMinutes'):
            base_cooldown = self.action_manager.defaultCooldownMinutes.get(capability, 2.0)
        else:
            # Fallback defaults
            base_cooldown = 2.0

        abs_deviation = abs(deviation)

        # Adaptive scaling based on deviation severity
        if abs_deviation > 5:
            # Major deviations - longer cooldown to allow significant change
            return base_cooldown * 1.5
        elif abs_deviation > 3:
            # Medium deviations - moderate cooldown extension
            return base_cooldown * 1.2
        elif abs_deviation < 1:
            # Small deviations - shorter cooldown for more responsive control
            return base_cooldown * 0.8

        # Normal deviations - use base cooldown
        return base_cooldown

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
        max_temp = tent_data.get("maxTemp", 30)
        min_temp = tent_data.get("minTemp", 15)
        current_humidity = tent_data.get("humidity", 50)
        max_humidity = tent_data.get("maxHumidity", 80)
        min_humidity = tent_data.get("minHumidity", 40)

        # Define buffer zones
        HEATER_BUFFER = 2.0    # Don't activate heater within 2°C of max temp
        COOLER_BUFFER = 2.0    # Don't activate cooler within 2°C of min temp
        HUMIDIFIER_BUFFER = 5.0   # Don't activate humidifier within 5% of max humidity
        DEHUMIDIFIER_BUFFER = 5.0 # Don't activate dehumidifier within 5% of min humidity

        # Calculate cutoff temperatures
        heater_cutoff_temp = max_temp - HEATER_BUFFER
        cooler_cutoff_temp = min_temp + COOLER_BUFFER
        humidifier_cutoff_humidity = max_humidity - HUMIDIFIER_BUFFER
        dehumidifier_cutoff_humidity = min_humidity + DEHUMIDIFIER_BUFFER

        filtered_actions = []

        for action in action_map:
            capability = getattr(action, 'capability', '')
            action_type = getattr(action, 'action', '')

            # Apply temperature buffer zones
            if capability == "canHeat" and action_type == "Increase":
                if current_temp >= heater_cutoff_temp:
                    _LOGGER.debug(
                        f"{self.ogb.room}: Skipping heater activation - "
                        f"temp {current_temp}°C too close to max {max_temp}°C (buffer: {HEATER_BUFFER}°C)"
                    )
                    continue

            elif capability == "canCool" and action_type == "Increase":
                if current_temp <= cooler_cutoff_temp:
                    _LOGGER.debug(
                        f"{self.ogb.room}: Skipping cooler activation - "
                        f"temp {current_temp}°C too close to min {min_temp}°C (buffer: {COOLER_BUFFER}°C)"
                    )
                    continue

            elif capability == "canCool" and action_type == "Reduce":
                # Reducing cooling (less cooling) is appropriate when temp is high
                # This increases VPD by allowing temperature to rise
                pass  # Allow this action

            # Apply humidity buffer zones
            elif capability == "canHumidify" and action_type == "Increase":
                if current_humidity >= humidifier_cutoff_humidity:
                    _LOGGER.debug(
                        f"{self.ogb.room}: Skipping humidifier activation - "
                        f"humidity {current_humidity}% too close to max {max_humidity}% (buffer: {HUMIDIFIER_BUFFER}%)"
                    )
                    continue

            elif capability == "canDehumidify" and action_type == "Increase":
                if current_humidity <= dehumidifier_cutoff_humidity:
                    _LOGGER.debug(
                        f"{self.ogb.room}: Skipping dehumidifier activation - "
                        f"humidity {current_humidity}% too close to min {min_humidity}% (buffer: {DEHUMIDIFIER_BUFFER}%)"
                    )
                    continue

            # Action passed buffer zone checks
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
                temp_actions = self._create_temperature_correction_actions(temp_dev, priority="high")
                enhanced.extend(temp_actions)

        # Humidity deviation handling based on VPD status
        if abs(hum_dev) > 5.0:  # Significant humidity deviation
            if vpd_status in ["critical", "high"]:
                # Urgent humidity correction
                hum_actions = self._create_humidity_correction_actions(hum_dev, priority="high")
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

        # Humidity emergency actions - only if we have dehumidification capability
        if hum_dev > 10.0 and caps.get("canDehumidify", {}).get("state"):
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

        # Humidity correction - only if we have dehumidification capability
        if hum_dev > 7.0 and caps.get("canDehumidify", {}).get("state"):
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

        # Gentle humidity correction - check capability exists first
        if abs(hum_dev) > 3.0:
            if hum_dev > 0 and caps.get("canDehumidify", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canDehumidify",
                    action="Increase",
                    Name=self.ogb.room,
                    message="Medium VPD: Balanced humidity adjustment",
                    priority="medium"
                ))
            elif hum_dev < 0 and caps.get("canHumidify", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canHumidify",
                    action="Increase",
                    Name=self.ogb.room,
                    message="Medium VPD: Balanced humidity adjustment",
                    priority="medium"
                ))

        return actions

    def _create_temperature_correction_actions(self, temp_dev, priority="medium"):
        """Create temperature correction actions.
        
        Only creates actions for capabilities that actually exist.
        """
        actions = []
        caps = self.ogb.dataStore.get("capabilities") or {}

        # Import locally to avoid circular imports
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

        if temp_dev > 0:  # Too hot - need cooling
            if caps.get("canCool", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canCool",
                    action="Increase",
                    Name=self.ogb.room,
                    message=f"Temperature correction (deviation: {temp_dev:.1f}°C)",
                    priority=priority
                ))
        else:  # Too cold - need heating
            if caps.get("canHeat", {}).get("state"):
                actions.append(OGBActionPublication(
                    capability="canHeat",
                    action="Increase",
                    Name=self.ogb.room,
                    message=f"Temperature correction (deviation: {temp_dev:.1f}°C)",
                    priority=priority
                ))

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

        # Apply buffer zones to prevent oscillation near limits
        enhanced_actions = self._apply_buffer_zones(enhanced_actions, tent_data)

        # Add VPD-status aware enhancements
        enhanced_actions = self._add_vpd_context_enhancements(
            enhanced_actions, vpd_status, temp_deviation, hum_deviation, tent_data
        )

        # Add deviation-based actions with VPD context
        enhanced_actions = self._add_deviation_actions_with_context(
            enhanced_actions, temp_deviation, hum_deviation, vpd_status
        )

        # Add emergency actions
        # Add CO2 actions
        # Priority: Emergency (highest) > VPD Context > Deviation > Base > CO2 (lowest)

        return enhanced_actions

    def _resolve_action_conflicts(self, action_map: List) -> List:
        """
        Resolve conflicts between actions targeting the same capability.

        Args:
            action_map: List of actions that may conflict

        Returns:
            Resolved action list with conflicts removed
        """
        # Group actions by capability
        actions_by_capability = {}
        for action in action_map:
            cap = getattr(action, "capability", None)
            if cap:
                if cap not in actions_by_capability:
                    actions_by_capability[cap] = []
                actions_by_capability[cap].append(action)

        resolved_actions = []

        # For each capability, select the best action
        for cap, actions in actions_by_capability.items():
            if len(actions) == 1:
                resolved_actions.extend(actions)
            else:
                # Select highest priority action
                priority_order = {"high": 1, "medium": 2, "low": 3}
                best_action = min(
                    actions,
                    key=lambda x: priority_order.get(
                        getattr(x, "priority", "medium"), 2
                    ),
                )
                resolved_actions.append(best_action)

        return resolved_actions

    async def _execute_actions(self, action_map: List):
        """
        Execute the final list of actions.

        Args:
            action_map: Actions to execute
        """
        await self.action_manager.publicationActionHandler(action_map)
        await self.ogb.eventManager.emit("LogForClient", action_map, haEvent=True)

    async def _night_hold_fallback(self, action_map: List):
        """
        Handle night hold fallback for VPD actions.

        When lights are off and night VPD hold is NOT active, this performs
        energy-saving fallback by:
        1. Ignoring VPD-increasing actions (heating, cooling, humidity, lighting, CO2)
        2. Allowing only basic ventilation (exhaust, intake)
        3. Reducing climate control devices to minimum state

        Args:
            action_map: Actions to process during night hold
        """
        _LOGGER.debug(f"{self.ogb.room}: VPD Night Hold NOT ACTIVE - Executing energy-saving fallback")

        # Emit event for logging
        await self.ogb.eventManager.emit(
            "LogForClient",
            {"Name": self.ogb.room, "NightVPDHold": "NotActive Executing-Fallback"},
            haEvent=True,
        )

        # Define device categories for night hold
        excluded_caps = {
            "canHeat",      # No heating at night
            "canCool",      # No cooling at night
            "canHumidify",  # No humidification at night
            "canClimate",   # No climate control at night
            "canDehumidify", # No dehumidification at night
            "canLight",     # No lighting at night (obviously)
            "canCO2",       # No CO2 control at night
        }

        # Devices that get reduced to minimum state at night
        reduce_caps = {
            "canHeat",      # Reduce heating
            "canCool",      # Reduce cooling
            "canHumidify",  # Reduce humidification
            "canClimate",   # Reduce climate control
            "canDehumidify", # Reduce dehumidification
            "canCO2",       # Reduce CO2
        }

        # Filter actions to only allow ventilation
        filtered_actions = [
            action for action in action_map
            if action.capability not in excluded_caps
        ]

        # Create reduction actions for climate control devices
        reduction_actions = []
        for action in action_map:
            if action.capability in reduce_caps:
                # Create a "Reduce" action for this device
                reduction_action = self._create_reduced_action(action)
                reduction_actions.append(reduction_action)

        # Combine allowed actions with reduction actions
        final_actions = filtered_actions + reduction_actions

        if final_actions:
            _LOGGER.info(
                f"{self.ogb.room}: Night hold fallback - executing {len(final_actions)} actions "
                f"({len(filtered_actions)} ventilation, {len(reduction_actions)} reductions)"
            )
            await self._execute_actions(final_actions)
        else:
            _LOGGER.debug(f"{self.ogb.room}: Night hold fallback - no actions to execute")

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
        return {
            "room": self.ogb.room,
            "dampening_enabled": True,  # Always enabled in this module
            "active_cooldowns": len(self.action_manager.actionHistory),
            "emergency_mode": getattr(self.action_manager, "_emergency_mode", False),
        }
