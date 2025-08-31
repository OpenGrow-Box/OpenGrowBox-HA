import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

_LOGGER = logging.getLogger(__name__)

from .OGBDataClasses.OGBPublications import OGBWaterAction, OGBWaterPublication

class FeedMode(Enum):
    DISABLED = "Disabled"
    AUTOMATIC = "Automatic"
    OWN_PLAN = "Own-Plan"

class FeedParameterType(Enum):
    EC_TARGET = "EC_Target"
    PH_TARGET = "PH_Target"
    NUT_A_ML = "Nut_A_ml"
    NUT_B_ML = "Nut_B_ml"
    NUT_C_ML = "Nut_C_ml"
    NUT_W_ML = "Nut_W_ml"
    NUT_X_ML = "Nut_X_ml"
    NUT_Y_ML = "Nut_Y_ml"
    NUT_PH_ML = "Nut_PH_ml"

@dataclass
class PlantStageConfig:
    vpdRange: list[float]
    minTemp: float
    maxTemp: float
    minHumidity: float
    maxHumidity: float
    
@dataclass
class FeedConfig:
    ph_target: float = 6.0
    ec_target: float = 1.2
    nutrients: Dict[str, float] = field(default_factory=dict)

class OGBFeedManager:
    def __init__(self, hass, dataStore, eventManager, room: str):
        self.name = "OGB Feed Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False
        
        # Feed Mode
        self.feed_mode = FeedMode.DISABLED
        self.current_plant_stage = "EarlyVeg"  # Default stage
        
        # Plant stages configuration
        self.plantStages: Dict[str, PlantStageConfig] = {
            "Germination": PlantStageConfig([0.412, 0.70], 20, 24, 65, 80),
            "Clones": PlantStageConfig([0.412, 0.65], 20, 24, 65, 80),
            "EarlyVeg": PlantStageConfig([0.65, 0.80], 20, 26, 55, 70),
            "MidVeg": PlantStageConfig([0.80, 1.0], 20, 27, 55, 65),
            "LateVeg": PlantStageConfig([1.05, 1.1], 20, 27, 55, 65),
            "EarlyFlower": PlantStageConfig([1.0, 1.25], 22, 26, 50, 65),
            "MidFlower": PlantStageConfig([1.1, 1.35], 22, 25, 45, 60),
            "LateFlower": PlantStageConfig([1.2, 1.65], 20, 24, 40, 55),
        }
        
        # Automatic mode feed configs per stage
        self.automaticFeedConfigs: Dict[str, FeedConfig] = {
            "Germination": FeedConfig(ph_target=6.2, ec_target=0.6, nutrients={"A": 0.5, "B": 0.5}),
            "Clones": FeedConfig(ph_target=6.0, ec_target=0.8, nutrients={"A": 1.0, "B": 1.0}),
            "EarlyVeg": FeedConfig(ph_target=5.8, ec_target=1.2, nutrients={"A": 2.0, "B": 2.0, "C": 1.0}),
            "MidVeg": FeedConfig(ph_target=5.8, ec_target=1.6, nutrients={"A": 3.0, "B": 3.0, "C": 2.0}),
            "LateVeg": FeedConfig(ph_target=5.8, ec_target=1.8, nutrients={"A": 3.5, "B": 3.5, "C": 2.5}),
            "EarlyFlower": FeedConfig(ph_target=6.0, ec_target=2.0, nutrients={"A": 2.0, "B": 4.0, "C": 3.0}),
            "MidFlower": FeedConfig(ph_target=6.0, ec_target=2.2, nutrients={"A": 1.5, "B": 5.0, "C": 4.0}),
            "LateFlower": FeedConfig(ph_target=6.2, ec_target=1.8, nutrients={"A": 1.0, "B": 3.0, "C": 2.0}),
        }
        
        # Target parameters
        self.target_ph: float = 0.0
        self.ph_toleration: float = 0.2
        self.target_ec: float = 0.0
        self.ec_toleration: float = 0.2
        self.target_temp: float = 0.0
        self.temp_toleration: float = 2.0
        self.target_oxi: float = 0.0
        self.oxi_toleration: float = 1.0
        self.nutrients: Dict[str, float] = {}
        
        # Current measurements
        self.current_ec: float = 0.0
        self.current_tds: float = 0.0
        self.current_ph: float = 0.0
        self.current_temp: float = 0.0
        self.current_sal: float = 0.0
        self.current_oxi: float = 0.0
        
        # Rate limiting and sensor settling
        self.last_pump_action: Dict[str, datetime] = {}
        self.min_interval_between_actions: timedelta = timedelta(seconds=30)
        self.sensor_settle_time: timedelta = timedelta(seconds=60)
        self.last_action_time: Optional[datetime] = None
        
        # Register event handlers
        self.eventManager.on("LogValidation", self._handleLogForClient)
        self.eventManager.on("FeedUpdate", self._on_feed_update)
        self.eventManager.on("CheckForFeed", self._check_if_feed_need)
        self.eventManager.on("FeedModeChange", self._feed_mode_change)
        self.eventManager.on("FeedModeValueChange", self._feed_mode_targets_change)
        self.eventManager.on("PlantStageChange", self._plant_stage_change)
                
        asyncio.create_task(self.init())

    async def init(self):
        """Initialize the feed manager with validation"""
        self.is_initialized = True
        # Load current plant stage from dataStore
        self.current_plant_stage = self.dataStore.getDeep("Plant.CurrentStage") or "EarlyVeg"
        _LOGGER.info(f"[{self.room}] OGB Feed Manager initialized successfully")

    async def _plant_stage_change(self, new_stage: str):
        """Handle plant stage changes"""
        if new_stage not in self.plantStages:
            _LOGGER.warning(f"[{self.room}] Unknown plant stage: {new_stage}")
            return
            
        self.current_plant_stage = new_stage
        
        # If in automatic mode, update targets based on new stage
        if self.feed_mode == FeedMode.AUTOMATIC:
            await self._update_automatic_targets()
                  
        _LOGGER.info(f"[{self.room}] Plant stage changed to: {new_stage}")

    async def _update_automatic_targets(self):
        """Update feed targets based on current plant stage in automatic mode"""
        if self.current_plant_stage not in self.automaticFeedConfigs:
            return
            
        feed_config = self.automaticFeedConfigs[self.current_plant_stage]
        
        self.target_ph = feed_config.ph_target
        self.target_ec = feed_config.ec_target
        self.nutrients = feed_config.nutrients.copy()
        
        # Update dataStore with automatic values
        self.dataStore.setDeep("Feed.PH_Target", self.target_ph)
        self.dataStore.setDeep("Feed.EC_Target", self.target_ec)
        
        for nutrient, amount in self.nutrients.items():
            self.dataStore.setDeep(f"Feed.Nut_{nutrient}_ml", amount)
        
        _LOGGER.info(f"[{self.room}] Updated automatic targets for stage {self.current_plant_stage}: "
                    f"pH={self.target_ph}, EC={self.target_ec}, Nutrients={self.nutrients}")

    async def _feed_mode_change(self, feedMode: str):
        """Check current Feed mode"""
        controlOption = self.dataStore.get("mainControl")        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        try:
            self.feed_mode = FeedMode(feedMode)
        except ValueError:
            _LOGGER.warning(f"[{self.room}] Unknown feed mode: {feedMode}")
            return False
        
        if self.feed_mode == FeedMode.AUTOMATIC:
            await self._handle_automatic_mode()
        elif self.feed_mode == FeedMode.OWN_PLAN:
            await self._handle_own_plan_mode()
        elif self.feed_mode == FeedMode.DISABLED:
            await self._handle_disabled_mode()
    
        _LOGGER.info(f"[{self.room}] Feed mode changed to: {feedMode}")

    async def _handle_automatic_mode(self):
        """Handle automatic feed mode - uses predefined values based on plant stage"""
        await self._update_automatic_targets()
        await self._emit_vpd_targets()
        
        # Start automatic monitoring
        if self.is_initialized:
            await self._apply_feeding()

    async def _handle_own_plan_mode(self):
        """Handle own plan mode - uses user-defined min/max values"""
        # Load user-defined targets from dataStore
        self.target_ph = self.dataStore.getDeep("Feed.PH_Target") or 6.0
        self.target_ec = self.dataStore.getDeep("Feed.EC_Target") or 1.2
        
        # Load nutrient settings
        self.nutrients = {
            "A": self.dataStore.getDeep("Feed.Nut_A_ml") or 0.0,
            "B": self.dataStore.getDeep("Feed.Nut_B_ml") or 0.0,
            "C": self.dataStore.getDeep("Feed.Nut_C_ml") or 0.0,
            "W": self.dataStore.getDeep("Feed.Nut_W_ml") or 0.0,
            "X": self.dataStore.getDeep("Feed.Nut_X_ml") or 0.0,
            "Y": self.dataStore.getDeep("Feed.Nut_Y_ml") or 0.0,
            "PH": self.dataStore.getDeep("Feed.Nut_PH_ml") or 0.0,
        }
        
        # Emit VPD targets based on current stage
        await self._emit_vpd_targets()
        
        _LOGGER.info(f"[{self.room}] Own plan mode activated with pH={self.target_ph}, "
                    f"EC={self.target_ec}, Nutrients={self.nutrients}")

    async def _handle_disabled_mode(self):
        """Handle disabled mode - no automatic feeding"""
        self.target_ph = 0.0
        self.target_ec = 0.0
        self.nutrients = {}
        _LOGGER.info(f"[{self.room}] Feed mode disabled")

    async def _feed_mode_targets_change(self, data):
        """Handle changes to feed mode values with type detection"""
        if not isinstance(data, dict) or 'type' not in data or 'value' not in data:
            _LOGGER.error(f"[{self.room}] Invalid feed mode value change data: {data}")
            return
        
        param_type = data['type']
        new_value = data['value']

        # Mapping von kurzen Strings auf FeedParameterType
        mapper = {
            "ec_target": FeedParameterType.EC_TARGET,
            "ph_target": FeedParameterType.PH_TARGET,
            "a_ml": FeedParameterType.NUT_A_ML,
            "b_ml": FeedParameterType.NUT_B_ML,
            "c_ml": FeedParameterType.NUT_C_ML,
            "w_ml": FeedParameterType.NUT_W_ML,
            "x_ml": FeedParameterType.NUT_X_ML,
            "y_ml": FeedParameterType.NUT_Y_ML,
            "ph_ml": FeedParameterType.NUT_PH_ML,
        }
        
        try:
            # String in Enum umwandeln
            if isinstance(param_type, str):
                param_type = mapper.get(param_type)
                if not param_type:
                    _LOGGER.error(f"[{self.room}] Unknown feed parameter type: {data['type']}")
                    return
            
            # Handle different parameter types
            if param_type == FeedParameterType.EC_TARGET:
                await self._update_feed_parameter("EC_Target", float(new_value))
                self.target_ec = float(new_value)
                
            elif param_type == FeedParameterType.PH_TARGET:
                await self._update_feed_parameter("PH_Target", float(new_value))
                self.target_ph = float(new_value)
                
            elif param_type in [
                FeedParameterType.NUT_A_ML, FeedParameterType.NUT_B_ML, 
                FeedParameterType.NUT_C_ML, FeedParameterType.NUT_W_ML,
                FeedParameterType.NUT_X_ML, FeedParameterType.NUT_Y_ML,
                FeedParameterType.NUT_PH_ML
            ]:
                nutrient_key = param_type.value.split('_')[1]  # Extract A, B, C, etc.
                await self._update_feed_parameter(param_type.value, float(new_value))
                self.nutrients[nutrient_key] = float(new_value)
            
            # Nur anwenden, wenn im OWN_PLAN Modus
            if self.feed_mode == FeedMode.OWN_PLAN and self.is_initialized:
                await self._apply_feeding()
                
        except (ValueError, KeyError) as e:
            _LOGGER.error(f"[{self.room}] Error processing feed parameter change: {e}")

    async def _update_feed_parameter(self, parameter: str, value: float):
        """Update a feed parameter in dataStore"""
        current_value = self.dataStore.getDeep(f"Feed.{parameter}")
        
        if current_value != value:
            self.dataStore.setDeep(f"Feed.{parameter}", value)
            _LOGGER.info(f"[{self.room}] Updated {parameter}: {current_value} -> {value}")

    # Updated setter methods with type information
    async def _update_feed_mode(self, data):
        """Update OGB Feed Modes"""
        controlOption = self.dataStore.get("mainControl")        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return
        
        value = data.newState[0]
        await self._feed_mode_change(value)

    async def _update_feed_ec_target(self, data):
        """Update Hydro Feed EC"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.EC_TARGET,
            'value': new_value
        })

    async def _update_feed_ph_target(self, data):
        """Update Feed PH Target"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.PH_TARGET,
            'value': new_value
        })

    async def _update_feed_nut_a_ml(self, data):
        """Update Nutrient A ml"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.NUT_A_ML,
            'value': new_value
        })

    async def _update_feed_nut_b_ml(self, data):
        """Update Nutrient B ml"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.NUT_B_ML,
            'value': new_value
        })
            
    async def _update_feed_nut_c_ml(self, data):
        """Update Nutrient C ml"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.NUT_C_ML,
            'value': new_value
        })

    async def _update_feed_nut_w_ml(self, data):
        """Update Nutrient W ml"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.NUT_W_ML,
            'value': new_value
        })

    async def _update_feed_nut_x_ml(self, data):
        """Update Nutrient X ml"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.NUT_X_ML,
            'value': new_value
        })
            
    async def _update_feed_nut_y_ml(self, data):
        """Update Nutrient Y ml"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.NUT_Y_ML,
            'value': new_value
        })

    async def _update_feed_nut_ph_ml(self, data):
        """Update PH Nutrient ml"""
        new_value = float(data.newState[0])
        await self._feed_mode_targets_change({
            'type': FeedParameterType.NUT_PH_ML,
            'value': new_value
        })

    # Rest of the original methods remain the same...
    async def _check_if_feed_need(self, payload):
        """Handle incoming hydro values and check if feeding is needed"""
        try:
            # Only process if feeding is enabled
            if self.feed_mode == FeedMode.DISABLED:
                return
                
            # Update current measurements
            self.current_ec = float(getattr(payload, 'ecCurrent', 0.0) or 0.0)
            self.current_tds = float(getattr(payload, 'tdsCurrent', 0.0) or 0.0)
            self.current_ph = float(getattr(payload, 'phCurrent', 0.0) or 0.0)
            self.current_temp = float(getattr(payload, 'waterTemp', 0.0) or 0.0)
            self.current_oxi = float(getattr(payload, 'oxiCurrent', 0.0) or 0.0)
            self.current_sal = float(getattr(payload, 'salCurrent', 0.0) or 0.0)

            _LOGGER.info(f"[{self.room}] Incoming Hydro values: pH={self.current_ph:.2f}, "
                        f"EC={self.current_ec:.2f}, TDS={self.current_tds:.2f}, "
                        f"TEMP={self.current_temp:.2f}, OXI={self.current_oxi:.2f}, "
                        f"SAL={self.current_sal:.2f}")

            # Check if feeding is needed
            await self._check_ranges_and_feed()

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error processing hydro values: {str(e)}")

    async def _check_ranges_and_feed(self):
        """Check if current values are within tolerances and trigger actions if needed"""
        try:
            if self.feed_mode == FeedMode.DISABLED:
                return
                
            # Check if enough time has passed since last action
            current_time = datetime.now()
            if self.last_action_time and (current_time - self.last_action_time) < self.sensor_settle_time:
                _LOGGER.debug(f"[{self.room}] Waiting for sensor settle time")
                return

            # pH check
            if self.current_ph is not None and self.target_ph > 0:
                ph_diff = self.current_ph - self.target_ph
                if abs(ph_diff) > self.ph_toleration:
                    action = "pH_up_pump" if ph_diff < 0 else "pH_down_pump"
                    _LOGGER.warning(f"[{self.room}] pH out of range ({self.current_ph:.2f} vs target {self.target_ph:.2f}), "
                                  f"triggering {action}")
                    if await self.feed_action(action):
                        self.last_action_time = current_time
                        return

            # EC check
            if self.current_ec is not None and self.target_ec > 0:
                ec_diff = self.current_ec - self.target_ec
                if abs(ec_diff) > self.ec_toleration:
                    if ec_diff < 0:
                        _LOGGER.warning(f"[{self.room}] EC too low ({self.current_ec:.2f} vs target {self.target_ec:.2f}), "
                                      f"dosing nutrients")
                        if await self.feed_action("ec_up_pump"):
                            self.last_action_time = current_time
                            return

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in range check: {str(e)}")

    async def _on_feed_update(self, payload):
        """Handle new feed target values"""
        try:
            if self.feed_mode == FeedMode.DISABLED:
                return
                
            # Update target values with validation
            self.target_ph = float(payload.get("ph", self.target_ph))
            self.target_ec = float(payload.get("ec", self.target_ec))
            self.target_temp = float(payload.get("temp", self.target_temp))
            self.target_oxi = float(payload.get("oxi", self.target_oxi))
            self.nutrients = payload.get("nutrients", self.nutrients)

            _LOGGER.info(f"[{self.room}] New Feed targets: pH={self.target_ph:.2f}, "
                        f"EC={self.target_ec:.2f}, TEMP={self.target_temp:.2f}, "
                        f"OXI={self.target_oxi:.2f}, Nutrients={self.nutrients}")

            if self.is_initialized:
                await self._apply_feeding()

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error processing feed update: {str(e)}")

    async def _apply_feeding(self):
        """Apply dosing based on target values"""
        try:
            if self.feed_mode == FeedMode.DISABLED:
                return
                
            # Check if enough time has passed since last action
            current_time = datetime.now()
            if self.last_action_time and (current_time - self.last_action_time) < self.sensor_settle_time:
                return

            # pH adjustment
            current_ph = self.dataStore.get_value(f"OGB_HydroCurrentPH_{self.room}")
            if current_ph is not None and self.target_ph > 0:
                ph_diff = current_ph - self.target_ph
                if abs(ph_diff) > self.ph_toleration:
                    action = "pH_up_pump" if ph_diff < 0 else "pH_down_pump"
                    if await self.feed_action(action):
                        self.last_action_time = current_time
                        return

            # EC adjustment
            current_ec = self.dataStore.get_value(f"OGB_HydroCurrentEC_{self.room}")
            if current_ec is not None and self.target_ec > 0:
                if current_ec < self.target_ec - self.ec_toleration:
                    if await self.feed_action("ec_up_pump"):
                        self.last_action_time = current_time
                        return

            # Nutrient dosing
            for nutrient, amount in self.nutrients.items():
                if amount > 0:
                    if await self.feed_action(f"nutrient_{nutrient.lower()}", cycle=amount):
                        self.last_action_time = current_time
                        return

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error applying feeding: {str(e)}")

    async def feed_action(self, device_id: str, action: str = "on", cycle: float = 1.0) -> bool:
        """Execute a pump action with rate limiting"""
        try:
            # Check rate limiting
            last_action_time = self.last_pump_action.get(device_id)
            current_time = datetime.now()
            
            if last_action_time and (current_time - last_action_time) < self.min_interval_between_actions:
                _LOGGER.warning(f"[{self.room}] Pump action for {device_id} rate limited")
                return False

            pump_action = {
                "Device": device_id,
                "Action": action,
                "Cycle": float(cycle),
            }
            _LOGGER.warning(f"[{self.room}] FeedAction: {pump_action}")
            
            await self.PumpAction(pump_action)
            self.last_pump_action[device_id] = current_time
            return True

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in feed action for {device_id}: {str(e)}")
            return False

    async def PumpAction(self, pumpAction):
        """Execute pump action and emit events"""
        try:
            if isinstance(pumpAction, dict):
                dev = pumpAction.get("Device", "<unknown>")
                action = pumpAction.get("Action", "off")
                cycle = float(pumpAction.get("Cycle", 1.0))
            else:
                dev = pumpAction.Device
                action = pumpAction.Action
                cycle = float(pumpAction.Cycle)

            message = "Start Pump" if action == "on" else "Stop Pump"
            waterAction = OGBWaterAction(
                Name=self.room,
                Device=dev,
                Cycle=cycle,
                Action=action,
                Message=message
            )

            await self.eventManager.emit("LogForClient", waterAction, haEvent=True)

            if action == "on":
                await self.eventManager.emit("Increase Pump", pumpAction)
            elif action == "off":
                await self.eventManager.emit("Reduce Pump", pumpAction)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in PumpAction: {str(e)}")

    def _handleLogForClient(self, data):
        """Handle logging for client"""
        try:
            _LOGGER.info(f"[{self.room}] ClientLog: {data}")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in client log handling: {str(e)}")