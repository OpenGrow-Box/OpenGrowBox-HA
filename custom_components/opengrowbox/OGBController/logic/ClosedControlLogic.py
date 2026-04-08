"""
OpenGrowBox Closed Control Logic

Core algorithms for ambient-enhanced temperature and humidity control in closed environments.
Provides VPD-perfection-like precision but optimized for sealed chambers with ambient awareness.
"""

import logging
from typing import Optional, Dict, Any

_LOGGER = logging.getLogger(__name__)


class ClosedControlLogic:
    """
    Core control logic for closed environments with ambient-enhanced optimization.

    Calculates optimal temperature and humidity targets using plant stage requirements
    enhanced by ambient condition analysis for energy-efficient, precise control.
    """

    def __init__(self, data_store, room: str):
        """
        Initialize the closed control logic.

        Args:
            data_store: Reference to the data store
            room: Room identifier
        """
        self.data_store = data_store
        self.room = room

        self.temp_tolerance = 1.0  # 1°C tolerance for control
        self.humidity_tolerance = 3.0  # 3% RH tolerance for control

        self.ambient_temp_influence = 0.3
        self.ambient_humidity_influence = 0.4
        self.ambient_buffer_zone = 2.0

    def get_control_limits(self) -> Dict[str, Any]:
        """
        Get the control limits for closed environment from tentData.

        tentData is ALWAYS filled with either:
        - plantstage-specific min/max values (from plantStages config)
        - OR user-defined min/max values (from UI)

        These are the control limits - Closed Environment maintains temperature
        and humidity within these bounds.

        Returns:
            Dict with minTemp, maxTemp, minHumidity, maxHumidity or None values
        """
        limits = {
            "minTemp": None,
            "maxTemp": None,
            "minHumidity": None,
            "maxHumidity": None,
        }

        # Get limits directly from tentData (always filled)
        limits["minTemp"] = self.data_store.getDeep("tentData.minTemp")
        limits["maxTemp"] = self.data_store.getDeep("tentData.maxTemp")
        limits["minHumidity"] = self.data_store.getDeep("tentData.minHumidity")
        limits["maxHumidity"] = self.data_store.getDeep("tentData.maxHumidity")

        _LOGGER.debug(
            f"{self.room}: Closed limits - "
            f"Temp: {limits.get('minTemp')} / {limits.get('maxTemp')}°C, "
            f"Humidity: {limits.get('minHumidity')} / {limits.get('maxHumidity')}%"
        )

        return limits

    def calculate_temperature_deviation(self) -> Dict[str, Any]:
        """
        Calculate temperature deviation from limits.

        Returns deviation and direction for control decisions.

        Returns:
            Dict with current, min, max, deviation, status
        """
        limits = self.get_control_limits()
        current = self.data_store.getDeep("tentData.temperature")

        if current is None or limits.get("minTemp") is None or limits.get("maxTemp") is None:
            return {"current": None, "min": limits.get("minTemp"), "max": limits.get("maxTemp"), "deviation": 0, "status": "no_data"}

        try:
            current = float(current)
            min_temp = float(limits["minTemp"])
            max_temp = float(limits["maxTemp"])

            if current < min_temp:
                deviation = current - min_temp
                status = "too_low"
            elif current > max_temp:
                deviation = current - max_temp
                status = "too_high"
            else:
                deviation = 0
                status = "in_range"

            return {
                "current": current,
                "min": min_temp,
                "max": max_temp,
                "deviation": deviation,
                "status": status
            }
        except (TypeError, ValueError):
            return {"current": None, "min": limits.get("minTemp"), "max": limits.get("maxTemp"), "deviation": 0, "status": "invalid"}

    def calculate_humidity_deviation(self) -> Dict[str, Any]:
        """
        Calculate humidity deviation from limits.

        Returns deviation and direction for control decisions.

        Returns:
            Dict with current, min, max, deviation, status
        """
        limits = self.get_control_limits()
        current = self.data_store.getDeep("tentData.humidity")

        if current is None or limits.get("minHumidity") is None or limits.get("maxHumidity") is None:
            return {"current": None, "min": limits.get("minHumidity"), "max": limits.get("maxHumidity"), "deviation": 0, "status": "no_data"}

        try:
            current = float(current)
            min_hum = float(limits["minHumidity"])
            max_hum = float(limits["maxHumidity"])

            if current < min_hum:
                deviation = current - min_hum
                status = "too_low"
            elif current > max_hum:
                deviation = current - max_hum
                status = "too_high"
            else:
                deviation = 0
                status = "in_range"

            return {
                "current": current,
                "min": min_hum,
                "max": max_hum,
                "deviation": deviation,
                "status": status
            }
        except (TypeError, ValueError):
            return {"current": None, "min": limits.get("minHumidity"), "max": limits.get("maxHumidity"), "deviation": 0, "status": "invalid"}

    def get_ambient_temperature(self) -> Optional[float]:
        """
        Get ambient temperature for decision making.

        Returns:
            Ambient temperature from tentData.AmbientTemp or None
        """
        return self.data_store.getDeep("tentData.AmbientTemp")


    def get_ambient_temperature(self) -> Optional[float]:
        """
        Get ambient temperature for decision making.

        Returns:
            Ambient temperature from tentData.AmbientTemp or None
        """
        return self.data_store.getDeep("tentData.AmbientTemp")

    def _get_plant_stage_data(self, plant_stage: str) -> Optional[dict]:
        """
        Get plant stage data from datastore.

        Args:
            plant_stage: Plant stage name

        Returns:
            Plant stage data dictionary, or None
        """
        if not plant_stage:
            return None

        plant_stages = self.data_store.get("plantStages")
        if plant_stages and plant_stage in plant_stages:
            return plant_stages[plant_stage]
        return None

    def get_temperature_targets_for_stage(self, plant_stage: str) -> Optional[dict]:
        """
        Get temperature targets for a specific plant stage from datastore.

        Args:
            plant_stage: Plant stage name

        Returns:
            Temperature target dictionary with minTemp/maxTemp, or None
        """
        stage_data = self._get_plant_stage_data(plant_stage)
        if stage_data:
            return {
                "min": stage_data.get("minTemp"),
                "max": stage_data.get("maxTemp"),
                "optimal": (stage_data.get("minTemp", 0) + stage_data.get("maxTemp", 0)) / 2
            }
        return None

    def get_humidity_targets_for_stage(self, plant_stage: str) -> Optional[dict]:
        """
        Get humidity targets for a specific plant stage from datastore.

        Args:
            plant_stage: Plant stage name

        Returns:
            Humidity target dictionary with minHumidity/maxHumidity, or None
        """
        stage_data = self._get_plant_stage_data(plant_stage)
        if stage_data:
            return {
                "min": stage_data.get("minHumidity"),
                "max": stage_data.get("maxHumidity"),
                "optimal": (stage_data.get("minHumidity", 0) + stage_data.get("maxHumidity", 0)) / 2
            }
        return None

    def set_ambient_influence(self, temp_influence: Optional[float] = None, humidity_influence: Optional[float] = None):
        """
        Set ambient influence strength.

        Args:
            temp_influence: Temperature influence (0.0-1.0)
            humidity_influence: Humidity influence (0.0-1.0)
        """
        if temp_influence is not None:
            self.ambient_temp_influence = max(0.0, min(1.0, temp_influence))
        if humidity_influence is not None:
            self.ambient_humidity_influence = max(0.0, min(1.0, humidity_influence))

        _LOGGER.info(
            f"Ambient influence updated for {self.room}: "
            f"temp={self.ambient_temp_influence}, humidity={self.ambient_humidity_influence}"
        )

    def get_control_parameters(self) -> dict:
        """
        Get current control parameters.

        Returns:
            Dictionary with control parameters
        """
        return {
            "ambient_temp_influence": self.ambient_temp_influence,
            "ambient_humidity_influence": self.ambient_humidity_influence,
            "ambient_buffer_zone": self.ambient_buffer_zone,
            "temp_tolerance": self.temp_tolerance,
            "humidity_tolerance": self.humidity_tolerance,
            "current_plant_stage": self._get_current_plant_stage(),
        }
