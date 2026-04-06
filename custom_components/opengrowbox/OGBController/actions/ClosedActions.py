"""
OpenGrowBox Closed Environment Actions Module

Handles closed-loop environmental control actions for sealed grow chambers.
Manages CO2, O2, humidity, and air recirculation without traditional ventilation.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

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
        self.recirculation_humidity_band = 5.0  # %RH: use air mixing before active humidity control
        self.humidify_temp_guard = 0.7  # °C: avoid humidifying when heating is likely needed
        self.cooling_humidity_guard = 4.0  # %RH: avoid humidifying when already close to upper humidity band
        self.air_mixing_temp_trigger = 1.2  # °C: only mix air when gradient is meaningful
        self.air_mixing_humidity_trigger = 3.5  # %RH: avoid unnecessary mixing near target
        self._o2_warning_logged = False

    # =================================================================
    # CO2 Control Actions
    # =================================================================

    async def maintain_co2(self, capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Maintain optimal CO2 levels for photosynthesis in closed environment.

        Args:
            capabilities: Device capabilities and states

        Returns:
            List of action maps for CO2 control
        """
        # Check CO2 control switch - skip if disabled
        co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
        if not co2_control_enabled:
            return []

        # Load CO2 thresholds fresh from datastore (user may have changed them)
        co2_target_min = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.minPPM", 800)
        co2_target_max = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.maxPPM", 1500)

        current_co2 = self.ogb.dataStore.getDeep("tentData.co2Level")
        if current_co2 is None:
            _LOGGER.warning(f"CO2 sensor not available for {self.ogb.room}")
            return []

        # Check if lights are on - don't inject CO2 at night (like VPD Perfection)
        is_light_on = bool(self.ogb.dataStore.getDeep("isPlantDay.islightON", False))

        action_message = "Closed Environment CO2 Maintenance"

        # CO2 control logic for closed environment
        _LOGGER.warning(f"Closed Environment CO2 maintenance: current={current_co2}, min={co2_target_min}, max={co2_target_max}, light_on={is_light_on}")

        # Emergency high CO2 overrides normal reduction to avoid duplicate actions
        if current_co2 > self.co2_emergency_high:
            _LOGGER.warning(f"CO2 emergency high: {current_co2} > {self.co2_emergency_high}, forcing ventilation")
            return await self._emergency_co2_ventilation(capabilities, action_message)

        # Only inject CO2 when lights are ON (photosynthesis active)
        elif current_co2 < co2_target_min:
            if is_light_on:
                _LOGGER.warning("CO2 below min, lights ON - injecting CO2")
                return await self._inject_co2(capabilities, action_message)
            else:
                _LOGGER.warning("CO2 below min but lights OFF - skipping CO2 injection (like VPD)")
                return []

        # Always reduce CO2 if too high, even at night (safety first)
        elif current_co2 > co2_target_max:
            _LOGGER.warning("CO2 above max - reducing")
            return await self._reduce_co2(capabilities, action_message)

        return []

    async def _inject_co2(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Inject CO2 to increase levels."""
        action_map = []

        _LOGGER.warning(f"Inject CO2: canCO2 state={capabilities.get('canCO2', {}).get('state')}")
        if capabilities.get("canCO2", {}).get("state", False):
            action_map.append(
                self._create_action("canCO2", "Increase", action_message)
            )

        return action_map

    async def _reduce_co2(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Reduce CO2 through air exchange or recirculation."""
        action_map = []

        # Prefer internal air movement first. If that is not enough, use exhaust.
        if self._can_control_air_movement(capabilities):
            action_map.append(
                self._create_action("canVentilate", "Increase", action_message)
            )
        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(
                self._create_action("canExhaust", "Increase", action_message)
            )

        return action_map

    async def _emergency_co2_ventilation(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Emergency CO2 ventilation for dangerously high levels."""
        action_map = []

        # Emergency = Maximum ventilation to quickly reduce CO2
        # Use Increase since we want maximum air exchange
        if capabilities.get("canVentilate", {}).get("state", False):
            action_map.append(
                self._create_action("canVentilate", "Increase", action_message)
            )
        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(
                self._create_action("canExhaust", "Increase", action_message)
            )
        if capabilities.get("canIntake", {}).get("state", False):
            action_map.append(
                self._create_action("canIntake", "Increase", action_message)
            )

        return action_map

    # =================================================================
    # O2 Safety Monitoring
    # =================================================================

    async def monitor_o2_safety(self, capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Monitor O2 levels and trigger emergency ventilation if too low.

        Args:
            capabilities: Device capabilities and states

        Returns:
            List of action maps for O2 safety
        """
        current_o2 = self.ogb.dataStore.getDeep("tentData.o2Level")
        if current_o2 is None:
            if not self._o2_warning_logged:
                _LOGGER.info(
                    f"{self.ogb.room}: O2 safety monitor inactive - no O2 sensor available in Closed Environment"
                )
                self._o2_warning_logged = True
            return []

        try:
            current_o2 = float(current_o2)
        except (TypeError, ValueError):
            _LOGGER.warning(f"{self.ogb.room}: Invalid O2 reading for Closed Environment: {current_o2}")
            return []

        self._o2_warning_logged = False
        action_message = "Closed Environment O2 Safety"

        if current_o2 < self.o2_emergency_low:
            _LOGGER.warning(f"{self.ogb.room}: Critical low O2 detected: {current_o2}%")
            return await self._emergency_o2_ventilation(capabilities, action_message)
        elif current_o2 < self.o2_warning_low:
            _LOGGER.warning(f"{self.ogb.room}: Low O2 warning: {current_o2}%")

        return []

    async def _emergency_o2_ventilation(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Emergency O2 ventilation for dangerously low levels."""
        action_map = []

        if capabilities.get("canVentilate", {}).get("state", False):
            action_map.append(
                self._create_action("canVentilate", "Increase", action_message)
            )
        if capabilities.get("canIntake", {}).get("state", False):
            action_map.append(
                self._create_action("canIntake", "Increase", action_message)
            )
        if capabilities.get("canExhaust", {}).get("state", False):
            action_map.append(
                self._create_action("canExhaust", "Increase", action_message)
            )

        return action_map

    async def _dehumidify(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Reduce humidity using dehumidifier."""
        action_map = []

        if capabilities.get("canDehumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canDehumidify", "Increase", action_message)
            )
        elif self._can_control_air_movement(capabilities):
            action_map.append(
                self._create_action("canVentilate", "Increase", f"{action_message}: air mixing fallback")
            )

        return action_map

    async def _humidify(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Increase humidity using humidifier."""
        action_map = []

        current_temp = self.ogb.dataStore.getDeep("tentData.temperature")
        target_temp = await self._get_reference_temperature_target()
        current_humidity = self.ogb.dataStore.getDeep("tentData.humidity")
        target_humidity = await self._get_reference_humidity_target()

        if (
            current_temp is not None
            and target_temp is not None
            and float(current_temp) < float(target_temp) - self.humidify_temp_guard
        ):
            _LOGGER.info(
                f"{self.ogb.room}: Skipping humidify because temperature is below target "
                f"({current_temp} < {target_temp} - {self.humidify_temp_guard})"
            )
            return await self._stabilize_with_air_movement(
                capabilities,
                f"{action_message}: skipped humidify due to low temperature",
            )

        if (
            current_humidity is not None
            and target_humidity is not None
            and float(current_humidity) >= float(target_humidity) - self.cooling_humidity_guard
        ):
            _LOGGER.info(
                f"{self.ogb.room}: Skipping humidify because humidity is already close to target "
                f"({current_humidity} vs {target_humidity})"
            )
            return await self._stabilize_with_air_movement(
                capabilities,
                f"{action_message}: skipped humidify near target",
            )

        if capabilities.get("canHumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canHumidify", "Increase", action_message)
            )
            if self._can_control_air_movement(capabilities):
                action_map.append(
                    self._create_action("canVentilate", "Increase", f"{action_message}: distribute humidity")
                )
        elif self._can_control_air_movement(capabilities):
            action_map.append(
                self._create_action("canVentilate", "Increase", f"{action_message}: air mixing fallback")
            )

        return action_map

    # =================================================================
    # Air Recirculation Control
    # =================================================================

    async def optimize_air_recirculation(self, capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Optimize air recirculation for CO2 distribution and thermal uniformity.
        Maintains air movement in closed environment.

        Args:
            capabilities: Device capabilities and states

        Returns:
            List of action maps for air recirculation
        """
        action_message = "Air Recirculation Optimization"

        temp_gradient = self._calculate_temp_gradient()
        humidity_delta = self._calculate_humidity_delta_to_target()

        if not self._can_control_air_movement(capabilities):
            _LOGGER.debug(f"{self.ogb.room}: Closed air recirculation skipped - no air movement capability")
            return []

        if (
            temp_gradient >= self.air_mixing_temp_trigger
            or humidity_delta >= self.air_mixing_humidity_trigger
            or self._should_distribute_co2()
        ):
            _LOGGER.debug(
                f"{self.ogb.room}: Closed air mixing triggered temp_gradient={temp_gradient:.2f} "
                f"humidity_delta={humidity_delta:.2f} co2_distribution={self._should_distribute_co2()}"
            )
            return await self._stabilize_with_air_movement(capabilities, action_message)
        else:
            _LOGGER.debug(
                f"{self.ogb.room}: Closed air mixing not needed temp_gradient={temp_gradient:.2f} "
                f"humidity_delta={humidity_delta:.2f}"
            )

        return []

    def _calculate_temp_gradient(self) -> float:
        """
        Calculate temperature gradient across the environment.

        Returns:
            Maximum temperature difference in Celsius
        """
        canopy_temp = self.ogb.dataStore.getDeep("tentData.temperature")
        ambient_temp = self.ogb.dataStore.getDeep("tentData.AmbientTemp")

        if canopy_temp is None or ambient_temp is None:
            return 0.0

        try:
            return abs(float(canopy_temp) - float(ambient_temp))
        except (TypeError, ValueError):
            return 0.0

    # =================================================================
    # Temperature Control (VPD-style: control when outside min/max)
    # =================================================================

    async def control_temperature_closed(self, capabilities: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
        """
        Control temperature like VPD: control when outside min/max bounds.

        Args:
            capabilities: Device capabilities and states

        Returns:
            Tuple of (action_map, temp_status) for temperature control
        """
        temp_dev = self.control_logic.calculate_temperature_deviation()

        if temp_dev.get("status") == "no_data":
            _LOGGER.debug(f"{self.ogb.room}: Closed temperature control skipped - no data")
            return [], "no_data"

        if temp_dev.get("status") == "invalid":
            _LOGGER.warning(f"{self.ogb.room}: Closed temperature control skipped - invalid data")
            return [], "invalid"

        current = temp_dev.get("current")
        min_temp = temp_dev.get("min")
        max_temp = temp_dev.get("max")
        status = temp_dev.get("status")

        _LOGGER.debug(
            f"{self.ogb.room}: Closed temp control: {current:.1f}°C (min={min_temp:.1f}, max={max_temp:.1f}, status={status})"
        )

        if status == "too_low":
            action_message = f"Closed temp: too cold ({current:.1f}°C < {min_temp:.1f}°C)"
            actions = await self._increase_temperature(capabilities, action_message)
            return actions, "heizen"
        elif status == "too_high":
            action_message = f"Closed temp: too hot ({current:.1f}°C > {max_temp:.1f}°C)"
            actions = await self._decrease_temperature(capabilities, action_message)
            return actions, "kuehlen"
        else:
            _LOGGER.debug(f"{self.ogb.room}: Closed temp in range, no action")
            return [], "stabil"

    async def _increase_temperature(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Increase temperature using available heating devices."""
        action_map = []

        if capabilities.get("canHeat", {}).get("state", False):
            action_map.append(
                self._create_action("canHeat", "Increase", action_message)
            )
        elif capabilities.get("canClimate", {}).get("state", False):
            action_map.append(
                self._create_action("canClimate", "Increase", action_message)
            )

        return action_map

    async def _decrease_temperature(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Decrease temperature using available cooling devices."""
        action_map = []

        if capabilities.get("canCool", {}).get("state", False):
            action_map.append(
                self._create_action("canCool", "Increase", action_message)
            )
        elif capabilities.get("canClimate", {}).get("state", False):
            action_map.append(
                self._create_action("canClimate", "Reduce", action_message)
            )
        elif self._can_control_air_movement(capabilities):
            action_map.append(
                self._create_action("canVentilate", "Increase", f"{action_message}: air mixing fallback")
            )

        return action_map

    # =================================================================
    # Humidity Control (VPD-style: control when outside min/max)
    # =================================================================

    async def control_humidity_closed(self, capabilities: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
        """
        Control humidity like VPD: control when outside min/max bounds.
        For closed environments, humidity rises continuously so we act when too high.

        Args:
            capabilities: Device capabilities and states

        Returns:
            Tuple of (action_map, hum_status) for humidity control
        """
        hum_dev = self.control_logic.calculate_humidity_deviation()

        if hum_dev.get("status") == "no_data":
            _LOGGER.debug(f"{self.ogb.room}: Closed humidity control skipped - no data")
            return [], "no_data"

        if hum_dev.get("status") == "invalid":
            _LOGGER.warning(f"{self.ogb.room}: Closed humidity control skipped - invalid data")
            return [], "invalid"

        current = hum_dev.get("current")
        min_hum = hum_dev.get("min")
        max_hum = hum_dev.get("max")
        status = hum_dev.get("status")

        _LOGGER.debug(
            f"{self.ogb.room}: Closed humidity control: {current:.1f}% (min={min_hum:.1f}, max={max_hum:.1f}, status={status})"
        )

        if status == "too_low":
            action_message = f"Closed humidity: too dry ({current:.1f}% < {min_hum:.1f}%)"
            actions = await self._increase_humidity(capabilities, action_message)
            return actions, "befeuchten"
        elif status == "too_high":
            action_message = f"Closed humidity: too humid ({current:.1f}% > {max_hum:.1f}%)"
            actions = await self._decrease_humidity(capabilities, action_message)
            return actions, "entfeuchten"
        else:
            _LOGGER.debug(f"{self.ogb.room}: Closed humidity in range, no action")
            return [], "stabil"

    async def _increase_humidity(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Increase humidity using available humidification devices."""
        action_map = []

        if capabilities.get("canHumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canHumidify", "Increase", action_message)
            )
            if self._can_control_air_movement(capabilities):
                action_map.append(
                    self._create_action("canVentilate", "Increase", f"{action_message}: distribute humidity")
                )

        return action_map

    async def _decrease_humidity(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Decrease humidity using available dehumidification devices."""
        action_map = []

        if capabilities.get("canDehumidify", {}).get("state", False):
            action_map.append(
                self._create_action("canDehumidify", "Increase", action_message)
            )
        elif capabilities.get("canClimate", {}).get("state", False):
            action_map.append(
                self._create_action("canClimate", "Increase", action_message)
            )
        elif self._can_control_air_movement(capabilities):
            action_map.append(
                self._create_action("canVentilate", "Increase", f"{action_message}: air mixing fallback")
            )

        return action_map

    # =================================================================
    # Integrated Ambient-Enhanced Closed Environment Control
    # =================================================================

    async def _check_closed_deadbands(self) -> Dict[str, any]:
        """
        Check separate deadbands for Closed Environment (Temp and Humidity).
        
        Returns:
            Dict with temp_in_deadband, hum_in_deadband, and deviations
        """
        current_temp = self.ogb.dataStore.getDeep("tentData.temperature")
        target_temp = await self._get_reference_temperature_target()
        temp_deadband = self.ogb.dataStore.getDeep(
            "controlOptionData.deadband.closedTempDeadband", 0.5
        )
        
        current_hum = self.ogb.dataStore.getDeep("tentData.humidity")
        target_hum = await self._get_reference_humidity_target()
        hum_deadband = self.ogb.dataStore.getDeep(
            "controlOptionData.deadband.closedHumidDeadband", 1.5
        )
        
        temp_in_db = False
        hum_in_db = False
        temp_dev = None
        hum_dev = None
        
        if current_temp is not None and target_temp is not None:
            temp_dev = abs(float(current_temp) - float(target_temp))
            temp_in_db = temp_dev <= temp_deadband
            
        if current_hum is not None and target_hum is not None:
            hum_dev = abs(float(current_hum) - float(target_hum))
            hum_in_db = hum_dev <= hum_deadband
            
        return {
            "temp_in_deadband": temp_in_db,
            "hum_in_deadband": hum_in_db,
            "temp_deviation": temp_dev,
            "hum_deviation": hum_dev,
            "temp_target": target_temp,
            "hum_target": target_hum,
        }

    async def execute_closed_environment_cycle(self, capabilities: Dict[str, Any]):
        """
        Execute complete closed environment control cycle with ambient enhancement.
        Coordinates all closed-loop control actions with ambient awareness.
        Collects all actions and sends them in ONE LogForClient event.

        Args:
            capabilities: Device capabilities and states
        """
        _LOGGER.debug(
            f"{self.ogb.room}: ClosedActions cycle start - using VPD-style control"
        )

        # 1. Check Smart Deadband (VPD-based - primary trigger)
        smart_deadband_active = self.ogb.dataStore.getDeep("controlOptionData.deadband.active") or False
        
        # 2. Check Closed Environment specific deadbands (Temp and Humidity)
        closed_db_status = await self._check_closed_deadbands()
        temp_in_db = closed_db_status["temp_in_deadband"]
        hum_in_db = closed_db_status["hum_in_deadband"]
        temp_dev = closed_db_status["temp_deviation"]
        hum_dev = closed_db_status["hum_deviation"]
        temp_target = closed_db_status["temp_target"]
        hum_target = closed_db_status["hum_target"]
        
        # Collect all actions
        all_actions = []
        
        # O2 Safety (always - emergency)
        o2_actions = await self.monitor_o2_safety(capabilities)
        all_actions.extend(o2_actions)
        
        # CO2 Control (always - important for Closed Environment)
        co2_actions = await self.maintain_co2(capabilities)
        all_actions.extend(co2_actions)
        
        # Temperature Control (only if NOT in temp deadband AND smart deadband not active)
        temp_status = "stabil"
        if not temp_in_db and not smart_deadband_active:
            temp_actions, temp_status = await self.control_temperature_closed(capabilities)
            all_actions.extend(temp_actions)
        elif temp_in_db:
            temp_status = f"stabil (deadband ±{temp_dev:.1f}°C)"
            _LOGGER.debug(f"{self.ogb.room}: Temperature in deadband ({temp_dev:.1f}°C deviation) - skipping temp actions")
        elif smart_deadband_active:
            temp_status = "paused (VPD deadband)"
            _LOGGER.debug(f"{self.ogb.room}: Smart Deadband active - skipping temp actions")
        
        # Humidity Control (only if NOT in humidity deadband AND smart deadband not active)
        hum_status = "stabil"
        if not hum_in_db and not smart_deadband_active:
            hum_actions, hum_status = await self.control_humidity_closed(capabilities)
            all_actions.extend(hum_actions)
        elif hum_in_db:
            hum_status = f"stabil (deadband ±{hum_dev:.1f}%)"
            _LOGGER.debug(f"{self.ogb.room}: Humidity in deadband ({hum_dev:.1f}% deviation) - skipping humidity actions")
        elif smart_deadband_active:
            hum_status = "paused (VPD deadband)"
            _LOGGER.debug(f"{self.ogb.room}: Smart Deadband active - skipping humidity actions")
        
        # Air Recirculation (only if neither temp nor humidity in deadband AND smart deadband not active)
        if not temp_in_db and not hum_in_db and not smart_deadband_active:
            air_actions = await self.optimize_air_recirculation(capabilities)
            all_actions.extend(air_actions)
        else:
            _LOGGER.debug(f"{self.ogb.room}: Skipping air recirculation (deadband active or smart deadband)")

        # Execute all collected actions at once
        if all_actions:
            _LOGGER.info(f"{self.ogb.room}: Executing {len(all_actions)} closed environment actions")
            await self.action_manager.checkLimitsAndPublicateNoVPD(all_actions)

        # Emit consolidated log event (like VPD Perfection)
        await self._emit_closed_environment_log(
            capabilities, all_actions, temp_status, hum_status, 
            temp_dev, hum_dev, temp_target, hum_target, smart_deadband_active
        )

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
            "co2_target_min": self.ogb.dataStore.getDeep("controlOptionData.co2ppm.minPPM", 800),
            "co2_target_max": self.ogb.dataStore.getDeep("controlOptionData.co2ppm.maxPPM", 1500),
            "temperature_target": self._get_cached_reference_temperature_target(),
            "o2_current": self.ogb.dataStore.getDeep("tentData.o2Level"),
            "o2_emergency_threshold": self.o2_emergency_low,
            "humidity_current": self.ogb.dataStore.getDeep("tentData.humidity"),
            "humidity_target": self._get_cached_reference_humidity_target(),
            "air_movement_available": self.ogb.dataStore.getDeep("capabilities.canVentilate.state", False),
        }

    def _can_control_air_movement(self, capabilities: Dict[str, Any]) -> bool:
        """Return True if the room has a supported air-movement device."""
        return capabilities.get("canVentilate", {}).get("state", False)

    async def _stabilize_with_air_movement(self, capabilities: Dict[str, Any], action_message: str) -> List[Dict[str, Any]]:
        """Use supported air movement to mix the room before stronger actions."""
        if not self._can_control_air_movement(capabilities) or not self.action_manager:
            return []

        return [self._create_action("canVentilate", "Increase", action_message)]

    def _calculate_humidity_delta_to_target(self) -> float:
        """Return absolute humidity deviation from target."""
        current_humidity = self.ogb.dataStore.getDeep("tentData.humidity")
        target_humidity = self._get_cached_reference_humidity_target()

        if target_humidity is None:
            plant_stage = self.ogb.dataStore.get("plantStage")
            if plant_stage:
                stage_data = self.ogb.dataStore.getDeep(f"plantStages.{plant_stage}") or {}
                min_humidity = stage_data.get("minHumidity")
                max_humidity = stage_data.get("maxHumidity")
                if min_humidity is not None and max_humidity is not None:
                    try:
                        min_humidity_value = float(min_humidity)
                        max_humidity_value = float(max_humidity)
                    except (TypeError, ValueError):
                        min_humidity_value = None
                        max_humidity_value = None

                    if min_humidity_value is not None and max_humidity_value is not None:
                        target_humidity = (min_humidity_value + max_humidity_value) / 2

        if current_humidity is None or target_humidity is None:
            return 0.0

        try:
            return abs(float(current_humidity) - float(target_humidity))
        except (TypeError, ValueError):
            return 0.0

    def _should_distribute_co2(self) -> bool:
        """Return True if recent CO2 control likely benefits from air mixing."""
        current_co2 = self.ogb.dataStore.getDeep("tentData.co2Level")
        min_co2_raw = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.minPPM", 800)
        max_co2_raw = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.maxPPM", 1500)

        if current_co2 is None:
            return False

        try:
            current_co2 = float(current_co2)
            min_co2 = float(800 if min_co2_raw is None else min_co2_raw)
            max_co2 = float(1500 if max_co2_raw is None else max_co2_raw)
        except (TypeError, ValueError):
            return False

        return current_co2 < min_co2 or current_co2 > max_co2

    async def _get_reference_temperature_target(self) -> Optional[float]:
        """Return the best available closed-mode temperature target."""
        return await self.control_logic.calculate_optimal_temperature_target()

    async def _get_reference_humidity_target(self) -> Optional[float]:
        """Return the best available closed-mode humidity target."""
        return await self.control_logic.calculate_optimal_humidity_target()

    def _get_cached_reference_temperature_target(self) -> Optional[float]:
        """Return best-effort temperature target from active room limits."""
        min_temp = self.ogb.dataStore.getDeep("tentData.minTemp")
        max_temp = self.ogb.dataStore.getDeep("tentData.maxTemp")
        if min_temp is None or max_temp is None:
            return None

        try:
            return (float(min_temp) + float(max_temp)) / 2
        except (TypeError, ValueError):
            return None

    def _get_cached_reference_humidity_target(self) -> Optional[float]:
        """Return best-effort humidity target from active room limits."""
        min_humidity = self.ogb.dataStore.getDeep("tentData.minHumidity")
        max_humidity = self.ogb.dataStore.getDeep("tentData.maxHumidity")
        if min_humidity is None or max_humidity is None:
            return None

        try:
            return (float(min_humidity) + float(max_humidity)) / 2
        except (TypeError, ValueError):
            return None

    async def _emit_closed_environment_log(
        self,
        capabilities: Dict[str, Any],
        all_actions: List[Dict[str, Any]],
        temp_status: str = "stabil",
        hum_status: str = "stabil",
        temp_dev: Optional[float] = None,
        hum_dev: Optional[float] = None,
        temp_target: Optional[float] = None,
        hum_target: Optional[float] = None,
        smart_deadband_active: bool = False
    ):
        """
        Emit a consolidated Closed Environment summary for the client log.
        Format consistent with VPD Perfection/VPD Target.
        """
        temp_now = self.ogb.dataStore.getDeep("tentData.temperature")
        humidity_now = self.ogb.dataStore.getDeep("tentData.humidity")
        co2_now = self.ogb.dataStore.getDeep("tentData.co2Level")
        
        # Use provided targets or fetch from datastore
        if temp_target is None:
            temp_target = await self._get_reference_temperature_target()
        if hum_target is None:
            hum_target = await self._get_reference_humidity_target()
            
        co2_min = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.minPPM", 800)
        co2_max = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.maxPPM", 1500)
        is_light_on = bool(self.ogb.dataStore.getDeep("isPlantDay.islightON", False))

        # Calculate deviations (use provided or calculate)
        if temp_dev is None:
            temp_deviation = 0.0
            if temp_now is not None and temp_target is not None:
                try:
                    temp_deviation = round(float(temp_now) - float(temp_target), 1)
                except (TypeError, ValueError):
                    pass
        else:
            temp_deviation = temp_dev
            
        if hum_dev is None:
            hum_deviation = 0.0
            if humidity_now is not None and hum_target is not None:
                try:
                    hum_deviation = round(float(humidity_now) - float(hum_target), 1)
                except (TypeError, ValueError):
                    pass
        else:
            hum_deviation = hum_dev

        # Determine CO2 status
        co2_status = self._determine_co2_status(co2_now, co2_min, co2_max, is_light_on)

        # Build actions string (like VPD Perfection format: "canExhaust:Increase, canCool:Reduce")
        actions_str = self._build_actions_string(all_actions)

        # Build message based on deadband status
        if smart_deadband_active:
            message = f"Closed Environment: Smart Deadband active - {len(all_actions)} actions (CO2/O2 only)"
        elif "deadband" in temp_status.lower() or "deadband" in hum_status.lower():
            message = f"Closed Environment: {len(all_actions)} actions (some paused by deadband)"
        else:
            message = f"Closed Environment: {len(all_actions)} actions executed"

        # Emit consolidated LogForClient event (like VPD Perfection)
        await self.ogb.eventManager.emit(
            "LogForClient",
            {
                "Name": self.ogb.room,
                "message": message,
                "actions": actions_str,
                "actionCount": len(all_actions),
                "tempDeviation": abs(temp_deviation),
                "humDeviation": abs(hum_deviation),
                "co2Status": co2_status,
                "tempStatus": temp_status,
                "humStatus": hum_status,
                "smartDeadbandActive": smart_deadband_active,
                # Additional context fields
                "tempCurrent": temp_now,
                "tempTarget": temp_target,
                "humCurrent": humidity_now,
                "humTarget": hum_target,
                "co2Current": co2_now,
                "co2TargetMin": co2_min,
                "co2TargetMax": co2_max,
            },
            haEvent=True,
            debug_type="INFO",
        )

    def _determine_co2_status(self, current_co2, min_co2, max_co2, is_light_on: bool) -> str:
        """Determine CO2 control status."""
        try:
            if current_co2 is None:
                return "no_sensor"
            current_value = float(current_co2)
            min_value = float(min_co2)
            max_value = float(max_co2)
        except (TypeError, ValueError):
            return "invalid"

        if current_value > self.co2_emergency_high:
            return "notfallentlastung"
        if current_value > max_value:
            return "senken"
        if current_value < min_value:
            return "anheben" if is_light_on else "nachtpause"
        return "stabil"

    def _build_actions_string(self, actions: List[Dict[str, Any]]) -> str:
        """Build actions string in format 'capability:action, capability:action'."""
        if not actions:
            return "none"

        action_parts = []
        for action in actions:
            capability = getattr(action, 'capability', None)
            action_type = getattr(action, 'action', None)
            if capability and action_type:
                action_parts.append(f"{capability}:{action_type}")

        return ", ".join(action_parts) if action_parts else "none"

    def _describe_temperature_decision(self, current_temp, target_temp, capabilities: Dict[str, Any]) -> str:
        """Deprecated: Use _determine_co2_status instead."""
        try:
            if current_temp is None or target_temp is None:
                return "Temp: kein Ziel" if target_temp is None else "Temp: kein Sensor"
            delta = float(target_temp) - float(current_temp)
        except (TypeError, ValueError):
            return "Temp: ungueltiger Wert"

        if abs(delta) <= self.temp_tolerance:
            return "Temp: stabil"
        if delta > 0:
            if capabilities.get("canHeat", {}).get("state", False):
                return "Temp: heizen"
            if capabilities.get("canClimate", {}).get("state", False):
                return "Temp: Klima heizt"
            return "Temp: Heizen nicht verfuegbar"
        if capabilities.get("canCool", {}).get("state", False):
            return "Temp: kuehlen"
        if capabilities.get("canClimate", {}).get("state", False):
            return "Temp: Klima kuehlt"
        if self._can_control_air_movement(capabilities):
            return "Temp: Luftmischung Fallback"
        return "Temp: Kuehlen nicht verfuegbar"

    def _describe_humidity_decision(self, current_humidity, target_humidity, capabilities: Dict[str, Any]) -> str:
        """Deprecated: Use temp_status/hum_status from control methods instead."""
        try:
            if current_humidity is None or target_humidity is None:
                return "rF: kein Ziel" if target_humidity is None else "rF: kein Sensor"
            delta = float(target_humidity) - float(current_humidity)
        except (TypeError, ValueError):
            return "rF: ungueltiger Wert"

        if abs(delta) <= self.humidity_tolerance:
            return "rF: stabil"
        if delta > 0:
            if capabilities.get("canHumidify", {}).get("state", False):
                return "rF: befeuchten"
            return "rF: Befeuchten nicht verfuegbar"
        if capabilities.get("canDehumidify", {}).get("state", False):
            return "rF: entfeuchten"
        if capabilities.get("canClimate", {}).get("state", False):
            return "rF: Klima trocknet"
        if self._can_control_air_movement(capabilities):
            return "rF: Luftmischung Fallback"
        return "rF: Entfeuchten nicht verfuegbar"

    def _describe_air_mixing_decision(self, capabilities: Dict[str, Any]) -> str:
        """Deprecated: Air mixing is now part of the consolidated log."""
        if not self._can_control_air_movement(capabilities):
            return "Luft: nicht verfuegbar"

        temp_gradient = self._calculate_temp_gradient()
        humidity_delta = self._calculate_humidity_delta_to_target()
        if (
            temp_gradient >= self.air_mixing_temp_trigger
            or humidity_delta >= self.air_mixing_humidity_trigger
            or self._should_distribute_co2()
        ):
            return "Luft: mischen"
        return "Luft: stabil"

    def _describe_o2_decision(self) -> str:
        """Deprecated: O2 status is now part of the consolidated log."""
        current_o2 = self.ogb.dataStore.getDeep("tentData.o2Level")
        if current_o2 is None:
            return "O2: Sensor fehlt"

        try:
            current_value = float(current_o2)
        except (TypeError, ValueError):
            return "O2: ungueltiger Wert"

        if current_value < self.o2_emergency_low:
            return "O2: Notfallentlastung"
        if current_value < self.o2_warning_low:
            return "O2: Warnung"
        return "O2: stabil"

    def _format_value(self, value, unit: str) -> str:
        """Format a value with unit for display."""
        if value is None:
            return "N/A"
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "N/A"
        if unit == "ppm":
            return f"{numeric_value:.0f}{unit}"
        return f"{numeric_value:.1f}{unit}"
