# Data Cleanup System - Automated Data Management

## Overview

The OpenGrowBox Data Cleanup System provides intelligent data lifecycle management to prevent memory bloat and maintain system performance. It automatically manages sensor data retention, performs data aggregation, and ensures optimal database performance through configurable cleanup policies.

## System Architecture

### Core Components

#### 1. OGBDataCleanupManager (Main Controller)
```python
class OGBDataCleanupManager:
    """Automated data cleanup and lifecycle management."""
```

#### 2. Cleanup Policies
- **Sensor Data**: Raw sensor readings with configurable retention
- **Aggregated Data**: Statistical summaries and trends
- **Historical Archives**: Long-term data preservation
- **Temporary Files**: Cache and temporary data cleanup

#### 3. Cleanup Strategies
- **Time-Based**: Age-based data removal
- **Size-Based**: Storage limit enforcement
- **Priority-Based**: Importance-based retention
- **Compression**: Data archiving and compression

## Cleanup Policies and Retention

### Data Retention Categories

```python
DATA_RETENTION_POLICIES = {
    "sensor_raw": {
        "retention_days": 7,           # Raw sensor data: 7 days
        "cleanup_interval": 3600,      # Clean every hour
        "compression_threshold": 30,   # Compress after 30 days
        "aggregation_enabled": True    # Create daily summaries
    },
    "action_history": {
        "retention_days": 30,          # Action logs: 30 days
        "cleanup_interval": 86400,     # Clean daily
        "compression_threshold": 90,   # Compress after 90 days
        "aggregation_enabled": False   # Keep detailed logs
    },
    "performance_metrics": {
        "retention_days": 365,         # Performance data: 1 year
        "cleanup_interval": 604800,    # Clean weekly
        "compression_threshold": 180,  # Compress after 180 days
        "aggregation_enabled": True    # Monthly summaries
    },
    "calibration_data": {
        "retention_days": -1,          # Keep indefinitely
        "cleanup_interval": 0,         # No automatic cleanup
        "compression_threshold": 365,  # Compress after 1 year
        "aggregation_enabled": False   # Keep all calibration history
    },
    "system_logs": {
        "retention_days": 90,          # System logs: 90 days
        "cleanup_interval": 86400,     # Clean daily
        "compression_threshold": 180,  # Compress after 180 days
        "aggregation_enabled": False   # Keep detailed logs
    }
}
```

### Intelligent Retention Logic

```python
def calculate_data_retention(self, data_type: str, data_age_days: int) -> bool:
    """Determine if data should be retained based on intelligent policies."""

    policy = self.get_retention_policy(data_type)

    # Always keep critical data
    if data_type in ["calibration_data", "system_errors"]:
        return True

    # Check age-based retention
    if data_age_days > policy["retention_days"] and policy["retention_days"] > 0:
        # Check if data has high value (exceptions)
        if self.is_high_value_data(data_type, data_age_days):
            return True
        return False

    # Check size-based limits
    if self.is_storage_limit_exceeded(data_type):
        return self.should_compress_instead_of_delete(data_type, data_age_days)

    return True
```

## Automated Cleanup Processes

### Sensor Data Cleanup

```python
async def cleanup_sensor_data(self):
    """Clean up old sensor data while preserving important readings."""

    cutoff_date = datetime.now() - timedelta(days=self.retention_days)

    # Get all sensor data keys
    sensor_keys = await self.get_sensor_data_keys()

    cleaned_count = 0
    compressed_count = 0

    for key in sensor_keys:
        # Get data age and importance
        data_age = await self.get_data_age(key)
        importance_score = await self.calculate_data_importance(key)

        if data_age > self.retention_days:
            if importance_score > 0.8:  # High importance data
                # Compress instead of delete
                await self.compress_sensor_data(key)
                compressed_count += 1
            else:
                # Delete old data
                await self.delete_sensor_data(key)
                cleaned_count += 1

    _LOGGER.info(f"Cleaned {cleaned_count} sensor records, compressed {compressed_count}")
```

### Data Aggregation

```python
async def perform_data_aggregation(self):
    """Aggregate raw data into summaries for long-term storage."""

    # Aggregate by data type
    for data_type in ["sensor_readings", "action_history", "performance_metrics"]:

        # Get raw data for aggregation period
        raw_data = await self.get_raw_data_for_period(data_type, "daily")

        if not raw_data:
            continue

        # Calculate aggregations
        daily_summary = {
            "date": datetime.now().date(),
            "data_type": data_type,
            "count": len(raw_data),
            "average": statistics.mean(raw_data) if raw_data else 0,
            "minimum": min(raw_data) if raw_data else 0,
            "maximum": max(raw_data) if raw_data else 0,
            "standard_deviation": statistics.stdev(raw_data) if len(raw_data) > 1 else 0,
            "data_quality_score": self.calculate_data_quality_score(raw_data)
        }

        # Store aggregated data
        await self.store_aggregated_data(daily_summary)

        # Mark raw data for potential cleanup
        await self.mark_raw_data_for_cleanup(raw_data, data_type)
```

### Compression and Archiving

```python
async def compress_old_data(self):
    """Compress old data to save storage space."""

    compression_candidates = await self.find_compression_candidates()

    for candidate in compression_candidates:
        try:
            # Read original data
            original_data = await self.read_data_for_compression(candidate)

            # Compress data (gzip, bz2, or custom algorithm)
            compressed_data = await self.compress_data(original_data)

            # Calculate compression ratio
            original_size = len(json.dumps(original_data))
            compressed_size = len(compressed_data)
            ratio = compressed_size / original_size

            # Store compressed data
            await self.store_compressed_data(candidate, compressed_data, ratio)

            # Update metadata
            await self.update_compression_metadata(candidate, ratio)

            _LOGGER.debug(f"Compressed {candidate}: {ratio:.2f} compression ratio")

        except Exception as e:
            _LOGGER.error(f"Compression failed for {candidate}: {e}")
```

## Storage Optimization

### Adaptive Storage Management

```python
async def optimize_storage_usage(self):
    """Dynamically optimize storage based on usage patterns."""

    storage_stats = await self.get_storage_statistics()

    # Check if storage limits are approached
    if storage_stats["usage_percent"] > 85:
        await self.implement_aggressive_cleanup()
    elif storage_stats["usage_percent"] > 70:
        await self.implement_moderate_cleanup()

    # Optimize based on data access patterns
    access_patterns = await self.analyze_data_access_patterns()

    # Move frequently accessed data to faster storage
    await self.optimize_data_placement(access_patterns)

    # Implement predictive cleanup
    await self.predict_and_schedule_cleanup(storage_stats, access_patterns)
```

### Storage Monitoring

```python
def get_storage_statistics(self) -> Dict[str, Any]:
    """Get comprehensive storage usage statistics."""

    return {
        "total_size_mb": self.calculate_total_data_size(),
        "usage_percent": self.calculate_storage_usage_percent(),
        "data_type_breakdown": self.get_data_type_sizes(),
        "growth_rate": self.calculate_data_growth_rate(),
        "compression_savings": self.calculate_compression_savings(),
        "retention_compliance": self.check_retention_policy_compliance(),
        "performance_metrics": self.get_storage_performance_metrics()
    }
```

## Data Quality Management

### Data Validation and Cleaning

```python
async def validate_and_clean_data(self):
    """Validate data integrity and clean corrupted entries."""

    validation_results = {
        "total_records": 0,
        "valid_records": 0,
        "corrupted_records": 0,
        "repaired_records": 0,
        "deleted_records": 0
    }

    # Check each data type
    for data_type in self.monitored_data_types:
        records = await self.get_data_records(data_type)

        for record in records:
            validation_results["total_records"] += 1

            if self.is_corrupted_record(record):
                validation_results["corrupted_records"] += 1

                if self.can_repair_record(record):
                    await self.repair_corrupted_record(record)
                    validation_results["repaired_records"] += 1
                    validation_results["valid_records"] += 1
                else:
                    await self.delete_corrupted_record(record)
                    validation_results["deleted_records"] += 1
            else:
                validation_results["valid_records"] += 1

    return validation_results
```

### Duplicate Detection and Removal

```python
async def remove_duplicate_data(self):
    """Detect and remove duplicate data entries."""

    duplicate_stats = {
        "scanned_records": 0,
        "duplicates_found": 0,
        "duplicates_removed": 0,
        "space_saved_mb": 0
    }

    # Scan for duplicates by data type
    for data_type in self.data_types_with_duplicates:
        duplicates = await self.find_duplicate_records(data_type)

        duplicate_stats["scanned_records"] += await self.get_record_count(data_type)
        duplicate_stats["duplicates_found"] += len(duplicates)

        # Remove duplicates, keeping the most recent or highest quality
        space_saved = await self.remove_duplicates(duplicates, data_type)

        duplicate_stats["duplicates_removed"] += len(duplicates)
        duplicate_stats["space_saved_mb"] += space_saved

    return duplicate_stats
```

## Performance Monitoring

### Cleanup Performance Tracking

```python
class CleanupPerformanceMonitor:
    """Monitor cleanup operation performance."""

    def __init__(self):
        self.performance_history = []
        self.alert_thresholds = {
            "cleanup_duration_seconds": 300,  # 5 minutes max
            "memory_usage_mb": 500,           # 500MB max
            "cpu_usage_percent": 80           # 80% max
        }

    def record_cleanup_performance(self, operation: str, metrics: Dict[str, Any]):
        """Record performance metrics for cleanup operations."""

        performance_record = {
            "timestamp": datetime.now(),
            "operation": operation,
            "duration_seconds": metrics.get("duration", 0),
            "records_processed": metrics.get("records_processed", 0),
            "memory_peak_mb": metrics.get("memory_peak", 0),
            "cpu_average_percent": metrics.get("cpu_average", 0),
            "success": metrics.get("success", True)
        }

        self.performance_history.append(performance_record)

        # Check for performance issues
        self.check_performance_alerts(performance_record)

    def check_performance_alerts(self, record: Dict[str, Any]):
        """Check if performance metrics exceed thresholds."""

        alerts = []

        for metric, threshold in self.alert_thresholds.items():
            if record.get(metric, 0) > threshold:
                alerts.append({
                    "metric": metric,
                    "value": record[metric],
                    "threshold": threshold,
                    "severity": "warning" if record[metric] < threshold * 1.5 else "critical"
                })

        if alerts:
            self.trigger_performance_alerts(alerts)
```

## System Integration

### Event-Driven Cleanup

```python
async def setup_event_listeners(self):
    """Set up event listeners for cleanup triggers."""

    # System events
    self.event_manager.on("SystemStartup", self.perform_startup_cleanup)
    self.event_manager.on("SystemShutdown", self.perform_shutdown_cleanup)

    # Data events
    self.event_manager.on("DataStorageFull", self.perform_emergency_cleanup)
    self.event_manager.on("DataCorruptionDetected", self.perform_corruption_cleanup)

    # Maintenance events
    self.event_manager.on("MaintenanceWindow", self.perform_maintenance_cleanup)
    self.event_manager.on("StorageOptimization", self.optimize_storage_usage)
```

### Health Monitoring Integration

```python
async def integrate_with_health_monitoring(self):
    """Integrate cleanup system with health monitoring."""

    # Register cleanup metrics with health monitor
    await self.health_monitor.register_metric_source(
        "data_cleanup",
        self.get_cleanup_health_metrics
    )

    # Set up alerts for cleanup issues
    self.health_monitor.add_alert_condition(
        "cleanup_performance_degraded",
        lambda: self.is_cleanup_performance_degraded()
    )

    self.health_monitor.add_alert_condition(
        "storage_critical",
        lambda: self.get_storage_usage_percent() > 95
    )
```

## Configuration and Management

### Cleanup Configuration Schema

```python
CLEANUP_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "retention_policies": {
            "type": "object",
            "patternProperties": {
                ".*": {
                    "type": "object",
                    "properties": {
                        "retention_days": {"type": "integer", "minimum": -1},
                        "cleanup_interval": {"type": "integer", "minimum": 0},
                        "compression_threshold": {"type": "integer", "minimum": 0},
                        "aggregation_enabled": {"type": "boolean"}
                    }
                }
            }
        },
        "storage_limits": {
            "type": "object",
            "properties": {
                "max_storage_mb": {"type": "integer", "minimum": 100},
                "warning_threshold_percent": {"type": "integer", "minimum": 50, "maximum": 95},
                "critical_threshold_percent": {"type": "integer", "minimum": 80, "maximum": 99}
            }
        },
        "performance_limits": {
            "type": "object",
            "properties": {
                "max_cleanup_duration_seconds": {"type": "integer", "minimum": 60},
                "max_memory_usage_mb": {"type": "integer", "minimum": 100},
                "max_cpu_usage_percent": {"type": "integer", "minimum": 10, "maximum": 100}
            }
        },
        "advanced_options": {
            "type": "object",
            "properties": {
                "compression_algorithm": {
                    "type": "string",
                    "enum": ["gzip", "bz2", "lzma", "none"]
                },
                "parallel_processing": {"type": "boolean", "default": True},
                "batch_size": {"type": "integer", "minimum": 1, "default": 1000}
            }
        }
    }
}
```

### Manual Cleanup Operations

```python
async def perform_manual_cleanup(self, cleanup_type: str, parameters: Dict[str, Any] = None):
    """Perform manual cleanup operations."""

    if cleanup_type == "full_database_cleanup":
        await self.perform_full_database_cleanup()
    elif cleanup_type == "sensor_data_only":
        await self.cleanup_specific_data_type("sensor_raw", parameters)
    elif cleanup_type == "compress_all":
        await self.compress_all_eligible_data()
    elif cleanup_type == "validate_integrity":
        return await self.validate_data_integrity()
    elif cleanup_type == "optimize_storage":
        await self.optimize_storage_layout()
    else:
        raise ValueError(f"Unknown cleanup type: {cleanup_type}")
```

---

**Last Updated**: December 24, 2025
**Version**: 2.0 (Intelligent Data Management)
**Status**: Production Ready