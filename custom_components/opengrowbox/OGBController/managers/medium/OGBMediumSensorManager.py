"""
OpenGrowBox Medium Sensor Manager

Handles sensor registration, reading updates, and data aggregation
for grow medium sensor management.

Responsibilities:
- Sensor registration and initialization
- Reading updates and processing
- Data aggregation and averaging
- Sensor-to-medium mapping
"""

import logging
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class SensorReading:
    """Individual sensor measurement with timestamp"""

    def __init__(
        self,
        value: Any,
        unit: str,
        sensor_type: str,
        device_name: str,
        timestamp: datetime,
        entity_id: str,
    ):
        self.value = value
        self.unit = unit
        self.sensor_type = sensor_type
        self.device_name = device_name
        self.timestamp = timestamp
        self.entity_id = entity_id

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "value": self.value,
            "unit": self.unit,
            "sensor_type": self.sensor_type,
            "device_name": self.device_name,
            "timestamp": self.timestamp.isoformat(),
            "entity_id": self.entity_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SensorReading":
        """Create from dictionary"""
        return cls(
            value=data["value"],
            unit=data["unit"],
            sensor_type=data["sensor_type"],
            device_name=data["device_name"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            entity_id=data["entity_id"],
        )


class ReadingHistory:
    """Manages limited history per sensor with intelligent aggregation"""

    def __init__(self, max_entries: int = 10):
        self.max_entries = max_entries
        self.readings: deque = deque(maxlen=max_entries)

    def add(self, reading: SensorReading) -> None:
        """Adds new measurement, oldest is automatically deleted"""
        self.readings.append(reading)

    def get_latest(self) -> Optional[SensorReading]:
        """Returns newest measurement"""
        return self.readings[-1] if self.readings else None

    def get_average(self) -> Optional[float]:
        """Calculates average of measurements"""
        if not self.readings:
            return None
        try:
            values = [
                float(r.value)
                for r in self.readings
                if isinstance(r.value, (int, float, str))
            ]
            return sum(values) / len(values) if values else None
        except (ValueError, TypeError):
            return None

    def get_all(self) -> list:
        """Returns all measurements in chronological order"""
        return list(self.readings)

    def to_dict(self) -> Optional[Dict[str, Any]]:
        """Serializes only relevant data"""
        if not self.readings:
            return None
        latest = self.get_latest()
        average = self.get_average()
        return {
            "latest": latest.to_dict() if latest else None,
            "average": average,
            "count": len(self.readings),
            "readings": [
                r.to_dict() for r in list(self.readings)[-5:]
            ],  # Last 5 readings
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Restores from serialized data"""
        if "readings" in data:
            self.readings.clear()
            for reading_data in data["readings"]:
                reading = SensorReading.from_dict(reading_data)
                self.readings.append(reading)


class OGBMediumSensorManager:
    """
    Sensor manager for grow medium sensor operations.

    Handles sensor registration, reading updates, data aggregation,
    and sensor-to-medium mapping.
    """

    def __init__(self, room: str, data_store, event_manager):
        """
        Initialize sensor manager.

        Args:
            room: Room identifier
            data_store: Data store instance
            event_manager: Event manager instance
        """
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager

        # Sensor tracking
        self.registered_sensors: Dict[str, Dict[str, Any]] = {}
        self.sensor_histories: Dict[str, ReadingHistory] = {}

        # Aggregated values
        self.aggregated_values: Dict[str, Any] = {}

    async def register_sensor(self, sensor_data: dict) -> bool:
        """
        Register a sensor with the medium.

        Args:
            sensor_data: Sensor registration data

        Returns:
            True if registration successful
        """
        try:
            entity_id = sensor_data.get("entity_id")
            sensor_type = sensor_data.get("sensor_type")
            device_name = sensor_data.get("device_name", "unknown")

            if not entity_id or not sensor_type:
                _LOGGER.error(
                    f"{self.room} - Invalid sensor data: missing entity_id or sensor_type"
                )
                return False

            # Check if already registered
            if entity_id in self.registered_sensors:
                _LOGGER.debug(f"{self.room} - Sensor {entity_id} already registered")
                return True

            # Create sensor entry
            sensor_entry = {
                "entity_id": entity_id,
                "sensor_type": sensor_type,
                "device_name": device_name,
                "registered_at": datetime.now(),
                "last_reading": None,
                "last_update": None,
                "status": "active",
            }

            # Add to registered sensors
            self.registered_sensors[entity_id] = sensor_entry

            # Initialize reading history
            self.sensor_histories[entity_id] = ReadingHistory()

            # Register with medium label if available
            medium_label = sensor_data.get("medium_label")
            if medium_label:
                await self._register_sensor_to_medium(
                    entity_id, sensor_type, medium_label
                )

            _LOGGER.info(f"{self.room} - Registered sensor {entity_id} ({sensor_type})")
            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error registering sensor: {e}")
            return False

    async def _register_sensor_to_medium(
        self, entity_id: str, sensor_type: str, medium_label: str
    ) -> None:
        """
        Register sensor with medium label for cross-referencing.

        Args:
            entity_id: Sensor entity ID
            sensor_type: Sensor type
            medium_label: Medium label
        """
        try:
            await self.event_manager.emit(
                "RegisterSensorToMedium",
                {
                    "entity_id": entity_id,
                    "sensor_type": sensor_type,
                    "medium_label": medium_label,
                    "room": self.room,
                    "device_name": f"medium_{medium_label}",
                },
            )
        except Exception as e:
            _LOGGER.error(f"{self.room} - Error registering sensor to medium: {e}")

    def unregister_sensor(self, entity_id: str) -> bool:
        """
        Unregister a sensor from the medium.

        Args:
            entity_id: Sensor entity ID

        Returns:
            True if unregistration successful
        """
        try:
            if entity_id in self.registered_sensors:
                del self.registered_sensors[entity_id]

            if entity_id in self.sensor_histories:
                del self.sensor_histories[entity_id]

            # Update aggregated values for the unregistered sensor type
            sensor_type = self.registered_sensors[entity_id]["sensor_type"]
            self._update_aggregated_value(sensor_type)

            _LOGGER.info(f"{self.room} - Unregistered sensor {entity_id}")
            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error unregistering sensor {entity_id}: {e}")
            return False

    async def update_sensor_reading(self, sensor_data: Dict[str, Any]) -> bool:
        """
        Update sensor reading with new data.

        Args:
            sensor_data: Sensor reading data

        Returns:
            True if update successful
        """
        try:
            entity_id = sensor_data.get("entity_id") or sensor_data.get("Name")
            value = sensor_data.get("value") or sensor_data.get("newState", [None])[0]

            if not entity_id or value is None:
                return False

            # Check if sensor is registered
            if entity_id not in self.registered_sensors:
                _LOGGER.debug(
                    f"{self.room} - Sensor {entity_id} not registered, ignoring update"
                )
                return False

            sensor_entry = self.registered_sensors[entity_id]
            sensor_type = sensor_entry["sensor_type"]

            # Create reading object
            reading = SensorReading(
                value=value,
                unit=sensor_data.get("unit", ""),
                sensor_type=sensor_type,
                device_name=sensor_entry["device_name"],
                timestamp=datetime.now(),
                entity_id=entity_id,
            )

            # Add to history
            if entity_id in self.sensor_histories:
                self.sensor_histories[entity_id].add(reading)

            # Update sensor entry
            sensor_entry["last_reading"] = value
            sensor_entry["last_update"] = reading.timestamp

            # Update aggregated values
            self._update_aggregated_value(sensor_type)

            # Update datastore
            self._update_datastore_value(sensor_type, value)

            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error updating sensor reading: {e}")
            return False

    def _update_aggregated_value(self, sensor_type: str) -> None:
        """
        Update aggregated value for a sensor type.

        Args:
            sensor_type: Sensor type to aggregate
        """
        try:
            # Get all sensors of this type
            type_sensors = [
                entity_id
                for entity_id, sensor in self.registered_sensors.items()
                if sensor["sensor_type"] == sensor_type and sensor["status"] == "active"
            ]

            if not type_sensors:
                return

            # Calculate average across all sensors of this type
            values = []
            for entity_id in type_sensors:
                history = self.sensor_histories.get(entity_id)
                if history:
                    latest = history.get_latest()
                    if latest and isinstance(latest.value, (int, float)):
                        values.append(float(latest.value))

            if values:
                aggregated_value = sum(values) / len(values)
                self.aggregated_values[sensor_type] = aggregated_value
            else:
                self.aggregated_values.pop(sensor_type, None)

        except Exception as e:
            _LOGGER.error(
                f"{self.room} - Error updating aggregated value for {sensor_type}: {e}"
            )

    def _update_datastore_value(self, sensor_type: str, value: Any) -> None:
        """
        Update datastore with sensor value.

        Args:
            sensor_type: Sensor type
            value: Sensor value
        """
        try:
            # Map sensor types to datastore paths
            path_mapping = {
                "temperature": "Medium.current_temp",
                "humidity": "Medium.current_humidity",
                "ph": "Hydro.ph_current",
                "ec": "Hydro.ec_current",
                "moisture": "Medium.current_moisture",
            }

            if sensor_type in path_mapping:
                self.data_store.setDeep(path_mapping[sensor_type], value)

        except Exception as e:
            _LOGGER.error(
                f"{self.room} - Error updating datastore for {sensor_type}: {e}"
            )

    def get_sensor_value(self, sensor_type: str) -> Optional[Any]:
        """
        Get current value for a sensor type.

        Args:
            sensor_type: Sensor type to query

        Returns:
            Current aggregated value or None
        """
        return self.aggregated_values.get(sensor_type)

    def get_sensor_history(self, sensor_type: str) -> Optional[Dict[str, Any]]:
        """
        Get reading history for a sensor type.

        Args:
            sensor_type: Sensor type

        Returns:
            History data dictionary or None
        """
        # Find first sensor of this type
        for entity_id, sensor in self.registered_sensors.items():
            if sensor["sensor_type"] == sensor_type:
                history = self.sensor_histories.get(entity_id)
                return history.to_dict() if history else None

        return None

    def get_all_sensor_values(self) -> Dict[str, Any]:
        """
        Get all current sensor values.

        Returns:
            Dictionary of all sensor values
        """
        return self.aggregated_values.copy()

    def get_registered_sensors(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all registered sensors.

        Returns:
            Dictionary of registered sensors
        """
        return self.registered_sensors.copy()

    def get_sensor_count(self) -> Dict[str, int]:
        """
        Get count of sensors by type.

        Returns:
            Dictionary with sensor counts by type
        """
        counts = {}
        for sensor in self.registered_sensors.values():
            sensor_type = sensor["sensor_type"]
            counts[sensor_type] = counts.get(sensor_type, 0) + 1

        return counts

    def reset_sensor_histories(self) -> None:
        """
        Reset all sensor reading histories.
        """
        for history in self.sensor_histories.values():
            history.readings.clear()

        _LOGGER.info(f"{self.room} - Reset all sensor histories")

    def get_sensor_status(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a specific sensor.

        Args:
            entity_id: Sensor entity ID

        Returns:
            Sensor status dictionary or None
        """
        if entity_id not in self.registered_sensors:
            return None

        sensor = self.registered_sensors[entity_id]
        history = self.sensor_histories.get(entity_id)

        return {
            "entity_id": entity_id,
            "sensor_type": sensor["sensor_type"],
            "device_name": sensor["device_name"],
            "status": sensor["status"],
            "last_reading": sensor["last_reading"],
            "last_update": sensor.get("last_update"),
            "history_count": len(history.readings) if history else 0,
            "registered_at": sensor["registered_at"],
        }
