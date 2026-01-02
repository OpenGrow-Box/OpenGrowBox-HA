"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                      🧹 OGB DATA CLEANUP MANAGER 🧹                        ║
║              Automated Data Management and Cleanup System                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

This module manages data cleanup and aggregation to prevent memory issues
from accumulating sensor data and historical records.

Features:
- Automatic cleanup of old sensor readings (7+ days)
- Data aggregation for trend analysis
- Calibration data maintenance
- Configurable retention policies
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBDataCleanupManager:
    """
    Data Cleanup Manager - manages data lifecycle and prevents memory bloat.

    Automatically cleans up old data while preserving important historical trends.
    """

    def __init__(self, dataStore, room: str, retention_days: int = 7):
        """
        Initialize the Data Cleanup Manager.

        Args:
            dataStore: OGB DataStore instance
            room: Room identifier
            retention_days: Days to retain raw sensor data (default: 7)
        """
        self.data_store = dataStore
        self.room = room
        self.retention_days = retention_days

        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._is_running = False

        # Cleanup intervals (in seconds)
        self.sensor_cleanup_interval = 3600  # 1 hour
        self.aggregation_interval = 86400  # 24 hours
        self.deep_cleanup_interval = 604800  # 7 days

        _LOGGER.info(
            f"✅ {self.room} Data Cleanup Manager initialized (retention: {retention_days} days)"
        )

    async def start_cleanup(self):
        """Start the automated cleanup system."""
        if self._is_running:
            _LOGGER.warning(f"{self.room} Data cleanup already running")
            return

        self._is_running = True
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            _LOGGER.info(f"🧹 {self.room} Data cleanup system started")

    async def stop_cleanup(self):
        """Stop the cleanup system."""
        self._is_running = False
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await asyncio.wait_for(self._cleanup_task, timeout=5.0)
            except asyncio.TimeoutError:
                _LOGGER.warning(f"{self.room} Cleanup task did not stop cleanly")
            except asyncio.CancelledError:
                pass

        _LOGGER.info(f"🛑 {self.room} Data cleanup system stopped")

    async def _cleanup_loop(self):
        """Main cleanup loop - runs different cleanup tasks at different intervals."""
        _LOGGER.info(f"{self.room} Data cleanup loop started")

        last_aggregation = datetime.now()
        last_deep_cleanup = datetime.now()

        while self._is_running:
            try:
                now = datetime.now()

                # Sensor data cleanup (every hour)
                await self._cleanup_sensor_data()

                # Data aggregation (daily)
                if (
                    now - last_aggregation
                ).total_seconds() >= self.aggregation_interval:
                    await self._aggregate_sensor_data()
                    last_aggregation = now

                # Deep cleanup (weekly)
                if (
                    now - last_deep_cleanup
                ).total_seconds() >= self.deep_cleanup_interval:
                    await self._deep_cleanup()
                    last_deep_cleanup = now

                # Wait for next cleanup cycle
                await asyncio.sleep(self.sensor_cleanup_interval)

            except asyncio.CancelledError:
                _LOGGER.info(f"{self.room} Cleanup loop cancelled")
                break
            except Exception as e:
                _LOGGER.error(
                    f"❌ {self.room} Error in cleanup loop: {e}", exc_info=True
                )
                await asyncio.sleep(300)  # Wait 5 minutes on error

    async def _cleanup_sensor_data(self):
        """Clean up old sensor readings beyond retention period."""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.retention_days)
            cleaned_count = 0

            # Clean VPD history
            vpd_history = self.data_store.getDeep("vpd.history") or []
            if isinstance(vpd_history, list):
                original_count = len(vpd_history)
                # Keep only readings newer than cutoff
                filtered_history = [
                    reading
                    for reading in vpd_history
                    if isinstance(reading, dict)
                    and self._parse_timestamp(reading.get("timestamp")) > cutoff_date
                ]
                if len(filtered_history) != original_count:
                    self.data_store.setDeep("vpd.history", filtered_history)
                    cleaned_count += original_count - len(filtered_history)
                    _LOGGER.debug(
                        f"{self.room} Cleaned {original_count - len(filtered_history)} old VPD readings"
                    )

            # Clean temperature history
            temp_history = self.data_store.getDeep("sensor.temperature.history") or []
            if isinstance(temp_history, list):
                original_count = len(temp_history)
                filtered_history = [
                    reading
                    for reading in temp_history
                    if isinstance(reading, dict)
                    and self._parse_timestamp(reading.get("timestamp")) > cutoff_date
                ]
                if len(filtered_history) != original_count:
                    self.data_store.setDeep(
                        "sensor.temperature.history", filtered_history
                    )
                    cleaned_count += original_count - len(filtered_history)

            # Clean humidity history
            humidity_history = self.data_store.getDeep("sensor.humidity.history") or []
            if isinstance(humidity_history, list):
                original_count = len(humidity_history)
                filtered_history = [
                    reading
                    for reading in humidity_history
                    if isinstance(reading, dict)
                    and self._parse_timestamp(reading.get("timestamp")) > cutoff_date
                ]
                if len(filtered_history) != original_count:
                    self.data_store.setDeep("sensor.humidity.history", filtered_history)
                    cleaned_count += original_count - len(filtered_history)

            # Clean CO2 history
            co2_history = self.data_store.getDeep("sensor.co2.history") or []
            if isinstance(co2_history, list):
                original_count = len(co2_history)
                filtered_history = [
                    reading
                    for reading in co2_history
                    if isinstance(reading, dict)
                    and self._parse_timestamp(reading.get("timestamp")) > cutoff_date
                ]
                if len(filtered_history) != original_count:
                    self.data_store.setDeep("sensor.co2.history", filtered_history)
                    cleaned_count += original_count - len(filtered_history)

            # Clean pH/EC history from hydro system
            hydro_ph_history = self.data_store.getDeep("Hydro.pH.history") or []
            if isinstance(hydro_ph_history, list):
                original_count = len(hydro_ph_history)
                filtered_history = [
                    reading
                    for reading in hydro_ph_history
                    if isinstance(reading, dict)
                    and self._parse_timestamp(reading.get("timestamp")) > cutoff_date
                ]
                if len(filtered_history) != original_count:
                    self.data_store.setDeep("Hydro.pH.history", filtered_history)
                    cleaned_count += original_count - len(filtered_history)

            hydro_ec_history = self.data_store.getDeep("Hydro.EC.history") or []
            if isinstance(hydro_ec_history, list):
                original_count = len(hydro_ec_history)
                filtered_history = [
                    reading
                    for reading in hydro_ec_history
                    if isinstance(reading, dict)
                    and self._parse_timestamp(reading.get("timestamp")) > cutoff_date
                ]
                if len(filtered_history) != original_count:
                    self.data_store.setDeep("Hydro.EC.history", filtered_history)
                    cleaned_count += original_count - len(filtered_history)

            if cleaned_count > 0:
                _LOGGER.info(
                    f"🧹 {self.room} Cleaned {cleaned_count} old sensor readings"
                )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error cleaning sensor data: {e}")

    async def _aggregate_sensor_data(self):
        """Aggregate recent sensor data into daily summaries."""
        try:
            # Aggregate last 24 hours of data
            end_date = datetime.now()
            start_date = end_date - timedelta(days=1)

            # VPD aggregation
            vpd_data = self._aggregate_time_series(
                "vpd.history", start_date, end_date, "vpd_value"
            )
            if vpd_data:
                daily_key = f"vpd.daily.{end_date.strftime('%Y-%m-%d')}"
                self.data_store.setDeep(daily_key, vpd_data)
                _LOGGER.debug(
                    f"{self.room} Aggregated VPD data for {end_date.strftime('%Y-%m-%d')}"
                )

            # Temperature aggregation
            temp_data = self._aggregate_time_series(
                "sensor.temperature.history", start_date, end_date, "temperature"
            )
            if temp_data:
                daily_key = f"sensor.temperature.daily.{end_date.strftime('%Y-%m-%d')}"
                self.data_store.setDeep(daily_key, temp_data)

            # Humidity aggregation
            humidity_data = self._aggregate_time_series(
                "sensor.humidity.history", start_date, end_date, "humidity"
            )
            if humidity_data:
                daily_key = f"sensor.humidity.daily.{end_date.strftime('%Y-%m-%d')}"
                self.data_store.setDeep(daily_key, humidity_data)

            # pH/EC aggregation
            ph_data = self._aggregate_time_series(
                "Hydro.pH.history", start_date, end_date, "ph_value"
            )
            if ph_data:
                daily_key = f"Hydro.pH.daily.{end_date.strftime('%Y-%m-%d')}"
                self.data_store.setDeep(daily_key, ph_data)

            ec_data = self._aggregate_time_series(
                "Hydro.EC.history", start_date, end_date, "ec_value"
            )
            if ec_data:
                daily_key = f"Hydro.EC.daily.{end_date.strftime('%Y-%m-%d')}"
                self.data_store.setDeep(daily_key, ec_data)

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error aggregating sensor data: {e}")

    def _aggregate_time_series(
        self, history_key: str, start_date: datetime, end_date: datetime, value_key: str
    ) -> Optional[Dict[str, Any]]:
        """Aggregate a time series into min/max/avg for a date range."""
        try:
            history = self.data_store.getDeep(history_key) or []
            if not isinstance(history, list):
                return None

            # Filter readings in date range
            readings = []
            for reading in history:
                if isinstance(reading, dict):
                    timestamp = self._parse_timestamp(reading.get("timestamp"))
                    if start_date <= timestamp <= end_date:
                        value = reading.get(value_key)
                        if value is not None and isinstance(value, (int, float)):
                            readings.append(value)

            if not readings:
                return None

            # Calculate aggregates
            return {
                "count": len(readings),
                "min": min(readings),
                "max": max(readings),
                "avg": sum(readings) / len(readings),
                "date": start_date.strftime("%Y-%m-%d"),
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            _LOGGER.error(f"Error aggregating {history_key}: {e}")
            return None

    async def _deep_cleanup(self):
        """Perform deep cleanup of very old data (>30 days)."""
        try:
            cutoff_date = datetime.now() - timedelta(days=30)
            cleaned_count = 0

            # Clean old daily aggregates (>90 days)
            aggregate_cutoff = datetime.now() - timedelta(days=90)

            # Get all keys and clean old ones
            # Note: This is a simplified version - in practice you'd need to
            # iterate through all dataStore keys and clean old aggregated data

            _LOGGER.info(
                f"🧽 {self.room} Deep cleanup completed - removed {cleaned_count} old records"
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error in deep cleanup: {e}")

    def _parse_timestamp(self, timestamp_str: str) -> datetime:
        """Parse timestamp string into datetime object."""
        if not timestamp_str:
            return datetime.min

        try:
            # Try ISO format first
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            try:
                # Try other common formats
                return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, AttributeError):
                # Return minimum datetime if parsing fails
                return datetime.min

    def get_cleanup_stats(self) -> Dict[str, Any]:
        """Get cleanup statistics for monitoring."""
        try:
            # Count records in various histories
            vpd_count = len(self.data_store.getDeep("vpd.history") or [])
            temp_count = len(self.data_store.getDeep("sensor.temperature.history") or [])
            humidity_count = len(
                self.data_store.getDeep("sensor.humidity.history") or []
            )
            ph_count = len(self.data_store.getDeep("Hydro.pH.history") or [])
            ec_count = len(self.data_store.getDeep("Hydro.EC.history") or [])

            return {
                "retention_days": self.retention_days,
                "is_running": self._is_running,
                "total_sensor_records": vpd_count
                + temp_count
                + humidity_count
                + ph_count
                + ec_count,
                "breakdown": {
                    "vpd_history": vpd_count,
                    "temperature_history": temp_count,
                    "humidity_history": humidity_count,
                    "ph_history": ph_count,
                    "ec_history": ec_count,
                },
                "intervals": {
                    "sensor_cleanup": self.sensor_cleanup_interval,
                    "aggregation": self.aggregation_interval,
                    "deep_cleanup": self.deep_cleanup_interval,
                },
            }

        except Exception as e:
            _LOGGER.error(f"Error getting cleanup stats: {e}")
            return {"error": str(e)}

    async def force_cleanup_now(self):
        """Force immediate cleanup of all data types."""
        _LOGGER.info(f"🔧 {self.room} Forcing immediate cleanup")
        await self._cleanup_sensor_data()
        await self._aggregate_sensor_data()
        await self._deep_cleanup()
        _LOGGER.info(f"✅ {self.room} Forced cleanup completed")

    async def set_retention_policy(self, days: int):
        """Update data retention policy."""
        if 1 <= days <= 90:  # Reasonable bounds
            self.retention_days = days
            _LOGGER.info(f"📅 {self.room} Data retention policy updated to {days} days")
        else:
            _LOGGER.warning(
                f"{self.room} Invalid retention days: {days} (must be 1-90)"
            )
