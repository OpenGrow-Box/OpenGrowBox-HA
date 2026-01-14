"""CO2 aggregation manager for OpenGrowBox.

Handles CO2 sensor aggregation and writes averaged values to datastore.
Similar to OGBVPDManager but specific to CO2 sensors.
"""

import logging
from typing import List, Optional
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class OGBCO2Manager:
    """Manages CO2 sensor data aggregation for accurate readings."""

    def __init__(self, event_manager, data_store, room):
        """
        Initialize CO2 Manager.

        Args:
            event_manager: Event manager for pub/sub
            data_store: Datastore for storing CO2 values
            room: Room name for logging
        """
        self.event_manager = event_manager
        self.data_store = data_store
        self.room = room
        self.co2_sensors: List[float] = []
        self.max_sensor_count = 5  # Keep last N readings
        self.last_write_time: Optional[datetime] = None

        # Register for CO2 update events from sensors
        self.event_manager.on("CO2Check", self.handle_co2_update)

        _LOGGER.info(f"{self.room}: OGBCO2Manager initialized")

    def handle_co2_update(self, value: float):
        """
        Handle CO2 sensor updates and aggregate values.

        Args:
            value: CO2 reading in ppm
        """
        try:
            # Validate CO2 value
            if not isinstance(value, (int, float)) or value < 0 or value > 5000:
                _LOGGER.warning(
                    f"{self.room}: Invalid CO2 value received: {value}, ignoring"
                )
                return

            # Add to sensor list
            self.co2_sensors.append(float(value))

            # Keep only last N values
            if len(self.co2_sensors) > self.max_sensor_count:
                self.co2_sensors = self.co2_sensors[-self.max_sensor_count:]

            # Calculate average
            avg_co2 = sum(self.co2_sensors) / len(self.co2_sensors)

            # Write to datastore (same path as hardcoded in Sensor.py)
            self.data_store.setDeep("tentData.co2Level", avg_co2)

            _LOGGER.debug(
                f"{self.room}: CO2 aggregated - count={len(self.co2_sensors)}, "
                f"avg={avg_co2:.1f} ppm"
            )

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error handling CO2 update: {e}")

    def reset_sensor_data(self):
        """Reset sensor data buffer."""
        self.co2_sensors = []
        _LOGGER.debug(f"{self.room}: CO2 sensor data reset")
