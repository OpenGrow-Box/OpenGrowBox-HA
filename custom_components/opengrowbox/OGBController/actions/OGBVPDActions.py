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

    def _add_bounds_correction_actions(self, action_map, capabilities, context=""):
        """
        Prüft Temp/Humidity Bounds und fügt Korrektur-Actions hinzu.
        Wird aufgerufen NACH der normalen VPD Logik.
        """
        current_temp = self.ogb.dataStore.getDeep("tentData.temperature")
        current_hum = self.ogb.dataStore.getDeep("tentData.humidity")
        min_temp = self.ogb.dataStore.getDeep("tentData.minTemp")
        max_temp = self.ogb.dataStore.getDeep("tentData.maxTemp")
        min_hum = self.ogb.dataStore.getDeep("tentData.minHumidity")
        max_hum = self.ogb.dataStore.getDeep("tentData.maxHumidity")
        
        # Hysterese-Puffer
        TEMP_BUFFER = 1.5
        HUM_BUFFER = 3.0
        
        correction_actions = []
        
        # Temp zu niedrig
        if current_temp is not None and min_temp is not None:
            if current_temp < (min_temp + TEMP_BUFFER):
                if capabilities.get("canHeat", {}).get("state", False):
                    correction_actions.append(self._create_action("canHeat", "Increase", f"{context}Bounds: Temp low ({current_temp:.1f} < {min_temp})"))
        
        # Temp zu hoch
        if current_temp is not None and max_temp is not None:
            if current_temp > (max_temp - TEMP_BUFFER):
                if capabilities.get("canCool", {}).get("state", False):
                    correction_actions.append(self._create_action("canCool", "Increase", f"{context}Bounds: Temp high ({current_temp:.1f} > {max_temp})"))
        
        # Humidity zu niedrig
        if current_hum is not None and min_hum is not None:
            if current_hum < (min_hum + HUM_BUFFER):
                if capabilities.get("canHumidify", {}).get("state", False):
                    correction_actions.append(self._create_action("canHumidify", "Increase", f"{context}Bounds: Humidity low ({current_hum:.1f} < {min_hum})"))
        
        # Humidity zu hoch
        if current_hum is not None and max_hum is not None:
            if current_hum > (max_hum - HUM_BUFFER):
                if capabilities.get("canDehumidify", {}).get("state", False):
                    correction_actions.append(self._create_action("canDehumidify", "Increase", f"{context}Bounds: Humidity high ({current_hum:.1f} > {max_hum})"))
        
        return action_map + correction_actions

    # =================================================================
    # Basic VPD Control Actions
    # =================================================================

    async def increase_vpd(self, capabilities: Dict[str, Any]):
        """
        Increase VPD by adjusting appropriate devices.
        
        STAGED ACTIVATION: Based on VPD deviation magnitude
        - Small deviation (< 0.10): Only 1 device (exhaust)
        - Medium deviation (< 0.20): 2 devices (exhaust + heat)
        - Large deviation (>= 0.20): All devices (original behavior)
        """
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self._is_light_on()
        action_message = "VPD-Increase Action"
        
        # Calculate deviation for staged activation
        currentVPD = self.ogb.dataStore.getDeep("vpd.current")
        perfectionVPD = self.ogb.dataStore.getDeep("vpd.perfection")
        try:
            deviation = abs(float(currentVPD) - float(perfectionVPD))
        except (TypeError, ValueError):
            deviation = 0.0

        action_map = []

        # STAGED ACTIVATION based on deviation
        if deviation < 0.10:
            # Stage 1: Small deviation - Only most effective device
            _LOGGER.info(f"{self.ogb.room}: VPD increase - small deviation ({deviation:.3f}), using minimal correction")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(
                    self._create_action("canExhaust", "Increase", action_message)
                )
        
        elif deviation < 0.20:
            # Stage 2: Medium deviation - Two key devices
            _LOGGER.info(f"{self.ogb.room}: VPD increase - medium deviation ({deviation:.3f}), using moderate correction")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(
                    self._create_action("canExhaust", "Increase", action_message)
                )
            if capabilities.get("canHeat", {}).get("state", False):
                action_map.append(
                    self._create_action("canHeat", "Increase", action_message)
                )
        
        else:
            # Stage 3: Large deviation - All devices (original behavior)
            _LOGGER.info(f"{self.ogb.room}: VPD increase - large deviation ({deviation:.3f}), using full correction")
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

        await self.action_manager.checkLimitsAndPublicate(action_map)

    async def reduce_vpd(self, capabilities: Dict[str, Any]):
        """
        Reduce VPD by adjusting appropriate devices.
        
        STAGED ACTIVATION: Based on VPD deviation magnitude
        - Small deviation (< 0.10): Only 1 device (exhaust)
        - Medium deviation (< 0.20): 2 devices (exhaust + humidifier)
        - Large deviation (>= 0.20): All devices (original behavior)
        """
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self._is_light_on()
        action_message = "VPD-Reduce Action"
        
        # Calculate deviation for staged activation
        currentVPD = self.ogb.dataStore.getDeep("vpd.current")
        perfectionVPD = self.ogb.dataStore.getDeep("vpd.perfection")
        try:
            deviation = abs(float(currentVPD) - float(perfectionVPD))
        except (TypeError, ValueError):
            deviation = 0.0

        action_map = []

        # STAGED ACTIVATION based on deviation
        if deviation < 0.10:
            # Stage 1: Small deviation - Only most effective device
            _LOGGER.info(f"{self.ogb.room}: VPD reduce - small deviation ({deviation:.3f}), using minimal correction")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(
                    self._create_action("canExhaust", "Reduce", action_message)
                )
        
        elif deviation < 0.20:
            # Stage 2: Medium deviation - Two key devices
            _LOGGER.info(f"{self.ogb.room}: VPD reduce - medium deviation ({deviation:.3f}), using moderate correction")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(
                    self._create_action("canExhaust", "Reduce", action_message)
                )
            if capabilities.get("canHumidify", {}).get("state", False):
                action_map.append(
                    self._create_action("canHumidify", "Increase", action_message)
                )
        
        else:
            # Stage 3: Large deviation - All devices (original behavior)
            _LOGGER.info(f"{self.ogb.room}: VPD reduce - large deviation ({deviation:.3f}), using full correction")
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

        await self.action_manager.checkLimitsAndPublicate(action_map)

    async def increase_vpd_target(self, capabilities: Dict[str, Any]):
        """Increase VPD for VPD Target mode with staged activation."""
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self._is_light_on()
        action_message = "VPD-Target Increase Action"

        # Calculate deviation for staged activation
        currentVPD = self.ogb.dataStore.getDeep("vpd.current")
        targetedVPD = self.ogb.dataStore.getDeep("vpd.targeted")
        try:
            deviation = abs(float(currentVPD) - float(targetedVPD))
        except (TypeError, ValueError):
            deviation = 0.0

        action_map = []

        # STAGED ACTIVATION based on deviation
        if deviation < 0.10:
            # Stage 1: Small deviation - Only most effective device
            _LOGGER.info(f"{self.ogb.room}: VPD target increase - small deviation ({deviation:.3f}), using minimal correction")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(self._create_action("canExhaust", "Increase", action_message))
        elif deviation < 0.20:
            # Stage 2: Medium deviation - Two key devices
            _LOGGER.info(f"{self.ogb.room}: VPD target increase - medium deviation ({deviation:.3f}), using moderate correction")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(self._create_action("canExhaust", "Increase", action_message))
            if capabilities.get("canHeat", {}).get("state", False):
                action_map.append(self._create_action("canHeat", "Increase", action_message))
        else:
            # Stage 3: Large deviation - All devices (original behavior)
            _LOGGER.info(f"{self.ogb.room}: VPD target increase - large deviation ({deviation:.3f}), using full correction")
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

        # Bounds-Korrektur hinzufügen
        action_map = self._add_bounds_correction_actions(action_map, capabilities, "Target-")

        await self.action_manager.checkLimitsAndPublicateTarget(action_map)

    async def reduce_vpd_target(self, capabilities: Dict[str, Any]):
        """Reduce VPD for VPD Target mode with staged activation."""
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
        is_light_on = self._is_light_on()
        action_message = "VPD-Target Reduce Action"

        # Calculate deviation for staged activation
        currentVPD = self.ogb.dataStore.getDeep("vpd.current")
        targetedVPD = self.ogb.dataStore.getDeep("vpd.targeted")
        try:
            deviation = abs(float(currentVPD) - float(targetedVPD))
        except (TypeError, ValueError):
            deviation = 0.0

        action_map = []

        # STAGED ACTIVATION based on deviation
        if deviation < 0.10:
            # Stage 1: Small deviation - Only most effective device
            _LOGGER.info(f"{self.ogb.room}: VPD target reduce - small deviation ({deviation:.3f}), using minimal correction")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(self._create_action("canExhaust", "Reduce", action_message))
        elif deviation < 0.20:
            # Stage 2: Medium deviation - Two key devices
            _LOGGER.info(f"{self.ogb.room}: VPD target reduce - medium deviation ({deviation:.3f}), using moderate correction")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(self._create_action("canExhaust", "Reduce", action_message))
            if capabilities.get("canHumidify", {}).get("state", False):
                action_map.append(self._create_action("canHumidify", "Increase", action_message))
        else:
            # Stage 3: Large deviation - All devices (original behavior)
            _LOGGER.info(f"{self.ogb.room}: VPD target reduce - large deviation ({deviation:.3f}), using full correction")
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

        # Bounds-Korrektur hinzufügen
        action_map = self._add_bounds_correction_actions(action_map, capabilities, "Target-")

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
