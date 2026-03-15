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
        Get the actual min/max control limits for closed environment.
        Returns the broad safety limits (not perfection range).
        This is used like VPD: control when outside min/max bounds.
        
        Returns:
            Dict with minTemp, maxTemp, minHumidity, maxHumidity or None values
        """
        limits = {
            "minTemp": None,
            "maxTemp": None,
            "minHumidity": None,
            "maxHumidity": None,
        }
        
        limits["minTemp"] = self._get_limit_value("tentData.minTemp", "minTemp")
        limits["maxTemp"] = self._get_limit_value("tentData.maxTemp", "maxTemp")
        limits["minHumidity"] = self._get_limit_value("tentData.minHumidity", "minHumidity")
        limits["maxHumidity"] = self._get_limit_value("tentData.maxHumidity", "maxHumidity")
        
        _LOGGER.debug(
            f"{self.room}: Closed limits - "
            f"Temp: {limits.get('minTemp')} / {limits.get('maxTemp')}°C, "
            f"Humidity: {limits.get('minHumidity')} / {limits.get('maxHumidity')}%"
        )
        
        return limits
    
    def _get_limit_value(self, tent_key: str, stage_key: str) -> Optional[float]:
        """Get a limit value from tentData or plantStages."""
        value = self.data_store.getDeep(tent_key)
        
        if value is None:
            plant_stage = self._get_current_plant_stage()
            if plant_stage:
                stage_data = self._get_plant_stage_data(plant_stage)
                if stage_data:
                    value = stage_data.get(stage_key)
        
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        
        return None

    def calculate_temperature_deviation(self) -> Dict[str, Any]:
        """
        Calculate temperature deviation from limits (like VPD).
        Returns the deviation and direction for control decisions.
        
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
        Calculate humidity deviation from limits (like VPD).
        Returns the deviation and direction for control decisions.
        
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
    
    async def calculate_optimal_temperature_target(self) -> Optional[float]:
        """Legacy method - returns midpoint for compatibility."""
        limits = self.get_control_limits()
        if limits.get("minTemp") and limits.get("maxTemp"):
            return (limits["minTemp"] + limits["maxTemp"]) / 2
        return None
    
    async def calculate_optimal_humidity_target(self) -> Optional[float]:
        """Legacy method - returns midpoint for compatibility."""
        limits = self.get_control_limits()
        if limits.get("minHumidity") and limits.get("maxHumidity"):
            return (limits["minHumidity"] + limits["maxHumidity"]) / 2
        return None

    def _calculate_ambient_temperature_factor(self) -> float:
        """
        Calculate ambient temperature influence factor.

        Returns:
            Temperature adjustment factor in Celsius
        """
        ambient_temp = self.data_store.getDeep("tentData.AmbientTemp")
        internal_temp = self.data_store.getDeep("tentData.temperature")

        if ambient_temp is None or internal_temp is None:
            return 0.0

        # Calculate temperature gradient
        gradient = internal_temp - ambient_temp

        # Ambient influence logic for energy optimization
        if ambient_temp > 25 and gradient > self.ambient_buffer_zone:
            # Warm ambient, reduce internal heating needs
            return -1.5
        elif ambient_temp < 15 and gradient < -self.ambient_buffer_zone:
            # Cold ambient, increase internal heating buffer
            return 2.0
        elif ambient_temp > 30:
            # Very hot ambient, significant cooling buffer
            return -2.5
        elif ambient_temp < 5:
            # Very cold ambient, significant heating buffer
            return 3.0
        else:
            # Ambient within comfortable range, minimal influence
            return gradient * 0.1  # Small gradient-based adjustment

    def _calculate_ambient_humidity_factor(self) -> float:
        """
        Calculate ambient humidity influence factor.

        Returns:
            Humidity adjustment factor as percentage
        """
        ambient_humidity = self.data_store.getDeep("tentData.AmbientHum")
        internal_humidity = self.data_store.getDeep("tentData.humidity")

        if ambient_humidity is None or internal_humidity is None:
            return 0.0

        # Calculate humidity gradient
        gradient = internal_humidity - ambient_humidity

        # Ambient influence logic for dehumidifier optimization
        if ambient_humidity < 30 and internal_humidity > 50:
            # Dry ambient, can allow higher internal humidity
            return 5.0  # Increase target by 5%
        elif ambient_humidity > 80 and internal_humidity < 60:
            # Humid ambient, be more aggressive with dehumidification
            return -8.0  # Decrease target by 8%
        elif ambient_humidity < 20:
            # Very dry ambient, significant optimization opportunity
            return 8.0
        elif ambient_humidity > 90:
            # Very humid ambient, significant dehumidification need
            return -10.0
        else:
            # Ambient humidity in normal range
            return gradient * 0.2  # Small gradient-based adjustment

    def _get_current_plant_stage(self) -> Optional[str]:
        """
        Get the current plant stage.

        Returns:
            Current plant stage name, or None
        """
        plant_stage = self.data_store.get("plantStage")
        if plant_stage:
            # Normalize plant stage names
            return plant_stage.replace(" ", "").replace("-", "")
        return None

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
