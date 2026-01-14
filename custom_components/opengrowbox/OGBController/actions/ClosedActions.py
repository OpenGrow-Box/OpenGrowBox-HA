"""
OpenGrowBox Closed Environment Actions Module

Handles closed-loop environmental control actions for sealed grow chambers.
Manages CO2, O2, humidity, and air recirculation without traditional ventilation.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict

from ..managers.OGBActionManager import OGBActionManager
from ..logic.ClosedControlLogic import ClosedControlLogic

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class ClosedActions:
    """
    Closed environment action management for OpenGrowBox.

    Handles all closed-loop environmental control actions including:
    - CO2 level maintenance (800-1500 ppm for optimal photosynthesis)
    - O2 safety monitoring (<19% emergency ventilation trigger)
    - Precise humidity control without ventilation
    - Air recirculation for CO2 distribution and thermal uniformity
    """

    def __init__(self, ogb: "OpenGrowBox"):
        """
        Initialize closed environment actions.

        Args:
            ogb: Reference to the parent OpenGrowBox instance
        """
        self.ogb = ogb
        self.action_manager: OGBActionManager = ogb.actionManager

        # Control logic for ambient-enhanced calculations
        self.control_logic = ClosedControlLogic(ogb.dataStore, ogb.room)

        # CO2 control parameters - load from datastore or use defaults
        self.co2_target_min = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.minPPM", 800)  # ppm - minimum for photosynthesis
        self.co2_target_max = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.maxPPM", 1500)  # ppm - maximum for efficiency
        self.co2_emergency_high = 2000  # ppm - ventilation trigger

        # O2 safety parameters
        self.o2_emergency_low = 19.0  # % - ventilation trigger
        self.o2_warning_low = 20.0  # % - warning threshold

        # Humidity control parameters (closed environment specific)
        self.humidity_buffer = 2.0  # %RH buffer for stability

        # Ambient-enhanced control parameters
        self.temp_tolerance = 1.0  # °C tolerance for temperature control
        self.humidity_tolerance = 3.0  # %RH tolerance for humidity control

    # =================================================================
    # CO2 Control Actions
    # =================================================================

    async def maintain_co2(self, capabilities: Dict[str, Any]):
        """
        Maintain optimal CO2 levels for photosynthesis in closed environment.

        Args:
            capabilities: Device capabilities and states
        """
        # Check CO2 control switch - skip if disabled
        co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
        if not co2_control_enabled:
            return

        current_co2 = self.ogb.dataStore.getDeep("tentData.co2Level")
        if current_co2 is None:
            _LOGGER.warning(f"CO2 sensor not available for {self.ogb.room}")
            return

        action_message = "CO2 Maintenance Action"

        # CO2 control logic for closed environment
        _LOGGER.warning(f"CO2 maintenance: current={current_co2}, min={self.co2_target_min}, max={self.co2_target_max}")
        if current_co2 < self.co2_target_min:
            # Increase CO2 - use CO2 injection if available
            _LOGGER.warning("CO2 below min, injecting")
            await self._inject_co2(capabilities, action_message)
        elif current_co2 > self.co2_target_max:
            # Decrease CO2 - use air recirculation or minimal ventilation
            _LOGGER.warning("CO2 above max, reducing")
            await self._reduce_co2(capabilities, action_message)

        # Emergency high CO2 - force ventilation
        if current_co2 > self.co2_emergency_high:
            await self._emergency_co2_ventilation(capabilities, action_message)

    async def _inject_co2(self, capabilities: Dict[str, Any], action_message: str):
        """Inject CO2 to increase levels."""
        action_map = []

        _LOGGER.warning(f"Inject CO2: canCO2 state={capabilities.get('canCO2', {}).get('state')}")
        if capabilities["canCO2"]["state"]:
            action_map.append(
                self._create_action("canCO2", "Increase", action_message)
            )

        if action_map:
            _LOGGER.warning("Calling action_manager.checkLimitsAndPublicate for CO2 increase")
            await self.action_manager.checkLimitsAndPublicate(action_map)

    async def _reduce_co2(self, capabilities: Dict[str, Any], action_message: str):
        """Reduce CO2 through air exchange or recirculation."""
        action_map = []

        # In closed environment, use minimal ventilation or enhanced recirculation
        if capabilities["canVentilate"]["state"]:
            action_map.append(
                self._create_action("canVentilate", "Minimal", action_message)
            )
        elif capabilities["canRecirculate"]["state"]:
            action_map.append(
                self._create_action("canRecirculate", "Increase", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    async def _emergency_co2_ventilation(self, capabilities: Dict[str, Any], action_message: str):
        """Emergency CO2 ventilation for dangerously high levels."""
        action_map = []

        if capabilities["canVentilate"]["state"]:
            action_map.append(
                self._create_action("canVentilate", "Emergency", action_message)
            )
        if capabilities["canExhaust"]["state"]:
            action_map.append(
                self._create_action("canExhaust", "Emergency", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    # =================================================================
    # O2 Safety Monitoring
    # =================================================================

    async def monitor_o2_safety(self, capabilities: Dict[str, Any]):
        """
        Monitor O2 levels and trigger emergency ventilation if too low.

        Args:
            capabilities: Device capabilities and states
        """
        # O2 monitoring not implemented - skip for now
        # current_o2 = self.ogb.dataStore.getDeep("tentData.o2Level")
        # if current_o2 is None:
        #     _LOGGER.warning(f"O2 sensor not available for {self.ogb.room}")
        #     return
        return

        action_message = "O2 Safety Action"

        # Emergency O2 low - immediate ventilation
        if current_o2 < self.o2_emergency_low:
            await self._emergency_o2_ventilation(capabilities, action_message)
        elif current_o2 < self.o2_warning_low:
            _LOGGER.warning(f"Low O2 warning in {self.ogb.room}: {current_o2}%")

    async def _emergency_o2_ventilation(self, capabilities: Dict[str, Any], action_message: str):
        """Emergency O2 ventilation for dangerously low levels."""
        action_map = []

        if capabilities["canVentilate"]["state"]:
            action_map.append(
                self._create_action("canVentilate", "Emergency", action_message)
            )
        if capabilities["canIntake"]["state"]:
            action_map.append(
                self._create_action("canIntake", "Emergency", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    # =================================================================
    # Humidity Control (Closed Environment)
    # =================================================================

    async def control_humidity_closed(self, capabilities: Dict[str, Any]):
        """
        Control humidity in closed environment using dehumidifiers/humidifiers.
        No ventilation-based humidity control.

        Args:
            capabilities: Device capabilities and states
        """
        current_humidity = self.ogb.dataStore.getDeep("tentData.humidity")
        target_humidity = self.ogb.dataStore.getDeep("targets.humidity")

        if current_humidity is None or target_humidity is None:
            return

        action_message = "Closed Environment Humidity Control"

        # Apply buffer for stability
        humidity_delta = current_humidity - target_humidity

        if abs(humidity_delta) > self.humidity_buffer:
            if humidity_delta > 0:
                # Too humid - dehumidify
                await self._dehumidify(capabilities, action_message)
            else:
                # Too dry - humidify
                await self._humidify(capabilities, action_message)

    async def _dehumidify(self, capabilities: Dict[str, Any], action_message: str):
        """Reduce humidity using dehumidifier."""
        action_map = []

        if capabilities["canDehumidify"]["state"]:
            action_map.append(
                self._create_action("canDehumidify", "Increase", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    async def _humidify(self, capabilities: Dict[str, Any], action_message: str):
        """Increase humidity using humidifier."""
        action_map = []

        if capabilities["canHumidify"]["state"]:
            action_map.append(
                self._create_action("canHumidify", "Increase", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    # =================================================================
    # Air Recirculation Control
    # =================================================================

    async def optimize_air_recirculation(self, capabilities: Dict[str, Any]):
        """
        Optimize air recirculation for CO2 distribution and thermal uniformity.
        Maintains air movement in closed environment.

        Args:
            capabilities: Device capabilities and states
        """
        action_message = "Air Recirculation Optimization"

        # Base recirculation for CO2 distribution
        action_map = []

        if capabilities["canRecirculate"]["state"]:
            action_map.append(
                self._create_action("canRecirculate", "Optimize", action_message)
            )

        # Additional recirculation based on temperature gradients
        temp_gradient = self._calculate_temp_gradient()
        if temp_gradient > 2.0:  # Significant temperature difference
            if capabilities["canRecirculate"]["state"]:
                action_map.append(
                    self._create_action("canRecirculate", "Increase", action_message)
                )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    def _calculate_temp_gradient(self) -> float:
        """
        Calculate temperature gradient across the environment.

        Returns:
            Maximum temperature difference in Celsius
        """
        # This would use multiple temperature sensors
        # For now, return a placeholder
        return 0.0

    # =================================================================
    # Ambient-Enhanced Temperature Control
    # =================================================================

    async def control_temperature_ambient_aware(self, capabilities: Dict[str, Any]):
        """
        Control temperature using ambient-enhanced targets.
        Similar to VPD perfection but optimized for closed environments.

        Args:
            capabilities: Device capabilities and states
        """
        # Calculate optimal target using ambient-enhanced logic
        target_temp = await self.control_logic.calculate_optimal_temperature_target()

        if target_temp is None:
            _LOGGER.warning(f"Temperature target calculation failed for {self.ogb.room}")
            return

        # Get current temperature
        current_temp = self.ogb.dataStore.getDeep("tentData.temperature")
        if current_temp is None:
            return

        # Calculate temperature delta
        temp_delta = target_temp - current_temp

        # Apply control with tolerance
        if abs(temp_delta) > self.temp_tolerance:
            action_message = f"Ambient-enhanced temperature control (target: {target_temp:.1f}°C)"

            if temp_delta > 0:
                # Need to increase temperature
                await self._increase_temperature(capabilities, action_message)
            else:
                # Need to decrease temperature
                await self._decrease_temperature(capabilities, action_message)

    async def _increase_temperature(self, capabilities: Dict[str, Any], action_message: str):
        """Increase temperature using available heating devices."""
        action_map = []

        if capabilities["canHeat"]["state"]:
            action_map.append(
                self._create_action("canHeat", "Increase", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    async def _decrease_temperature(self, capabilities: Dict[str, Any], action_message: str):
        """Decrease temperature using available cooling devices."""
        action_map = []

        if capabilities["canCool"]["state"]:
            action_map.append(
                self._create_action("canCool", "Increase", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    # =================================================================
    # Ambient-Enhanced Humidity Control
    # =================================================================

    async def control_humidity_ambient_aware(self, capabilities: Dict[str, Any]):
        """
        Control humidity using ambient-enhanced targets.
        Optimized for closed environments with ambient awareness.

        Args:
            capabilities: Device capabilities and states
        """
        # Calculate optimal target using ambient-enhanced logic
        target_humidity = await self.control_logic.calculate_optimal_humidity_target()

        if target_humidity is None:
            _LOGGER.warning(f"Humidity target calculation failed for {self.ogb.room}")
            return

        # Get current humidity
        current_humidity = self.ogb.dataStore.getDeep("tentData.humidity")
        if current_humidity is None:
            return

        # Calculate humidity delta
        humidity_delta = target_humidity - current_humidity

        # Apply control with tolerance
        if abs(humidity_delta) > self.humidity_tolerance:
            action_message = f"Ambient-enhanced humidity control (target: {target_humidity:.1f}%)"

            if humidity_delta > 0:
                # Need to increase humidity
                await self._increase_humidity(capabilities, action_message)
            else:
                # Need to decrease humidity
                await self._decrease_humidity(capabilities, action_message)

    async def _increase_humidity(self, capabilities: Dict[str, Any], action_message: str):
        """Increase humidity using available humidification devices."""
        action_map = []

        if capabilities["canHumidify"]["state"]:
            action_map.append(
                self._create_action("canHumidify", "Increase", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    async def _decrease_humidity(self, capabilities: Dict[str, Any], action_message: str):
        """Decrease humidity using available dehumidification devices."""
        action_map = []

        if capabilities["canDehumidify"]["state"]:
            action_map.append(
                self._create_action("canDehumidify", "Increase", action_message)
            )

        if action_map:
            await self.action_manager.checkLimitsAndPublicate(action_map)

    # =================================================================
    # Integrated Ambient-Enhanced Closed Environment Control
    # =================================================================

    async def execute_closed_environment_cycle(self, capabilities: Dict[str, Any]):
        """
        Execute complete closed environment control cycle with ambient enhancement.
        Coordinates all closed-loop control actions with ambient awareness.

        Args:
            capabilities: Device capabilities and states
        """
        # Priority order: Safety first, then ambient-enhanced environmental control
        await self.monitor_o2_safety(capabilities)
        await self.maintain_co2(capabilities)
        await self.control_temperature_ambient_aware(capabilities)
        await self.control_humidity_ambient_aware(capabilities)
        await self.optimize_air_recirculation(capabilities)

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

    def get_closed_environment_status(self) -> Dict[str, Any]:
        """
        Get closed environment control status.

        Returns:
            Dictionary with closed environment status
        """
        return {
            "room": self.ogb.room,
            "co2_current": self.ogb.dataStore.getDeep("tentData.co2Level"),
            "co2_target_min": self.co2_target_min,
            "co2_target_max": self.co2_target_max,
            "o2_current": None,  # O2 monitoring not implemented
            "o2_emergency_threshold": self.o2_emergency_low,
            "humidity_current": self.ogb.dataStore.getDeep("tentData.humidity"),
            "humidity_target": self.ogb.dataStore.getDeep("targets.humidity"),
            "recirculation_active": self.ogb.dataStore.getDeep("devices.recirculation.state"),
        }