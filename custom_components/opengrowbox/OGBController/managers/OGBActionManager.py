"""
OpenGrowBox Base Action Manager

Core orchestration and coordination for all action management.
Handles event registration, cooldown management, dampening logic,
and emergency overrides. This is the main entry point for action processing.
"""

import asyncio
import copy
import dataclasses
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ..data.OGBDataClasses.OGBPublications import (OGBActionPublication,
                                              OGBHydroAction, OGBRetrieveAction,
                                              OGBWaterAction,
                                              OGBWeightPublication)
from ..data.OGBParams.OGBParams import DEFAULT_DEVICE_COOLDOWNS

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class OGBActionManager:
    """
    Base action manager for OpenGrowBox.

    Coordinates all action processing including:
    - Event registration and routing
    - Cooldown and dampening management
    - Emergency override handling
    - Action history tracking
    - Integration with specialized action modules
    """

    def __init__(self, hass, data_store, event_manager, room):
        """
        Initialize the base action manager.

        Args:
            hass: Home Assistant instance
            data_store: Data store instance
            event_manager: Event manager instance
            room: Room identifier
        """
        self.hass = hass
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.name = "OGB Action Manager"

        # Action state
        self.isInitialized = False
        self.actionHistory: Dict[str, Dict[str, Any]] = {}
        
        # Load cooldowns from datastore or use defaults
        self.defaultCooldownMinutes = self._load_cooldowns_from_datastore()

        # Lock for thread-safe action history updates
        self._action_history_lock = asyncio.Lock()
        self.adaptiveCooldownEnabled = False  # Disabled by default - user sets exact cooldowns
        self._emergency_mode = False

        # Initialize specialized action modules
        self.vpd_actions = None
        self.emergency_actions = None
        self.dampening_actions = None
        self.premium_actions = None
        self.closed_actions = None
        self.pump_controller = None

        # Register events
        self._register_events()

    def _register_events(self):
        """Register all action-related events."""
        # VPD events
        self.event_manager.on("increase_vpd", self._handle_increase_vpd)
        self.event_manager.on("reduce_vpd", self._handle_reduce_vpd)
        self.event_manager.on("FineTune_vpd", self._handle_fine_tune_vpd)
        self.event_manager.on("vpdt_increase_vpd", self._handle_vpdt_increase_vpd)
        self.event_manager.on("vpdt_reduce_vpd", self._handle_vpdt_reduce_vpd)
        self.event_manager.on("vpdt_finetune_vpd", self._handle_vpdt_finetune_vpd)

        # Premium events
        self.event_manager.on("PIDActions", self._handle_pid_actions)
        self.event_manager.on("MPCActions", self._handle_mpc_actions)
        self.event_manager.on("AIActions", self._handle_ai_actions)

        # Closed environment events
        self.event_manager.on("closed_environment_cycle", self._handle_closed_environment_cycle)
        self.event_manager.on("maintain_co2", self._handle_maintain_co2)
        self.event_manager.on("monitor_o2_safety", self._handle_monitor_o2_safety)
        self.event_manager.on("control_humidity_closed", self._handle_control_humidity_closed)
        self.event_manager.on("optimize_air_recirculation", self._handle_optimize_air_recirculation)

        # Water events
        self.event_manager.on("PumpAction", self._handle_pump_action)
        self.event_manager.on("RetrieveAction", self._handle_retrieve_action)

        # Device adjustment
        self.event_manager.on("AdjustDeviceGCD", self.adjustDeviceGCD)

    async def initialize_action_modules(self, ogb):
        """
        Initialize specialized action modules after other components are ready.

        Args:
            ogb: The OpenGrowBox instance
        """
        try:
            # Store reference to ogb instance
            self.ogb = ogb

            # Import and initialize specialized modules
            from ..actions import OGBDampeningActions, OGBEmergencyActions, OGBPremiumActions, OGBVPDActions
            from ..actions.ClosedActions import ClosedActions

            # Initialize pump controller
            from .hydro.tank.OGBPumpControlManager import OGBPumpControlManager
            from .hydro.tank.OGBFeedCalibrationManager import OGBFeedCalibrationManager

            calibration_manager = OGBFeedCalibrationManager(self.room, self.data_store, self.event_manager, self.hass)
            self.pump_controller = OGBPumpControlManager(
                self.room, self.data_store, self.event_manager, self.hass, calibration_manager
            )

            self.vpd_actions = OGBVPDActions(self.ogb)
            self.emergency_actions = OGBEmergencyActions(self.ogb)
            self.dampening_actions = OGBDampeningActions(self.ogb)
            self.premium_actions = OGBPremiumActions(self.ogb)
            self.closed_actions = ClosedActions(self.ogb)

            self.isInitialized = True
            _LOGGER.info(f"Action modules initialized for {self.room}")

        except Exception as e:
            _LOGGER.error(f"Error initializing action modules for {self.room}: {e}")
            self.isInitialized = False

    def _load_cooldowns_from_datastore(self) -> Dict[str, float]:
        """
        Load user-defined cooldowns from datastore, falling back to defaults.
        
        Returns:
            Dictionary of capability -> cooldown in minutes
        """
        # Start with default cooldowns
        cooldowns = DEFAULT_DEVICE_COOLDOWNS.copy()
        
        try:
            # Try to load user-defined cooldowns from datastore
            user_cooldowns = self.data_store.getDeep("controlOptions.deviceCooldowns")
            
            if user_cooldowns and isinstance(user_cooldowns, dict):
                # Update defaults with user values
                updated_count = 0
                for capability, minutes in user_cooldowns.items():
                    if capability in cooldowns:
                        cooldowns[capability] = float(minutes)
                        updated_count += 1
                    else:
                        _LOGGER.warning(
                            f"{self.room}: Unknown capability '{capability}' in user cooldowns, skipping"
                        )
                
                if updated_count > 0:
                    _LOGGER.info(
                        f"{self.room}: Loaded {updated_count} user-defined cooldown(s) from datastore: {user_cooldowns}"
                    )
            else:
                _LOGGER.debug(
                    f"{self.room}: No user cooldowns found in datastore (user_cooldowns={user_cooldowns})"
                )
        except Exception as e:
            _LOGGER.warning(
                f"{self.room}: Failed to load user cooldowns from datastore: {e}. Using defaults."
            )
        
        return cooldowns

    def _save_cooldowns_to_datastore(self):
        """
        Save current cooldowns to datastore for persistence.
        """
        try:
            # Save current cooldowns to datastore
            self.data_store.setDeep("controlOptions.deviceCooldowns", self.defaultCooldownMinutes)
            _LOGGER.info(
                f"{self.room}: Saved {len(self.defaultCooldownMinutes)} cooldown(s) to datastore"
            )
        except Exception as e:
            _LOGGER.error(
                f"{self.room}: Failed to save cooldowns to datastore: {e}"
            )

    # =================================================================
    # Core Action Logic
    # =================================================================

    def _isActionAllowed(
        self, capability: str, action: str, deviation: float = 0
    ) -> bool:
        """
        Check if an action is allowed based on cooldown rules.

        Args:
            capability: Device capability
            action: Action type
            deviation: Current deviation from target

        Returns:
            True if action is allowed
        """
        now = datetime.now()

        if capability not in self.actionHistory:
            return True

        history = self.actionHistory[capability]

        # Skip cooldown for emergency actions
        if self._emergency_mode:
            _LOGGER.warning(
                f"{self.room}: Emergency mode - bypassing cooldown for {capability}"
            )
            return True

        # Check if still in cooldown
        if now < history.get("cooldown_until", now):
            _LOGGER.debug(
                f"{self.room}: {capability} still in cooldown until {history['cooldown_until']}"
            )
            return False

        # Check if same action is being repeated too quickly
        if history.get("action_type") == action and now < history.get(
            "repeat_cooldown", now
        ):
            _LOGGER.debug(
                f"{self.room}: {capability} repeat of '{action}' still blocked"
            )
            return False

        return True

    def _calculateAdaptiveCooldown(self, capability: str, deviation: float) -> float:
        """
        Calculate cooldown time.

        Args:
            capability: Device capability
            deviation: Current deviation from target

        Returns:
            Cooldown time in minutes
        """
        # Get base cooldown (user-defined)
        baseCooldown = self.defaultCooldownMinutes.get(capability, 2)

        # Check if adaptive cooldown is enabled (default: False)
        adaptive_enabled = self.data_store.getDeep("controlOptions.adaptiveCooldownEnabled", False)
        if not adaptive_enabled:
            # User says x, user gets x!
            # Unless in emergency mode
            if self._emergency_mode:
                # In emergency: Reduce cooldown for faster response
                emergency_factor = self.data_store.getDeep("controlOptions.emergencyCooldownFactor", 0.5)
                return baseCooldown * emergency_factor
            return baseCooldown

        # Adaptive cooldown is enabled - load user-configurable thresholds and factors
        thresholds = self.data_store.getDeep("controlOptions.adaptiveCooldownThresholds", {
            "critical": 5.0, "high": 3.0, "near": 1.0, "veryNear": 0.5
        })
        factors = self.data_store.getDeep("controlOptions.adaptiveCooldownFactors", {
            "critical": 1.5, "high": 1.2, "near": 2.0, "veryNear": 3.0
        })

        abs_dev = abs(deviation)

        # Apply adaptive factors if user explicitly enabled this feature
        if abs_dev > thresholds["critical"]:
            return baseCooldown * factors["critical"]
        elif abs_dev > thresholds["high"]:
            return baseCooldown * factors["high"]
        elif abs_dev < thresholds["veryNear"]:
            return baseCooldown * factors["veryNear"]
        elif abs_dev < thresholds["near"]:
            return baseCooldown * factors["near"]

        return baseCooldown

    def _registerAction(self, capability: str, action: str, deviation: float = 0):
        """
        Register an action in the history system.

        Args:
            capability: Device capability
            action: Action type
            deviation: Current deviation from target
        """
        now = datetime.now()

        cooldownMinutes = self._calculateAdaptiveCooldown(capability, deviation)
        cooldownUntil = now + timedelta(minutes=cooldownMinutes)

        # Longer cooldown for repeating the same action
        repeatCooldown = now + timedelta(minutes=cooldownMinutes * 0.5)

        self.actionHistory[capability] = {
            "last_action": now,
            "action_type": action,
            "cooldown_until": cooldownUntil,
            "repeat_cooldown": repeatCooldown,
            "deviation": deviation,
        }

        _LOGGER.debug(
            f"{self.room}: {capability} '{action}' registered, cooldown until {cooldownUntil}"
        )

    def _filterActionsByDampening(
        self, actionMap, tempDeviation: float = 0, humDeviation: float = 0
    ):
        """
        Filter actions based on dampening rules.

        Args:
            actionMap: List of actions to filter
            tempDeviation: Temperature deviation
            humDeviation: Humidity deviation

        Returns:
            Tuple of (filtered_actions, blocked_actions)
        """
        # Resolve conflicting actions first
        actionMap = self._remove_conflicting_actions(actionMap)

        filteredActions = []
        blockedActions = []

        for action in actionMap:
            capability = action.capability
            actionType = action.action

            # Determine relevant deviation for this capability
            deviation = 0
            if capability in ["canHumidify", "canDehumidify"]:
                deviation = humDeviation
            elif capability in ["canHeat", "canCool", "canClimate"]:
                deviation = tempDeviation
            else:
                deviation = max(abs(tempDeviation), abs(humDeviation))

            if self._isActionAllowed(capability, actionType, deviation):
                filteredActions.append(action)
                self._registerAction(capability, actionType, deviation)
            else:
                blockedActions.append(action)

        if blockedActions:
            _LOGGER.info(
                f"{self.room}: {len(blockedActions)} actions blocked by dampening"
            )

        return filteredActions, blockedActions

    def _getEmergencyOverride(self, tentData: Dict[str, Any]) -> List[str]:
        """
        Check if emergency override of dampening is necessary.

        Args:
            tentData: Current tent data

        Returns:
            List of emergency conditions
        """
        emergencyConditions = []

        # Defensive parsing - skip emergency evaluation on incomplete/invalid data
        try:
            temperature = float(tentData.get("temperature"))
            max_temp = float(tentData.get("maxTemp"))
            min_temp = float(tentData.get("minTemp"))
        except (TypeError, ValueError, AttributeError):
            return emergencyConditions

        # Configurable buffers to avoid noisy emergency toggling near limits
        temp_emergency_buffer = float(
            self.data_store.getDeep("controlOptions.emergencyTempBuffer") or 0.5
        )
        humidity_emergency_threshold = float(
            self.data_store.getDeep("controlOptions.emergencyHumidityThreshold") or 90.0
        )
        condensation_emergency_buffer = float(
            self.data_store.getDeep("controlOptions.emergencyCondensationBuffer") or 0.2
        )

        # Emergency only when clearly outside safe zone, not merely at the limit
        if temperature >= (max_temp + temp_emergency_buffer):
            emergencyConditions.append("critical_overheat")
        if temperature <= (min_temp - temp_emergency_buffer):
            emergencyConditions.append("critical_cold")

        dewpoint = tentData.get("dewpoint")
        try:
            if dewpoint is not None and float(dewpoint) >= (temperature - condensation_emergency_buffer):
                emergencyConditions.append("immediate_condensation_risk")
        except (TypeError, ValueError):
            pass

        humidity = tentData.get("humidity")
        try:
            if humidity is not None and float(humidity) >= humidity_emergency_threshold:
                emergencyConditions.append("critical_humidity")
        except (TypeError, ValueError):
            pass

        return emergencyConditions

    def _clearCooldownForEmergency(self, emergencyConditions: List[str]):
        """
        Clear cooldowns during emergencies.

        Args:
            emergencyConditions: List of emergency conditions
        """
        if not emergencyConditions:
            return

        # Set emergency mode flag
        self._emergency_mode = True

        # Clear all cooldowns
        now = datetime.now()
        for capability in self.actionHistory:
            self.actionHistory[capability]["cooldown_until"] = now

        # Clear emergency mode after short delay to allow actions
        # Track the task to prevent orphaned tasks
        if not hasattr(self, '_background_tasks'):
            self._background_tasks = set()
        task = asyncio.create_task(self._clear_emergency_mode())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _clear_emergency_mode(self):
        """Clear emergency mode after delay."""
        await asyncio.sleep(5)  # 5 seconds
        self._emergency_mode = False
        _LOGGER.info(f"{self.room}: Emergency mode cleared")

    # =================================================================
    # Event Handlers
    # =================================================================

    async def adjustDeviceGCD(self, data):
        """
        Adjust device cooldown settings.

        Args:
            data: Adjustment data
        """
        _LOGGER.warning(f"{data}")
        cap = data.get("cap")
        minutes = data.get("minutes")

        if cap in self.defaultCooldownMinutes:
            self.defaultCooldownMinutes[cap] = minutes
            _LOGGER.warning(
                f"Cooldown for {cap} set to {minutes} minutes. GCDS: {self.defaultCooldownMinutes}"
            )
            # Save to datastore for persistence
            self._save_cooldowns_to_datastore()
        else:
            _LOGGER.error(f"Unknown capability: {cap}")

    async def _handle_increase_vpd(self, capabilities):
        """Handle VPD increase requests."""
        _LOGGER.debug(f"🔥 {self.room}: _handle_increase_vpd CALLED (vpd_actions={self.vpd_actions is not None})")
        if self.vpd_actions:
            await self.vpd_actions.increase_vpd(capabilities)
        else:
            _LOGGER.debug(f"🔥 {self.room}: vpd_actions NOT INITIALIZED - action skipped!")

    async def _handle_reduce_vpd(self, capabilities):
        """Handle VPD reduce requests."""
        _LOGGER.debug(f"🔥 {self.room}: _handle_reduce_vpd CALLED (vpd_actions={self.vpd_actions is not None})")
        if self.vpd_actions:
            await self.vpd_actions.reduce_vpd(capabilities)
        else:
            _LOGGER.debug(f"🔥 {self.room}: vpd_actions NOT INITIALIZED - action skipped!")

    async def _handle_fine_tune_vpd(self, capabilities):
        """Handle VPD fine-tune requests."""
        _LOGGER.debug(f"🔥 {self.room}: _handle_fine_tune_vpd CALLED (vpd_actions={self.vpd_actions is not None})")
        if self.vpd_actions:
            await self.vpd_actions.fine_tune_vpd(capabilities)
        else:
            _LOGGER.debug(f"🔥 {self.room}: vpd_actions NOT INITIALIZED - action skipped!")

    async def _handle_vpdt_increase_vpd(self, capabilities):
        """Handle VPD Target increase requests."""
        _LOGGER.debug(f"{self.room}: vpdt_increase_vpd CALLED")
        if self.vpd_actions:
            await self.vpd_actions.increase_vpd_target(capabilities)

    async def _handle_vpdt_reduce_vpd(self, capabilities):
        """Handle VPD Target reduce requests."""
        _LOGGER.debug(f"{self.room}: vpdt_reduce_vpd CALLED")
        if self.vpd_actions:
            await self.vpd_actions.reduce_vpd_target(capabilities)

    async def _handle_vpdt_finetune_vpd(self, capabilities):
        """Handle VPD Target fine-tune requests."""
        _LOGGER.debug(f"{self.room}: vpdt_finetune_vpd CALLED")
        if self.vpd_actions:
            await self.vpd_actions.fine_tune_vpd_target(capabilities)

    async def _handle_pid_actions(self, premActions):
        """Handle PID control actions."""
        if self.premium_actions:
            await self.premium_actions.PIDActions(premActions)

    async def _handle_mpc_actions(self, premActions):
        """Handle MPC control actions."""
        if self.premium_actions:
            await self.premium_actions.MPCActions(premActions)

    async def _handle_ai_actions(self, premActions):
        """Handle AI control actions."""
        if self.premium_actions:
            await self.premium_actions.AIActions(premActions)

    async def _handle_pump_action(self, data):
        """Handle pump actions - emit events directly like original code."""
        if isinstance(data, dict):
            dev = data.get("Device") or data.get("id") or "<unknown>"
            action = data.get("Action") or data.get("action")
            cycle = data.get("Cycle") or data.get("cycle")
        else:
            # Handle dataclass objects
            dev = getattr(data, 'Device', '<unknown>')
            action = getattr(data, 'Action', None)
            cycle = getattr(data, 'Cycle', None)

        # Emit pump control events directly (like original code)
        message = "Unknown Pump Action"
        if action == "on":
            message = "Start Pump"
            await self.event_manager.emit("Increase Pump", data)
        elif action == "off":
            message = "Stop Pump"
            await self.event_manager.emit("Reduce Pump", data)

        # Create water action for logging
        water_action = {"Name": self.room, "Device": dev, "Cycle": cycle, "Action": action, "Message": message}
        await self.event_manager.emit("LogForClient", water_action, haEvent=True, debug_type="INFO")

    async def _handle_retrieve_action(self, data):
        """Handle retrieve actions."""
        if isinstance(data, dict):
            dev = data.get("Device") or data.get("id") or "<unknown>"
            action = data.get("Action") or data.get("action")
            cycle = data.get("Cycle") or data.get("cycle")
        else:
            # Handle dataclass objects
            dev = getattr(data, 'Device', '<unknown>')
            action = getattr(data, 'Action', None)
            cycle = getattr(data, 'Cycle', None)

        # Retrieve pumps are also registered pump devices - emit events for Pump devices to handle
        if action in ["on", "off"]:
            await self.event_manager.emit("Increase Pump" if action == "on" else "Reduce Pump", data)

            # Create water action for logging
            message = "Start Retrieve Pump" if action == "on" else "Stop Retrieve Pump"
            water_action = {"Name": self.room, "Device": dev, "Cycle": cycle, "Action": action, "Message": message}
            await self.event_manager.emit("LogForClient", water_action, haEvent=True, debug_type="INFO")

    def _calculate_weighted_deviations(self, tent_data: Dict[str, Any]) -> Tuple[float, float, float, float, float, float, float, float, str]:
        """
        Calculate real and weighted temperature and humidity deviations.

        Args:
            tent_data: Current tent environmental data

        Returns:
            Tuple of (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
                     tempWeight, humWeight, tempPercentage, humPercentage, weightMessage)
            - real_temp_dev: Real absolute deviation from min/max (for display)
            - real_hum_dev: Real absolute deviation from min/max (for display)
            - weighted_temp_dev: Weighted deviation (for action prioritization)
            - weighted_hum_dev: Weighted deviation (for action prioritization)
            - tempWeight: Temperature weight factor
            - humWeight: Humidity weight factor
            - tempPercentage: Deviation as percentage of range (0-100%)
            - humPercentage: Deviation as percentage of range (0-100%)
            - weightMessage: Description of deviation status
        """
        # Get control settings
        ownWeights = self.data_store.getDeep("controlOptions.ownWeights")

        if ownWeights:
            tempWeight = self.data_store.getDeep("controlOptionData.weights.temp")
            humWeight = self.data_store.getDeep("controlOptionData.weights.hum")
        else:
            plantStage = self.data_store.get("plantStage")

            # Late flower stages need higher humidity priority
            if plantStage in ["LateFlower", "MidFlower"]:
                tempWeight = self.data_store.getDeep("controlOptionData.weights.defaultValue") * 1
                humWeight = self.data_store.getDeep("controlOptionData.weights.defaultValue") * 1.25
            else:
                tempWeight = self.data_store.getDeep("controlOptionData.weights.defaultValue")
                humWeight = self.data_store.getDeep("controlOptionData.weights.defaultValue")

        # 1. Calculate REAL deviations (for display - absolute, not weighted)
        real_temp_dev = 0.0
        real_hum_dev = 0.0
        weightMessage = ""

        if tent_data["temperature"] > tent_data["maxTemp"]:
            real_temp_dev = round(tent_data["temperature"] - tent_data["maxTemp"], 2)
            weightMessage = f"Temp Too High: +{real_temp_dev}°C"
        elif tent_data["temperature"] < tent_data["minTemp"]:
            real_temp_dev = round(tent_data["temperature"] - tent_data["minTemp"], 2)
            weightMessage = f"Temp Too Low: {real_temp_dev}°C"

        if tent_data["humidity"] > tent_data["maxHumidity"]:
            real_hum_dev = round(tent_data["humidity"] - tent_data["maxHumidity"], 2)
            if weightMessage:
                weightMessage += f", Humidity Too High: +{real_hum_dev}%"
            else:
                weightMessage = f"Humidity Too High: +{real_hum_dev}%"
        elif tent_data["humidity"] < tent_data["minHumidity"]:
            real_hum_dev = round(tent_data["humidity"] - tent_data["minHumidity"], 2)
            if weightMessage:
                weightMessage += f", Humidity Too Low: {real_hum_dev}%"
            else:
                weightMessage = f"Humidity Too Low: {real_hum_dev}%"

        # 2. Calculate WEIGHTED deviations (for action prioritization)
        weighted_temp_dev = round(real_temp_dev * tempWeight, 2)
        weighted_hum_dev = round(real_hum_dev * humWeight, 2)

        # 3. Calculate percentage of range (for better context)
        temp_range = max(1.0, abs(tent_data["maxTemp"] - tent_data["minTemp"]))
        hum_range = max(1.0, abs(tent_data["maxHumidity"] - tent_data["minHumidity"]))

        tempPercentage = round((abs(real_temp_dev) / temp_range) * 100, 1) if real_temp_dev != 0 else 0.0
        humPercentage = round((abs(real_hum_dev) / hum_range) * 100, 1) if real_hum_dev != 0 else 0.0

        return (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
                tempWeight, humWeight, tempPercentage, humPercentage, weightMessage)

    def _is_vpd_in_deadband(self) -> Tuple[bool, str]:
        """
        Check if current VPD is within deadband.

        Returns:
            Tuple of (is_in_deadband, reason_message)

        For VPD modes, checks if VPD is within configured deadband.
        For Closed Environment, returns False (no VPD deadband).
        """
        try:
            mode = self.data_store.get("tentMode")

            if mode == "VPD Perfection":
                current_vpd = self.data_store.getDeep("vpd.current")
                target_vpd = self.data_store.getDeep("vpd.perfection")
                deadband = self.data_store.getDeep("controlOptionData.deadband.vpdDeadband") or 0.05
            elif mode == "VPD Target":
                current_vpd = self.data_store.getDeep("vpd.current")
                target_vpd = self.data_store.getDeep("vpd.targeted")
                deadband = self.data_store.getDeep("controlOptionData.deadband.vpdTargetDeadband") or 0.05
            else:
                return False, ""

            if current_vpd is None or target_vpd is None:
                return False, ""

            deviation = abs(float(current_vpd) - float(target_vpd))

            if deviation <= deadband:
                return True, f"VPD {current_vpd:.3f} within deadband ±{deadband:.3f} of target {target_vpd:.3f}"

            return False, ""
        except (TypeError, ValueError) as e:
            _LOGGER.warning(f"{self.room}: Error checking VPD deadband: {e}")
            return False, ""

    async def _emit_quiet_zone_idle(self):
        """
        Emit idle signal when VPD is in deadband.
        Logs quiet zone status and ensures devices stay idle.
        """
        await self.event_manager.emit("LogForClient", {
            "Name": self.room,
            "message": "VPD in deadband - devices paused",
            "VPDStatus": "InDeadband",
            "deadbandActive": True
        }, haEvent=True, debug_type="INFO")

        _LOGGER.info(
            f"{self.room}: VPD in deadband - entering quiet zone, no device actions"
        )

    CONFLICTING_PAIRS = [
        ("canHumidify", "canDehumidify"),
        ("canHeat", "canCool"),
        ("canExhaust", "canHumidify"),
    ]

    def _remove_conflicting_actions(self, actionMap: List) -> List:
        """
        Remove actions that directly contradict each other.
        Keeps the higher-priority action of each conflicting pair.

        Args:
            actionMap: List of actions to filter

        Returns:
            Filtered list without conflicting actions
        """
        cap_to_action = {}
        for action in actionMap:
            cap = getattr(action, 'capability', None)
            if cap:
                cap_to_action[cap] = action

        blocked_caps = set()
        prio_map = {"high": 3, "medium": 2, "low": 1}

        for cap_a, cap_b in self.CONFLICTING_PAIRS:
            if cap_a in cap_to_action and cap_b in cap_to_action:
                action_a = cap_to_action[cap_a]
                action_b = cap_to_action[cap_b]

                prio_a = prio_map.get(getattr(action_a, 'priority', 'medium'), 2)
                prio_b = prio_map.get(getattr(action_b, 'priority', 'medium'), 2)

                if prio_b > prio_a:
                    blocked_caps.add(cap_a)
                    _LOGGER.info(
                        f"{self.room}: Conflict resolved – {cap_b} (prio={prio_b}) "
                        f"overrides {cap_a} (prio={prio_a})"
                    )
                else:
                    blocked_caps.add(cap_b)
                    _LOGGER.info(
                        f"{self.room}: Conflict resolved – {cap_a} (prio={prio_a}) "
                        f"overrides {cap_b} (prio={prio_b})"
                    )

        return [a for a in actionMap if getattr(a, 'capability', None) not in blocked_caps]

    # =================================================================
    # Closed Environment Action Handlers
    # =================================================================

    async def _handle_closed_environment_cycle(self, capabilities):
        """Handle complete closed environment control cycle."""
        if self.closed_actions:
            await self.closed_actions.execute_closed_environment_cycle(capabilities)
        else:
            _LOGGER.warning(f"{self.room}: closed_actions not initialized")

    async def _handle_maintain_co2(self, capabilities):
        """Handle CO2 maintenance requests."""
        if self.closed_actions:
            await self.closed_actions.maintain_co2(capabilities)
        else:
            _LOGGER.warning(f"{self.room}: closed_actions not initialized, skipping CO2 maintenance")

    async def _handle_monitor_o2_safety(self, capabilities):
        """Handle O2 safety monitoring requests."""
        if self.closed_actions:
            await self.closed_actions.monitor_o2_safety(capabilities)
        else:
            _LOGGER.warning(f"{self.room}: closed_actions not initialized")

    async def _handle_control_humidity_closed(self, capabilities):
        """Handle closed environment humidity control."""
        if self.closed_actions:
            await self.closed_actions.control_humidity_closed(capabilities)
        else:
            _LOGGER.warning(f"{self.room}: closed_actions not initialized")

    async def _handle_optimize_air_recirculation(self, capabilities):
        """Handle air recirculation optimization."""
        if self.closed_actions:
            await self.closed_actions.optimize_air_recirculation(capabilities)
        else:
            _LOGGER.warning(f"{self.room}: closed_actions not initialized")

    # =================================================================
    # Status and Utility Methods
    # =================================================================

    def getDampeningStatus(self) -> Dict[str, Any]:
        """
        Get current dampening status.

        Returns:
            Dictionary with dampening status for all capabilities
        """
        now = datetime.now()
        status = {}

        for capability, history in self.actionHistory.items():
            cooldownRemaining = history.get("cooldown_until", now) - now
            status[capability] = {
                "last_action": history.get("last_action"),
                "action_type": history.get("action_type"),
                "cooldown_remaining_seconds": max(0, cooldownRemaining.total_seconds()),
                "is_blocked": now < history.get("cooldown_until", now),
            }

        return status

    def clearDampeningHistory(self):
        """Clear the dampening history (for debugging/reset)."""
        self.actionHistory.clear()
        _LOGGER.info(f"{self.room}: Dampening history reset")

    def get_action_status(self) -> Dict[str, Any]:
        """
        Get comprehensive action status.

        Returns:
            Dictionary with action system status
        """
        return {
            "room": self.room,
            "initialized": self.isInitialized,
            "emergency_mode": self._emergency_mode,
            "adaptive_cooldown_enabled": self.adaptiveCooldownEnabled,
            "active_cooldowns": len(
                [
                    c
                    for c in self.actionHistory.values()
                    if datetime.now() < c.get("cooldown_until", datetime.now())
                ]
            ),
            "total_actions_tracked": len(self.actionHistory),
            "modules": {
                "vpd_actions": self.vpd_actions is not None,
                "emergency_actions": self.emergency_actions is not None,
                "dampening_actions": self.dampening_actions is not None,
                "premium_actions": self.premium_actions is not None,
            },
        }

    # =================================================================
    # Action Processing Methods
    # =================================================================

    async def _check_vpd_night_hold(self, actionMap: List) -> bool:
        """
        Check if VPD Night Hold is active and handle accordingly.
        
        CRITICAL: This check MUST happen BEFORE any action processing.
        If light is OFF and nightVPDHold is False/None, we should NOT run VPD actions.
        
        Logic:
        - If light is ON: Always allow VPD actions (return True)
        - If light is OFF AND nightVPDHold is True: Allow VPD actions (return True)
        - If light is OFF AND nightVPDHold is False/None: Block VPD actions, run fallback (return False)
        
        Returns:
            True if actions should continue, False if blocked by night hold
        """
        nightVPDHold = self.data_store.getDeep("controlOptions.nightVPDHold")
        islightON = self.data_store.getDeep("isPlantDay.islightON")
        
        _LOGGER.debug(f"{self.room}: VPD Night Hold check - islightON={islightON}, nightVPDHold={nightVPDHold}")
        
        # Use truthiness check: not islightON handles False/None, not nightVPDHold handles False/None
        if not islightON and not nightVPDHold:
            _LOGGER.debug(f"{self.room}: VPD Night Hold NOT ACTIVE - Ignoring VPD actions (light is OFF)")
            await self._night_hold_fallback(actionMap)
            return False
        
        return True

    async def _night_hold_fallback(self, actionMap: List):
        """
        Handle actions when VPD Night Hold is not active (power-saving night mode).
        
        Logic:
        - Climate devices (Heating, Cooling, Humidifier, Dehumidifier, Climate, CO2, Light) 
          are reduced to minimum to save power
        - Ventilation devices (Exhaust, Ventilation, Intake, Window) are actively controlled
          to prevent mold by ensuring air circulation
        - Exhaust/Ventilation: Increase to maintain air exchange
        - Intake: Adjust based on outside conditions (increase if outside temp allows, reduce if too cold)
        - Window: Controlled like ventilation for air exchange
        """
        _LOGGER.debug(f"{self.room}: Night Hold Power-Saving Mode - Managing ventilation for mold prevention")
        
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        # Climate devices that should be minimized at night to save power
        climateCaps = {"canHeat", "canCool", "canHumidify", "canClimate", "canDehumidify", "canCO2", "canLight"}
        
        # Ventilation devices that should be actively controlled
        ventilationCaps = {"canExhaust", "canVentilate", "canIntake", "canWindow"}
        
        finalActions = []
        
        # 1. Reduce all climate devices to minimum
        for action in actionMap:
            cap = getattr(action, 'capability', None)
            if cap in climateCaps:
                finalActions.append(OGBActionPublication(
                    capability=cap,
                    action="Reduce",
                    Name=self.room,
                    message="NightHold: Climate device reduced for power saving",
                    priority="low"
                ))
        
        # 2. Get capabilities to check what ventilation devices are available
        caps = self.data_store.get("capabilities") or {}
        
        # 3. Always increase Exhaust and Ventilation for air exchange (prevent mold)
        if caps.get("canExhaust", {}).get("state", False):
            finalActions.append(OGBActionPublication(
                capability="canExhaust",
                action="Increase",
                Name=self.room,
                message="NightHold: Exhaust increased for air exchange (mold prevention)",
                priority="medium"
            ))
        
        if caps.get("canVentilate", {}).get("state", False):
            finalActions.append(OGBActionPublication(
                capability="canVentilate",
                action="Increase",
                Name=self.room,
                message="NightHold: Ventilation increased for air circulation (mold prevention)",
                priority="medium"
            ))
        
        # 4. Window - treat like ventilation for air exchange
        if caps.get("canWindow", {}).get("state", False):
            finalActions.append(OGBActionPublication(
                capability="canWindow",
                action="Increase",
                Name=self.room,
                message="NightHold: Window opened for air exchange (mold prevention)",
                priority="medium"
            ))
        
        # 5. Intake - adjust based on outside temperature
        if caps.get("canIntake", {}).get("state", False):
            outside_temp = self.data_store.getDeep("tentData.AmbientTemp")
            inside_temp = self.data_store.getDeep("tentData.temperature")
            min_temp = self.data_store.getDeep("tentData.minTemp")
            
            try:
                outside_temp = float(outside_temp) if outside_temp is not None else None
                inside_temp = float(inside_temp) if inside_temp is not None else None
                min_temp = float(min_temp) if min_temp is not None else 18.0
            except (ValueError, TypeError):
                outside_temp = None
                inside_temp = None
                min_temp = 18.0
            
            # Logic: Only intake outside air if it's not too cold (would require heating)
            # If outside is warm enough, increase intake for fresh air
            # If outside is too cold, reduce intake to save heating power
            if outside_temp is not None and inside_temp is not None:
                # Safe margin: don't intake if outside is more than 3°C below target min
                if outside_temp >= (min_temp - 3):
                    # Outside is warm enough - increase intake for fresh air
                    finalActions.append(OGBActionPublication(
                        capability="canIntake",
                        action="Increase",
                        Name=self.room,
                        message=f"NightHold: Intake increased (outside {outside_temp}°C warm enough)",
                        priority="medium"
                    ))
                else:
                    # Outside is too cold - reduce intake to save heating
                    finalActions.append(OGBActionPublication(
                        capability="canIntake",
                        action="Reduce",
                        Name=self.room,
                        message=f"NightHold: Intake reduced (outside {outside_temp}°C too cold, saving heat)",
                        priority="low"
                    ))
            else:
                # No outside temp data - default to moderate intake
                finalActions.append(OGBActionPublication(
                    capability="canIntake",
                    action="Increase",
                    Name=self.room,
                    message="NightHold: Intake increased (no outside temp data, default to air exchange)",
                    priority="low"
                ))
        
        # Emit summary log
        climate_count = len([a for a in finalActions if getattr(a, 'capability', '') in climateCaps])
        vent_count = len([a for a in finalActions if getattr(a, 'capability', '') in ventilationCaps])
        
        await self.event_manager.emit("LogForClient", {
            "Name": self.room,
            "NightVPDHold": "NotActive Power-Saving Mode",
            "message": f"Night hold: {climate_count} climate devices reduced, {vent_count} ventilation devices managed",
            "climateDevices": climate_count,
            "ventilationDevices": vent_count
        }, haEvent=True, debug_type="INFO")
        
        # Execute all night hold actions
        if finalActions:
            _LOGGER.info(
                f"{self.room}: Night Hold executing {len(finalActions)} actions - "
                f"Climate minimized, Ventilation active for mold prevention"
            )
            await self.publicationActionHandler(finalActions)

    async def _log_vpd_results(
        self,
        real_temp_dev: float,
        real_hum_dev: float,
        tempPercentage: float,
        humPercentage: float,
        final_actions: List,
        blocked_actions: List,
        dampening_enabled: bool,
    ):
        """
        Log VPD processing results.

        Args:
            real_temp_dev: Real temperature deviation
            real_hum_dev: Real humidity deviation
            tempPercentage: Temperature deviation percentage
            humPercentage: Humidity deviation percentage
            final_actions: Final list of actions to execute
            blocked_actions: Actions blocked by dampening
            dampening_enabled: Whether dampening is enabled
        """
        # Determine VPD status
        current_vpd = self.data_store.getDeep("vpd.current")
        target_vpd = self.data_store.getDeep("vpd.perfection")
        if target_vpd is None:
            target_vpd = self.data_store.getDeep("vpd.targeted")

        vpd_deviation = 0.0
        vpd_status = "unknown"
        if current_vpd is not None and target_vpd is not None:
            vpd_deviation = abs(float(current_vpd) - float(target_vpd))
            if vpd_deviation <= 0.1:
                vpd_status = "low"
            elif vpd_deviation <= 0.3:
                vpd_status = "medium"
            elif vpd_deviation <= 0.5:
                vpd_status = "high"
            else:
                vpd_status = "critical"

        # Create action summary
        action_summary = ", ".join([f"{a.capability}:{a.action}" for a in final_actions])

        # Build log message
        if dampening_enabled and blocked_actions:
            message = (
                f"VPD Perfection: Core Logic + Dampening: {len(final_actions)} actions executed "
                f"({len(blocked_actions)} blocked by cooldown)"
            )
        elif dampening_enabled:
            message = (
                f"VPD Perfection: Core Logic + Dampening: {len(final_actions)} actions executed"
            )
        else:
            message = (
                f"VPD Perfection: Core Logic only (dampening disabled): {len(final_actions)} actions executed"
            )

        # Emit log
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "message": message,
                "actions": action_summary,
                "actionCount": len(final_actions),
                "blockedActions": len(blocked_actions),
                "dampeningEnabled": dampening_enabled,
                "tempDeviation": real_temp_dev,
                "humDeviation": real_hum_dev,
                "tempPercentage": tempPercentage,
                "humPercentage": humPercentage,
                "vpdCurrent": current_vpd,
                "vpdTarget": target_vpd,
                "vpdDeviation": round(vpd_deviation, 2),
                "vpdStatus": vpd_status,
            },
            haEvent=True,
            debug_type="INFO",
        )

    async def checkLimitsAndPublicate(self, actionMap: List):
        """
        Process VPD Perfection actions with clean separation of Core Logic and Dampening.

        Flow:
        1. Mode Check (only VPD modes: Perfection, Target, Closed Environment)
        2. Night Hold Check (always)
        3. Deadband Check (always)
        4. Calculate Deviations (always)
        5. Core VPD Logic (always): Buffer zones, VPD context, conflicts
        6. Dampening Features (if enabled): Cooldown, emergency override
        7. Environment Guard (always)
        8. Execute actions (always)

        NOTE: Core VPD Logic is only applied to VPD Perfection and VPD Target modes,
              NOT to Closed Environment or Premium modes (PID/MPC/AI).

        Args:
            actionMap: List of actions to process
        """
        # Check if this is a VPD mode (not Premium PID/MPC/AI or Closed Environment)
        mode = self.data_store.get("tentMode")
        # Core VPD Logic only for VPD Perfection and VPD Target, NOT for Closed Environment
        vpd_modes = {"VPD Perfection", "VPD Target"}
        is_vpd_mode = mode in vpd_modes

        if not is_vpd_mode:
            _LOGGER.warning(
                f"{self.room}: Core VPD Logic skipped - mode '{mode}' is not a VPD mode. "
                f"Actions will be executed directly without Core VPD Logic."
            )
            # For non-VPD modes, just apply Environment Guard and execute
            final_actions = await self._apply_environment_guard(actionMap)
            await self.publicationActionHandler(final_actions)
            return

        # CRITICAL: Check VPD Night Hold FIRST - if false, don't run actions
        if not await self._check_vpd_night_hold(actionMap):
            return

        # Check deadband - if VPD is in quiet zone, pause all devices
        in_deadband, reason = self._is_vpd_in_deadband()
        if in_deadband:
            await self._emit_quiet_zone_idle()
            return

        # Get tent data and calculate weighted deviations
        tent_data = self.data_store.get("tentData")
        (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
         tempWeight, humWeight, tempPercentage, humPercentage, weightMessage) = self._calculate_weighted_deviations(tent_data)

        # STEP 1: CORE VPD LOGIC (ALWAYS active)
        # - Buffer Zones (prevent oscillation)
        # - VPD Context (priorities based on VPD status)
        # - Deviations-based actions (intelligent additional actions)
        # - Conflict resolution (resolve contradictory actions)
        if self.dampening_actions:
            enhanced_actions = await self.dampening_actions.process_core_vpd_logic(
                actionMap, weighted_temp_dev, weighted_hum_dev, tent_data
            )
        else:
            # Fallback: Only resolve conflicts
            enhanced_actions = self._resolve_action_conflicts(actionMap)

        # STEP 2: DAMPENING FEATURES (only if enabled)
        # - Cooldown filtering (user-defined base cooldowns)
        # - Repeat cooldown (prevent immediate same action)
        # - Emergency override (bypass cooldown in critical conditions)
        dampening_enabled = self.data_store.getDeep("controlOptions.vpdDeviceDampening", False)
        blocked_actions = []

        if dampening_enabled and self.dampening_actions:
            filtered_actions, blocked_actions = await self.dampening_actions.process_dampening_features(
                enhanced_actions, weighted_temp_dev, weighted_hum_dev, tent_data
            )
            final_actions = self._resolve_action_conflicts(filtered_actions)
        else:
            # No dampening - use enhanced actions directly
            final_actions = enhanced_actions

        # STEP 3: ENVIRONMENT GUARD (always active)
        final_actions = await self._apply_environment_guard(final_actions)

        # STEP 4: Execute actions
        await self.publicationActionHandler(final_actions)

        # Log results
        await self._log_vpd_results(
            real_temp_dev, real_hum_dev, tempPercentage, humPercentage,
            final_actions, blocked_actions, dampening_enabled
        )

    async def checkLimitsAndPublicateTarget(self, actionMap: List):
        """
        Process VPD Target actions with VPD-based deviation only (no temp/hum weights).

        NOTE: Core VPD Logic is only applied to VPD Perfection and VPD Target modes,
              NOT to Closed Environment or Premium modes (PID/MPC/AI).
        """
        # Check if this is a VPD mode (not Premium PID/MPC/AI or Closed Environment)
        mode = self.data_store.get("tentMode")
        # Core VPD Logic only for VPD Perfection and VPD Target, NOT for Closed Environment
        vpd_modes = {"VPD Perfection", "VPD Target"}
        is_vpd_mode = mode in vpd_modes

        if not is_vpd_mode:
            _LOGGER.warning(
                f"{self.room}: Core VPD Logic skipped - mode '{mode}' is not a VPD mode. "
                f"Actions will be executed directly without Core VPD Logic."
            )
            final_actions = await self._apply_environment_guard(actionMap)
            await self.publicationActionHandler(final_actions)
            return

        if not await self._check_vpd_night_hold(actionMap):
            return

        # Check deadband - if VPD is in quiet zone, pause all devices
        in_deadband, reason = self._is_vpd_in_deadband()
        if in_deadband:
            await self._emit_quiet_zone_idle()
            return

        # For VPD Target: Calculate VPD deviation only (not temp/hum weighted deviations)
        # VPD Target is based on VPD value only, not plant stage temp/hum limits
        current_vpd = self.data_store.getDeep("vpd.current")
        target_vpd = self.data_store.getDeep("vpd.targeted")

        if current_vpd is not None and target_vpd is not None:
            vpd_deviation = round(float(current_vpd) - float(target_vpd), 2)
            vpd_message = f"VPD Deviation: {vpd_deviation} kPa (Current: {current_vpd}, Target: {target_vpd})"
        else:
            vpd_deviation = 0
            vpd_message = "VPD values not available"

        # Use 0 for temp/hum deviations in VPD Target mode (not applicable)
        weighted_temp_dev = 0
        weighted_hum_dev = 0

        # Get tent data for Core VPD Logic
        tent_data = self.data_store.get("tentData")

        # STEP 1: CORE VPD LOGIC (ALWAYS active for VPD modes)
        if self.dampening_actions:
            enhanced_actions = await self.dampening_actions.process_core_vpd_logic(
                actionMap, weighted_temp_dev, weighted_hum_dev, tent_data
            )
        else:
            enhanced_actions = self._resolve_action_conflicts(actionMap)

        # STEP 2: DAMPENING FEATURES (only if enabled)
        dampening_enabled = self.data_store.getDeep("controlOptions.vpdDeviceDampening", False)
        blocked_actions = []

        if dampening_enabled and self.dampening_actions:
            filtered_actions, blocked_actions = await self.dampening_actions.process_dampening_features(
                enhanced_actions, weighted_temp_dev, weighted_hum_dev, tent_data
            )
            final_actions = self._resolve_action_conflicts(filtered_actions)
        else:
            final_actions = enhanced_actions

        # STEP 3: ENVIRONMENT GUARD (always active)
        final_actions = await self._apply_environment_guard(final_actions)

        # STEP 4: Execute actions
        await self.publicationActionHandler(final_actions)

        # Log results
        current_vpd_for_log = self.data_store.getDeep("vpd.current")
        target_vpd_for_log = self.data_store.getDeep("vpd.targeted")
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "message": f"VPD Target: {len(final_actions)} actions executed ({len(blocked_actions)} blocked by cooldown)",
                "actions": ", ".join([f"{a.capability}:{a.action}" for a in final_actions]),
                "actionCount": len(final_actions),
                "blockedActions": len(blocked_actions),
                "dampeningEnabled": dampening_enabled,
                "vpdDeviation": vpd_deviation,
                "vpdCurrent": current_vpd_for_log,
                "vpdTarget": target_vpd_for_log,
            },
             haEvent=True,
             debug_type="INFO",
         )

    async def checkLimitsAndPublicateNoVPD(self, actionMap: List):
        """
        Process actions WITHOUT VPD Night Hold check.

        Used by Closed Environment mode where CO2/safety actions should
        bypass the VPD Night Hold logic.

        NOTE: Closed Environment has its own logic, so it only needs:
        - Conflict resolution
        - Environment Guard
        - Execute

        It does NOT need Core VPD Logic (Buffer Zones, VPD Context, Deviations-based Actions)
        because Closed Environment creates actions based on its own control logic.

        Args:
            actionMap: List of actions to process
        """
        if not actionMap:
            return

        # Get tent data and calculate weighted deviations for logging
        tent_data = self.data_store.get("tentData")
        (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
         tempWeight, humWeight, tempPercentage, humPercentage, weightMessage) = self._calculate_weighted_deviations(tent_data)

        final_actions = actionMap

        # IMPORTANT: Closed Environment must bypass all VPD-specific processing.
        # We only keep lightweight per-capability conflict resolution here so
        # Closed actions can still avoid duplicate commands in the same cycle
        # without being filtered by VPD night-hold or VPD deviation logic.
        if self.dampening_actions:
            final_actions = self.dampening_actions._resolve_action_conflicts(actionMap)

        final_actions = await self._apply_environment_guard(final_actions)
        await self.publicationActionHandler(final_actions)

    async def checkLimitsAndPublicateWithDampening(self, actionMap: List):
        """
        Process actions with full dampening logic.
        
        This is the main entry point for dampening-aware VPD action processing.
        Delegates to dampening_actions module if available.
        
        Args:
            actionMap: List of actions to process
        """
        # CRITICAL: Check VPD Night Hold FIRST - if false, don't run actions
        if not await self._check_vpd_night_hold(actionMap):
            return
        
        # Check if device dampening is enabled in control options
        dampening_enabled = self.data_store.getDeep("controlOptions.vpdDeviceDampening", False)
        if self.dampening_actions and dampening_enabled:
            await self.dampening_actions.process_actions_with_dampening(actionMap)
        else:
            # Fallback: execute actions directly without dampening
            _LOGGER.info(f"{self.room}: VPD mode with dampening (dampening disabled) - executing {len(actionMap)} actions directly")
            
            # Log actions before environment guard
            action_summary = ", ".join([f"{getattr(a, 'capability', 'unknown')}:{getattr(a, 'action', 'unknown')}" for a in actionMap])
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "message": f"VPD dampening mode: executing {len(actionMap)} actions",
                    "actions": action_summary,
                    "actionCount": len(actionMap),
                    "dampeningEnabled": False,
                },
                haEvent=True,
                debug_type="INFO",
            )
            
            final_actions = await self._apply_environment_guard(actionMap)
            await self.publicationActionHandler(final_actions)

    async def publicationActionHandler(self, actionMap: List):
        """
        Execute device actions and emit DataRelease event.
        
        This is the core action execution method that:
        1. Stores actions in history for analytics
        2. Emits device-specific events
        3. Triggers DataRelease for Premium API sync
        
        Args:
            actionMap: List of actions to execute
        """
        tentMode = self.data_store.get("tentMode") or "VPD Perfection"
        if tentMode == "Disabled":
            _LOGGER.info(f"{self.room}: Actions skipped - tent mode is Disabled")
            return

        actionMap = await self._apply_environment_guard(actionMap)
        _LOGGER.debug(f"{self.room}: Executing {len(actionMap)} validated actions")

        # CRITICAL: Send LogForClient as bundle (original format expected by UI)
        if actionMap:
            await self.event_manager.emit("LogForClient", actionMap, haEvent=True, debug_type="INFO")

        
        # Build action set with all actions from this execution cycle
        # API expects: {device: "exhaust", action: "Increase", priority: "high", reason: "...", controllerType: "VPD-P"}
        current_time = time.time()
        action_set = {
            "actions": [],
            "timestamp": current_time,
            "room": self.room,
            "controllerType": self._map_tentmode_to_controller_type(tentMode),
        }
        
        for action in actionMap:
            # Map capability to device name (canExhaust -> exhaust, canHeat -> heat, etc.)
            capability = getattr(action, 'capability', '')
            device = capability.replace('can', '').lower() if capability.startswith('can') else capability.lower()
            
            action_entry = {
                "device": device,
                "action": getattr(action, 'action', 'Eval'),
                "priority": getattr(action, 'priority', 'medium') or 'medium',
                "reason": getattr(action, 'message', ''),
                "timestamp": current_time,
                "controllerType": action_set["controllerType"],
                # Keep capability for backwards compatibility with HA format conversion
                "capability": capability,
            }
            action_set["actions"].append(action_entry)

        # Use lock for thread-safe action history updates to prevent race conditions
        async with self._action_history_lock:
            previousActions = self.data_store.get("previousActions") or []
            
            # Only add if we have actions
            if action_set["actions"]:
                previousActions.append(action_set)

            # Keep only the last 5 action sets (API expects max 5)
            previousActions = previousActions[-5:]
            self.data_store.set("previousActions", previousActions)
        
        # CRITICAL: Also store actionData for API compatibility
        # The API's HistoricalDataTrainer.extractActionsFromRecord() expects actionData.controlCommands
        # This ensures ALL modes (VPD Perfection, PID, MPC, AI, etc.) provide data for AI training
        controlCommands = []
        for action_entry in action_set.get("actions", []):
            controlCommands.append({
                "device": action_entry.get("device", ""),
                "action": action_entry.get("action", "Eval"),
                "priority": action_entry.get("priority", "medium"),
                "reason": action_entry.get("reason", ""),
                "timestamp": action_entry.get("timestamp", current_time),
                "controllerType": action_set.get("controllerType", "VPD-P"),
            })
        
        actionData = {
            "controllerType": action_set.get("controllerType", "VPD-P"),
            "commandCount": len(controlCommands),
            "controlCommands": controlCommands,
        }
        self.data_store.set("actionData", actionData)
        
        # DEBUG: Log the format being saved
        _LOGGER.debug(f"🔍 {self.room} actionData: {actionData}")

        # Execute device-specific actions
        for action in actionMap:
            actionCap = getattr(action, 'capability', None)
            actionType = getattr(action, 'action', None)
            actionMessage = getattr(action, 'message', '')
            
            if not actionCap or not actionType:
                continue
                
            _LOGGER.debug(f"{self.room}: {actionCap} - {actionType} - {actionMessage}")

            # Emit device-specific events with error handling
            try:
                if actionCap == "canExhaust":
                    await self.event_manager.emit(f"{actionType} Exhaust", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Exhaust executed.")
                elif actionCap == "canIntake":
                    await self.event_manager.emit(f"{actionType} Intake", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Intake executed.")
                elif actionCap == "canVentilate":
                    await self.event_manager.emit(f"{actionType} Ventilation", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Ventilation executed.")
                elif actionCap == "canWindow":
                    await self.event_manager.emit(f"{actionType} Ventilation", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Window (via Ventilation) executed.")
                elif actionCap == "canHumidify":
                    await self.event_manager.emit(f"{actionType} Humidifier", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Humidifier executed.")
                elif actionCap == "canDehumidify":
                    await self.event_manager.emit(f"{actionType} Dehumidifier", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Dehumidifier executed.")
                elif actionCap == "canHeat":
                    await self.event_manager.emit(f"{actionType} Heater", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Heater executed.")
                elif actionCap == "canCool":
                    await self.event_manager.emit(f"{actionType} Cooler", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Cooler executed.")
                elif actionCap == "canClimate":
                    await self.event_manager.emit(f"{actionType} Climate", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Climate executed.")
                elif actionCap == "canCO2":
                    _LOGGER.warning(f"{self.room}: Emitting {actionType} CO2")
                    await self.event_manager.emit(f"{actionType} CO2", actionType)
                    _LOGGER.warning(f"{self.room}: {actionType} CO2 executed.")
                elif actionCap == "canLight":
                    await self.event_manager.emit(f"{actionType} Light", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} Light executed.")
            except Exception as e:
                _LOGGER.error(f"{self.room}: Failed to execute {actionCap} {actionType} action: {e}")
                # Continue with next action instead of failing the entire batch

        # Emit DataRelease event for Premium API synchronization (ONLY IF mainControl is Premium)
        mainControl = self.data_store.get("mainControl")
        if mainControl == "Premium":
            await self.event_manager.emit("DataRelease", True)

    async def _apply_environment_guard(self, action_map: List) -> List:
        """Rewrite unsafe air-exchange increases to reductions under cold ambient risk.
        
        Only active when ambientControl is enabled in controlOptions.
        """
        if not action_map:
            return action_map

        # Check if ambientControl is enabled - EnvironmentGuard only makes sense
        # when the user wants to control outside air exchange
        ambient_control = self.data_store.getDeep("controlOptions.ambientControl", False)
        if not ambient_control:
            _LOGGER.debug(f"{self.room}: EnvironmentGuard skipped - ambientControl not enabled")
            return action_map

        # Lazy import avoids circular import during module initialization.
        try:
            from ..actions.OGBEnvironmentGuard import evaluate_environment_guard
        except ModuleNotFoundError:
            _LOGGER.warning(
                "%s: OGBEnvironmentGuard module missing, skipping environment guard rewrite",
                self.room,
            )
            return action_map

        guarded_actions = []

        for action in action_map:
            cap = getattr(action, "capability", None)
            action_type = getattr(action, "action", None)

            if not cap or not action_type:
                guarded_actions.append(action)
                continue

            should_block, metadata = evaluate_environment_guard(
                self.data_store,
                self.room,
                cap,
                action_type,
                message=getattr(action, "message", ""),
                priority=getattr(action, "priority", ""),
                source="action_manager",
            )

            if should_block:
                reason = metadata.get("reason", "environment_guard")
                old_message = getattr(action, "message", "")
                new_message = f"{old_message} (EnvironmentGuard:{reason})".strip()
                try:
                    guarded_actions.append(
                        dataclasses.replace(
                            action,
                            action="Reduce",
                            message=new_message,
                            priority=getattr(action, "priority", "medium") or "medium",
                        )
                    )
                except TypeError:
                    setattr(action, "action", "Reduce")
                    setattr(action, "message", new_message)
                    guarded_actions.append(action)

                _LOGGER.info(
                    f"{self.room}: EnvironmentGuard blocked {cap} Increase -> Reduce "
                    f"(reason={reason}, selectedSource={metadata.get('selectedSource')})"
                )
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Action": "EnvironmentGuard",
                        "Device": cap,
                        "From": "Increase",
                        "To": "Reduce",
                        "Reason": reason,
                        "Message": (
                            f"EnvironmentGuard blocked {cap}: {reason} "
                            f"(indoor={metadata.get('indoorTemp')}°C/{metadata.get('indoorHum')}%, "
                            f"source={metadata.get('selectedSource')}/{metadata.get('selectedTemp')}°C/{metadata.get('selectedHum')}%)"
                        ),
                        "selectedSource": metadata.get("selectedSource"),
                        "selectedTemp": metadata.get("selectedTemp"),
                        "selectedHum": metadata.get("selectedHum"),
                        "indoorTemp": metadata.get("indoorTemp"),
                        "indoorHum": metadata.get("indoorHum"),
                        "maxHumidity": metadata.get("maxHumidity"),
                        "minHumidity": metadata.get("minHumidity"),
                        "blockedCount": metadata.get("blockedCount"),
                        "lockUntil": metadata.get("lockUntil"),
                        "priority": metadata.get("priority"),
                    },
                    haEvent=True,
                    debug_type="WARNING",
                )
            else:
                reason = metadata.get("reason", "allowed")
                if "humidity" in reason.lower() or "temp" in reason.lower():
                    await self.event_manager.emit(
                        "LogForClient",
                        {
                            "Name": self.room,
                            "Action": "EnvironmentGuard",
                            "Device": cap,
                            "From": "Increase",
                            "To": "Increase",
                            "Reason": reason,
                            "Message": (
                                f"EnvironmentGuard allowed {cap}: {reason} "
                                f"(indoor={metadata.get('indoorTemp')}°C/{metadata.get('indoorHum')}%, "
                                f"source={metadata.get('selectedSource')}/{metadata.get('selectedTemp')}°C/{metadata.get('selectedHum')}%)"
                            ),
                            "selectedSource": metadata.get("selectedSource"),
                            "selectedTemp": metadata.get("selectedTemp"),
                            "selectedHum": metadata.get("selectedHum"),
                            "indoorTemp": metadata.get("indoorTemp"),
                            "indoorHum": metadata.get("indoorHum"),
                            "maxHumidity": metadata.get("maxHumidity"),
                            "minHumidity": metadata.get("minHumidity"),
                            "priority": metadata.get("priority"),
                        },
                        haEvent=True,
                        debug_type="DEBUG",
                    )
                guarded_actions.append(action)

        return guarded_actions

    def _selectCriticalEmergencyAction(self, actionMap: List, emergencyConditions: List[str]):
        """
        Select the most critical action during emergencies.
        
        Args:
            actionMap: Available actions
            emergencyConditions: Current emergency conditions
            
        Returns:
            The most critical action or None
        """
        if not actionMap or not emergencyConditions:
            return None

        # Priority mapping based on emergency type
        emergencyPriority = {
            "critical_overheat": ["canCool", "canExhaust", "canVentilate"],
            "critical_cold": ["canHeat"],
            "immediate_condensation_risk": ["canDehumidify", "canExhaust", "canVentilate"],
            "critical_humidity": ["canDehumidify", "canExhaust"],
        }

        # Find highest priority action
        for condition in emergencyConditions:
            priorityCaps = emergencyPriority.get(condition, [])
            for cap in priorityCaps:
                for action in actionMap:
                    actionCap = getattr(action, 'capability', None)
                    actionType = getattr(action, 'action', None)
                    if actionCap == cap and actionType in ["Increase", "Reduce"]:
                        _LOGGER.critical(
                            f"{self.room}: Emergency override for {cap} - {actionType}"
                        )
                        return action

        # Fallback: return first available action
        return actionMap[0] if actionMap else None

    def _map_tentmode_to_controller_type(self, tentMode: str) -> str:
        """
        Map tentMode to controller type for history storage.
        Must match ogb-grow-api/src/history/CompactDataSchema.js mapTentModeToControllerType()
        
        Args:
            tentMode: The tent mode from dataStore
            
        Returns:
            Controller type identifier (e.g., 'VPD-P', 'PID', 'AI')
        """
        if not tentMode:
            return 'NONE'
        
        mode_map = {
            # Local HA control modes (from select.py OGB_TentMode)
            'VPD Perfection': 'VPD-P',
            'VPD Target': 'VPD-T',
            'Closed Environment': 'CLOSED',
            'Script Mode': 'SCRIPT',
            'Drying': 'DRY',
            'Disabled': 'OFF',
            # Premium API control modes
            'AI Control': 'AI',
            'PID Control': 'PID',
            'MPC Control': 'MPC',
            'Premium': 'PREM',
        }
        
        return mode_map.get(tentMode, tentMode[:6].upper().replace(' ', ''))

    async def async_shutdown(self):
        """Shutdown action manager and cleanup resources."""
        try:
            _LOGGER.info(f"Shutting down Action Manager for {self.room}")
            
            # Cancel all background tasks
            if hasattr(self, '_background_tasks'):
                for task in self._background_tasks:
                    if not task.done():
                        task.cancel()
                self._background_tasks.clear()
            
            # Clear action history
            self.actionHistory.clear()
            
            # Clear previous actions from datastore
            self.data_store.set("previousActions", [])
            
            _LOGGER.info(f"Action Manager shutdown complete for {self.room}")
            
        except Exception as e:
            _LOGGER.error(f"Error during Action Manager shutdown: {e}")
