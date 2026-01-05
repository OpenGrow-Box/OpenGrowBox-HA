"""
OpenGrowBox Crop Steering Configuration Manager

Handles preset configurations, medium adjustments, and configuration management
for the Crop Steering system.

Responsibilities:
- Base preset definitions
- Medium-specific adjustments
- Growth phase adjustments
- Configuration building and validation
"""

import logging
from enum import Enum
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)


class CSMode(Enum):
    """Crop Steering operation modes."""

    DISABLED = "Disabled"
    CONFIG = "Config"
    AUTOMATIC = "Automatic"
    MANUAL_P0 = "Manual-p0"
    MANUAL_P1 = "Manual-p1"
    MANUAL_P2 = "Manual-p2"
    MANUAL_P3 = "Manual-p3"


class OGBCSConfigurationManager:
    """
    Configuration manager for Crop Steering.

    Handles all preset configurations, medium adjustments, and configuration
    building for the Crop Steering system.
    """

    def __init__(self, data_store, room: str):
        """
        Initialize configuration manager.

        Args:
            data_store: Data store instance
            room: Room identifier for logging
        """
        self.data_store = data_store
        self.room = room

        # Medium-specific preset adjustments
        self._medium_adjustments = {
            "rockwool": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
            "coco": {"vwc_offset": 3, "ec_offset": -0.1, "drainage_factor": 0.9},
            "soil": {"vwc_offset": -5, "ec_offset": 0.2, "drainage_factor": 0.7},
            "perlite": {"vwc_offset": -8, "ec_offset": 0.1, "drainage_factor": 1.2},
            "aero": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
            "water": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
            "custom": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
        }

    def get_base_presets(self) -> Dict[str, Dict[str, Any]]:
        """
        Base presets for automatic mode (rockwool defaults).
        User configurations from DataStore are merged on top of defaults.

        Returns:
            Dictionary of phase presets with user overrides applied
        """
        # Default presets
        presets = {
            "p0": {
                # P0: Monitoring - Warte auf Dryback Signal
                # No irrigation in P0 - just monitoring until VWC drops below min
                "description": "Initial Monitoring Phase",
                "VWCTarget": 58.0,
                "VWCMin": 55.0,
                "VWCMax": 65.0,
                "ECTarget": 2.0,
                "MinEC": 1.8,
                "MaxEC": 2.2,
                "trigger_condition": "vwc_below_min",
            },
            "p1": {
                # P1: Saturation - Schnelle Sättigung des Blocks
                "description": "Saturation Phase",
                "VWCTarget": 70.0,
                "VWCMax": 68.0,
                "VWCMin": 55.0,
                "ECTarget": 1.8,
                "MinEC": 1.6,
                "MaxEC": 2.0,
                "irrigation_duration": 45,
                "max_cycles": 10,
                "wait_between": 180,
                "trigger_condition": "vwc_above_target",
            },
            "p2": {
                # P2: Maintenance - Halte Level während Lichtphase
                "description": "Day Maintenance Phase",
                "VWCTarget": 65.0,
                "VWCMax": 68.0,
                "VWCMin": 62.0,
                "hold_percentage": 0.95,
                "ECTarget": 2.0,
                "MinEC": 1.8,
                "MaxEC": 2.2,
                "irrigation_duration": 20,
                "irrigation_interval": 1800,  # 30 min between maintenance shots
                "check_light": True,
                "trigger_condition": "light_off",
            },
            "p3": {
                # P3: Night Dryback - Kontrollierter nächtlicher Dryback
                "description": "Night Dryback Phase",
                "VWCTarget": 60.0,
                "VWCMax": 68.0,
                "VWCMin": 52.0,  # Lower for night dryback
                "target_dryback_percent": 10.0,
                "min_dryback_percent": 8.0,
                "max_dryback_percent": 12.0,
                "emergency_threshold": 0.85,  # 85% of VWCMin = emergency
                "ECTarget": 2.2,
                "MinEC": 2.0,
                "MaxEC": 2.5,
                "ec_increase_step": 0.1,
                "ec_decrease_step": 0.1,
                "irrigation_duration": 15,
                "max_emergency_shots": 2,  # Max 2 emergency irrigations per night
                "irrigation_interval": 3600,  # 1 hour between P3 emergency shots
                "trigger_condition": "light_on",
            },
        }

        # Merge with user configurations from DataStore
        self._apply_user_settings(presets)

        return presets

    def _is_valid_nonzero(self, value) -> bool:
        """
        Check if a value is valid and non-zero.
        Handles strings like '0.0', '0', actual numbers, and None.
        
        CRITICAL: DataStore often stores values as strings like '35.0', '0.0'
        Simple comparison like `val != 0` fails for strings!
        """
        if value is None:
            return False
        try:
            return float(value) != 0.0
        except (ValueError, TypeError):
            return False

    def _apply_user_settings(self, presets: Dict[str, Dict[str, Any]]) -> None:
        """
        Apply user settings from DataStore to presets.
        User settings override defaults.
        
        Reads from CropSteering.Substrate.{phase}.{parameter} paths
        as set by the core OGBConfigurationManager.
        
        CRITICAL: Uses _is_valid_nonzero() to properly check string values like '0.0'
        """
        for phase in ["p0", "p1", "p2", "p3"]:
            # EC parameters
            ec_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_EC")
            if self._is_valid_nonzero(ec_target):
                presets[phase]["ECTarget"] = float(ec_target)

            min_ec = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Min_EC")
            if self._is_valid_nonzero(min_ec):
                presets[phase]["MinEC"] = float(min_ec)

            max_ec = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Max_EC")
            if self._is_valid_nonzero(max_ec):
                presets[phase]["MaxEC"] = float(max_ec)

            # VWC parameters
            vwc_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Target")
            if self._is_valid_nonzero(vwc_target):
                presets[phase]["VWCTarget"] = float(vwc_target)
                _LOGGER.debug(f"{self.room} - User VWCTarget for {phase}: {vwc_target}")

            vwc_min = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Min")
            if self._is_valid_nonzero(vwc_min):
                presets[phase]["VWCMin"] = float(vwc_min)
                _LOGGER.debug(f"{self.room} - User VWCMin for {phase}: {vwc_min}")

            vwc_max = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Max")
            if self._is_valid_nonzero(vwc_max):
                presets[phase]["VWCMax"] = float(vwc_max)
                _LOGGER.debug(f"{self.room} - User VWCMax for {phase}: {vwc_max}")

            # Irrigation parameters
            shot_duration_path = f"CropSteering.Substrate.{phase}.Shot_Duration_Sec"
            shot_duration = self.data_store.getDeep(shot_duration_path)
            _LOGGER.warning(f"{self.room} - Reading {shot_duration_path} = {shot_duration}")
            if self._is_valid_nonzero(shot_duration):
                presets[phase]["irrigation_duration"] = int(float(shot_duration))
                _LOGGER.warning(f"{self.room} - Set irrigation_duration for {phase} = {int(float(shot_duration))}s")

            shot_interval = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_Intervall")
            if self._is_valid_nonzero(shot_interval):
                interval_sec = int(float(shot_interval) * 60)  # Convert minutes to seconds
                if phase == "p1":
                    presets[phase]["wait_between"] = interval_sec
                else:
                    presets[phase]["irrigation_interval"] = interval_sec

            irrigation_freq = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Irrigation_Frequency")
            if self._is_valid_nonzero(irrigation_freq):
                presets[phase]["irrigation_frequency"] = int(float(irrigation_freq))

            # Dryback parameters
            dryback_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Dryback_Target_Percent")
            if self._is_valid_nonzero(dryback_target):
                presets[phase]["target_dryback_percent"] = float(dryback_target)

            dryback_duration = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Dryback_Duration_Hours")
            if self._is_valid_nonzero(dryback_duration):
                presets[phase]["dryback_duration"] = int(float(dryback_duration))
                
            # Moisture dryback
            moisture_dryback = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Moisture_Dryback")
            if self._is_valid_nonzero(moisture_dryback):
                presets[phase]["moisture_dryback"] = float(moisture_dryback)

            # Shot Sum (max_cycles) - number of irrigation shots per cycle
            shot_sum = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_Sum")
            if self._is_valid_nonzero(shot_sum):
                presets[phase]["max_cycles"] = int(float(shot_sum))
                _LOGGER.debug(f"{self.room} - User max_cycles for {phase}: {shot_sum}")

    def get_automatic_presets(
        self, medium_type: str = "rockwool"
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get presets with user values from DataStore.
        
        User values ALWAYS win - no drainage_factor bullshit on timing!
        drainage_factor is ONLY used for VWC/EC thresholds (medium water retention).

        Args:
            medium_type: Type of growing medium

        Returns:
            Dictionary of phase presets with user overrides
        """
        # DEBUG: Dump what's in CropSteering.Substrate to see if user values are there
        substrate_data = self.data_store.getDeep("CropSteering.Substrate") or {}
        _LOGGER.warning(f"🔍 {self.room} - CropSteering.Substrate RAW DATA: {substrate_data}")
        
        # Also check the full CropSteering object
        cs_data = self.data_store.getDeep("CropSteering") or {}
        _LOGGER.warning(f"🔍 {self.room} - CropSteering FULL DATA keys: {list(cs_data.keys())}")
        
        base_presets = self.get_base_presets()

        # Get medium-specific adjustments (ONLY for VWC/EC thresholds!)
        adjustments = self._medium_adjustments.get(
            medium_type, self._medium_adjustments["rockwool"]
        )
        vwc_offset = adjustments["vwc_offset"]
        ec_offset = adjustments["ec_offset"]
        
        _LOGGER.warning(f"{self.room} - Building presets for medium '{medium_type}'")

        adjusted_presets = {}
        for phase, preset in base_presets.items():
            adjusted_presets[phase] = preset.copy()

            # ========== IRRIGATION TIMING - User values or defaults (NO drainage factor!) ==========
            
            # Duration (seconds) - User says 91s = 91s, period.
            user_duration = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_Duration_Sec")
            if self._is_valid_nonzero(user_duration):
                adjusted_presets[phase]["irrigation_duration"] = int(float(user_duration))
                _LOGGER.warning(f"{self.room} - {phase} duration: {int(float(user_duration))}s (USER)")
            else:
                default_val = preset.get("irrigation_duration", 30)
                adjusted_presets[phase]["irrigation_duration"] = default_val
                _LOGGER.warning(f"{self.room} - {phase} duration: {default_val}s (DEFAULT)")
            
            # Interval (minutes in UI -> seconds internally)
            user_interval = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_Intervall")
            if self._is_valid_nonzero(user_interval):
                interval_sec = int(float(user_interval) * 60)
                adjusted_presets[phase]["wait_between"] = interval_sec
                adjusted_presets[phase]["irrigation_interval"] = interval_sec
                _LOGGER.warning(f"{self.room} - {phase} interval: {interval_sec}s / {float(user_interval):.1f}min (USER)")
            else:
                default_val = preset.get("wait_between", preset.get("irrigation_interval", 180))
                adjusted_presets[phase]["wait_between"] = default_val
                adjusted_presets[phase]["irrigation_interval"] = default_val
                _LOGGER.warning(f"{self.room} - {phase} interval: {default_val}s (DEFAULT)")
            
            # Shot Sum / Max Cycles
            user_shot_sum = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_Sum")
            if self._is_valid_nonzero(user_shot_sum):
                adjusted_presets[phase]["max_cycles"] = int(float(user_shot_sum))
                _LOGGER.warning(f"{self.room} - {phase} max_cycles: {int(float(user_shot_sum))} (USER)")
            else:
                default_val = preset.get("max_cycles", 10)
                adjusted_presets[phase]["max_cycles"] = default_val
                _LOGGER.warning(f"{self.room} - {phase} max_cycles: {default_val} (DEFAULT)")

            # ========== VWC/EC THRESHOLDS - Medium adjustments apply here ==========
            # CRITICAL: Ensure preset values are float before adding offset
            for key in ["VWCTarget", "VWCMin", "VWCMax"]:
                if key in preset:
                    base_val = float(preset[key]) if preset[key] is not None else 0.0
                    adjusted_presets[phase][key] = base_val + vwc_offset

            for key in ["ECTarget", "MinEC", "MaxEC"]:
                if key in preset:
                    base_val = float(preset[key]) if preset[key] is not None else 0.0
                    adjusted_presets[phase][key] = base_val + ec_offset

        return adjusted_presets

    def get_phase_growth_adjustments(
        self, plant_phase: str, generative_week: int
    ) -> Dict[str, float]:
        """
        Wachstumsphasen-spezifische Anpassungen

        Vegetativ: Mehr Feuchtigkeit, weniger Dryback (generativ)
        Generativ: Weniger Feuchtigkeit, mehr Dryback (vegetativ)

        Args:
            plant_phase: Current plant phase ('veg' or 'gen')
            generative_week: Week number in generative phase

        Returns:
            Dictionary of adjustment values
        """
        adjustments = {"vwc_modifier": 0.0, "dryback_modifier": 0.0, "ec_modifier": 0.0}

        if plant_phase == "veg":
            # Vegetative Phase: Fördern Wachstum
            adjustments["vwc_modifier"] = 2.0  # +2% Feuchtigkeit
            adjustments["dryback_modifier"] = -2.0  # -2% Dryback (weniger Stress)
            adjustments["ec_modifier"] = -0.1  # Etwas niedrigere EC

        elif plant_phase == "gen":
            # Flowering Phase: Fördern Blütenbildung
            if generative_week <= 3:
                # Early Flower: Übergang
                adjustments["vwc_modifier"] = 1.0
                adjustments["dryback_modifier"] = -1.0
                adjustments["ec_modifier"] = 0.05
            elif generative_week <= 5:
                # Mid Flower: Verstärkt generativ
                adjustments["vwc_modifier"] = -2.0  # -2% Feuchtigkeit
                adjustments["dryback_modifier"] = 2.0  # +2% Dryback (mehr Stress)
                adjustments["ec_modifier"] = 0.2  # Höhere EC
            elif generative_week <= 7:
                # Mid Flower: Verstärkt generativ
                adjustments["vwc_modifier"] = 2.0  # -2% Feuchtigkeit
                adjustments["dryback_modifier"] = -2.0  # +2% Dryback (mehr Stress)
                adjustments["ec_modifier"] = 0.1  # Höhere EC
            else:
                # Late Flower: Maximal generativ
                adjustments["vwc_modifier"] = -3.0  # -3% Feuchtigkeit
                adjustments["dryback_modifier"] = 3.0  # +3% Dryback
                adjustments["ec_modifier"] = 0.3  # Noch höhere EC

        return adjustments

    def get_adjusted_preset(
        self,
        phase: str,
        plant_phase: str,
        generative_week: int,
        medium_type: str = "rockwool",
    ) -> Dict[str, Any]:
        """
        Hole Preset und wende Wachstumsphasen-Anpassungen an

        Args:
            phase: Phase identifier (p0, p1, p2, p3)
            plant_phase: Current plant phase
            generative_week: Week in generative phase
            medium_type: Growing medium type

        Returns:
            Adjusted preset configuration
        """
        base_preset = self.get_automatic_presets(medium_type)[phase].copy()
        adjustments = self.get_phase_growth_adjustments(plant_phase, generative_week)

        # Wende Anpassungen an
        if "VWCTarget" in base_preset:
            base_preset["VWCTarget"] += adjustments["vwc_modifier"]
        if "VWCMax" in base_preset:
            base_preset["VWCMax"] += adjustments["vwc_modifier"]
        if "VWCMin" in base_preset:
            base_preset["VWCMin"] += adjustments["vwc_modifier"]

        if "target_dryback_percent" in base_preset:
            base_preset["target_dryback_percent"] += adjustments["dryback_modifier"]

        if "ECTarget" in base_preset:
            base_preset["ECTarget"] += adjustments["ec_modifier"]

        return base_preset

    def get_medium_adjustments(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all medium-specific adjustments.

        Returns:
            Dictionary of medium adjustment parameters
        """
        return self._medium_adjustments.copy()

    def validate_configuration(self, config: Dict[str, Any]) -> bool:
        """
        Validate a configuration dictionary.

        Args:
            config: Configuration to validate

        Returns:
            True if valid, False otherwise
        """
        required_keys = ["VWCTarget", "VWCMin", "VWCMax", "ECTarget", "MinEC", "MaxEC"]

        for key in required_keys:
            if key not in config:
                _LOGGER.error(f"{self.room} - Missing required config key: {key}")
                return False

            value = config[key]
            if not isinstance(value, (int, float)):
                _LOGGER.error(
                    f"{self.room} - Invalid value for {key}: {value} (must be numeric)"
                )
                return False

        # Validate ranges
        if config["VWCMin"] >= config["VWCMax"]:
            _LOGGER.error(f"{self.room} - VWCMin must be less than VWCMax")
            return False

        if config["MinEC"] >= config["MaxEC"]:
            _LOGGER.error(f"{self.room} - MinEC must be less than MaxEC")
            return False

        return True
