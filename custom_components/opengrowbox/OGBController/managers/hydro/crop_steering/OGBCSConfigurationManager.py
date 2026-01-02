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
        }

    def _load_user_presets(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Load user-configured crop steering presets from datastore.

        Returns:
            User presets if available, None otherwise
        """
        try:
            presets = {}

            for phase in ["p0", "p1", "p2", "p3"]:
                phase_config = {}

                # Load all user-configured parameters for this phase
                # EC parameters
                ec_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_EC")
                if ec_target is not None:
                    phase_config["ECTarget"] = float(ec_target)

                min_ec = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Min_EC")
                if min_ec is not None:
                    phase_config["MinEC"] = float(min_ec)

                max_ec = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Max_EC")
                if max_ec is not None:
                    phase_config["MaxEC"] = float(max_ec)

                # VWC parameters
                vwc_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Target")
                if vwc_target is not None:
                    phase_config["VWCTarget"] = float(vwc_target)

                vwc_min = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Min")
                if vwc_min is not None:
                    phase_config["VWCMin"] = float(vwc_min)

                vwc_max = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Max")
                if vwc_max is not None:
                    phase_config["VWCMax"] = float(vwc_max)

                # Irrigation parameters
                shot_duration = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_Duration_Sec")
                if shot_duration is not None:
                    phase_config["irrigation_duration"] = int(shot_duration)

                irrigation_freq = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Irrigation_Frequency")
                if irrigation_freq is not None:
                    phase_config["irrigation_frequency"] = int(irrigation_freq)

                # Dryback parameters
                dryback_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Dryback_Target_Percent")
                if dryback_target is not None:
                    phase_config["dryback_target"] = float(dryback_target)

                dryback_duration = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Dryback_Duration_Hours")
                if dryback_duration is not None:
                    phase_config["dryback_duration"] = int(dryback_duration)

                # Only add phase if it has user configurations
                if phase_config:
                    presets[phase] = phase_config
                    _LOGGER.info(f"{self.room} - Loaded user config for phase {phase}: {phase_config}")

            return presets if presets else None

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error loading user crop steering presets: {e}")
            return None

    def get_base_presets(self) -> Dict[str, Dict[str, Any]]:
        """
        Base presets for automatic mode (rockwool defaults).
        These are adjusted based on medium type and can be overridden by user configurations.

        Returns:
            Dictionary of phase presets
        """
        # Check for user-configured presets first
        user_presets = self._load_user_presets()
        if user_presets:
            _LOGGER.info(f"{self.room} - Using user-configured crop steering presets")
            return user_presets

        # Default presets (will be merged with user configs at the end)
        presets = {
            "p0": {
                # P0: Monitoring - Warte auf Dryback Signal
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
                "irrigation_interval": 3600,  # 1 hour between P3 emergency shots
                "max_emergency_shots": 2,  # Max 2 emergency irrigations per night
                "trigger_condition": "light_on",
            },
        }

        # Merge with user configurations
        user_presets = self._load_user_presets()
        if user_presets:
            # Merge user configs with defaults
            for phase, user_config in user_presets.items():
                if phase in presets:
                    presets[phase].update(user_config)
                    _LOGGER.info(f"{self.room} - Merged user config for phase {phase}")
                else:
                    presets[phase] = user_config
                    _LOGGER.info(f"{self.room} - Added user config for phase {phase}")

        return presets

    def _load_user_presets(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Load user-configured crop steering presets from datastore.

        Returns:
            User presets if available, None otherwise
        """
        try:
            presets = {}

            for phase in ["p0", "p1", "p2", "p3"]:
                phase_config = {}

                # Load all user-configured parameters for this phase
                # EC parameters
                ec_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_EC")
                if ec_target is not None:
                    phase_config["ECTarget"] = float(ec_target)

                min_ec = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Min_EC")
                if min_ec is not None:
                    phase_config["MinEC"] = float(min_ec)

                max_ec = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Max_EC")
                if max_ec is not None:
                    phase_config["MaxEC"] = float(max_ec)

                # VWC parameters
                vwc_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Target")
                if vwc_target is not None:
                    phase_config["VWCTarget"] = float(vwc_target)

                vwc_min = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Min")
                if vwc_min is not None:
                    phase_config["VWCMin"] = float(vwc_min)

                vwc_max = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.VWC_Max")
                if vwc_max is not None:
                    phase_config["VWCMax"] = float(vwc_max)

                # Irrigation parameters
                shot_duration = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Shot_Duration_Sec")
                if shot_duration is not None:
                    phase_config["irrigation_duration"] = int(shot_duration)

                irrigation_freq = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Irrigation_Frequency")
                if irrigation_freq is not None:
                    phase_config["irrigation_frequency"] = int(irrigation_freq)

                # Dryback parameters
                dryback_target = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Dryback_Target_Percent")
                if dryback_target is not None:
                    phase_config["dryback_target"] = float(dryback_target)

                dryback_duration = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.Dryback_Duration_Hours")
                if dryback_duration is not None:
                    phase_config["dryback_duration"] = int(dryback_duration)

                # Only add phase if it has user configurations
                if phase_config:
                    presets[phase] = phase_config
                    _LOGGER.info(f"{self.room} - Loaded user config for phase {phase}: {phase_config}")

            return presets if presets else None

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error loading user crop steering presets: {e}")
            return None

    def get_automatic_presets(
        self, medium_type: str = "rockwool"
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get medium-adjusted automatic presets.
        Applies medium-specific adjustments to base presets.

        Args:
            medium_type: Type of growing medium

        Returns:
            Dictionary of adjusted phase presets
        """
        base_presets = self.get_base_presets()

        # Get medium-specific adjustments
        adjustments = self._medium_adjustments.get(
            medium_type, self._medium_adjustments["rockwool"]
        )

        vwc_offset = adjustments["vwc_offset"]
        ec_offset = adjustments["ec_offset"]
        drainage_factor = adjustments["drainage_factor"]

        adjusted_presets = {}
        for phase, preset in base_presets.items():
            adjusted_presets[phase] = preset.copy()

            # Adjust VWC targets based on medium
            for key in ["VWCTarget", "VWCMin", "VWCMax"]:
                if key in preset:
                    adjusted_presets[phase][key] = preset[key] + vwc_offset

            # Adjust EC targets based on medium
            for key in ["ECTarget", "MinEC", "MaxEC"]:
                if key in preset:
                    adjusted_presets[phase][key] = preset[key] + ec_offset

            # Adjust irrigation timing based on drainage
            if "irrigation_duration" in preset:
                adjusted_presets[phase]["irrigation_duration"] = int(
                    preset["irrigation_duration"] * drainage_factor
                )
            if "wait_between" in preset:
                adjusted_presets[phase]["wait_between"] = int(
                    preset["wait_between"] / drainage_factor
                )

        _LOGGER.debug(
            f"{self.room} - Presets adjusted for {medium_type}: vwc_offset={vwc_offset}, ec_offset={ec_offset}"
        )

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
