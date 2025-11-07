import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import json

_LOGGER = logging.getLogger(__name__)

from .OGBDataClasses.OGBPublications import OGBWaterAction, OGBWaterPublication, OGBHydroAction

class ECUnit(Enum):
    """Unterstützte EC-Einheiten"""
    MS_CM = "mS/cm"      # Millisiemens pro cm (Standard, normalerweise 0-3)
    US_CM = "µS/cm"      # Mikrosiemens pro cm (normalerweise 0-3000)

class FeedMode(Enum):
    DISABLED = "Disabled"
    AUTOMATIC = "Automatic"
    OWN_PLAN = "Own-Plan"
    CONFIG = "Config"
    
    
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
    """Feed pump device names"""
    NUTRIENT_A = "switch.feedpump_a"       # Veg nutrient
    NUTRIENT_B = "switch.feedpump_b"       # Flower nutrient
    NUTRIENT_C = "switch.feedpump_c"       # Micro nutrient
    WATER = "switch.feedpump_w"            # Water pump
    CUSTOM_X = "switch.feedpump_x"         # Custom - free use
    CUSTOM_Y = "switch.feedpump_y"         # Custom - free use
    PH_DOWN = "switch.feedpump_pp"         # pH minus (pH-)
    PH_UP = "switch.feedpump_pm"           # pH plus (pH+)

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
    ml_per_second: float = 0.5
    min_dose_ml: float = 0.5
    max_dose_ml: float = 25.0

@dataclass
class PumpCalibration:
    """Pump calibration data"""
    pump_type: str
    target_dose_ml: float = 0.0
    actual_dose_ml: float = 0.0
    expected_ec_change: float = 0.0
    actual_ec_change: float = 0.0
    calibration_factor: float = 1.0
    last_calibration: Optional[datetime] = None
    calibration_count: int = 0
    
    def calculate_adjustment(self) -> float:
        """Berechnet Anpassungsfaktor basierend auf letztem Ergebnis"""
        if self.target_dose_ml <= 0 or self.actual_dose_ml <= 0:
            return 1.0
        # Wenn Istwert zu hoch war, nächstes Mal weniger dosieren
        adjustment = self.target_dose_ml / self.actual_dose_ml
        # Sanfte Anpassung (max 20% Änderung pro Kalibrierung)
        adjustment = max(0.8, min(1.2, adjustment))
        return adjustment
    
class OGBTankFeedManager:
    def __init__(self, hass, dataStore, eventManager, room: str):
        self.name = "OGB Tank Feed Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False
        
        # EC Unit Configuration
        self.ec_unit = ECUnit.MS_CM
        self.ec_raw_threshold = 100  # Wenn > 100, wahrscheinlich µS/cm
        
        # Feed Mode
        self.feed_mode = FeedMode.DISABLED
        self.current_plant_stage = self.dataStore.get("plantStage") or "LateVeg"
        
        # Plant stages configuration
        self.plantStages = self.dataStore.get("plantStages")
        
        # Pump configurations
        self.pump_config = PumpConfig()
        
        # Pump Kalibrierung
        self.pump_calibrations: Dict[str, PumpCalibration] = {
            pump.value: PumpCalibration(pump_type=pump.value)
            for pump in PumpType
        }
        
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
                nutrients={"A": 3.0, "B": 1.0, "C": 2.0}
            ),
            "MidVeg": FeedConfig(
                ph_target=5.8, 
                ec_target=1.6, 
                nutrients={"A": 3.0, "B": 1.0, "C": 2.0}
            ),
            "LateVeg": FeedConfig(
                ph_target=5.8, 
                ec_target=1.8, 
                nutrients={"A": 3.0, "B": 1.0, "C": 2.0}
            ),
            "EarlyFlower": FeedConfig(
                ph_target=6.0, 
                ec_target=2.0, 
                nutrients={"A": 1.0, "B": 3.0, "C": 2.0}
            ),
            "MidFlower": FeedConfig(
                ph_target=6.0, 
                ec_target=2.2, 
                nutrients={"A": 1.0, "B": 3.0, "C": 2.0}
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
        
        # EC Messwerte für Kalibrierung
        self.ec_before_dose: float = 0.0
        self.ec_after_dose: float = 0.0
        
        # Rate limiting and sensor settling
        self.last_action_time: Optional[datetime] = None
        self.sensor_settle_time: timedelta = timedelta(seconds=120)
        
        # Dosing calculation
        self.reservoir_volume_liters: float = 50.0
        
        # Calibration tracking
        self.calibration_mode: bool = False
        self.calibration_in_progress: str = ""  # Pump type being calibrated
        
        # Register event handlers
        self.eventManager.on("LogValidation", self._handleLogForClient)
        self.eventManager.on("FeedUpdate", self._on_feed_update)
        self.eventManager.on("CheckForFeed", self._check_if_feed_need)
        self.eventManager.on("FeedModeChange", self._feed_mode_change)
        self.eventManager.on("FeedModeValueChange", self._feed_mode_targets_change)
        self.eventManager.on("PlantStageChange", self._plant_stage_change)
        self.eventManager.on("CalibrateNutrientPump", self._start_pump_calibration)
                
        asyncio.create_task(self.init())

    async def init(self):
        """Initialize the feed manager with validation"""
        self.is_initialized = True
        
        # Load current plant stage
        self.current_plant_stage = self.dataStore.get("plantStage")
        
        # Load reservoir volume
        self.reservoir_volume_liters = self.dataStore.getDeep("Hydro.ReservoirVolume") or 100.0
        
        # Load EC Unit
        stored_unit = self.dataStore.getDeep("Hydro.EC_Unit")
        if stored_unit:
            try:
                self.ec_unit = ECUnit(stored_unit)
            except ValueError:
                self.ec_unit = ECUnit.MS_CM
        
        # Load calibration data
        await self._load_calibration_data()
        
        _LOGGER.info(f"[{self.room}] OGB Feed Manager initialized - Reservoir: {self.reservoir_volume_liters}L, EC Unit: {self.ec_unit.value}")

    def _normalize_ec_value(self, ec_raw: float) -> float:
        """
        Normalisiert EC-Wert auf mS/cm Standard
        
        Logik:
        1. Wenn ec_raw > 100 → sehr wahrscheinlich µS/cm, durch 1000 teilen
        2. Wenn ec_raw <= 100 → bereits mS/cm
        
        Args:
            ec_raw: Roher EC-Wert vom Sensor
            
        Returns:
            EC-Wert normalisiert auf mS/cm
        """
        if ec_raw <= 0:
            return 0.0
        
        # Hauptregel: Wenn Wert > 100, dann µS/cm (Sensoren geben das in diesem Bereich aus)
        # mS/cm Sensoren geben normalerweise 0-3 zurück
        if ec_raw > 100:
            normalized = ec_raw / 1000
            _LOGGER.warning(
                f"[{self.room}] EC converted: {ec_raw} µS/cm → {normalized:.2f} mS/cm "
                f"(unit config: {self.ec_unit.value})"
            )
            return normalized
        
        # ec_raw <= 100 → bereits normalisiert
        return ec_raw

    async def _load_calibration_data(self):
        """Kalibrierungsdaten aus DataStore laden"""
        try:
            calib_json = self.dataStore.getDeep("Hydro.PumpCalibrations")
            if calib_json:
                calib_data = json.loads(calib_json)
                for pump_type, data in calib_data.items():
                    if pump_type in self.pump_calibrations:
                        cal = self.pump_calibrations[pump_type]
                        cal.calibration_factor = data.get("calibration_factor", 1.0)
                        cal.calibration_count = data.get("calibration_count", 0)
                        _LOGGER.info(f"[{self.room}] Loaded calibration for {pump_type}: factor={cal.calibration_factor:.2f}")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error loading calibration data: {e}")

    async def _save_calibration_data(self):
        """Kalibrierungsdaten im DataStore speichern"""
        try:
            calib_data = {}
            for pump_type, cal in self.pump_calibrations.items():
                calib_data[pump_type] = {
                    "calibration_factor": cal.calibration_factor,
                    "calibration_count": cal.calibration_count,
                    "last_calibration": cal.last_calibration.isoformat() if cal.last_calibration else None
                }
            self.dataStore.setDeep("Hydro.PumpCalibrations", json.dumps(calib_data))
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error saving calibration data: {e}")

    async def _start_pump_calibration(self, pump_type: str):
        """
        Starte Kalibrierroutine für eine Pumpe
        
        Der Prozess:
        1. EC vor Dosierung messen
        2. Pumpe für definierte Zeit aktivieren
        3. Wartet auf Sensor-Settling
        4. EC nach Dosierung messen
        5. Berechnet tatsächliche Dosierung vs. erwartet
        6. Passt Kalibrierungsfaktor an
        """
        try:
            if pump_type not in self.pump_calibrations:
                _LOGGER.error(f"[{self.room}] Unknown pump type for calibration: {pump_type}")
                return
            
            if self.calibration_in_progress:
                _LOGGER.warning(f"[{self.room}] Calibration already in progress for {self.calibration_in_progress}")
                return
            
            self.calibration_in_progress = pump_type
            cal = self.pump_calibrations[pump_type]
            
            _LOGGER.warning(f"[{self.room}] Starting calibration for {pump_type}")
            
            # Step 1: Initial EC measurement
            self.ec_before_dose = self.current_ec
            _LOGGER.info(f"[{self.room}] Calibration EC before: {self.ec_before_dose:.2f}")
            
            # Step 2: Activate pump with standard dose
            target_dose_ml = 5.0  # Standard calibration dose
            run_time = self._calculate_dose_time(target_dose_ml)
            
            await self._activate_pump(pump_type, run_time, target_dose_ml)
            
            # Step 3: Wait for sensor settle
            _LOGGER.info(f"[{self.room}] Waiting {self.sensor_settle_time.total_seconds()}s for sensor settle...")
            await asyncio.sleep(self.sensor_settle_time.total_seconds())
            
            # Step 4: Measure EC after dose
            self.ec_after_dose = self.current_ec
            actual_ec_change = self.ec_after_dose - self.ec_before_dose
            _LOGGER.info(f"[{self.room}] Calibration EC after: {self.ec_after_dose:.2f}, EC change: {actual_ec_change:.2f}")
            
            # Step 5 & 6: Calculate and adjust calibration
            if actual_ec_change > 0:
                # Erwartete EC Änderung: ~0.5 pro 5ml in 100L Reservoir
                expected_ec_change = (target_dose_ml / self.reservoir_volume_liters) * 10
                
                new_factor = cal.calculate_adjustment()
                cal.calibration_factor *= new_factor
                cal.target_dose_ml = target_dose_ml
                cal.actual_dose_ml = (actual_ec_change / expected_ec_change) * target_dose_ml
                cal.expected_ec_change = expected_ec_change
                cal.actual_ec_change = actual_ec_change
                cal.last_calibration = datetime.now()
                cal.calibration_count += 1
                
                _LOGGER.warning(f"[{self.room}] Calibration result for {pump_type}:")
                _LOGGER.warning(f"  Target dose: {target_dose_ml}ml, Actual: {cal.actual_dose_ml:.2f}ml")
                _LOGGER.warning(f"  Expected EC change: {expected_ec_change:.2f}, Actual: {actual_ec_change:.2f}")
                _LOGGER.warning(f"  New calibration factor: {cal.calibration_factor:.3f} (count: {cal.calibration_count})")
                
                await self._save_calibration_data()
            else:
                _LOGGER.error(f"[{self.room}] Calibration failed - no EC change detected")
            
            self.calibration_in_progress = ""
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error during pump calibration: {e}")
            self.calibration_in_progress = ""

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
        if new_stage not in self.automaticFeedConfigs:
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
            _LOGGER.warning(f"[{self.room}] Plant stage not in config: {self.current_plant_stage}")
            return
            
        feed_config = self.automaticFeedConfigs[self.current_plant_stage]
        
        self.target_ph = feed_config.ph_target
        self.target_ec = feed_config.ec_target
        self.nutrients = feed_config.nutrients.copy()
        
        # Update dataStore
        self.dataStore.setDeep("Hydro.PH_Target", self.target_ph)
        self.dataStore.setDeep("Hydro.EC_Target", self.target_ec)
        
        for nutrient, amount in self.nutrients.items():
            self.dataStore.setDeep(f"Hydro.Nut_{nutrient}_ml", amount)
        
        _LOGGER.warning(f"[{self.room}] Auto targets for {self.current_plant_stage}: "
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
        elif self.feed_mode == FeedMode.CONFIG:
            await self._handle_disabled_mode()
        _LOGGER.info(f"[{self.room}] Feed mode changed to: {feedMode}")

    async def _handle_automatic_mode(self):
        """Handle automatic feed mode"""
        await self._update_automatic_targets()
        
        if self.is_initialized:
            await self._apply_feeding()

    async def _handle_own_plan_mode(self):
        """Handle own plan mode"""
        self.target_ph = self.dataStore.getDeep("Hydro.PH_Target") or 6.0
        self.target_ec = self.dataStore.getDeep("Hydro.EC_Target") or 1.2
        
        self.nutrients = {
            "A": self.dataStore.getDeep("Hydro.Nut_A_ml") or 0.0,
            "B": self.dataStore.getDeep("Hydro.Nut_B_ml") or 0.0,
            "C": self.dataStore.getDeep("Hydro.Nut_C_ml") or 0.0,
            "W": self.dataStore.getDeep("Hydro.Nut_W_ml") or 0.0,
            "X": self.dataStore.getDeep("Hydro.Nut_X_ml") or 0.0,
            "Y": self.dataStore.getDeep("Hydro.Nut_Y_ml") or 0.0,
            "PH": self.dataStore.getDeep("Hydro.Nut_PH_ml") or 0.0,
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
        current_value = self.dataStore.getDeep(f"Hydro.{parameter}")
        
        if current_value != value:
            self.dataStore.setDeep(f"Hydro.{parameter}", value)
            _LOGGER.info(f"[{self.room}] Updated {parameter}: {current_value} -> {value}")

    async def _check_if_feed_need(self, payload):
        """Handle incoming hydro sensor values"""
        try:
            if self.feed_mode == FeedMode.DISABLED:
                return
                
            ec_raw = float(getattr(payload, 'ecCurrent', 0.0) or 0.0)
            tds_raw = float(getattr(payload, 'tdsCurrent', 0.0) or 0.0)
            ph_raw = float(getattr(payload, 'phCurrent', 0.0) or 0.0)
            temp_raw = float(getattr(payload, 'waterTemp', 0.0) or 0.0)
            oxi_raw = float(getattr(payload, 'oxiCurrent', 0.0) or 0.0)
            sal_raw = float(getattr(payload, 'salCurrent', 0.0) or 0.0)
            
            # Normalize EC value
            self.current_ec = self._normalize_ec_value(ec_raw)
            self.current_tds = tds_raw
            self.current_ph = ph_raw
            self.current_temp = temp_raw
            self.current_oxi = oxi_raw
            self.current_sal = sal_raw

            _LOGGER.warning(f"[{self.room}] Hydro values: pH={self.current_ph:.2f}, "
                        f"EC={self.current_ec:.2f} mS/cm (raw: {ec_raw}), Temp={self.current_temp:.2f}°C")

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
                _LOGGER.debug(f"[{self.room}] Waiting for sensor settle ({self.sensor_settle_time.total_seconds()}s)")
                return

            # pH adjustment (always first priority)
            if self.current_ph > 0 and self.target_ph > 0:
                ph_diff = self.current_ph - self.target_ph
                _LOGGER.warning(f"[{self.room}] pH diff check -> target={self.target_ph}, current={self.current_ph}, diff={ph_diff:.2f}")

                if ph_diff > self.ph_toleration:
                    _LOGGER.warning(f"[{self.room}] pH too high ({self.current_ph:.2f} > {self.target_ph:.2f})")
                    if await self._dose_ph_down():
                        self.last_action_time = current_time
                        return
                        
                elif ph_diff < -self.ph_toleration:
                    _LOGGER.warning(f"[{self.room}] pH too low ({self.current_ph:.2f} < {self.target_ph:.2f})")
                    if await self._dose_ph_up():
                        self.last_action_time = current_time
                        return

            # EC adjustment (second priority)
            if self.current_ec > 0 and self.target_ec > 0:
                ec_diff = self.current_ec - self.target_ec
                
                if ec_diff < -self.ec_toleration:
                    _LOGGER.warning(f"[{self.room}] EC too low ({self.current_ec:.2f} < {self.target_ec:.2f}) EC-DIFF:{ec_diff:.2f}")
                    if await self._dose_nutrients():
                        self.last_action_time = current_time
                        return

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in range check: {e}")

    async def _dose_ph_down(self) -> bool:
        """Dose pH down solution"""
        try:
            dose_ml = 1.0
            cal = self.pump_calibrations.get(PumpType.PH_DOWN.value)
            
            # Anwende Kalibrierungsfaktor
            if cal:
                dose_ml *= cal.calibration_factor
            
            run_time = self._calculate_dose_time(dose_ml)
            
            if run_time > 0:
                _LOGGER.warning(f"[{self.room}] Try to activate {PumpType.PH_DOWN.value} with {dose_ml:.2f}ml and {run_time:.1f}s (calibration factor: {cal.calibration_factor:.3f})")
                return await self._activate_pump(PumpType.PH_DOWN.value, run_time, dose_ml)
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing pH down: {e}")
        return False

    async def _dose_ph_up(self) -> bool:
        """Dose pH up solution"""
        try:
            dose_ml = 1.0
            cal = self.pump_calibrations.get(PumpType.PH_UP.value)
            
            # Anwende Kalibrierungsfaktor
            if cal:
                dose_ml *= cal.calibration_factor
            
            run_time = self._calculate_dose_time(dose_ml)
            
            if run_time > 0:
                _LOGGER.warning(f"[{self.room}] Try to activate {PumpType.PH_UP.value} with {dose_ml:.2f}ml and {run_time:.1f}s (calibration factor: {cal.calibration_factor:.3f})")
                return await self._activate_pump(PumpType.PH_UP.value, run_time, dose_ml)
                
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
                    
                    # Anwende Kalibrierungsfaktor
                    pump_enum = getattr(PumpType, f"NUTRIENT_{nutrient}")
                    cal = self.pump_calibrations.get(pump_enum.value)
                    if cal:
                        total_ml *= cal.calibration_factor
                    
                    if total_ml > 0:
                        run_time = self._calculate_dose_time(total_ml)
                        
                        if await self._activate_pump(pump_enum.value, run_time, total_ml):
                            # Wait between nutrients
                            await asyncio.sleep(30)
                            
            return True
                    
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing nutrients: {e}")
        return False

    async def _activate_pump(self, pump_type: str, run_time: float, dose_ml: float) -> bool:
        """Activate a pump for specified time using Home Assistant switch entity"""
        try:
            _LOGGER.warning(f"[{self.room}] Activating {pump_type}: {dose_ml:.1f}ml for {run_time:.1f}s")
            
            # Turn ON pump via Home Assistant service call
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": pump_type},
                blocking=False
            )
            
            # Log action
            waterAction = OGBWaterAction(
                Name=self.room,
                Device=pump_type,
                Cycle=dose_ml,
                Action="on",
                Message=f"Dosing {dose_ml:.1f}ml"
            )
            await self.eventManager.emit("LogForClient", waterAction, haEvent=True)
            
            # Wait for dose time
            await asyncio.sleep(run_time)
            
            # Turn OFF pump via Home Assistant service call
            await self.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": pump_type},
                blocking=False
            )
            
            _LOGGER.info(f"[{self.room}] Pump {pump_type} completed")
            return True
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error activating pump {pump_type}: {e}")
            return False

    async def _activate_pump2(self, pump_type: PumpType, run_time: float, dose_ml: float) -> bool:
        """Activate a pump for specified time using PumpAction event (Alternative)"""
        try:
            device_id = pump_type

            _LOGGER.warning(f"[{self.room}] Activating {pump_type}: {dose_ml:.1f}ml for {run_time:.1f}s")
            
            pumpAction = OGBHydroAction(
                Name=self.room, 
                Action="on", 
                Device=device_id, 
                Cycle=True
            )
            await self.eventManager.emit("PumpAction", pumpAction)
            
            # Log action
            waterAction = OGBWaterAction(
                Name=self.room,
                Device=pump_type,
                Cycle=dose_ml,
                Action="on",
                Message=f"Dosing {dose_ml:.1f}ml"
            )
            await self.eventManager.emit("LogForClient", waterAction, haEvent=True)
            
            # Wait for dose time
            await asyncio.sleep(run_time)
            
            # Turn OFF pump via PumpAction event
            pumpAction = OGBHydroAction(
                Name=self.room, 
                Action="off", 
                Device=device_id, 
                Cycle=True
            )
            await self.eventManager.emit("PumpAction", pumpAction)
            
            return True
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error activating pump {pump_type}: {e}")
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