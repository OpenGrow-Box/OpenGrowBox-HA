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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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
        self.event_manager = event_manager  # Also provide camelCase version for backwards compatibility
        self.room = room
        self.name = "OGB Action Manager"

        # Action state
        self.isInitialized = False
        self.actionHistory: Dict[str, Dict[str, Any]] = {}
        self.defaultCooldownMinutes = DEFAULT_DEVICE_COOLDOWNS.copy()
        self.adaptiveCooldownEnabled = True
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
        Calculate adaptive cooldown time based on deviation.

        Args:
            capability: Device capability
            deviation: Current deviation from target

        Returns:
            Cooldown time in minutes
        """
        baseCooldown = self.defaultCooldownMinutes.get(capability, 2)

        if not self.adaptiveCooldownEnabled:
            return baseCooldown

        # Larger deviation = longer cooldown (more time to take effect)
        if abs(deviation) > 5:
            return baseCooldown * 1.5
        elif abs(deviation) > 3:
            return baseCooldown * 1.2
        elif abs(deviation) < 1:
            return baseCooldown * 0.8

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

        # Lower threshold for emergency detection
        if tentData["temperature"] >= tentData["maxTemp"]:
            emergencyConditions.append("critical_overheat")
        if tentData["temperature"] <= tentData["minTemp"]:
            emergencyConditions.append("critical_cold")
        if tentData["dewpoint"] >= tentData["temperature"]:
            emergencyConditions.append("immediate_condensation_risk")
        if tentData.get("humidity", 0) > 85:
            emergencyConditions.append("critical_humidity")

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
        else:
            _LOGGER.error(f"Unknown capability: {cap}")

    async def _handle_increase_vpd(self, capabilities):
        """Handle VPD increase requests."""
        _LOGGER.error(f"🔥 {self.room}: _handle_increase_vpd CALLED (vpd_actions={self.vpd_actions is not None})")
        if self.vpd_actions:
            await self.vpd_actions.increase_vpd(capabilities)
        else:
            _LOGGER.debug(f"🔥 {self.room}: vpd_actions NOT INITIALIZED - action skipped!")

    async def _handle_reduce_vpd(self, capabilities):
        """Handle VPD reduce requests."""
        _LOGGER.error(f"🔥 {self.room}: _handle_reduce_vpd CALLED (vpd_actions={self.vpd_actions is not None})")
        if self.vpd_actions:
            await self.vpd_actions.reduce_vpd(capabilities)
        else:
            _LOGGER.debug(f"🔥 {self.room}: vpd_actions NOT INITIALIZED - action skipped!")

    async def _handle_fine_tune_vpd(self, capabilities):
        """Handle VPD fine-tune requests."""
        _LOGGER.error(f"🔥 {self.room}: _handle_fine_tune_vpd CALLED (vpd_actions={self.vpd_actions is not None})")
        if self.vpd_actions:
            await self.vpd_actions.fine_tune_vpd(capabilities)
        else:
            _LOGGER.debug(f"🔥 {self.room}: vpd_actions NOT INITIALIZED - action skipped!")

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
        await self.event_manager.emit("LogForClient", water_action, haEvent=True)

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
            await self.event_manager.emit("LogForClient", water_action, haEvent=True)

    # =================================================================
    # Closed Environment Action Handlers
    # =================================================================

    async def _handle_closed_environment_cycle(self, capabilities):
        """Handle complete closed environment control cycle."""
        if self.closed_actions:
            await self.closed_actions.execute_closed_environment_cycle(capabilities)
        else:
            _LOGGER.error(f"{self.room}: closed_actions not initialized")

    async def _handle_maintain_co2(self, capabilities):
        """Handle CO2 maintenance requests."""
        if self.closed_actions:
            await self.closed_actions.maintain_co2(capabilities)
        else:
            _LOGGER.error(f"{self.room}: closed_actions not initialized")

    async def _handle_monitor_o2_safety(self, capabilities):
        """Handle O2 safety monitoring requests."""
        if self.closed_actions:
            await self.closed_actions.monitor_o2_safety(capabilities)
        else:
            _LOGGER.error(f"{self.room}: closed_actions not initialized")

    async def _handle_control_humidity_closed(self, capabilities):
        """Handle closed environment humidity control."""
        if self.closed_actions:
            await self.closed_actions.control_humidity_closed(capabilities)
        else:
            _LOGGER.error(f"{self.room}: closed_actions not initialized")

    async def _handle_optimize_air_recirculation(self, capabilities):
        """Handle air recirculation optimization."""
        if self.closed_actions:
            await self.closed_actions.optimize_air_recirculation(capabilities)
        else:
            _LOGGER.error(f"{self.room}: closed_actions not initialized")

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
            _LOGGER.warning(f"{self.room}: VPD Night Hold NOT ACTIVE - Ignoring VPD actions (light is OFF)")
            await self._night_hold_fallback(actionMap)
            return False
        
        return True

    async def _night_hold_fallback(self, actionMap: List):
        """
        Handle actions when VPD Night Hold is not active.
        Reduces climate-affecting devices to safe levels during night.
        """
        _LOGGER.debug(f"{self.room}: VPD Night Hold NOT ACTIVE - Running fallback")
        await self.event_manager.emit("LogForClient", {
            "Name": self.room,
            "NightVPDHold": "NotActive Ignoring-VPD"
        }, haEvent=True)
        
        # Devices to exclude from normal actions during night
        excludeCaps = {"canHeat", "canCool", "canHumidify", "canClimate", "canDehumidify", "canLight", "canCO2"}
        # Devices to reduce during night
        modCaps = {"canHeat", "canCool", "canHumidify", "canClimate", "canDehumidify", "canCO2"}
        fallBackAction = "Reduce"
        
        # Filter out excluded actions
        filteredActions = [action for action in actionMap if getattr(action, 'capability', None) not in excludeCaps]
        
        # Create reduce actions for climate devices
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication
        reducedActions = [
            OGBActionPublication(
                capability=getattr(action, 'capability', ''),
                action=fallBackAction,
                Name=self.room,
                message="VPD-NightHold Device Reduction",
                priority="low"
            )
            for action in actionMap if getattr(action, 'capability', None) in modCaps
        ]
        
        # Execute fallback actions if any
        if filteredActions or reducedActions:
            await self.publicationActionHandler(filteredActions + reducedActions)

    async def checkLimitsAndPublicate(self, actionMap: List):
        """
        Process actions with basic limit checking (no dampening).
        
        This is the main entry point for basic VPD action processing.
        Delegates to dampening_actions module if available.
        
        Args:
            actionMap: List of actions to process
        """
        # CRITICAL: Check VPD Night Hold FIRST - if false, don't run actions
        if not await self._check_vpd_night_hold(actionMap):
            return
        
        if self.dampening_actions:
            await self.dampening_actions.process_actions_basic(actionMap)
        else:
            # Fallback: execute actions directly
            await self.publicationActionHandler(actionMap)

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
        
        if self.dampening_actions:
            await self.dampening_actions.process_actions_with_dampening(actionMap)
        else:
            # Fallback: execute actions directly with basic dampening
            filtered_actions, _ = self._filterActionsByDampening(actionMap)
            if filtered_actions:
                await self.publicationActionHandler(filtered_actions)

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
        _LOGGER.debug(f"{self.room}: Executing {len(actionMap)} validated actions")

        # Store previous actions for analytics - API expects specific format
        # Format: [{actions: [{device, action, priority, reason, timestamp, controllerType}, ...]}, ...]
        # See: ogb-grow-api/AGENTS.md lines 495-503 and CompactDataSchema.js
        previousActions = self.data_store.get("previousActions") or []
        current_time = time.time()
        
        # Get current tent mode for controller type
        tentMode = self.data_store.get("tentMode") or "VPD Perfection"

        # Build action set with all actions from this execution cycle
        # API expects: {device: "exhaust", action: "Increase", priority: "high", reason: "...", controllerType: "VPD-P"}
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
                    await self.event_manager.emit(f"{actionType} CO2", actionType)
                    _LOGGER.debug(f"{self.room}: {actionType} CO2 executed.")
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

