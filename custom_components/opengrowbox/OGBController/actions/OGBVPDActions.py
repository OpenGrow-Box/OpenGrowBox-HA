"""
OpenGrowBox VPD Actions Module

Handles VPD (Vapor Pressure Deficit) response actions for device control.
Manages increase, reduce, and fine-tune operations for VPD control with
and without dampening logic.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict

from ..managers.OGBActionManager import OGBActionManager

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class OGBVPDActions:
    """
    VPD action management for OpenGrowBox.

    Handles all VPD-related device control actions including:
    - VPD increase/reduce operations
    - Fine-tuning VPD values
    - Dampening-aware VPD control
    - Buffer-aware temperature control
    """

    def __init__(self, ogb: "OpenGrowBox"):
        """
        Initialize VPD actions.

        Args:
            ogb: Reference to the parent OpenGrowBox instance
        """
        self.ogb = ogb
        self.action_manager: OGBActionManager = ogb.actionManager

    def _is_light_on(self) -> bool:
        """Return True only when plant day/light is active."""
        return bool(self.ogb.dataStore.getDeep("isPlantDay.islightON", False))

    # =================================================================
    # Basic VPD Control Actions
    # =================================================================

    async def increase_vpd(self, capabilities: Dict[str, Any]):
        """
        Increase VPD by adjusting appropriate devices.

        Args:
            capabilities: Device capabilities and states
        """
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self._is_light_on()
        action_message = "VPD-Increase Action"

        action_map = []

        # Build action map for VPD increase
        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(
                self._create_action("canExhaust", "Increase", action_message)
            )
        if capabilities.get("canIntake", {}).get("state", False):
            action_map.append(
                self._create_action("canIntake", "Reduce", action_message)
            )
        if capabilities.get("canVentilate", {}).get("state", False):
            action_map.append(
                self._create_action("canVentilate", "Increase", action_message)
            )
        if capabilities.get("canHumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canHumidify", "Reduce", action_message)
            )
        if capabilities.get("canDehumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canDehumidify", "Increase", action_message)
            )
        if capabilities.get("canHeat", {}).get("state", False):
            action_map.append(
                self._create_action("canHeat", "Increase", action_message)
            )
        if capabilities.get("canCool", {}).get("state", False):
            action_map.append(self._create_action("canCool", "Reduce", action_message))
        if capabilities.get("canClimate", {}).get("state", False):
            action_map.append(self._create_action("canClimate", "Eval", action_message))
        # Check CO2 control switch for VPD-based CO2 actions
        co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
        if capabilities.get("canCO2", {}).get("state", False) and co2_control_enabled:
            if is_light_on:
                action_map.append(self._create_action("canCO2", "Increase", action_message))
            else:
                action_map.append(self._create_action("canCO2", "Reduce", action_message))
                _LOGGER.debug(
                    f"{self.ogb.room}: Night mode active - CO2 increase blocked, forcing CO2 off"
                )

        if vpd_light_control == True and capabilities.get("canLight", {}).get("state", False):
            action_map.append(
                self._create_action("canLight", "Increase", action_message)
            )

        action_map = self._apply_temperature_safety_overrides(
            action_map, capabilities, action_message
        )
        await self.action_manager.checkLimitsAndPublicate(action_map)

    async def reduce_vpd(self, capabilities: Dict[str, Any]):
        """
        Reduce VPD by adjusting appropriate devices.

        Args:
            capabilities: Device capabilities and states
        """
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self._is_light_on()
        action_message = "VPD-Reduce Action"

        action_map = []

        # Build action map for VPD reduction
        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(
                self._create_action("canExhaust", "Reduce", action_message)
            )
        if capabilities.get("canIntake", {}).get("state", False):
            action_map.append(
                self._create_action("canIntake", "Increase", action_message)
            )
        if capabilities.get("canVentilate", {}).get("state", False):
            action_map.append(
                self._create_action("canVentilate", "Reduce", action_message)
            )
        if capabilities.get("canHumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canHumidify", "Increase", action_message)
            )
        if capabilities.get("canDehumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canDehumidify", "Reduce", action_message)
            )
        if capabilities.get("canHeat", {}).get("state", False):
            action_map.append(self._create_action("canHeat", "Reduce", action_message))
        if capabilities.get("canCool", {}).get("state", False):
            action_map.append(
                self._create_action("canCool", "Increase", action_message)
            )
        if capabilities.get("canClimate", {}).get("state", False):
            action_map.append(self._create_action("canClimate", "Eval", action_message))
        # Check CO2 control switch for VPD-based CO2 actions
        co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
        if capabilities.get("canCO2", {}).get("state", False) and co2_control_enabled:
            action_map.append(self._create_action("canCO2", "Reduce", action_message))
            if not is_light_on:
                _LOGGER.debug(
                    f"{self.ogb.room}: Night mode active - keeping CO2 forced off"
                )

        if vpd_light_control == True and capabilities.get("canLight", {}).get("state", False):
            action_map.append(
                self._create_action("canLight", "Reduce", action_message)
            )

        action_map = self._apply_temperature_safety_overrides(
            action_map, capabilities, action_message
        )
        await self.action_manager.checkLimitsAndPublicate(action_map)

    async def increase_vpd_target(self, capabilities: Dict[str, Any]):
        """Increase VPD for VPD Target mode."""
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self._is_light_on()
        action_message = "VPD-Target Increase Action"

        action_map = []

        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(self._create_action("canExhaust", "Increase", action_message))
        if capabilities.get("canIntake", {}).get("state", False):
            action_map.append(self._create_action("canIntake", "Reduce", action_message))
        if capabilities.get("canVentilate", {}).get("state", False):
            action_map.append(self._create_action("canVentilate", "Increase", action_message))
        if capabilities.get("canHumidify", {}).get("state", False):
            action_map.append(self._create_action("canHumidify", "Reduce", action_message))
        if capabilities.get("canDehumidify", {}).get("state", False):
            action_map.append(self._create_action("canDehumidify", "Increase", action_message))
        if capabilities.get("canHeat", {}).get("state", False):
            action_map.append(self._create_action("canHeat", "Increase", action_message))
        if capabilities.get("canCool", {}).get("state", False):
            action_map.append(self._create_action("canCool", "Reduce", action_message))
        if capabilities.get("canClimate", {}).get("state", False):
            action_map.append(self._create_action("canClimate", "Eval", action_message))

        co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
        if capabilities.get("canCO2", {}).get("state", False) and co2_control_enabled:
            if is_light_on:
                action_map.append(self._create_action("canCO2", "Increase", action_message))
            else:
                action_map.append(self._create_action("canCO2", "Reduce", action_message))
                _LOGGER.debug(
                    f"{self.ogb.room}: Night mode active - CO2 target increase blocked, forcing CO2 off"
                )

        if vpd_light_control is True and capabilities.get("canLight", {}).get("state", False):
            action_map.append(self._create_action("canLight", "Increase", action_message))

        action_map = self._apply_temperature_safety_overrides(
            action_map, capabilities, action_message
        )
        await self.action_manager.checkLimitsAndPublicateTarget(action_map)

    async def reduce_vpd_target(self, capabilities: Dict[str, Any]):
        """Reduce VPD for VPD Target mode."""
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self._is_light_on()
        action_message = "VPD-Target Reduce Action"

        action_map = []

        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(self._create_action("canExhaust", "Reduce", action_message))
        if capabilities.get("canIntake", {}).get("state", False):
            action_map.append(self._create_action("canIntake", "Increase", action_message))
        if capabilities.get("canVentilate", {}).get("state", False):
            action_map.append(self._create_action("canVentilate", "Reduce", action_message))
        if capabilities.get("canHumidify", {}).get("state", False):
            action_map.append(self._create_action("canHumidify", "Increase", action_message))
        if capabilities.get("canDehumidify", {}).get("state", False):
            action_map.append(self._create_action("canDehumidify", "Reduce", action_message))
        if capabilities.get("canHeat", {}).get("state", False):
            action_map.append(self._create_action("canHeat", "Reduce", action_message))
        if capabilities.get("canCool", {}).get("state", False):
            action_map.append(
                self._create_action("canCool", "Increase", action_message)
            )
        if capabilities.get("canClimate", {}).get("state", False):
            action_map.append(self._create_action("canClimate", "Eval", action_message))

        co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
        if capabilities.get("canCO2", {}).get("state", False) and co2_control_enabled:
            action_map.append(self._create_action("canCO2", "Reduce", action_message))
            if not is_light_on:
                _LOGGER.debug(
                    f"{self.ogb.room}: Night mode active - keeping CO2 forced off"
                )

        if vpd_light_control is True and capabilities.get("canLight", {}).get("state", False):
            action_map.append(self._create_action("canLight", "Reduce", action_message))

        action_map = self._apply_temperature_safety_overrides(
            action_map, capabilities, action_message
        )
        await self.action_manager.checkLimitsAndPublicateTarget(action_map)

    async def fine_tune_vpd(self, capabilities: Dict[str, Any]):
        """
        Fine-tune VPD to reach target value.

        Args:
            capabilities: Device capabilities and states
        """
        # Get current VPD values
        current_vpd = self.ogb.dataStore.getDeep("vpd.current")
        perfection_vpd = self.ogb.dataStore.getDeep("vpd.perfection")

        # Validate VPD values before calculation
        if current_vpd is None or perfection_vpd is None:
            _LOGGER.warning(f"{self.ogb.room}: VPD values not available for fine-tuning (current={current_vpd}, perfect={perfection_vpd})")
            return

        # Calculate delta and round to two decimal places
        delta = round(perfection_vpd - current_vpd, 2)

        if delta > 0:
            _LOGGER.debug(f"Fine-tuning: {self.ogb.room} Increasing VPD by {delta}.")
            await self.increase_vpd(capabilities)
        elif delta < 0:
            _LOGGER.debug(f"Fine-tuning: {self.ogb.room} Reducing VPD by {-delta}.")
            await self.reduce_vpd(capabilities)

    async def fine_tune_vpd_target(self, capabilities: Dict[str, Any]):
        """Fine-tune VPD for VPD Target mode."""
        current_vpd = self.ogb.dataStore.getDeep("vpd.current")
        targeted_vpd = self.ogb.dataStore.getDeep("vpd.targeted")

        if current_vpd is None or targeted_vpd is None:
            _LOGGER.warning(f"{self.ogb.room}: VPD Target values missing for fine-tuning")
            return

        try:
            current_vpd = float(current_vpd)
            targeted_vpd = float(targeted_vpd)
        except (TypeError, ValueError):
            _LOGGER.warning(
                f"{self.ogb.room}: Invalid VPD Target values for fine-tuning "
                f"(current={current_vpd}, targeted={targeted_vpd})"
            )
            return

        delta = round(targeted_vpd - current_vpd, 2)

        if delta > 0:
            await self.increase_vpd_target(capabilities)
        elif delta < 0:
            await self.reduce_vpd_target(capabilities)

    # =================================================================
    # VPD Control with Dampening
    # =================================================================

    async def increase_vpd_damping(self, capabilities: Dict[str, Any]):
        """
        Increase VPD with dampening and buffer checks.

        Args:
            capabilities: Device capabilities and states
        """
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        action_message = "VPD-Increase Action"

        action_map = []

        # Build action map with buffer checks
        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(
                self._create_action("canExhaust", "Increase", action_message)
            )
        if capabilities.get("canIntake", {}).get("state", False):
            action_map.append(
                self._create_action("canIntake", "Reduce", action_message)
            )
        if capabilities.get("canVentilate", {}).get("state", False):
            action_map.append(
                self._create_action("canVentilate", "Increase", action_message)
            )
        if capabilities.get("canHumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canHumidify", "Reduce", action_message)
            )
        if capabilities.get("canDehumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canDehumidify", "Increase", action_message)
            )
        if capabilities.get("canHeat", {}).get("state", False):
            action_map.append(
                self._create_action("canHeat", "Increase", action_message)
            )
        if capabilities.get("canCool", {}).get("state", False):
            action_map.append(self._create_action("canCool", "Reduce", action_message))
        if capabilities.get("canClimate", {}).get("state", False):
            action_map.append(self._create_action("canClimate", "Eval", action_message))
        # CO2 Control check (Bug #8 fix)
        co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
        if capabilities.get("canCO2", {}).get("state", False) and co2_control_enabled:
            action_map.append(self._create_action("canCO2", "Increase", action_message))

        if vpd_light_control == True:
            if capabilities.get("canLight", {}).get("state", False):
                action_map.append(
                    self._create_action("canLight", "Increase", action_message)
                )

        action_map = self._apply_temperature_safety_overrides(
            action_map, capabilities, action_message
        )
        await self.action_manager.checkLimitsAndPublicateWithDampening(action_map)

    async def reduce_vpd_damping(self, capabilities: Dict[str, Any]):
        """
        Reduce VPD with dampening.

        Args:
            capabilities: Device capabilities and states
        """
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        action_message = "VPD-Reduce Action"

        action_map = []

        # Build action map for VPD reduction with dampening
        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(
                self._create_action("canExhaust", "Reduce", action_message)
            )
        if capabilities.get("canIntake", {}).get("state", False):
            action_map.append(
                self._create_action("canIntake", "Increase", action_message)
            )
        if capabilities.get("canVentilate", {}).get("state", False):
            action_map.append(
                self._create_action("canVentilate", "Reduce", action_message)
            )
        if capabilities.get("canHumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canHumidify", "Increase", action_message)
            )
        if capabilities.get("canDehumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canDehumidify", "Reduce", action_message)
            )
        if capabilities.get("canHeat", {}).get("state", False):
            action_map.append(self._create_action("canHeat", "Reduce", action_message))
        if capabilities.get("canCool", {}).get("state", False):
            action_map.append(
                self._create_action("canCool", "Increase", action_message)
            )
        if capabilities.get("canClimate", {}).get("state", False):
            action_map.append(self._create_action("canClimate", "Eval", action_message))
        # CO2 Control check (Bug #8 fix)
        co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
        if capabilities.get("canCO2", {}).get("state", False) and co2_control_enabled:
            action_map.append(self._create_action("canCO2", "Reduce", action_message))

        if vpd_light_control == True:
            if capabilities.get("canLight", {}).get("state", False):
                action_map.append(
                    self._create_action("canLight", "Reduce", action_message)
                )

        action_map = self._apply_temperature_safety_overrides(
            action_map, capabilities, action_message
        )
        await self.action_manager.checkLimitsAndPublicateWithDampening(action_map)

    async def fine_tune_vpd_damping(self, capabilities: Dict[str, Any]):
        """
        Fine-tune VPD with dampening to reach target value.

        Args:
            capabilities: Device capabilities and states
        """
        # Get current VPD values
        current_vpd = self.ogb.dataStore.getDeep("vpd.current")
        perfection_vpd = self.ogb.dataStore.getDeep("vpd.perfection")

        # Validate VPD values before calculation (Bug #2 fix)
        if current_vpd is None or perfection_vpd is None:
            _LOGGER.warning(f"{self.ogb.room}: VPD values not available for fine-tuning (current={current_vpd}, perfect={perfection_vpd})")
            return

        # Calculate delta and round to two decimal places
        delta = round(perfection_vpd - current_vpd, 2)

        if delta > 0:
            _LOGGER.debug(f"Fine-tuning: {self.ogb.room} Increasing VPD by {delta}.")
            await self.increase_vpd_damping(capabilities)
        elif delta < 0:
            _LOGGER.debug(f"Fine-tuning: {self.ogb.room} Reducing VPD by {-delta}.")
            await self.reduce_vpd_damping(capabilities)

    # =================================================================
    # Helper Methods
    # =================================================================

    def _create_action(self, capability: str, action: str, message: str):
        """
        Create an action publication.

        Args:
            capability: Device capability
            action: Action to perform
            message: Action message

        Returns:
            Action publication object
        """
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

        return OGBActionPublication(
            capability=capability,
            action=action,
            Name=self.ogb.room,
            message=message,
            priority="",
        )

    def _is_humidity_critical(self, tent_data: dict) -> bool:
        """Check if humidity is at critical level requiring emergency override.
        
        Critical means:
        - humidity >= maxHumidity (too wet - mold risk)
        - humidity <= minHumidity (too dry)
        """
        current_hum = tent_data.get("humidity")
        max_hum = tent_data.get("maxHumidity")
        min_hum = tent_data.get("minHumidity")

        if current_hum is None:
            return False

        current_hum_float = float(current_hum)

        is_critical_over_max = (
            max_hum is not None
            and current_hum_float >= float(max_hum)
        )

        is_critical_under_min = (
            min_hum is not None
            and current_hum_float <= float(min_hum)
        )

        return is_critical_over_max or is_critical_under_min

    def _apply_temperature_safety_overrides(
        self, action_map: list, capabilities: Dict[str, Any], action_message: str
    ) -> list:
        """Apply temperature safety transitions without skipping the whole VPD path."""
        tent_data = self.ogb.dataStore.get("tentData") or {}
        current_temp = tent_data.get("temperature")
        min_temp = tent_data.get("minTemp")
        max_temp = tent_data.get("maxTemp")

        if current_temp is None or min_temp is None or max_temp is None:
            return action_map

        heater_buffer = float(
            self.ogb.dataStore.getDeep("controlOptions.heaterBuffer") or 2.0
        )
        cooler_buffer = float(
            self.ogb.dataStore.getDeep("controlOptions.coolerBuffer") or 2.0
        )

        cold_guard = float(min_temp) + cooler_buffer
        hot_guard = float(max_temp) - heater_buffer

        humidity_critical = self._is_humidity_critical(tent_data)
        current_hum = tent_data.get("humidity")
        max_hum = tent_data.get("maxHumidity")

        actions_by_capability = {}
        for action in action_map:
            capability = getattr(action, "capability", None)
            if capability:
                actions_by_capability[capability] = action

        def set_action(capability: str, action: str):
            if capabilities.get(capability, {}).get("state", False):
                actions_by_capability[capability] = self._create_action(
                    capability,
                    action,
                    f"{action_message}",
                )

        if float(current_temp) <= cold_guard:
            set_action("canHeat", "Increase")
            set_action("canCool", "Reduce")

            if humidity_critical:
                _LOGGER.warning(
                    f"{self.ogb.room}: Humidity CRITICAL ({current_hum}%, max={max_hum}%), "
                    f"allowing air exchange despite cold temp to prevent mold!"
                )
            else:
                set_action("canExhaust", "Reduce")
                set_action("canVentilate", "Reduce")
                set_action("canIntake", "Reduce")
                _LOGGER.info(
                    f"{self.ogb.room}: Temp safety active (cold). temp={current_temp}°C, "
                    f"guard={cold_guard}°C -> heat up, cooling/airflow down"
                )

        elif float(current_temp) >= hot_guard:
            set_action("canHeat", "Reduce")
            set_action("canCool", "Increase")
            set_action("canExhaust", "Increase")
            set_action("canVentilate", "Increase")
            set_action("canIntake", "Increase")
            _LOGGER.info(
                f"{self.ogb.room}: Temp safety active (hot). temp={current_temp}°C, "
                f"guard={hot_guard}°C -> cooling/airflow up, heating down"
            )

        return list(actions_by_capability.values())

    def get_vpd_action_status(self) -> Dict[str, Any]:
        """
        Get VPD action status information.

        Returns:
            Dictionary with VPD action status
        """
        return {
            "room": self.ogb.room,
            "current_vpd": self.ogb.dataStore.getDeep("vpd.current"),
            "target_vpd": self.ogb.dataStore.getDeep("vpd.target"),
            "targeted_vpd": self.ogb.dataStore.getDeep("vpd.targeted"),
            "targeted_vpd_min": self.ogb.dataStore.getDeep("vpd.targetedMin"),
            "targeted_vpd_max": self.ogb.dataStore.getDeep("vpd.targetedMax"),
            "perfection_vpd": self.ogb.dataStore.getDeep("vpd.perfection"),
            "vpd_light_control": self.ogb.dataStore.getDeep(
                "controlOptions.vpdLightControl"
            ),
            "dampening_active": self.ogb.dataStore.getDeep(
                "controlOptions.vpdDeviceDampening"
            ),
        }
