"""
OpenGrowBox Closed Control Logic

Core algorithms for ambient-enhanced temperature and humidity control in closed environments.
Provides VPD-perfection-like precision but optimized for sealed chambers with ambient awareness.
"""

import logging
from typing import Optional

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

        # Plant stage ranges will be read from datastore (plantStages)
        # Similar to VPD perfection's range-based control
        self.temp_tolerance = 0.1  # 10% tolerance for perfection range
        self.humidity_tolerance = 0.15  # 15% tolerance for perfection range

        # Ambient influence parameters
        self.ambient_temp_influence = 0.3  # 30% ambient influence on temp
        self.ambient_humidity_influence = 0.4  # 40% ambient influence on humidity
        self.ambient_buffer_zone = 2.0  # °C/°C buffer for ambient effects

    async def calculate_optimal_temperature_target(self) -> Optional[float]:
        """
        Calculate optimal temperature target using plant stage ranges + ambient enhancement.
        Similar to VPD perfection: broad safety range + narrow perfection range with ambient optimization.

        Returns:
            Optimal temperature target in Celsius, or None if calculation fails
        """
        try:
            # Get plant stage ranges from datastore
            plant_stage = self._get_current_plant_stage()
            if not plant_stage:
                _LOGGER.warning("No plant stage available")
                return None

            stage_data = self._get_plant_stage_data(plant_stage)
            if not stage_data:
                _LOGGER.warning(f"Plant stage data not available for: {plant_stage}")
                return None

            # Broad safety range (never exceeded)
            broad_min = stage_data.get("minTemp")
            broad_max = stage_data.get("maxTemp")
            if broad_min is None or broad_max is None:
                _LOGGER.warning(f"Temperature range data missing for stage: {plant_stage}")
                return None

            # Calculate midpoint and perfection range (like VPD perfection)
            midpoint = (broad_min + broad_max) / 2
            perfection_range = (broad_max - broad_min) * self.temp_tolerance
            perfect_min = midpoint - perfection_range
            perfect_max = midpoint + perfection_range

            # Ensure perfection range stays within broad safety bounds
            perfect_min = max(broad_min, perfect_min)
            perfect_max = min(broad_max, perfect_max)

            # Get current temperature for control logic
            current_temp = self.data_store.getDeep("tentData.temperature")
            if current_temp is None:
                return midpoint  # Default to midpoint if no sensor data

            # Apply ambient enhancement to the perfection range
            ambient_factor = self._calculate_ambient_temperature_factor()

            # Adjust perfection range based on ambient conditions
            ambient_adjusted_min = perfect_min + ambient_factor
            ambient_adjusted_max = perfect_max + ambient_factor

            # Keep within broad safety bounds
            ambient_adjusted_min = max(broad_min, ambient_adjusted_min)
            ambient_adjusted_max = min(broad_max, ambient_adjusted_max)

            # Control logic similar to VPD perfection
            if current_temp < ambient_adjusted_min:
                # Too cold - target the adjusted minimum
                optimal_target = ambient_adjusted_min
                _LOGGER.debug(f"Temperature too low: targeting {optimal_target:.1f}°C (adjusted min)")
            elif current_temp > ambient_adjusted_max:
                # Too hot - target the adjusted maximum
                optimal_target = ambient_adjusted_max
                _LOGGER.debug(f"Temperature too high: targeting {optimal_target:.1f}°C (adjusted max)")
            else:
                # Within perfection range - target midpoint with ambient influence
                optimal_target = midpoint + (ambient_factor * self.ambient_temp_influence)
                optimal_target = max(ambient_adjusted_min, min(ambient_adjusted_max, optimal_target))
                _LOGGER.debug(f"Temperature in range: fine-tuning to {optimal_target:.1f}°C (midpoint)")

            _LOGGER.debug(
                f"Temperature ranges - Broad: [{broad_min:.1f}, {broad_max:.1f}]°C, "
                f"Perfection: [{ambient_adjusted_min:.1f}, {ambient_adjusted_max:.1f}]°C, "
                f"Current: {current_temp:.1f}°C, Target: {optimal_target:.1f}°C"
            )

            return optimal_target

        except Exception as e:
            _LOGGER.error(f"Error calculating temperature target: {e}")
            return None

    async def calculate_optimal_humidity_target(self) -> Optional[float]:
        """
        Calculate optimal humidity target using plant stage ranges + ambient enhancement.
        Similar to VPD perfection: broad safety range + narrow perfection range with ambient optimization.

        Returns:
            Optimal humidity target as percentage, or None if calculation fails
        """
        try:
            # Get plant stage ranges from datastore
            plant_stage = self._get_current_plant_stage()
            if not plant_stage:
                _LOGGER.warning("No plant stage available")
                return None

            stage_data = self._get_plant_stage_data(plant_stage)
            if not stage_data:
                _LOGGER.warning(f"Plant stage data not available for: {plant_stage}")
                return None

            # Broad safety range (never exceeded)
            broad_min = stage_data.get("minHumidity")
            broad_max = stage_data.get("maxHumidity")
            if broad_min is None or broad_max is None:
                _LOGGER.warning(f"Humidity range data missing for stage: {plant_stage}")
                return None

            # Calculate midpoint and perfection range
            midpoint = (broad_min + broad_max) / 2
            perfection_range = (broad_max - broad_min) * self.humidity_tolerance
            perfect_min = midpoint - perfection_range
            perfect_max = midpoint + perfection_range

            # Ensure perfection range stays within broad safety bounds
            perfect_min = max(broad_min, perfect_min)
            perfect_max = min(broad_max, perfect_max)

            # Get current humidity for control logic
            current_humidity = self.data_store.getDeep("tentData.humidity")
            if current_humidity is None:
                return midpoint  # Default to midpoint if no sensor data

            # Apply ambient enhancement to the perfection range
            ambient_factor = self._calculate_ambient_humidity_factor()

            # Adjust perfection range based on ambient conditions
            ambient_adjusted_min = perfect_min + ambient_factor
            ambient_adjusted_max = perfect_max + ambient_factor

            # Keep within broad safety bounds
            ambient_adjusted_min = max(broad_min, ambient_adjusted_min)
            ambient_adjusted_max = min(broad_max, ambient_adjusted_max)

            # Control logic similar to VPD perfection
            if current_humidity < ambient_adjusted_min:
                # Too dry - target the adjusted minimum
                optimal_target = ambient_adjusted_min
                _LOGGER.debug(f"Humidity too low: targeting {optimal_target:.1f}% (adjusted min)")
            elif current_humidity > ambient_adjusted_max:
                # Too humid - target the adjusted maximum
                optimal_target = ambient_adjusted_max
                _LOGGER.debug(f"Humidity too high: targeting {optimal_target:.1f}% (adjusted max)")
            else:
                # Within perfection range - target midpoint with ambient influence
                optimal_target = midpoint + (ambient_factor * self.ambient_humidity_influence)
                optimal_target = max(ambient_adjusted_min, min(ambient_adjusted_max, optimal_target))
                _LOGGER.debug(f"Humidity in range: fine-tuning to {optimal_target:.1f}% (midpoint)")

            _LOGGER.debug(
                f"Humidity ranges - Broad: [{broad_min:.1f}, {broad_max:.1f}]%, "
                f"Perfection: [{ambient_adjusted_min:.1f}, {ambient_adjusted_max:.1f}]%, "
                f"Current: {current_humidity:.1f}%, Target: {optimal_target:.1f}%"
            )

            return optimal_target

        except Exception as e:
            _LOGGER.error(f"Error calculating humidity target: {e}")
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