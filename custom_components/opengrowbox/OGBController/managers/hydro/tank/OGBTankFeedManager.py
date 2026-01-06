import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import json

_LOGGER = logging.getLogger(__name__)

from ....data.OGBDataClasses.OGBPublications import OGBWaterAction, OGBWaterPublication, OGBHydroAction

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


# Plant-specific nutrient profiles for hydroponic feeding
# Default: Cannabis - can be changed via dataStore.setDeep("Hydro.PlantType", "Tomato")
PLANT_TYPE_PROFILES: Dict[str, Dict[str, FeedConfig]] = {
    "Cannabis": {
        "Germination": FeedConfig(ph_target=6.0, ec_target=0.4, nutrients={"A": 0.4, "B": 0.3, "C": 0.2}),
        "Clones": FeedConfig(ph_target=6.0, ec_target=0.6, nutrients={"A": 0.6, "B": 0.4, "C": 0.3}),
        "EarlyVeg": FeedConfig(ph_target=5.8, ec_target=1.2, nutrients={"A": 2.0, "B": 1.0, "C": 0.8}),
        "MidVeg": FeedConfig(ph_target=5.8, ec_target=1.6, nutrients={"A": 2.5, "B": 1.2, "C": 1.0}),
        "LateVeg": FeedConfig(ph_target=5.8, ec_target=1.8, nutrients={"A": 2.5, "B": 1.5, "C": 1.0}),
        "EarlyFlower": FeedConfig(ph_target=6.0, ec_target=2.0, nutrients={"A": 1.5, "B": 2.5, "C": 1.2}),
        "MidFlower": FeedConfig(ph_target=6.0, ec_target=2.2, nutrients={"A": 1.2, "B": 3.0, "C": 1.5}),
        "LateFlower": FeedConfig(ph_target=6.0, ec_target=1.8, nutrients={"A": 1.0, "B": 2.5, "C": 1.2}),
    },
    "Tomato": {
        "Germination": FeedConfig(ph_target=6.0, ec_target=0.5, nutrients={"A": 0.5, "B": 0.3, "C": 0.2}),
        "Clones": FeedConfig(ph_target=6.0, ec_target=0.8, nutrients={"A": 0.8, "B": 0.5, "C": 0.3}),
        "EarlyVeg": FeedConfig(ph_target=6.0, ec_target=1.5, nutrients={"A": 2.0, "B": 1.0, "C": 0.8}),
        "MidVeg": FeedConfig(ph_target=6.0, ec_target=2.0, nutrients={"A": 2.5, "B": 1.5, "C": 1.0}),
        "LateVeg": FeedConfig(ph_target=6.0, ec_target=2.2, nutrients={"A": 2.5, "B": 1.8, "C": 1.0}),
        "EarlyFlower": FeedConfig(ph_target=6.2, ec_target=2.5, nutrients={"A": 1.5, "B": 2.5, "C": 1.2}),
        "MidFlower": FeedConfig(ph_target=6.2, ec_target=2.8, nutrients={"A": 1.5, "B": 3.0, "C": 1.5}),
        "LateFlower": FeedConfig(ph_target=6.2, ec_target=2.2, nutrients={"A": 1.0, "B": 2.5, "C": 1.0}),
    },
    "General": {
        "Germination": FeedConfig(ph_target=6.0, ec_target=0.5, nutrients={"A": 0.5, "B": 0.3, "C": 0.2}),
        "Clones": FeedConfig(ph_target=6.0, ec_target=0.8, nutrients={"A": 0.8, "B": 0.5, "C": 0.3}),
        "EarlyVeg": FeedConfig(ph_target=6.0, ec_target=1.2, nutrients={"A": 1.5, "B": 0.8, "C": 0.5}),
        "MidVeg": FeedConfig(ph_target=6.0, ec_target=1.5, nutrients={"A": 2.0, "B": 1.0, "C": 0.7}),
        "LateVeg": FeedConfig(ph_target=6.0, ec_target=1.8, nutrients={"A": 2.2, "B": 1.2, "C": 0.8}),
        "EarlyFlower": FeedConfig(ph_target=6.2, ec_target=2.0, nutrients={"A": 1.5, "B": 2.0, "C": 1.0}),
        "MidFlower": FeedConfig(ph_target=6.2, ec_target=2.2, nutrients={"A": 1.5, "B": 2.5, "C": 1.2}),
        "LateFlower": FeedConfig(ph_target=6.2, ec_target=1.8, nutrients={"A": 1.2, "B": 2.0, "C": 0.8}),
    },
}

# Default plant type for feed profiles
DEFAULT_PLANT_TYPE = "Cannabis"


class OGBTankFeedManager:
    def __init__(self, hass, dataStore, eventManager, room: str):
        self.name = "OGB Tank Feed Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = eventManager
        self.is_initialized = False
        
        # EC Unit Configuration
        self.ec_unit = ECUnit.MS_CM
        self.ec_raw_threshold = 100  # Wenn > 100, wahrscheinlich µS/cm
        
        # Feed Mode
        self.feed_mode = FeedMode.DISABLED
        self.current_plant_stage = self.data_store.get("plantStage") or "LateVeg"
        
        # Plant stages configuration
        self.plantStages = self.data_store.get("plantStages")
        
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

        # Load user-configured nutrient values
        self._load_user_nutrient_configurations()

    def _load_user_nutrient_configurations(self):
        """Load user-configured nutrient values from datastore"""
        try:
            # Load nutrient concentrations (ml per liter)
            self.nutrients = {
                "A": float(self.data_store.getDeep("Hydro.Nut_A_ml", 0.0) or 0.0),
                "B": float(self.data_store.getDeep("Hydro.Nut_B_ml", 0.0) or 0.0),
                "C": float(self.data_store.getDeep("Hydro.Nut_C_ml", 0.0) or 0.0),
                "W": float(self.data_store.getDeep("Hydro.Nut_W_ml", 0.0) or 0.0),
                "X": float(self.data_store.getDeep("Hydro.Nut_X_ml", 0.0) or 0.0),
                "Y": float(self.data_store.getDeep("Hydro.Nut_Y_ml", 0.0) or 0.0),
                "PH": float(self.data_store.getDeep("Hydro.Nut_PH_ml", 0.0) or 0.0),
            }

            # Remove zero values to avoid unnecessary pumping
            self.nutrients = {k: v for k, v in self.nutrients.items() if v > 0}

            _LOGGER.info(f"[{self.room}] Loaded user nutrient configurations: {self.nutrients}")

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error loading user nutrient configurations: {e}")
            # Fallback to empty dict
            self.nutrients = {}

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
        self.event_manager.on("LogValidation", self._handleLogForClient)
        self.event_manager.on("FeedUpdate", self._on_feed_update)
        self.event_manager.on("CheckForFeed", self._check_if_feed_need)
        self.event_manager.on("FeedModeChange", self._feed_mode_change)
        self.event_manager.on("FeedModeValueChange", self._feed_mode_targets_change)
        self.event_manager.on("PlantStageChange", self._plant_stage_change)
        self.event_manager.on("CalibrateNutrientPump", self._start_pump_calibration)

        # Proportional dosing events
        self.event_manager.on("DoseNutrients", self._dose_nutrients_proportional)
        self.event_manager.on("DosePHDown", self._dose_ph_down_proportional)
        self.event_manager.on("DosePHUp", self._dose_ph_up_proportional)

        # Initialize specialized managers
        from .OGBFeedLogicManager import OGBFeedLogicManager
        from .OGBFeedParameterManager import OGBFeedParameterManager

        self.feed_logic_manager = OGBFeedLogicManager(self.room, self.data_store, self.event_manager)
        self.feed_parameter_manager = OGBFeedParameterManager(self.room, self.data_store, self.event_manager)

        asyncio.create_task(self.init())

    async def init(self):
        """Initialize the feed manager with validation"""
        self.is_initialized = True
        
        # Load current plant stage
        self.current_plant_stage = self.data_store.get("plantStage")
        
        # Load reservoir volume
        self.reservoir_volume_liters = self.data_store.getDeep("Hydro.ReservoirVolume") or 100.0
        
        # Load EC Unit
        stored_unit = self.data_store.getDeep("Hydro.EC_Unit")
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
            calib_json = self.data_store.getDeep("Hydro.PumpCalibrations")
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
            self.data_store.setDeep("Hydro.PumpCalibrations", json.dumps(calib_data))
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
            if actual_ec_change > 0 and cal is not None:
                # For calibration, we use a configurable concentration factor
                # Default assumes 1ml of nutrient concentrate gives ~0.1 EC change in reservoir
                concentration_factor = self.data_store.getDeep("Hydro.CalibrationConcentrationFactor", 10.0)

                expected_ec_change = (target_dose_ml / self.reservoir_volume_liters) * concentration_factor

                cal.actual_ec_change = actual_ec_change
                cal.expected_ec_change = expected_ec_change
                cal.target_dose_ml = target_dose_ml
                cal.actual_dose_ml = (actual_ec_change / expected_ec_change) * target_dose_ml if expected_ec_change > 0 else target_dose_ml

                # Adjust calibration factor based on delivery accuracy
                new_factor = cal.calculate_adjustment()
                cal.calibration_factor *= new_factor
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

    async def _plant_stage_change(self, new_stage):
        """Handle plant stage changes.
        
        Args:
            new_stage: Either a string (stage name) or a dict with stage info.
                       Dict formats supported:
                       - {"new_stage": "...", "old_stage": "...", "room": "..."}
                       - {"plantStage": "...", "room": "...", "source": "..."}
                       - {"stage": "..."}
        """
        # Handle both string and dict payloads for backwards compatibility
        if isinstance(new_stage, dict):
            # Extract stage from dict - check multiple possible keys
            stage_value = (
                new_stage.get("new_stage") or 
                new_stage.get("plantStage") or 
                new_stage.get("stage")
            )
        else:
            stage_value = new_stage
        
        if not stage_value or stage_value not in self.automaticFeedConfigs:
            _LOGGER.warning(f"[{self.room}] Unknown or missing plant stage: {stage_value}")
            return
            
        self.current_plant_stage = stage_value
        
        # If in automatic mode, update targets based on new stage
        if self.feed_mode == FeedMode.AUTOMATIC:
            await self._update_automatic_targets()
                  
        _LOGGER.info(f"[{self.room}] Plant stage changed to: {stage_value}")

    async def _update_automatic_targets(self):
        """Update feed targets based on current plant stage in automatic mode"""
        if self.current_plant_stage not in self.automaticFeedConfigs:
            _LOGGER.warning(f"[{self.room}] Plant stage not in config: {self.current_plant_stage}")
            return
            
        feed_config = self.automaticFeedConfigs[self.current_plant_stage]

        self.target_ph = feed_config.ph_target
        self.target_ec = feed_config.ec_target

        # Preserve user-configured nutrient values, use defaults for unconfigured nutrients
        default_nutrients = feed_config.nutrients.copy()
        updated_nutrients = {}

        for nutrient in ["A", "B", "C", "W", "X", "Y", "PH"]:
            # Use user-configured value if available (> 0), otherwise use default
            user_value = self.nutrients.get(nutrient, 0.0)
            if user_value > 0:
                updated_nutrients[nutrient] = user_value
            elif nutrient in default_nutrients:
                updated_nutrients[nutrient] = default_nutrients[nutrient]
            else:
                updated_nutrients[nutrient] = 0.0

        self.nutrients = updated_nutrients

        # Update dataStore
        self.data_store.setDeep("Hydro.PH_Target", self.target_ph)
        self.data_store.setDeep("Hydro.EC_Target", self.target_ec)

        for nutrient, amount in self.nutrients.items():
            self.data_store.setDeep(f"Hydro.Nut_{nutrient}_ml", amount)

        _LOGGER.warning(f"[{self.room}] Auto targets for {self.current_plant_stage}: "
                    f"pH={self.target_ph}, EC={self.target_ec}, Nutrients={self.nutrients} "
                    f"(user-configured values preserved)")

    async def _feed_mode_change(self, feedMode: str):
        """Handle feed mode changes - delegate to specialized manager"""
        controlOption = self.data_store.get("mainControl")
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False

        # Delegate to specialized feed logic manager
        await self.feed_logic_manager.handle_feed_mode_change(feedMode)

        # Keep local reference in sync
        try:
            self.feed_mode = FeedMode(feedMode)
        except ValueError:
            _LOGGER.warning(f"[{self.room}] Unknown feed mode: {feedMode}")
            return False



    async def _feed_mode_targets_change(self, data):
        """Handle changes to feed parameters - delegate to parameter manager"""
        await self.feed_parameter_manager.handle_feed_mode_targets_change(data)



    async def _check_if_feed_need(self, payload):
        """Handle incoming hydro sensor values - delegate to feed logic manager"""
        try:
            if self.feed_mode == FeedMode.DISABLED:
                return

            # Extract sensor data
            sensor_data = {
                'ecCurrent': float(getattr(payload, 'ecCurrent', 0.0) or 0.0),
                'tdsCurrent': float(getattr(payload, 'tdsCurrent', 0.0) or 0.0),
                'phCurrent': float(getattr(payload, 'phCurrent', 0.0) or 0.0),
                'waterTemp': float(getattr(payload, 'waterTemp', 0.0) or 0.0),
                'oxiCurrent': float(getattr(payload, 'oxiCurrent', 0.0) or 0.0),
                'salCurrent': float(getattr(payload, 'salCurrent', 0.0) or 0.0),
            }

            # Normalize and store current values
            self.current_ec = self._normalize_ec_value(sensor_data['ecCurrent'])
            self.current_tds = sensor_data['tdsCurrent']
            self.current_ph = sensor_data['phCurrent']
            self.current_temp = sensor_data['waterTemp']
            self.current_oxi = sensor_data['oxiCurrent']
            self.current_sal = sensor_data['salCurrent']

            _LOGGER.warning(f"[{self.room}] Hydro values: pH={self.current_ph:.2f}, "
                        f"EC={self.current_ec:.2f} mS/cm, Temp={self.current_temp:.2f}°C")

            # Delegate feed decision to logic manager
            await self.feed_logic_manager.handle_feed_update(sensor_data)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error processing hydro values: {e}")



    async def _dose_ph_down(self) -> bool:
        """Dose pH down solution"""
        try:
            dose_ml = 1.0
            cal = self.pump_calibrations.get(PumpType.PH_DOWN.value)
            
            # Anwende Kalibrierungsfaktor
            calibration_factor = cal.calibration_factor if cal else 1.0
            dose_ml *= calibration_factor

            run_time = self._calculate_dose_time(dose_ml)

            if run_time > 0:
                _LOGGER.warning(f"[{self.room}] Try to activate {PumpType.PH_DOWN.value} with {dose_ml:.2f}ml and {run_time:.1f}s (calibration factor: {calibration_factor:.3f})")
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
            calibration_factor = cal.calibration_factor if cal else 1.0
            dose_ml *= calibration_factor

            run_time = self._calculate_dose_time(dose_ml)

            if run_time > 0:
                _LOGGER.warning(f"[{self.room}] Try to activate {PumpType.PH_UP.value} with {dose_ml:.2f}ml and {run_time:.1f}s (calibration factor: {calibration_factor:.3f})")
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

    async def _dose_nutrients_proportional(self, data: Dict[str, Any]):
        """Handle proportional nutrient dosing requests."""
        try:
            dose_ml = data.get('dose_ml', 0.0)
            if dose_ml <= 0:
                return

            _LOGGER.info(f"[{self.room}] Starting proportional nutrient dosing: {dose_ml:.2f}ml per nutrient")

            # Dose each nutrient proportionally
            await self._dose_nutrients_with_amount(dose_ml)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in proportional nutrient dosing: {e}")

    async def _dose_ph_down_proportional(self, data: Dict[str, Any]):
        """Handle proportional pH down dosing requests."""
        try:
            dose_ml = data.get('dose_ml', 0.0)
            if dose_ml <= 0:
                return

            _LOGGER.info(f"[{self.room}] Starting proportional pH down dosing: {dose_ml:.2f}ml")

            # Use the existing pH down method but with custom amount
            await self._dose_ph_down_with_amount(dose_ml)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in proportional pH down dosing: {e}")

    async def _dose_ph_up_proportional(self, data: Dict[str, Any]):
        """Handle proportional pH up dosing requests."""
        try:
            dose_ml = data.get('dose_ml', 0.0)
            if dose_ml <= 0:
                return

            _LOGGER.info(f"[{self.room}] Starting proportional pH up dosing: {dose_ml:.2f}ml")

            # Use the existing pH up method but with custom amount
            await self._dose_ph_up_with_amount(dose_ml)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in proportional pH up dosing: {e}")

    async def _dose_nutrients_with_amount(self, dose_ml: float) -> bool:
        """Dose nutrients with a specific amount per nutrient type."""
        try:
            # Dose in order: A, B, C with delays between
            nutrients_to_dose = ['A', 'B', 'C']

            for nutrient in nutrients_to_dose:
                if nutrient in self.nutrients and self.nutrients[nutrient] > 0:
                    # Apply calibration factor
                    pump_enum = getattr(PumpType, f"NUTRIENT_{nutrient}")
                    cal = self.pump_calibrations.get(pump_enum.value)
                    calibrated_dose = dose_ml * (cal.calibration_factor if cal else 1.0)

                    if calibrated_dose > 0:
                        run_time = self._calculate_dose_time(calibrated_dose)

                        if await self._activate_pump(pump_enum.value, run_time, calibrated_dose):
                            # Wait between nutrients to prevent mixing issues
                            await asyncio.sleep(30)
                        else:
                            _LOGGER.warning(f"[{self.room}] Failed to dose nutrient {nutrient}")

            return True

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing nutrients with amount: {e}")
            return False

    async def _dose_ph_down_with_amount(self, dose_ml: float) -> bool:
        """Dose pH down with a specific amount."""
        try:
            # Apply calibration factor
            cal = self.pump_calibrations.get(PumpType.PH_DOWN.value)
            calibrated_dose = dose_ml * (cal.calibration_factor if cal else 1.0)

            run_time = self._calculate_dose_time(calibrated_dose)

            if run_time > 0:
                _LOGGER.info(f"[{self.room}] Dosing {calibrated_dose:.2f}ml pH down (calibration factor: {cal.calibration_factor if cal else 1.0:.3f})")
                return await self._activate_pump(PumpType.PH_DOWN.value, run_time, calibrated_dose)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing pH down with amount: {e}")
        return False

    async def _dose_ph_up_with_amount(self, dose_ml: float) -> bool:
        """Dose pH up with a specific amount."""
        try:
            # Apply calibration factor
            cal = self.pump_calibrations.get(PumpType.PH_UP.value)
            calibrated_dose = dose_ml * (cal.calibration_factor if cal else 1.0)

            run_time = self._calculate_dose_time(calibrated_dose)

            if run_time > 0:
                _LOGGER.info(f"[{self.room}] Dosing {calibrated_dose:.2f}ml pH up (calibration factor: {cal.calibration_factor if cal else 1.0:.3f})")
                return await self._activate_pump(PumpType.PH_UP.value, run_time, calibrated_dose)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing pH up with amount: {e}")
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
                Cycle=str(dose_ml),
                Action="on",
                Message=f"Dosing {dose_ml:.1f}ml"
            )
            await self.event_manager.emit("LogForClient", waterAction, haEvent=True)
            
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
                Device=str(pump_type),
                Cycle=str(run_time)
            )
            await self.event_manager.emit("PumpAction", pumpAction)
            
            # Log action
            waterAction = OGBWaterAction(
                Name=self.room,
                Device=str(pump_type),
                Cycle=str(dose_ml),
                Action="on",
                Message=f"Dosing {dose_ml:.1f}ml"
            )
            await self.event_manager.emit("LogForClient", waterAction, haEvent=True)
            
            # Wait for dose time
            await asyncio.sleep(run_time)
            
            # Turn OFF pump via PumpAction event
            pumpAction = OGBHydroAction(
                Name=self.room,
                Action="off",
                Device=str(pump_type),
                Cycle=str(run_time)
            )
            await self.event_manager.emit("PumpAction", pumpAction)
            
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
        """Apply feeding logic - delegate to feed logic manager"""
        await self.feed_logic_manager._check_ranges_and_feed()

    def _handleLogForClient(self, data):
        """Handle logging for client"""
        try:
            _LOGGER.info(f"[{self.room}] ClientLog: {data}")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in client log: {e}")