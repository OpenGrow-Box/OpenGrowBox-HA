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
            action_map.append(self._create_action("canCO2", "Increase", action_message))

        if vpd_light_control == True and capabilities.get("canLight", {}).get("state", False):
            action_map.append(
                self._create_action("canLight", "Increase", action_message)
            )

        await self.action_manager.checkLimitsAndPublicate(action_map)

    async def reduce_vpd(self, capabilities: Dict[str, Any]):
        """
        Reduce VPD by adjusting appropriate devices.

        Args:
            capabilities: Device capabilities and states
        """
        vpd_light_control = self.ogb.dataStore.getDeep("controlOptions.vpdLightControl")
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

        if vpd_light_control == True and capabilities.get("canLight", {}).get("state", False):
            action_map.append(
                self._create_action("canLight", "Reduce", action_message)
            )

        await self.action_manager.checkLimitsAndPublicate(action_map)

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

        # BUFFER CHECK with Null safety
        tent_data = self.ogb.dataStore.get("tentData") or {}
        current_temp = tent_data.get("temperature")
        max_temp = tent_data.get("maxTemp")
        min_temp = tent_data.get("minTemp")

        heater_buffer = 2.0
        cooler_buffer = 2.0
        heater_cutoff_temp = max_temp - heater_buffer if max_temp is not None else None
        cooler_cutoff_temp = min_temp + cooler_buffer if min_temp is not None else None

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
        # Apply buffer: Only heat if temp is below heater cutoff
        if capabilities.get("canHeat", {}).get("state", False):
            if current_temp is None or heater_cutoff_temp is None or current_temp < heater_cutoff_temp:
                action_map.append(
                    self._create_action("canHeat", "Increase", action_message)
                )
            else:
                _LOGGER.debug(f"{self.ogb.room}: Skipping heater increase - temp {current_temp}°C >= cutoff {heater_cutoff_temp}°C")
        # Apply buffer: Only cool if temp is above cooler cutoff  
        if capabilities.get("canCool", {}).get("state", False):
            if current_temp is None or cooler_cutoff_temp is None or current_temp > cooler_cutoff_temp:
                action_map.append(self._create_action("canCool", "Reduce", action_message))
            else:
                _LOGGER.debug(f"{self.ogb.room}: Skipping cooler reduce - temp {current_temp}°C <= cutoff {cooler_cutoff_temp}°C")
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
            "perfection_vpd": self.ogb.dataStore.getDeep("vpd.perfection"),
            "vpd_light_control": self.ogb.dataStore.getDeep(
                "controlOptions.vpdLightControl"
            ),
            "dampening_active": self.ogb.dataStore.getDeep(
                "controlOptions.vpdDeviceDampening"
            ),
        }
