"""
OpenGrowBox Medium Properties Manager

Handles medium properties, status monitoring, and optimization checks
for grow medium management.

Responsibilities:
- Medium property definitions and management
- Status monitoring and health assessment
- Optimization checks (pH, EC ranges)
- Medium type configurations and presets
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class MediumType(Enum):
    """Enum for different grow mediums"""

    ROCKWOOL = "rockwool"
    SOIL = "soil"
    COCO = "coco"
    AERO = "aero"
    WATER = "water"
    PERLITE = "perlite"
    CUSTOM = "custom"


class ThresholdConfig:
    """Configuration for medium thresholds that trigger device actions"""

    def __init__(
        self,
        ph_min: Optional[float] = None,
        ph_max: Optional[float] = None,
        ec_min: Optional[float] = None,
        ec_max: Optional[float] = None,
        moisture_min: Optional[float] = None,
        moisture_max: Optional[float] = None,
        temp_min: Optional[float] = None,
        temp_max: Optional[float] = None,
    ):
        self.ph_min = ph_min
        self.ph_max = ph_max
        self.ec_min = ec_min
        self.ec_max = ec_max
        self.moisture_min = moisture_min
        self.moisture_max = moisture_max
        self.temp_min = temp_min
        self.temp_max = temp_max


class MediumProperties:
    """Properties of a grow medium"""

    def __init__(
        self,
        water_retention: float,
        air_porosity: float,
        ph_range: tuple[float, float],
        ec_range: tuple[float, float],
        watering_frequency: float,
        drainage_speed: str,
        nutrient_storage: float,
    ):
        self.water_retention = water_retention  # 0-100%
        self.air_porosity = air_porosity  # 0-100%
        self.ph_range = ph_range
        self.ec_range = ec_range
        self.watering_frequency = watering_frequency  # hours
        self.drainage_speed = drainage_speed
        self.nutrient_storage = nutrient_storage  # 0-100%


class OGBMediumPropertiesManager:
    """
    Properties manager for grow medium characteristics and status monitoring.

    Handles medium property definitions, status monitoring, optimization checks,
    and medium type configurations.
    """

    def __init__(self, room: str, data_store, event_manager):
        """
        Initialize properties manager.

        Args:
            room: Room identifier
            data_store: Data store instance
            event_manager: Event manager instance
        """
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager

        # Medium property presets
        self.medium_presets = self._initialize_medium_presets()

        # Current medium configuration
        self.medium_type = MediumType.ROCKWOOL
        self.volume_liters = 10.0
        self.properties = self.medium_presets[MediumType.ROCKWOOL]

    def _initialize_medium_presets(self) -> Dict[MediumType, MediumProperties]:
        """
        Initialize medium property presets.

        Returns:
            Dictionary of medium presets
        """
        return {
            MediumType.ROCKWOOL: MediumProperties(
                water_retention=70.0,
                air_porosity=25.0,
                ph_range=(5.5, 6.5),
                ec_range=(1.2, 2.4),
                watering_frequency=3.0,
                drainage_speed="fast",
                nutrient_storage=60.0,
            ),
            MediumType.SOIL: MediumProperties(
                water_retention=85.0,
                air_porosity=15.0,
                ph_range=(6.0, 7.0),
                ec_range=(1.5, 2.5),
                watering_frequency=2.0,
                drainage_speed="slow",
                nutrient_storage=80.0,
            ),
            MediumType.COCO: MediumProperties(
                water_retention=75.0,
                air_porosity=20.0,
                ph_range=(5.8, 6.5),
                ec_range=(1.8, 2.8),
                watering_frequency=2.5,
                drainage_speed="medium",
                nutrient_storage=70.0,
            ),
            MediumType.AERO: MediumProperties(
                water_retention=10.0,
                air_porosity=90.0,
                ph_range=(5.5, 6.0),
                ec_range=(1.0, 2.0),
                watering_frequency=0.5,  # Very frequent misting
                drainage_speed="very_fast",
                nutrient_storage=20.0,
            ),
            MediumType.WATER: MediumProperties(
                water_retention=100.0,
                air_porosity=0.0,
                ph_range=(5.5, 6.5),
                ec_range=(1.2, 2.0),
                watering_frequency=24.0,  # Daily top-up
                drainage_speed="none",
                nutrient_storage=10.0,
            ),
            MediumType.PERLITE: MediumProperties(
                water_retention=20.0,
                air_porosity=80.0,
                ph_range=(5.5, 7.0),
                ec_range=(1.0, 2.0),
                watering_frequency=4.0,
                drainage_speed="very_fast",
                nutrient_storage=30.0,
            ),
            MediumType.CUSTOM: MediumProperties(
                water_retention=50.0,
                air_porosity=50.0,
                ph_range=(5.5, 6.5),
                ec_range=(1.2, 2.4),
                watering_frequency=3.0,
                drainage_speed="medium",
                nutrient_storage=50.0,
            ),
        }

    def set_medium_type(
        self, medium_type: MediumType, volume_liters: float = 10.0
    ) -> bool:
        """
        Set the medium type and update properties.

        Args:
            medium_type: Type of growing medium
            volume_liters: Medium volume in liters

        Returns:
            True if medium type set successfully
        """
        try:
            if medium_type not in self.medium_presets:
                _LOGGER.error(f"{self.room} - Unknown medium type: {medium_type}")
                return False

            self.medium_type = medium_type
            self.volume_liters = volume_liters
            self.properties = self.medium_presets[medium_type]

            # Store in dataStore
            self.data_store.setDeep("Medium.type", medium_type.value)
            self.data_store.setDeep("Medium.volume", volume_liters)

            _LOGGER.info(
                f"{self.room} - Medium set to {medium_type.value} ({volume_liters}L)"
            )
            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error setting medium type: {e}")
            return False

    def get_medium_properties(self) -> Dict[str, Any]:
        """
        Get current medium properties.

        Returns:
            Dictionary of medium properties
        """
        return {
            "type": self.medium_type.value,
            "volume_liters": self.volume_liters,
            "water_retention": self.properties.water_retention,
            "air_porosity": self.properties.air_porosity,
            "ph_range": self.properties.ph_range,
            "ec_range": self.properties.ec_range,
            "watering_frequency": self.properties.watering_frequency,
            "drainage_speed": self.properties.drainage_speed,
            "nutrient_storage": self.properties.nutrient_storage,
        }

    def is_ph_optimal(self, ph_value: Optional[float] = None) -> bool:
        """
        Check if pH value is within optimal range for the medium.

        Args:
            ph_value: pH value to check (uses current if None)

        Returns:
            True if pH is optimal
        """
        if ph_value is None:
            ph_value = self.data_store.getDeep("Hydro.ph_current")

        if ph_value is None:
            return False

        min_ph, max_ph = self.properties.ph_range
        optimal = min_ph <= ph_value <= max_ph

        if not optimal:
            _LOGGER.debug(
                f"{self.room} - pH {ph_value:.2f} outside optimal range {min_ph:.1f}-{max_ph:.1f}"
            )

        return optimal

    def is_ec_optimal(self, ec_value: Optional[float] = None) -> bool:
        """
        Check if EC value is within optimal range for the medium.

        Args:
            ec_value: EC value to check (uses current if None)

        Returns:
            True if EC is optimal
        """
        if ec_value is None:
            ec_value = self.data_store.getDeep("Hydro.ec_current")

        if ec_value is None:
            return False

        min_ec, max_ec = self.properties.ec_range
        optimal = min_ec <= ec_value <= max_ec

        if not optimal:
            _LOGGER.debug(
                f"{self.room} - EC {ec_value:.2f} outside optimal range {min_ec:.2f}-{max_ec:.2f}"
            )

        return optimal

    def get_ph_status(self, ph_value: Optional[float] = None) -> Dict[str, Any]:
        """
        Get detailed pH status information.

        Args:
            ph_value: pH value to analyze (uses current if None)

        Returns:
            Dictionary with pH status details
        """
        if ph_value is None:
            ph_value = self.data_store.getDeep("Hydro.ph_current")

        min_ph, max_ph = self.properties.ph_range

        status = {
            "current": ph_value,
            "optimal_range": (min_ph, max_ph),
            "is_optimal": False,
            "status": "unknown",
            "recommendation": "",
        }

        if ph_value is None:
            status["status"] = "no_reading"
            status["recommendation"] = "Check pH sensor connection"
            return status

        status["is_optimal"] = min_ph <= ph_value <= max_ph

        if status["is_optimal"]:
            status["status"] = "optimal"
        elif ph_value < min_ph:
            status["status"] = "too_low"
            status["recommendation"] = (
                f"Increase pH by adding pH up (target: {min_ph:.1f})"
            )
        else:  # ph_value > max_ph
            status["status"] = "too_high"
            status["recommendation"] = (
                f"Decrease pH by adding pH down (target: {max_ph:.1f})"
            )

        return status

    def get_ec_status(self, ec_value: Optional[float] = None) -> Dict[str, Any]:
        """
        Get detailed EC status information.

        Args:
            ec_value: EC value to analyze (uses current if None)

        Returns:
            Dictionary with EC status details
        """
        if ec_value is None:
            ec_value = self.data_store.getDeep("Hydro.ec_current")

        min_ec, max_ec = self.properties.ec_range

        status = {
            "current": ec_value,
            "optimal_range": (min_ec, max_ec),
            "is_optimal": False,
            "status": "unknown",
            "recommendation": "",
        }

        if ec_value is None:
            status["status"] = "no_reading"
            status["recommendation"] = "Check EC sensor connection"
            return status

        status["is_optimal"] = min_ec <= ec_value <= max_ec

        if status["is_optimal"]:
            status["status"] = "optimal"
        elif ec_value < min_ec:
            status["status"] = "too_low"
            status["recommendation"] = (
                f"Add nutrients to reach EC {min_ec:.2f}-{max_ec:.2f}"
            )
        else:  # ec_value > max_ec
            status["status"] = "too_high"
            status["recommendation"] = (
                f"Dilute with water to reach EC {min_ec:.2f}-{max_ec:.2f}"
            )

        return status

    def get_moisture_status(
        self, moisture_value: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Get moisture status information.

        Args:
            moisture_value: Moisture value to analyze

        Returns:
            Dictionary with moisture status details
        """
        if moisture_value is None:
            moisture_value = self.data_store.getDeep("Medium.current_moisture")

        status = {
            "current": moisture_value,
            "optimal_range": (40.0, 80.0),  # General optimal range
            "is_optimal": False,
            "status": "unknown",
            "recommendation": "",
        }

        if moisture_value is None:
            status["status"] = "no_reading"
            status["recommendation"] = "Check moisture sensor connection"
            return status

        # Adjust optimal range based on medium type
        if self.medium_type == MediumType.AERO:
            optimal_range = (30.0, 50.0)  # Lower for aeroponics
        elif self.medium_type == MediumType.WATER:
            optimal_range = (95.0, 100.0)  # High for water culture
        else:
            optimal_range = (50.0, 75.0)  # Standard range

        status["optimal_range"] = optimal_range
        status["is_optimal"] = optimal_range[0] <= moisture_value <= optimal_range[1]

        if status["is_optimal"]:
            status["status"] = "optimal"
        elif moisture_value < optimal_range[0]:
            status["status"] = "too_dry"
            status["recommendation"] = "Increase watering frequency"
        else:  # moisture_value > optimal_range[1]
            status["status"] = "too_wet"
            status["recommendation"] = "Reduce watering frequency or improve drainage"

        return status

    def get_temperature_status(
        self, temp_value: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Get temperature status information.

        Args:
            temp_value: Temperature value to analyze

        Returns:
            Dictionary with temperature status details
        """
        if temp_value is None:
            temp_value = self.data_store.getDeep("Medium.current_temp")

        # Optimal temperature range (general for most plants)
        optimal_range = (18.0, 28.0)  # 18-28°C

        status = {
            "current": temp_value,
            "optimal_range": optimal_range,
            "is_optimal": False,
            "status": "unknown",
            "recommendation": "",
        }

        if temp_value is None:
            status["status"] = "no_reading"
            status["recommendation"] = "Check temperature sensor connection"
            return status

        status["is_optimal"] = optimal_range[0] <= temp_value <= optimal_range[1]

        if status["is_optimal"]:
            status["status"] = "optimal"
        elif temp_value < optimal_range[0]:
            status["status"] = "too_cold"
            status["recommendation"] = "Increase temperature or provide heating"
        else:  # temp_value > optimal_range[1]
            status["status"] = "too_hot"
            status["recommendation"] = "Decrease temperature or improve ventilation"

        return status

    def get_overall_status(self) -> Dict[str, Any]:
        """
        Get comprehensive medium status overview.

        Returns:
            Dictionary with overall medium health status
        """
        ph_status = self.get_ph_status()
        ec_status = self.get_ec_status()
        moisture_status = self.get_moisture_status()
        temp_status = self.get_temperature_status()

        # Calculate overall health score
        status_components = [ph_status, ec_status, moisture_status, temp_status]
        optimal_count = sum(1 for s in status_components if s.get("is_optimal", False))
        total_count = len(status_components)

        health_score = (optimal_count / total_count) * 100

        # Determine overall status
        if health_score >= 90:
            overall_status = "excellent"
        elif health_score >= 75:
            overall_status = "good"
        elif health_score >= 50:
            overall_status = "fair"
        else:
            overall_status = "poor"

        return {
            "overall_status": overall_status,
            "health_score": health_score,
            "medium_type": self.medium_type.value,
            "volume_liters": self.volume_liters,
            "ph_status": ph_status,
            "ec_status": ec_status,
            "moisture_status": moisture_status,
            "temperature_status": temp_status,
            "last_updated": datetime.now().isoformat(),
        }

    def get_watering_schedule(self) -> Dict[str, Any]:
        """
        Get recommended watering schedule based on medium properties.

        Returns:
            Dictionary with watering schedule information
        """
        base_frequency = self.properties.watering_frequency

        # Adjust based on current conditions
        ph_status = self.get_ph_status()
        ec_status = self.get_ec_status()
        moisture_status = self.get_moisture_status()

        # Increase frequency if conditions are suboptimal
        adjustment_factor = 1.0

        if not ph_status.get("is_optimal", True):
            adjustment_factor *= 1.2

        if not ec_status.get("is_optimal", True):
            adjustment_factor *= 1.1

        if moisture_status.get("status") == "too_dry":
            adjustment_factor *= 1.5
        elif moisture_status.get("status") == "too_wet":
            adjustment_factor *= 0.8

        recommended_frequency = base_frequency * adjustment_factor

        return {
            "base_frequency_hours": base_frequency,
            "adjustment_factor": adjustment_factor,
            "recommended_frequency_hours": recommended_frequency,
            "next_watering": datetime.now().timestamp()
            + (recommended_frequency * 3600),
            "reasoning": self._get_watering_reasoning(
                ph_status, ec_status, moisture_status
            ),
        }

    def _get_watering_reasoning(
        self, ph_status: Dict, ec_status: Dict, moisture_status: Dict
    ) -> List[str]:
        """
        Generate reasoning for watering schedule adjustments.

        Args:
            ph_status: pH status information
            ec_status: EC status information
            moisture_status: Moisture status information

        Returns:
            List of reasoning strings
        """
        reasoning = []

        if not ph_status.get("is_optimal", True):
            reasoning.append(f"pH {ph_status.get('status', 'suboptimal')}")

        if not ec_status.get("is_optimal", True):
            reasoning.append(f"EC {ec_status.get('status', 'suboptimal')}")

        if moisture_status.get("status") == "too_dry":
            reasoning.append("medium too dry")
        elif moisture_status.get("status") == "too_wet":
            reasoning.append("medium too wet")

        if not reasoning:
            reasoning.append("optimal conditions")

        return reasoning

    def get_nutrient_requirements(self) -> Dict[str, Any]:
        """
        Get nutrient requirements based on medium properties.

        Returns:
            Dictionary with nutrient requirement information
        """
        # Base requirements adjusted by medium nutrient storage capacity
        storage_factor = self.properties.nutrient_storage / 50.0  # Normalize to 50%

        return {
            "medium_type": self.medium_type.value,
            "nutrient_storage_capacity": self.properties.nutrient_storage,
            "recommended_feeding_frequency": self.properties.watering_frequency
            * storage_factor,
            "nutrient_concentration_factor": storage_factor,
            "notes": self._get_nutrient_notes(),
        }

    def _get_nutrient_notes(self) -> List[str]:
        """
        Get specific notes about nutrient management for this medium.

        Returns:
            List of nutrient management notes
        """
        notes = []

        if self.medium_type == MediumType.AERO:
            notes.append(
                "Aeroponic systems require frequent, low-concentration nutrient delivery"
            )
        elif self.medium_type == MediumType.WATER:
            notes.append(
                "Hydroponic systems need stable nutrient levels and pH monitoring"
            )
        elif self.medium_type == MediumType.SOIL:
            notes.append(
                "Soil systems benefit from organic matter and beneficial microbes"
            )
        elif self.medium_type == MediumType.COCO:
            notes.append("Coco coir may require calcium/magnesium supplementation")

        return notes

    def validate_medium_setup(self) -> Dict[str, Any]:
        """
        Validate the current medium setup and configuration.

        Returns:
            Dictionary with validation results
        """
        validation = {
            "valid": True,
            "warnings": [],
            "errors": [],
            "recommendations": [],
        }

        # Check if medium type is set
        if not hasattr(self, "medium_type") or self.medium_type is None:
            validation["errors"].append("Medium type not set")
            validation["valid"] = False

        # Check volume
        if self.volume_liters <= 0:
            validation["errors"].append("Invalid medium volume")
            validation["valid"] = False

        # Check properties exist
        if not hasattr(self, "properties") or self.properties is None:
            validation["errors"].append("Medium properties not initialized")
            validation["valid"] = False

        # Validate property ranges
        if hasattr(self, "properties") and self.properties:
            props = self.properties

            if not (0 <= props.water_retention <= 100):
                validation["warnings"].append("Water retention outside normal range")

            if not (0 <= props.air_porosity <= 100):
                validation["warnings"].append("Air porosity outside normal range")

            if props.ph_range[0] >= props.ph_range[1]:
                validation["errors"].append("Invalid pH range")
                validation["valid"] = False

            if props.ec_range[0] >= props.ec_range[1]:
                validation["errors"].append("Invalid EC range")
                validation["valid"] = False

        if validation["errors"]:
            validation["valid"] = False

        return validation
