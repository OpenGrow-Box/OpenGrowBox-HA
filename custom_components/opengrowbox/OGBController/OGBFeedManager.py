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

class PumpType(Enum):
    """Feed pump types with their Home Assistant entity IDs"""
    NUTRIENT_A = "switch.feedpump_a"      # Veg nutrient
    NUTRIENT_B = "switch.feedpump_b"      # Flower nutrient
    NUTRIENT_C = "switch.feedpump_c"      # Micro nutrient
    WATER = "switch.feedpump_w"           # Water pump
    CUSTOM_X = "switch.feedpump_x"        # Custom - free use
    CUSTOM_Y = "switch.feedpump_y"        # Custom - free use
    PH_DOWN = "switch.feedpump_pp"        # pH minus (pH-)
    PH_UP = "switch.feedpump_pm"          # pH plus (pH+)

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

@dataclass
class PumpConfig:
    """Configuration for pump dosing"""
    ml_per_second: float = 1.0  # Flow rate in ml/s
    min_dose_ml: float = 0.5    # Minimum dose
    max_dose_ml: float = 50.0   # Maximum dose per action

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
        self.current_plant_stage = "EarlyVeg"
        
        # Plant stages configuration
        self.plantStages = self.dataStore.get("plantStages")
        
        # Pump configurations
        self.pump_config = PumpConfig()
        self.pump_states: Dict[str, bool] = {pump.value: False for pump in PumpType}
        
        # Automatic mode feed configs per stage
        self.automaticFeedConfigs: Dict[str, FeedConfig] = {
            "Germination": FeedConfig(
                ph_target=6.2, 
                ec_target=0.6, 
                nutrients={"A": 0.5, "B": 0.5, "C": 0.3}
            ),
            "Clones": FeedConfig(
                ph_target=6.0, 
                ec_target=0.8, 
                nutrients={"A": 1.0, "B": 1.0, "C": 0.5}
            ),
            "EarlyVeg": FeedConfig(
                ph_target=5.8, 
                ec_target=1.2, 
                nutrients={"A": 2.0, "B": 1.0, "C": 1.0}  # More A for veg
            ),
            "MidVeg": FeedConfig(
                ph_target=5.8, 
                ec_target=1.6, 
                nutrients={"A": 3.0, "B": 1.5, "C": 2.0}
            ),
            "LateVeg": FeedConfig(
                ph_target=5.8, 
                ec_target=1.8, 
                nutrients={"A": 3.5, "B": 2.0, "C": 2.5}
            ),
            "EarlyFlower": FeedConfig(
                ph_target=6.0, 
                ec_target=2.0, 
                nutrients={"A": 2.0, "B": 4.0, "C": 3.0}  # More B for flower
            ),
            "MidFlower": FeedConfig(
                ph_target=6.0, 
                ec_target=2.2, 
                nutrients={"A": 1.5, "B": 5.0, "C": 4.0}
            ),
            "LateFlower": FeedConfig(
                ph_target=6.2, 
                ec_target=1.8, 
                nutrients={"A": 1.0, "B": 3.0, "C": 2.0}
            ),
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
        self.sensor_settle_time: timedelta = timedelta(seconds=90)  # Increased for better accuracy
        self.last_action_time: Optional[datetime] = None
        
        # Dosing calculation
        self.reservoir_volume_liters: float = 100.0  # Default reservoir size
        
        # Register event handlers
        self.eventManager.on("LogValidation", self._handleLogForClient)
        self.eventManager.on("FeedUpdate", self._on_feed_update)
        self.eventManager.on("CheckForFeed", self._check_if_feed_need)
        self.eventManager.on("FeedModeChange", self._feed_mode_change)
        self.eventManager.on("FeedModeValueChange", self._feed_mode_targets_change)
        self.eventManager.on("PlantStageChange", self._plant_stage_change)
        self.eventManager.on("PumpStateChange", self._on_pump_state_change)
                
        asyncio.create_task(self.init())

    async def init(self):
        """Initialize the feed manager with validation"""
        self.is_initialized = True
        
        # Load current plant stage
        self.current_plant_stage = self.dataStore.getDeep("Plant.CurrentStage") or "EarlyVeg"
        
        # Load reservoir volume
        self.reservoir_volume_liters = self.dataStore.getDeep("Feed.ReservoirVolume") or 100.0
        
        # Initialize pump states from Home Assistant
        await self._sync_pump_states()
        
        _LOGGER.info(f"[{self.room}] OGB Feed Manager initialized - Reservoir: {self.reservoir_volume_liters}L")

    async def _sync_pump_states(self):
        """Sync pump states with Home Assistant"""
        try:
            for pump in PumpType:
                entity_id = pump.value
                state = self.hass.states.get(entity_id)
                if state:
                    self.pump_states[entity_id] = state.state == "on"
                    _LOGGER.debug(f"[{self.room}] Pump {entity_id}: {self.pump_states[entity_id]}")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error syncing pump states: {e}")

    async def _on_pump_state_change(self, data: Dict[str, Any]):
        """Handle pump state changes from Home Assistant"""
        try:
            entity_id = data.get("entity_id")
            new_state = data.get("new_state") == "on"
            
            if entity_id in self.pump_states:
                old_state = self.pump_states[entity_id]
                self.pump_states[entity_id] = new_state
                
                if old_state != new_state:
                    state_str = "ON" if new_state else "OFF"
                    _LOGGER.info(f"[{self.room}] Pump {entity_id} changed to {state_str}")
                    
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling pump state change: {e}")

    def _calculate_dose_time(self, ml_amount: float) -> float:
        """Calculate pump run time in seconds for desired ml amount"""
        if ml_amount < self.pump_config.min_dose_ml:
            return 0.0
        
        ml_amount = min(ml_amount, self.pump_config.max_dose_ml)
        return ml_amount / self.pump_config.ml_per_second

    def _calculate_nutrient_dose(self, nutrient_ml_per_liter: float) -> float:
        """Calculate actual ml dose based on reservoir volume"""
        return nutrient_ml_per_liter * self.reservoir_volume_liters

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
        
        # Update dataStore
        self.dataStore.setDeep("Feed.PH_Target", self.target_ph)
        self.dataStore.setDeep("Feed.EC_Target", self.target_ec)
        
        for nutrient, amount in self.nutrients.items():
            self.dataStore.setDeep(f"Feed.Nut_{nutrient}_ml", amount)
        
        _LOGGER.info(f"[{self.room}] Auto targets for {self.current_plant_stage}: "
                    f"pH={self.target_ph}, EC={self.target_ec}, Nutrients={self.nutrients}")

    async def _feed_mode_change(self, feedMode: str):
        """Handle feed mode changes"""
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
        """Handle automatic feed mode"""
        await self._update_automatic_targets()
        
        if self.is_initialized:
            await self._apply_feeding()

    async def _handle_own_plan_mode(self):
        """Handle own plan mode"""
        self.target_ph = self.dataStore.getDeep("Feed.PH_Target") or 6.0
        self.target_ec = self.dataStore.getDeep("Feed.EC_Target") or 1.2
        
        self.nutrients = {
            "A": self.dataStore.getDeep("Feed.Nut_A_ml") or 0.0,
            "B": self.dataStore.getDeep("Feed.Nut_B_ml") or 0.0,
            "C": self.dataStore.getDeep("Feed.Nut_C_ml") or 0.0,
            "W": self.dataStore.getDeep("Feed.Nut_W_ml") or 0.0,
            "X": self.dataStore.getDeep("Feed.Nut_X_ml") or 0.0,
            "Y": self.dataStore.getDeep("Feed.Nut_Y_ml") or 0.0,
            "PH": self.dataStore.getDeep("Feed.Nut_PH_ml") or 0.0,
        }
        
        _LOGGER.info(f"[{self.room}] Own plan mode: pH={self.target_ph}, "
                    f"EC={self.target_ec}, Nutrients={self.nutrients}")

    async def _handle_disabled_mode(self):
        """Handle disabled mode"""
        self.target_ph = 0.0
        self.target_ec = 0.0
        self.nutrients = {}
        _LOGGER.info(f"[{self.room}] Feed mode disabled")

    async def _feed_mode_targets_change(self, data):
        """Handle changes to feed parameters"""
        if not isinstance(data, dict) or 'type' not in data or 'value' not in data:
            _LOGGER.error(f"[{self.room}] Invalid feed data: {data}")
            return
        
        param_type = data['type']
        new_value = data['value']

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
            if isinstance(param_type, str):
                param_type = mapper.get(param_type)
                if not param_type:
                    _LOGGER.error(f"[{self.room}] Unknown parameter: {data['type']}")
                    return
            
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
                nutrient_key = param_type.value.split('_')[1]
                await self._update_feed_parameter(param_type.value, float(new_value))
                self.nutrients[nutrient_key] = float(new_value)
            
            if self.feed_mode == FeedMode.OWN_PLAN and self.is_initialized:
                await self._apply_feeding()
                
        except (ValueError, KeyError) as e:
            _LOGGER.error(f"[{self.room}] Error processing parameter: {e}")

    async def _update_feed_parameter(self, parameter: str, value: float):
        """Update feed parameter in dataStore"""
        current_value = self.dataStore.getDeep(f"Feed.{parameter}")
        
        if current_value != value:
            self.dataStore.setDeep(f"Feed.{parameter}", value)
            _LOGGER.info(f"[{self.room}] Updated {parameter}: {current_value} -> {value}")

    async def _check_if_feed_need(self, payload):
        """Handle incoming hydro sensor values"""
        try:
            if self.feed_mode == FeedMode.DISABLED:
                return
                
            self.current_ec = float(getattr(payload, 'ecCurrent', 0.0) or 0.0)
            self.current_tds = float(getattr(payload, 'tdsCurrent', 0.0) or 0.0)
            self.current_ph = float(getattr(payload, 'phCurrent', 0.0) or 0.0)
            self.current_temp = float(getattr(payload, 'waterTemp', 0.0) or 0.0)
            self.current_oxi = float(getattr(payload, 'oxiCurrent', 0.0) or 0.0)
            self.current_sal = float(getattr(payload, 'salCurrent', 0.0) or 0.0)

            _LOGGER.info(f"[{self.room}] Hydro values: pH={self.current_ph:.2f}, "
                        f"EC={self.current_ec:.2f}, Temp={self.current_temp:.2f}°C")

            await self._check_ranges_and_feed()

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error processing hydro values: {e}")

    async def _check_ranges_and_feed(self):
        """Check values and trigger dosing if needed"""
        try:
            if self.feed_mode == FeedMode.DISABLED:
                return
                
            current_time = datetime.now()
            if self.last_action_time and (current_time - self.last_action_time) < self.sensor_settle_time:
                _LOGGER.debug(f"[{self.room}] Waiting for sensor settle")
                return

            # pH adjustment (always first priority)
            if self.current_ph > 0 and self.target_ph > 0:
                ph_diff = self.current_ph - self.target_ph
                
                if ph_diff > self.ph_toleration:
                    # pH too high, need pH down
                    _LOGGER.warning(f"[{self.room}] pH too high ({self.current_ph:.2f} > {self.target_ph:.2f})")
                    if await self._dose_ph_down():
                        self.last_action_time = current_time
                        return
                        
                elif ph_diff < -self.ph_toleration:
                    # pH too low, need pH up
                    _LOGGER.warning(f"[{self.room}] pH too low ({self.current_ph:.2f} < {self.target_ph:.2f})")
                    if await self._dose_ph_up():
                        self.last_action_time = current_time
                        return

            # EC adjustment (second priority)
            if self.current_ec > 0 and self.target_ec > 0:
                ec_diff = self.current_ec - self.target_ec
                
                if ec_diff < -self.ec_toleration:
                    # EC too low, add nutrients
                    _LOGGER.warning(f"[{self.room}] EC too low ({self.current_ec:.2f} < {self.target_ec:.2f})")
                    if await self._dose_nutrients():
                        self.last_action_time = current_time
                        return

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in range check: {e}")

    async def _dose_ph_down(self) -> bool:
        """Dose pH down solution"""
        try:
            # Small incremental dose to avoid overshooting
            dose_ml = 5.0
            run_time = self._calculate_dose_time(dose_ml)
            
            if run_time > 0:
                return await self._activate_pump(PumpType.PH_DOWN, run_time, dose_ml)
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing pH down: {e}")
        return False

    async def _dose_ph_up(self) -> bool:
        """Dose pH up solution"""
        try:
            dose_ml = 5.0
            run_time = self._calculate_dose_time(dose_ml)
            
            if run_time > 0:
                return await self._activate_pump(PumpType.PH_UP, run_time, dose_ml)
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing pH up: {e}")
        return False

    async def _dose_nutrients(self) -> bool:
        """Dose nutrients based on current stage and targets"""
        try:
            # Dose in order: A, B, C with delays between
            for nutrient in ["A", "B", "C"]:
                if nutrient in self.nutrients and self.nutrients[nutrient] > 0:
                    ml_per_liter = self.nutrients[nutrient]
                    total_ml = self._calculate_nutrient_dose(ml_per_liter)
                    
                    if total_ml > 0:
                        pump_type = getattr(PumpType, f"NUTRIENT_{nutrient}")
                        run_time = self._calculate_dose_time(total_ml)
                        
                        if await self._activate_pump(pump_type, run_time, total_ml):
                            # Wait between nutrients
                            await asyncio.sleep(5)
                            
            return True
                    
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing nutrients: {e}")
        return False

    async def _activate_pump(self, pump_type: PumpType, run_time: float, dose_ml: float) -> bool:
        """Activate a pump for specified time"""
        try:
            entity_id = pump_type.value
            
            # Check rate limiting
            last_action = self.last_pump_action.get(entity_id)
            if last_action and (datetime.now() - last_action) < self.min_interval_between_actions:
                _LOGGER.warning(f"[{self.room}] Pump {entity_id} rate limited")
                return False

            _LOGGER.info(f"[{self.room}] Activating {pump_type.name}: {dose_ml:.1f}ml for {run_time:.1f}s")
            
            # Turn on pump
            await self.hass.services.async_call(
                "switch", "turn_on",
                {"entity_id": entity_id}
            )
            
            # Log action
            waterAction = OGBWaterAction(
                Name=self.room,
                Device=pump_type.name,
                Cycle=dose_ml,
                Action="on",
                Message=f"Dosing {dose_ml:.1f}ml"
            )
            await self.eventManager.emit("LogForClient", waterAction, haEvent=True)
            
            # Wait for dose time
            await asyncio.sleep(run_time)
            
            # Turn off pump
            await self.hass.services.async_call(
                "switch", "turn_off",
                {"entity_id": entity_id}
            )
            
            self.last_pump_action[entity_id] = datetime.now()
            return True
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error activating pump {pump_type.name}: {e}")
            return False

    async def _on_feed_update(self, payload):
        """Handle feed target updates"""
        try:
            if self.feed_mode == FeedMode.DISABLED:
                return
                
            self.target_ph = float(payload.get("ph", self.target_ph))
            self.target_ec = float(payload.get("ec", self.target_ec))
            self.target_temp = float(payload.get("temp", self.target_temp))
            self.target_oxi = float(payload.get("oxi", self.target_oxi))
            self.nutrients = payload.get("nutrients", self.nutrients)

            _LOGGER.info(f"[{self.room}] New targets: pH={self.target_ph:.2f}, "
                        f"EC={self.target_ec:.2f}")

            if self.is_initialized:
                await self._apply_feeding()

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error processing feed update: {e}")

    async def _apply_feeding(self):
        """Apply feeding logic"""
        await self._check_ranges_and_feed()

    def _handleLogForClient(self, data):
        """Handle logging for client"""
        try:
            _LOGGER.info(f"[{self.room}] ClientLog: {data}")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in client log: {e}")