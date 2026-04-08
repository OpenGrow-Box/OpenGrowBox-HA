import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import json

_LOGGER = logging.getLogger(__name__)

from ....data.OGBDataClasses.OGBPublications import OGBWaterAction, OGBWaterPublication, OGBHydroAction
from ....managers.OGBNotifyManager import OGBNotificator
from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBReservoirManager import OGBReservoirManager

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

        # AMBIENT ROOM CHECK: Ambient rooms don't use Tank Feed
        if self.room.lower() == "ambient":
            _LOGGER.debug(f"{self.room}: Tank Feed Manager disabled - ambient room")
            return
        
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
        
        # EC Tracking für Feed-Zyklen (nicht Kalibrierung)
        self.feed_ec_before: float = 0.0
        self.feed_ec_after: float = 0.0
        self.feed_ec_added: float = 0.0
        self.feed_ec_consumed: float = 0.0
        self.feed_history: list = []  # Liste der letzten Feed-Zyklen
        
        # Rate limiting and sensor settling
        self.last_action_time: Optional[datetime] = None
        self.sensor_settle_time: timedelta = timedelta(seconds=120)
        
        # Dosing calculation
        self.reservoir_volume_liters: float = 50.0
        
        # Calibration tracking
        self.calibration_mode: bool = False
        self.calibration_in_progress: str = ""  # Pump type being calibrated
        
        # Notification manager for critical alerts
        self.notificator: Optional[OGBNotificator] = None
        
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
        from .OGBReservoirManager import OGBReservoirManager

        self.feed_logic_manager = OGBFeedLogicManager(self.room, self.data_store, self.event_manager)
        self.feed_parameter_manager = OGBFeedParameterManager(self.room, self.data_store, self.event_manager)
        
        # Reservoir manager for actual volume calculation
        self.reservoir_manager: Optional[OGBReservoirManager] = None

        asyncio.create_task(self.init())

    async def init(self):
        """Initialize the feed manager with validation"""
        self.is_initialized = True
        
        # Initialize notification manager
        self.notificator = OGBNotificator(self.hass, self.room)
        
        # Initialize reservoir manager
        self.reservoir_manager = OGBReservoirManager(
            self.hass,
            self.data_store,
            self.event_manager,
            self.room,
            self.notificator
        )
        await self.reservoir_manager.init()
        
        # Load current plant stage
        self.current_plant_stage = self.data_store.get("plantStage")
        
        # Load reservoir volume from dataStore (or use default 50L)
        reservoir_volume = self.data_store.getDeep("Hydro.ReservoirVolume")
        if reservoir_volume is None or reservoir_volume == 0:
            self.reservoir_volume_liters = 50.0  # Default 50L if not configured
        else:
            self.reservoir_volume_liters = float(reservoir_volume)
        
        # Load EC Unit
        stored_unit = self.data_store.getDeep("Hydro.EC_Unit")
        if stored_unit:
            try:
                self.ec_unit = ECUnit(stored_unit)
            except ValueError:
                self.ec_unit = ECUnit.MS_CM
        
        # Load calibration data
        await self._load_calibration_data()
        
        # Load feed history
        await self._load_feed_history()
        
        # Load pump flow rates (ml/min)
        self._load_pump_flow_rates()
        
        # Load nutrient concentrations (ml/L)
        self._load_nutrient_concentrations()
        
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

    async def _load_feed_history(self):
        """Load feed history from dataStore"""
        try:
            history_json = self.data_store.getDeep("Hydro.FeedHistory")
            if history_json:
                self.feed_history = json.loads(history_json)
                _LOGGER.info(f"[{self.room}] Loaded {len(self.feed_history)} feed history entries")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error loading feed history: {e}")
            self.feed_history = []

    async def _send_notification(self, level: str, message: str, title: str = None):
        """Send notification via notificator"""
        if not self.notificator:
            return
        
        if title is None:
            title = f"OGB TankFeed - {self.room}"
        
        try:
            if level == "critical":
                await self.notificator.critical(message=message, title=title)
            elif level == "warning":
                await self.notificator.warning(message=message, title=title)
            else:
                await self.notificator.info(message=message, title=title)
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error sending notification: {e}")

    def _log_to_client(self, message: str, log_type: str = "INFO", extra_data: dict = None):
        """Send clean log message to client via LogForClient"""
        try:
            log_data = {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": message,
            }
            if extra_data:
                log_data.update(extra_data)
            
            # Use asyncio.create_task to not block
            asyncio.create_task(
                self.event_manager.emit("LogForClient", log_data, haEvent=True, debug_type=log_type)
            )
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error logging to client: {e}")

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
                
                # Get actual water volume based on current reservoir level
                reservoir_level_percent = self.data_store.getDeep("Hydro.ReservoirLevel", 100.0)
                actual_water_volume = self.reservoir_volume_liters * (reservoir_level_percent / 100.0)
                
                concentration_factor = self.data_store.getDeep("Hydro.CalibrationConcentrationFactor", 10.0)

                expected_ec_change = (target_dose_ml / actual_water_volume) * concentration_factor

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
                await self.event_manager.emit("LogForClient", {
                    "Name": self.room,
                    "Type": "HYDROLOG",
                    "Message": "Pump calibration failed: no EC change detected",
                    "pump": pump_type
                }, haEvent=True, debug_type="ERROR")
            
            self.calibration_in_progress = ""
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error during pump calibration: {e}")
            await self.event_manager.emit("LogForClient", {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": "Pump calibration error",
                "pump": pump_type,
                "error": str(e)
            }, haEvent=True, debug_type="ERROR")
            self.calibration_in_progress = ""

    def _calculate_dose_time(self, ml_amount: float) -> float:
        """Calculate pump run time in seconds for desired ml amount"""
        if ml_amount < self.pump_config.min_dose_ml:
            return 0.0
        
        ml_amount = min(ml_amount, self.pump_config.max_dose_ml)
        return ml_amount / self.pump_config.ml_per_second

    def _calculate_nutrient_dose(self, nutrient_ml_per_liter: float) -> float:
        """Calculate actual ml dose based on current reservoir volume (from reservoir level)"""
        try:
            # Get current reservoir level percentage from dataStore
            reservoir_level_percent = self.data_store.getDeep("Hydro.ReservoirLevel", 100.0)
            
            # Calculate actual water volume in reservoir
            # If reservoir is 50% full, we have 50% of the max volume
            actual_water_volume = self.reservoir_volume_liters * (reservoir_level_percent / 100.0)
            
            # Calculate dose based on actual volume
            dose_ml = nutrient_ml_per_liter * actual_water_volume
            
            _LOGGER.debug(
                f"[{self.room}] Nutrient dose calculation: "
                f"reservoir={reservoir_level_percent:.1f}%, "
                f"actual_volume={actual_water_volume:.1f}L, "
                f"dose={dose_ml:.1f}ml (nutrient={nutrient_ml_per_liter}ml/L)"
            )
            
            return dose_ml
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error calculating nutrient dose: {e}")
            # Fallback to max volume
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

            _LOGGER.debug(f"[{self.room}] Hydro values: pH={self.current_ph:.2f}, "
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
            
            # Get calibration factor and calculate run time correctly
            # calibration_factor = ml/s, so run_time = ml / (ml/s) = seconds
            calibration_factor = cal.calibration_factor if cal else self.pump_config.ml_per_second
            run_time = dose_ml / calibration_factor if calibration_factor > 0 else dose_ml / self.pump_config.ml_per_second

            if run_time > 0:
                _LOGGER.warning(f"[{self.room}] Try to activate {PumpType.PH_DOWN.value} with {dose_ml:.2f}ml for {run_time:.1f}s (calibration factor: {calibration_factor:.3f} ml/s)")
                return await self._activate_pump(PumpType.PH_DOWN.value, run_time, dose_ml)
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing pH down: {e}")
            # Send notification for critical pump error
            await self._send_notification(
                "critical",
                f"pH down dosing failed: {str(e)}. Check pump and solution levels.",
                f"OGB {self.room}: Pump Error"
            )
            self._log_to_client(f"pH down dosing failed: {str(e)}", "ERROR", {"pump": "ph_down"})
        return False

    async def _dose_ph_up(self) -> bool:
        """Dose pH up solution"""
        try:
            dose_ml = 1.0
            cal = self.pump_calibrations.get(PumpType.PH_UP.value)
            
            # Get calibration factor and calculate run time correctly
            calibration_factor = cal.calibration_factor if cal else self.pump_config.ml_per_second
            run_time = dose_ml / calibration_factor if calibration_factor > 0 else dose_ml / self.pump_config.ml_per_second

            if run_time > 0:
                _LOGGER.warning(f"[{self.room}] Try to activate {PumpType.PH_UP.value} with {dose_ml:.2f}ml for {run_time:.1f}s (calibration factor: {calibration_factor:.3f} ml/s)")
                return await self._activate_pump(PumpType.PH_UP.value, run_time, dose_ml)
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing pH up: {e}")
            await self.event_manager.emit("LogForClient", {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": "Automatic pH up dosing failed",
                "error": str(e)
            }, haEvent=True, debug_type="ERROR")
        return False

    async def _dose_nutrients(self) -> bool:
        """Dose nutrients based on current stage and targets"""
        try:
            # Dose in order: A, B, C with delays between
            for nutrient in ["A", "B", "C"]:
                if nutrient in self.nutrients and self.nutrients[nutrient] > 0:
                    ml_per_liter = self.nutrients[nutrient]
                    total_ml = self._calculate_nutrient_dose(ml_per_liter)
                    
                    # Get calibration factor and calculate run time correctly
                    pump_enum = getattr(PumpType, f"NUTRIENT_{nutrient}")
                    cal = self.pump_calibrations.get(pump_enum.value)
                    calibration_factor = cal.calibration_factor if cal else self.pump_config.ml_per_second
                    
                    # Calculate run time: ml / (ml/s) = seconds
                    run_time = total_ml / calibration_factor if calibration_factor > 0 else total_ml / self.pump_config.ml_per_second
                    
                    if run_time > 0:
                        if await self._activate_pump(pump_enum.value, run_time, total_ml):
                            # Wait between nutrients (90 seconds for sensor settling)
                            await asyncio.sleep(90)
                            
            return True
                    
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing nutrients: {e}")
            await self.event_manager.emit("LogForClient", {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": "Automatic nutrient dosing failed",
                "error": str(e)
            }, haEvent=True, debug_type="ERROR")
        return False

    async def _dose_nutrients_proportional(self, data: Dict[str, Any]):
        """Handle proportional nutrient dosing requests with EC tracking using concentration-based dosing."""
        try:
            # EC Tracking: Save EC before dosing
            self.feed_ec_before = self.current_ec
            _LOGGER.info(f"[{self.room}] Starting concentration-based nutrient dosing (EC before: {self.feed_ec_before:.2f})")

            # Calculate doses based on concentration (ml/L) and current tank volume
            nutrient_doses = {}
            nutrients_to_dose = []
            
            # Calculate doses for all nutrients with concentration > 0
            for nutrient_type in ["A", "B", "C", "X", "Y"]:
                concentration = self.nutrient_concentrations.get(nutrient_type, 0.0)
                if concentration > 0 and nutrient_type in self.nutrients and self.nutrients[nutrient_type] > 0:
                    dose = self._calculate_dose_from_concentration(nutrient_type)
                    if dose > 0:
                        nutrient_doses[nutrient_type] = dose
                        nutrients_to_dose.append(nutrient_type)
            
            if not nutrients_to_dose:
                _LOGGER.warning(f"[{self.room}] No nutrients with concentration > 0 found to dose")
                return

            # Dose each nutrient based on calculated concentration-based doses
            success = await self._dose_nutrients_with_concentration(nutrient_doses)
            
            if success:
                # Wait for sensors to settle (90 seconds after last nutrient)
                await asyncio.sleep(90)
                
                # EC Tracking: Save EC after dosing
                self.feed_ec_after = self.current_ec
                self.feed_ec_added = self.feed_ec_after - self.feed_ec_before
                
                # Calculate total dose
                total_dose = sum(nutrient_doses.values())
                
                # Store feed cycle data
                feed_cycle = {
                    'timestamp': datetime.now().isoformat(),
                    'ec_before': self.feed_ec_before,
                    'ec_after': self.feed_ec_after,
                    'ec_added': self.feed_ec_added,
                    'dose_ml': total_dose,
                    'nutrients_dosed': nutrients_to_dose,
                    'nutrient_doses': nutrient_doses,
                }
                self.feed_history.append(feed_cycle)
                
                # Keep only last 100 entries
                if len(self.feed_history) > 100:
                    self.feed_history = self.feed_history[-100:]
                
                # Save to dataStore
                self.data_store.setDeep("Hydro.FeedHistory", json.dumps(self.feed_history))
                
                # Automatic calibration: Check if dosing was accurate
                await self._auto_calibrate_pumps(total_dose, nutrients_to_dose)
                
                _LOGGER.info(f"[{self.room}] Concentration-based dosing completed - EC: {self.feed_ec_before:.2f} → {self.feed_ec_after:.2f} (added: {self.feed_ec_added:.2f}), Total dose: {total_dose:.1f}ml")
                
                # Log to client
                self._log_to_client(
                    f"Nutrients dosed - EC: {self.feed_ec_before:.2f}→{self.feed_ec_after:.2f} (+{self.feed_ec_added:.2f}), Total: {total_dose:.1f}ml",
                    "INFO",
                    {"ec_before": self.feed_ec_before, "ec_after": self.feed_ec_after, "total_dose_ml": total_dose, "nutrients_dosed": nutrients_to_dose}
                )

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in concentration-based nutrient dosing: {e}")

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
                    # Get calibration factor and calculate run time correctly
                    pump_enum = getattr(PumpType, f"NUTRIENT_{nutrient}")
                    cal = self.pump_calibrations.get(pump_enum.value)
                    calibration_factor = cal.calibration_factor if cal else self.pump_config.ml_per_second
                    
                    # Calculate run time: ml / (ml/s) = seconds
                    run_time = dose_ml / calibration_factor if calibration_factor > 0 else dose_ml / self.pump_config.ml_per_second

                    if run_time > 0:
                        if await self._activate_pump(pump_enum.value, run_time, dose_ml):
                            # Wait between nutrients to prevent mixing issues (90 seconds for sensor settling)
                            await asyncio.sleep(90)
                        else:
                            _LOGGER.warning(f"[{self.room}] Failed to dose nutrient {nutrient}")

            return True

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing nutrients with amount: {e}")
            return False

    async def _dose_nutrients_with_concentration(self, nutrient_doses: Dict[str, float]) -> bool:
        """Dose nutrients with concentration-based doses (ml per nutrient)."""
        try:
            # Dose in order: A, B, C, X, Y with delays between
            # Only dose nutrients that have a dose > 0
            nutrients_to_dose = sorted(nutrient_doses.keys(), key=lambda x: {"A": 0, "B": 1, "C": 2, "X": 3, "Y": 4}.get(x, 99))

            for nutrient in nutrients_to_dose:
                dose_ml = nutrient_doses[nutrient]
                if dose_ml <= 0:
                    continue
                
                if nutrient in self.nutrients and self.nutrients[nutrient] > 0:
                    # Get pump enum for this nutrient
                    nutrient_to_pump = {
                        "A": PumpType.NUTRIENT_A,
                        "B": PumpType.NUTRIENT_B,
                        "C": PumpType.NUTRIENT_C,
                        "X": PumpType.CUSTOM_X,
                        "Y": PumpType.CUSTOM_Y,
                    }
                    pump_enum = nutrient_to_pump.get(nutrient)
                    if not pump_enum:
                        _LOGGER.warning(f"[{self.room}] No pump mapping for nutrient {nutrient}")
                        continue
                    
                    cal = self.pump_calibrations.get(pump_enum.value)
                    calibration_factor = cal.calibration_factor if cal else self.pump_config.ml_per_second
                    
                    # Calculate run time: ml / (ml/s) = seconds
                    run_time = dose_ml / calibration_factor if calibration_factor > 0 else dose_ml / self.pump_config.ml_per_second

                    if run_time > 0:
                        _LOGGER.debug(f"[{self.room}] Dosing {nutrient}: {dose_ml:.1f}ml for {run_time:.1f}s")
                        if await self._activate_pump(pump_enum.value, run_time, dose_ml):
                            # Wait between nutrients to prevent mixing issues (90 seconds for sensor settling)
                            await asyncio.sleep(90)
                        else:
                            _LOGGER.warning(f"[{self.room}] Failed to dose nutrient {nutrient}")

            return True

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing nutrients with concentration: {e}")
            return False

    async def _dose_ph_down_with_amount(self, dose_ml: float) -> bool:
        """Dose pH down with a specific amount."""
        try:
            # Get calibration factor and calculate run time correctly
            cal = self.pump_calibrations.get(PumpType.PH_DOWN.value)
            calibration_factor = cal.calibration_factor if cal else self.pump_config.ml_per_second
            
            # Calculate run time: ml / (ml/s) = seconds
            run_time = dose_ml / calibration_factor if calibration_factor > 0 else dose_ml / self.pump_config.ml_per_second

            if run_time > 0:
                _LOGGER.info(f"[{self.room}] Dosing {dose_ml:.2f}ml pH down for {run_time:.1f}s (calibration factor: {calibration_factor:.3f} ml/s)")
                return await self._activate_pump(PumpType.PH_DOWN.value, run_time, dose_ml)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error dosing pH down with amount: {e}")
        return False

    async def _dose_ph_up_with_amount(self, dose_ml: float) -> bool:
        """Dose pH up with a specific amount."""
        try:
            # Get calibration factor and calculate run time correctly
            cal = self.pump_calibrations.get(PumpType.PH_UP.value)
            calibration_factor = cal.calibration_factor if cal else self.pump_config.ml_per_second
            
            # Calculate run time: ml / (ml/s) = seconds
            run_time = dose_ml / calibration_factor if calibration_factor > 0 else dose_ml / self.pump_config.ml_per_second

            if run_time > 0:
                _LOGGER.info(f"[{self.room}] Dosing {dose_ml:.2f}ml pH up for {run_time:.1f}s (calibration factor: {calibration_factor:.3f} ml/s)")
                return await self._activate_pump(PumpType.PH_UP.value, run_time, dose_ml)

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
            await self.event_manager.emit("LogForClient", waterAction, haEvent=True, debug_type="INFO")
            
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
            await self.event_manager.emit("LogForClient", {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": "Pump activation failed",
                "pump": pump_type,
                "dose_ml": dose_ml,
                "error": str(e)
            }, haEvent=True, debug_type="ERROR")
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
            await self.event_manager.emit("LogForClient", waterAction, haEvent=True, debug_type="INFO")
            
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
            await self.event_manager.emit("LogForClient", {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": "Pump activation failed",
                "pump": str(pump_type),
                "dose_ml": dose_ml,
                "error": str(e)
            }, haEvent=True, debug_type="ERROR")
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

    def _load_pump_flow_rates(self):
        """Load pump flow rates from DataStore (ml/min -> ml/s)"""
        pump_flow_rates = {
            "A": self.data_store.getDeep("Hydro.Pump_FlowRate_A", 50.0) / 60.0,
            "B": self.data_store.getDeep("Hydro.Pump_FlowRate_B", 50.0) / 60.0,
            "C": self.data_store.getDeep("Hydro.Pump_FlowRate_C", 50.0) / 60.0,
            "W": self.data_store.getDeep("Hydro.Pump_FlowRate_W", 100.0) / 60.0,
            "X": self.data_store.getDeep("Hydro.Pump_FlowRate_X", 50.0) / 60.0,
            "Y": self.data_store.getDeep("Hydro.Pump_FlowRate_Y", 50.0) / 60.0,
            "PH_DOWN": self.data_store.getDeep("Hydro.Pump_FlowRate_PH_Down", 10.0) / 60.0,
            "PH_UP": self.data_store.getDeep("Hydro.Pump_FlowRate_PH_Up", 10.0) / 60.0,
        }
        
        # Update pump_config with default flow rate (will be overridden per-pump)
        for pump_type, ml_per_second in pump_flow_rates.items():
            if ml_per_second > 0:
                self.pump_config.ml_per_second = ml_per_second
                _LOGGER.debug(f"[{self.room}] Loaded {pump_type} flow rate: {ml_per_second * 60:.1f} ml/min")
    
    def _load_nutrient_concentrations(self):
        """Load nutrient concentrations from DataStore (ml/L)"""
        self.nutrient_concentrations = {
            "A": self.data_store.getDeep("Hydro.Nutrient_Concentration_A", 2.0),
            "B": self.data_store.getDeep("Hydro.Nutrient_Concentration_B", 2.0),
            "C": self.data_store.getDeep("Hydro.Nutrient_Concentration_C", 1.0),
            "X": self.data_store.getDeep("Hydro.Nutrient_Concentration_X", 0.0),
            "Y": self.data_store.getDeep("Hydro.Nutrient_Concentration_Y", 0.0),
            "PH_DOWN": self.data_store.getDeep("Hydro.Nutrient_Concentration_PH_Down", 0.5),
        }
        
        _LOGGER.debug(f"[{self.room}] Loaded nutrient concentrations: {self.nutrient_concentrations}")

    def _calculate_dose_from_concentration(self, nutrient_type: str) -> float:
        """Calculate ml dose based on nutrient concentration (ml/L) and current tank volume"""
        try:
            concentration_ml_per_l = self.nutrient_concentrations.get(nutrient_type, 0.0)
            if concentration_ml_per_l <= 0:
                return 0.0
            
            reservoir_level_percent = self.data_store.getDeep("Hydro.ReservoirLevel", 100.0)
            actual_water_volume = self.reservoir_volume_liters * (reservoir_level_percent / 100.0)
            dose_ml = concentration_ml_per_l * actual_water_volume
            
            _LOGGER.debug(
                f"[{self.room}] Concentration dose calculation: "
                f"nutrient={nutrient_type}, "
                f"concentration={concentration_ml_per_l:.2f}ml/L, "
                f"tank_volume={actual_water_volume:.1f}L, "
                f"dose={dose_ml:.1f}ml"
            )
            
            return dose_ml
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error calculating concentration dose: {e}")
            return 0.0

    def _get_pump_flow_rate(self, pump_type: str) -> float:
        """Get flow rate (ml/s) for specific pump type"""
        pump_type_map = {
            "A": self.data_store.getDeep("Hydro.Pump_FlowRate_A", 50.0) / 60.0,
            "B": self.data_store.getDeep("Hydro.Pump_FlowRate_B", 50.0) / 60.0,
            "C": self.data_store.getDeep("Hydro.Pump_FlowRate_C", 50.0) / 60.0,
            "W": self.data_store.getDeep("Hydro.Pump_FlowRate_W", 100.0) / 60.0,
            "X": self.data_store.getDeep("Hydro.Pump_FlowRate_X", 50.0) / 60.0,
            "Y": self.data_store.getDeep("Hydro.Pump_FlowRate_Y", 50.0) / 60.0,
            "PH_DOWN": self.data_store.getDeep("Hydro.Pump_FlowRate_PH_Down", 10.0) / 60.0,
            "PH_UP": self.data_store.getDeep("Hydro.Pump_FlowRate_PH_Up", 10.0) / 60.0,
        }
        return pump_type_map.get(pump_type, self.pump_config.ml_per_second)

    def _calculate_dose_time(self, ml_amount: float, pump_type: str = "A") -> float:
        """Calculate pump run time in seconds for desired ml amount using specific pump flow rate"""
        if ml_amount < self.pump_config.min_dose_ml:
            return 0.0
        
        ml_amount = min(ml_amount, self.pump_config.max_dose_ml)
        flow_rate_ml_per_sec = self._get_pump_flow_rate(pump_type)
        
        if flow_rate_ml_per_sec <= 0:
            _LOGGER.error(f"[{self.room}] Invalid flow rate for pump {pump_type}: {flow_rate_ml_per_sec}")
            return 0.0
        
        return ml_amount / flow_rate_ml_per_sec

    async def _auto_calibrate_pumps(self, target_dose_ml: float, nutrients_dosed: list = None):
        """Automatically calibrate pump accuracy based on EC change"""
        try:
            # Skip calibration if EC data is invalid
            if self.feed_ec_before <= 0 or self.feed_ec_after <= 0:
                return
            
            # Calculate EC change
            ec_change = self.feed_ec_after - self.feed_ec_before
            
            # Expected EC change based on standard concentration
            expected_ec_change_per_ml = 0.002  # 0.002 EC change per ml (for 100L tank)
            reservoir_volume_l = self.reservoir_volume_liters * (self.data_store.getDeep("Hydro.ReservoirLevel", 100.0) / 100.0)
            # target_dose_ml is already the total dose across all nutrients
            expected_total_ec_change = target_dose_ml * expected_ec_change_per_ml
            
            # Calculate accuracy score (how close was actual to expected)
            if expected_total_ec_change > 0:
                accuracy_ratio = ec_change / expected_total_ec_change
                accuracy_score = min(100.0, max(0.0, (1.0 / accuracy_ratio) * 100.0))
            else:
                accuracy_score = 100.0
            
            # Check if pump is inaccurate (accuracy < 70% or > 130%)
            if accuracy_score < 70.0 or accuracy_score > 130.0:
                # Update calibration factors for all dosed pumps
                pumps_to_calibrate = nutrients_dosed if nutrients_dosed else ["A", "B", "C"]
                
                # Mapping from nutrient type to PumpType enum
                nutrient_to_pump = {
                    "A": PumpType.NUTRIENT_A,
                    "B": PumpType.NUTRIENT_B,
                    "C": PumpType.NUTRIENT_C,
                    "X": PumpType.CUSTOM_X,
                    "Y": PumpType.CUSTOM_Y,
                }
                
                for pump_type in pumps_to_calibrate:
                    pump_enum = nutrient_to_pump.get(pump_type)
                    if not pump_enum:
                        _LOGGER.warning(f"[{self.room}] No pump mapping for nutrient {pump_type}")
                        continue
                    
                    if pump_enum.value in self.pump_calibrations:
                        cal = self.pump_calibrations[pump_enum.value]
                        
                        # Adjust calibration factor based on accuracy
                        if accuracy_score < 100.0:
                            # Pump delivered less than expected - increase dose next time
                            adjustment = 1.0 / (accuracy_score / 100.0)
                            adjustment = min(adjustment, 1.5)
                        else:
                            # Pump delivered more than expected - decrease dose next time
                            adjustment = 1.0 / (accuracy_score / 100.0)
                            adjustment = max(adjustment, 0.67)
                        
                        cal.calibration_factor *= adjustment
                        cal.calibration_factor = max(0.5, min(2.0, cal.calibration_factor))
                        cal.calibration_count += 1
                        cal.last_calibration = datetime.now()
                        
                        # Notify user about pump inaccuracy
                        self._log_to_client(
                            f"Pump {pump_type} recalibrated (accuracy: {accuracy_score:.1f}%) - "
                            f"New calibration factor: {cal.calibration_factor:.2f}x",
                            "WARNING",
                            {
                                "pump": pump_type,
                                "accuracy": accuracy_score,
                                "new_calibration": cal.calibration_factor,
                                "ec_before": self.feed_ec_before,
                                "ec_after": self.feed_ec_after,
                                "ec_change": ec_change
                            }
                        )
                        
                        # Send notification for critical inaccuracy
                        if accuracy_score < 50.0 or accuracy_score > 150.0:
                            await self._send_notification(
                                "critical",
                                f"Pump {pump_type} severely inaccurate ({accuracy_score:.1f}% accuracy). "
                                f"Check pump and calibration. EC: {self.feed_ec_before:.2f}→{self.feed_ec_after:.2f} (expected: {self.feed_ec_before + expected_total_ec_change:.2f})",
                                f"OGB {self.room}: Pump Calibration Alert"
                            )
                
                # Save calibration data
                await self._save_calibration_data()
                
                _LOGGER.info(
                    f"[{self.room}] Auto-calibration completed - "
                    f"EC change: {ec_change:.2f}, expected: {expected_total_ec_change:.2f}, "
                    f"accuracy: {accuracy_score:.1f}%"
                )
        
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in auto-calibration: {e}")

    async def _apply_feeding(self):
        """Apply feeding logic - delegate to feed logic manager"""
        await self.feed_logic_manager._check_ranges_and_feed()

    def _handleLogForClient(self, data):
        """Handle logging for client"""
        try:
            _LOGGER.info(f"[{self.room}] ClientLog: {data}")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in client log: {e}")