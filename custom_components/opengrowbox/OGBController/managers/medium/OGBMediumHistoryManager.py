"""
OpenGrowBox Medium History Manager

Handles reading history management, serialization, and data persistence
for grow medium sensor data.

Responsibilities:
- Sensor reading history storage and retrieval
- History serialization and deserialization
- Data aggregation and averaging over time
- History cleanup and memory management
"""

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBMediumHistoryManager:
    """
    History manager for grow medium sensor readings.

    Handles reading history storage, serialization, aggregation,
    and memory management for medium sensor data.
    """

    def __init__(self, room: str, data_store):
        """
        Initialize history manager.

        Args:
            room: Room identifier
            data_store: Data store instance
        """
        self.room = room
        self.data_store = data_store

        # History storage
        self.histories: Dict[str, deque] = {}

        # Configuration
        self.max_entries_per_sensor = 50
        self.history_retention_days = 7

    def add_reading(
        self, sensor_type: str, value: Any, unit: str, entity_id: str, device_name: str
    ) -> None:
        """
        Add a new sensor reading to history.

        Args:
            sensor_type: Type of sensor reading
            value: Sensor value
            unit: Unit of measurement
            entity_id: Sensor entity ID
            device_name: Device name
        """
        try:
            # Create reading entry
            reading = {
                "value": value,
                "unit": unit,
                "sensor_type": sensor_type,
                "entity_id": entity_id,
                "device_name": device_name,
                "timestamp": datetime.now(),
            }

            # Initialize history for this sensor if needed
            if sensor_type not in self.histories:
                self.histories[sensor_type] = deque(maxlen=self.max_entries_per_sensor)

            # Add to history
            self.histories[sensor_type].append(reading)

            # Store in dataStore (keep only recent entries)
            self._store_reading(sensor_type, reading)

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error adding reading to history: {e}")

    def get_recent_readings(
        self, sensor_type: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get recent readings for a sensor type.

        Args:
            sensor_type: Type of sensor
            limit: Maximum number of readings to return

        Returns:
            List of recent readings
        """
        if sensor_type not in self.histories:
            return []

        readings = list(self.histories[sensor_type])
        return readings[-limit:] if limit > 0 else readings

    def get_average_reading(self, sensor_type: str, hours: int = 1) -> Optional[float]:
        """
        Get average reading for a sensor type over the specified hours.

        Args:
            sensor_type: Type of sensor
            hours: Number of hours to average over

        Returns:
            Average value or None if no data
        """
        if sensor_type not in self.histories:
            return None

        cutoff_time = datetime.now() - timedelta(hours=hours)

        # Filter readings within time window
        recent_readings = [
            reading
            for reading in self.histories[sensor_type]
            if reading["timestamp"] > cutoff_time
        ]

        if not recent_readings:
            return None

        # Extract numeric values
        values = []
        for reading in recent_readings:
            try:
                value = reading["value"]
                if isinstance(value, (int, float)):
                    values.append(float(value))
                elif isinstance(value, str):
                    # Try to convert string to float
                    values.append(float(value))
            except (ValueError, TypeError):
                continue

        return sum(values) / len(values) if values else None

    def get_reading_trends(self, sensor_type: str, hours: int = 24) -> Dict[str, Any]:
        """
        Get reading trends and statistics for a sensor type.

        Args:
            sensor_type: Type of sensor
            hours: Number of hours to analyze

        Returns:
            Dictionary with trend information
        """
        readings = self.get_recent_readings(sensor_type, limit=100)

        if not readings:
            return {"available": False}

        # Filter by time
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_readings = [r for r in readings if r["timestamp"] > cutoff_time]

        if not recent_readings:
            return {"available": False}

        # Extract values
        values = []
        timestamps = []

        for reading in recent_readings:
            try:
                value = reading["value"]
                if isinstance(value, (int, float)):
                    values.append(float(value))
                    timestamps.append(reading["timestamp"])
            except (ValueError, TypeError):
                continue

        if not values:
            return {"available": False, "reason": "no_numeric_values"}

        # Calculate statistics
        min_val = min(values)
        max_val = max(values)
        avg_val = sum(values) / len(values)

        # Calculate trend (slope)
        if len(values) >= 2:
            # Simple linear trend
            time_diffs = [
                (t - timestamps[0]).total_seconds() / 3600 for t in timestamps
            ]
            trend_slope = self._calculate_trend_slope(time_diffs, values)
        else:
            trend_slope = 0.0

        # Determine trend direction
        if trend_slope > 0.01:
            trend_direction = "increasing"
        elif trend_slope < -0.01:
            trend_direction = "decreasing"
        else:
            trend_direction = "stable"

        return {
            "available": True,
            "count": len(values),
            "min": min_val,
            "max": max_val,
            "average": avg_val,
            "range": max_val - min_val,
            "trend_slope": trend_slope,
            "trend_direction": trend_direction,
            "time_span_hours": hours,
        }

    def _calculate_trend_slope(self, times: List[float], values: List[float]) -> float:
        """
        Calculate the slope of the trend line.

        Args:
            times: Time values (hours)
            values: Sensor values

        Returns:
            Slope of the trend line
        """
        try:
            n = len(values)
            if n < 2:
                return 0.0

            # Calculate means
            mean_time = sum(times) / n
            mean_value = sum(values) / n

            # Calculate slope
            numerator = sum(
                (times[i] - mean_time) * (values[i] - mean_value) for i in range(n)
            )
            denominator = sum((times[i] - mean_time) ** 2 for i in range(n))

            return numerator / denominator if denominator != 0 else 0.0

        except Exception as e:
            _LOGGER.error(f"Error calculating trend slope: {e}")
            return 0.0

    def get_history_summary(self, sensor_type: str) -> Dict[str, Any]:
        """
        Get a summary of reading history for a sensor type.

        Args:
            sensor_type: Type of sensor

        Returns:
            Dictionary with history summary
        """
        readings = self.get_recent_readings(sensor_type, limit=100)

        summary = {
            "sensor_type": sensor_type,
            "total_readings": len(readings),
            "available": len(readings) > 0,
        }

        if not readings:
            return summary

        # Get time range
        timestamps = [r["timestamp"] for r in readings]
        oldest = min(timestamps)
        newest = max(timestamps)

        summary.update(
            {
                "oldest_reading": oldest.isoformat(),
                "newest_reading": newest.isoformat(),
                "time_span_hours": (newest - oldest).total_seconds() / 3600,
            }
        )

        # Get trends
        trends = self.get_reading_trends(sensor_type)
        if trends.get("available"):
            summary["trends"] = trends

        return summary

    def export_history(self, sensor_type: str) -> Optional[Dict[str, Any]]:
        """
        Export complete history for a sensor type.

        Args:
            sensor_type: Type of sensor

        Returns:
            Dictionary with complete history data or None
        """
        readings = self.get_recent_readings(sensor_type, limit=0)  # Get all

        if not readings:
            return None

        return {
            "sensor_type": sensor_type,
            "exported_at": datetime.now().isoformat(),
            "total_readings": len(readings),
            "readings": [
                {
                    "timestamp": r["timestamp"].isoformat(),
                    "value": r["value"],
                    "unit": r["unit"],
                    "entity_id": r["entity_id"],
                    "device_name": r["device_name"],
                }
                for r in readings
            ],
        }

    def import_history(self, history_data: Dict[str, Any]) -> bool:
        """
        Import history data for a sensor type.

        Args:
            history_data: History data to import

        Returns:
            True if import successful
        """
        try:
            sensor_type = history_data.get("sensor_type")
            readings = history_data.get("readings", [])

            if not sensor_type or not readings:
                return False

            # Initialize history for this sensor
            if sensor_type not in self.histories:
                self.histories[sensor_type] = deque(maxlen=self.max_entries_per_sensor)

            # Add readings
            for reading_data in readings:
                try:
                    timestamp = datetime.fromisoformat(reading_data["timestamp"])

                    # Only import readings within retention period
                    if datetime.now() - timestamp < timedelta(
                        days=self.history_retention_days
                    ):
                        reading = {
                            "value": reading_data["value"],
                            "unit": reading_data["unit"],
                            "sensor_type": sensor_type,
                            "entity_id": reading_data["entity_id"],
                            "device_name": reading_data["device_name"],
                            "timestamp": timestamp,
                        }

                        self.histories[sensor_type].append(reading)

                except (ValueError, KeyError) as e:
                    _LOGGER.warning(f"Skipping invalid reading during import: {e}")
                    continue

            _LOGGER.info(
                f"{self.room} - Imported {len(readings)} readings for {sensor_type}"
            )
            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error importing history: {e}")
            return False

    def _store_reading(self, sensor_type: str, reading: Dict[str, Any]) -> None:
        """
        Store a reading in the dataStore.

        Args:
            sensor_type: Type of sensor
            reading: Reading data to store
        """
        try:
            # Store in dataStore with limited history
            history_key = f"Medium.History.{sensor_type}"

            # Get existing history
            existing_history = self.data_store.getDeep(history_key) or []

            # Add new reading
            existing_history.append(
                {
                    "timestamp": reading["timestamp"].isoformat(),
                    "value": reading["value"],
                    "unit": reading["unit"],
                    "entity_id": reading["entity_id"],
                }
            )

            # Keep only recent entries
            max_stored = min(self.max_entries_per_sensor, 20)  # Limit stored history
            if len(existing_history) > max_stored:
                existing_history = existing_history[-max_stored:]

            # Store back
            self.data_store.setDeep(history_key, existing_history)

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error storing reading: {e}")

    def cleanup_old_history(self, days_to_keep: int = 7) -> int:
        """
        Clean up old history entries beyond the retention period.

        Args:
            days_to_keep: Number of days to keep history

        Returns:
            Number of entries cleaned up
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=days_to_keep)
            total_cleaned = 0

            for sensor_type, history in self.histories.items():
                # Filter out old entries
                original_count = len(history)
                filtered_history = deque(
                    (
                        reading
                        for reading in history
                        if reading["timestamp"] > cutoff_time
                    ),
                    maxlen=self.max_entries_per_sensor,
                )

                self.histories[sensor_type] = filtered_history
                cleaned_count = original_count - len(filtered_history)
                total_cleaned += cleaned_count

                if cleaned_count > 0:
                    _LOGGER.debug(
                        f"{self.room} - Cleaned {cleaned_count} old entries for {sensor_type}"
                    )

            # Also clean dataStore
            self._cleanup_datastore_history(days_to_keep)

            return total_cleaned

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error cleaning up history: {e}")
            return 0

    def _cleanup_datastore_history(self, days_to_keep: int) -> None:
        """
        Clean up old history entries from dataStore.

        Args:
            days_to_keep: Number of days to keep
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=days_to_keep)

            # Get all history keys
            history_keys = [
                key
                for key in self.data_store.getAllKeys()
                if key.startswith("Medium.History.")
            ]

            for key in history_keys:
                history_data = self.data_store.getDeep(key)
                if history_data and isinstance(history_data, list):
                    # Filter out old entries
                    filtered_data = [
                        entry
                        for entry in history_data
                        if datetime.fromisoformat(entry["timestamp"]) > cutoff_time
                    ]

                    if len(filtered_data) != len(history_data):
                        self.data_store.setDeep(key, filtered_data)
                        _LOGGER.debug(
                            f"Cleaned {len(history_data) - len(filtered_data)} entries from {key}"
                        )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error cleaning dataStore history: {e}")

    def get_storage_stats(self) -> Dict[str, Any]:
        """
        Get storage statistics for history data.

        Returns:
            Dictionary with storage statistics
        """
        stats = {
            "total_sensors": len(self.histories),
            "total_readings": sum(len(history) for history in self.histories.values()),
            "max_entries_per_sensor": self.max_entries_per_sensor,
            "retention_days": self.history_retention_days,
            "sensor_breakdown": {},
        }

        for sensor_type, history in self.histories.items():
            stats["sensor_breakdown"][sensor_type] = {
                "count": len(history),
                "oldest": None,
                "newest": None,
            }

            if history:
                timestamps = [r["timestamp"] for r in history]
                stats["sensor_breakdown"][sensor_type].update(
                    {
                        "oldest": min(timestamps).isoformat(),
                        "newest": max(timestamps).isoformat(),
                    }
                )

        return stats

    def clear_history(self, sensor_type: Optional[str] = None) -> int:
        """
        Clear history data.

        Args:
            sensor_type: Specific sensor type to clear, or None for all

        Returns:
            Number of entries cleared
        """
        try:
            cleared_count = 0

            if sensor_type:
                if sensor_type in self.histories:
                    cleared_count = len(self.histories[sensor_type])
                    self.histories[sensor_type].clear()
                    # Also clear from dataStore
                    history_key = f"Medium.History.{sensor_type}"
                    self.data_store.setDeep(history_key, [])
            else:
                for sensor_type_key in list(self.histories.keys()):
                    cleared_count += len(self.histories[sensor_type_key])
                    self.histories[sensor_type_key].clear()

                # Clear all history from dataStore
                history_keys = [
                    key
                    for key in self.data_store.getAllKeys()
                    if key.startswith("Medium.History.")
                ]
                for key in history_keys:
                    self.data_store.setDeep(key, [])

            _LOGGER.info(f"{self.room} - Cleared {cleared_count} history entries")
            return cleared_count

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error clearing history: {e}")
            return 0
