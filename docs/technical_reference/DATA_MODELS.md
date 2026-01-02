# Data Models and Schemas

## Overview

OpenGrowBox uses structured data models throughout the system for configuration, sensor data, device states, and system events. This guide documents all data schemas and their relationships.

## Core Data Structures

### Plant Configuration Model

```python
# Plant configuration data structure
class PlantConfig:
    """Plant-specific configuration and environmental targets."""

    type: str                    # Plant species (Cannabis, Tomato, etc.)
    stage: str                   # Growth stage (Germination, EarlyVeg, etc.)
    strain: str                  # Specific strain/variety
    start_date: str              # ISO 8601 start date
    expected_harvest: str        # ISO 8601 expected harvest date
    container_size: float        # Container size in liters
    plant_count: int             # Number of plants
    current_height: float        # Current plant height (cm)
    estimated_yield: float       # Estimated yield (grams)

    # Environmental targets by stage
    environmental_targets: Dict[str, EnvironmentalTarget]

class EnvironmentalTarget:
    """Environmental targets for specific growth stage."""

    vpd_range: Tuple[float, float]        # VPD range (kPa)
    temperature_min: float                # Min temperature (°C)
    temperature_max: float                # Max temperature (°C)
    temperature_target: float             # Target temperature (°C)
    humidity_min: float                   # Min humidity (%)
    humidity_max: float                   # Max humidity (%)
    humidity_target: float                # Target humidity (%)
    co2_min: float                        # Min CO2 (ppm)
    co2_max: float                        # Max CO2 (ppm)
    co2_target: float                     # Target CO2 (ppm)
    light_schedule: str                   # Light schedule (18/6, 12/12, etc.)
    irrigation_frequency: int             # Hours between watering
    nutrient_strength: float              # Nutrient concentration multiplier
```

### Sensor Data Models

```python
# Sensor reading data structure
class SensorReading:
    """Individual sensor reading with metadata."""

    sensor_id: str               # Unique sensor identifier
    sensor_type: str             # Type: temperature, humidity, co2, etc.
    value: float                 # Sensor value
    unit: str                    # Unit of measurement
    timestamp: str               # ISO 8601 timestamp
    quality: str                 # Quality indicator: good, degraded, bad
    calibration_applied: bool    # Whether calibration was applied
    raw_value: float             # Raw uncalibrated value
    zone: str                    # Zone identifier
    accuracy: float              # Estimated accuracy (±value)

class VPDData:
    """Vapor Pressure Deficit calculation data."""

    current_vpd: float           # Current VPD (kPa)
    target_vpd: float            # Target VPD (kPa)
    perfection_vpd: float        # Calculated perfect VPD
    tolerance: float             # Tolerance percentage
    range_min: float             # Minimum acceptable VPD
    range_max: float             # Maximum acceptable VPD
    temperature: float           # Temperature used in calculation
    humidity: float              # Humidity used in calculation
    calculation_method: str      # Calculation method used
    timestamp: str               # Calculation timestamp

class ZoneSensorData:
    """Complete sensor data for a zone."""

    zone_id: str
    timestamp: str
    sensors: Dict[str, SensorReading]
    vpd_data: VPDData
    environmental_status: str    # good, warning, critical
    last_update: str
    data_quality_score: float    # 0-100 quality score
```

### Device Data Models

```python
# Device configuration and state
class Device:
    """Hardware device configuration."""

    id: str                      # Unique device identifier
    name: str                    # Human-readable name
    type: str                    # Device type: fan, pump, light, etc.
    entity_id: str               # Home Assistant entity ID
    capabilities: List[str]      # Device capabilities
    settings: Dict[str, Any]     # Device-specific settings
    calibration_data: Dict[str, Any]  # Calibration information
    installation_date: str       # Installation timestamp
    firmware_version: str        # Device firmware version
    hardware_version: str        # Hardware revision

class DeviceState:
    """Current device operational state."""

    device_id: str
    state: str                   # on, off, error, unknown
    power_level: float           # Current power level (0-100)
    target_power_level: float    # Target power level
    last_action: str             # Timestamp of last action
    last_action_result: str      # Result: success, failed, timeout
    error_code: str              # Current error code if any
    error_message: str           # Error description
    operational_hours: float     # Total operational hours
    maintenance_due: str         # Next maintenance date
    status: str                  # online, offline, degraded

class DeviceCapability:
    """Device capability definition."""

    name: str                    # Capability name
    description: str             # Human-readable description
    control_type: str            # binary, variable, pwm
    voltage_range: Tuple[float, float]  # Min/max voltage
    safety_limits: Dict[str, Any]  # Safety constraints
    dampening_required: bool     # Whether action dampening is needed
    emergency_capable: bool      # Can be used in emergency situations
    calibration_required: bool   # Requires periodic calibration
```

### Control System Models

```python
# Control mode configuration
class ControlMode:
    """Environmental control mode settings."""

    mode_name: str               # VPD_Perfection, VPD_Target, Target, etc.
    active: bool                 # Whether mode is currently active
    settings: Dict[str, Any]     # Mode-specific settings
    environmental_targets: EnvironmentalTarget
    device_assignments: Dict[str, str]  # Device -> capability mapping
    safety_limits: Dict[str, Any]  # Safety constraints
    transition_rules: List[TransitionRule]  # Mode transition rules

class ControlAction:
    """Control system action."""

    action_id: str
    timestamp: str
    action_type: str             # set_power_level, turn_on, turn_off, etc.
    target_device: str           # Target device ID
    parameters: Dict[str, Any]   # Action parameters
    reason: str                  # Reason for action
    priority: str                # high, medium, low
    dampening_applied: bool      # Whether dampening was applied
    expected_duration: int       # Expected action duration (seconds)
    result: str                  # pending, executed, failed

class TransitionRule:
    """Control mode transition rule."""

    from_mode: str
    to_mode: str
    trigger_condition: str       # Environmental condition
    trigger_value: Any           # Condition value
    transition_delay: int        # Delay before transition (seconds)
    hysteresis: float            # Hysteresis value
    enabled: bool                # Whether rule is enabled
```

## Event Data Models

### Event Base Structure

```python
# Base event structure
class Event:
    """Base event structure."""

    id: str                      # Unique event identifier
    type: str                    # Event type (sensor.update, device.error, etc.)
    timestamp: str               # ISO 8601 timestamp
    source: str                  # Event source component
    data: Dict[str, Any]         # Event-specific data
    metadata: Dict[str, Any]     # Additional metadata
    correlation_id: str          # Correlation identifier
    processing_priority: str     # high, medium, low

    # Event processing metadata
    created_at: float            # Unix timestamp when created
    processed_at: float          # Unix timestamp when processed
    processing_duration: float   # Processing time in seconds
    handler_count: int           # Number of handlers that processed event
```

### Sensor Events

```python
class SensorEvent(Event):
    """Sensor-related event."""

    sensor_id: str
    sensor_type: str
    value: float
    unit: str
    zone: str
    quality: str
    threshold_breached: bool     # Whether threshold was breached
    threshold_type: str          # above, below, within_range
    previous_value: float        # Previous sensor value
    change_rate: float           # Rate of change (value/second)

class SensorCalibrationEvent(Event):
    """Sensor calibration event."""

    sensor_id: str
    calibration_type: str        # offset, multiplier, full
    old_offset: float
    new_offset: float
    old_multiplier: float
    new_multiplier: float
    reference_value: float       # Calibration reference
    accuracy_improvement: float  # Improvement in accuracy
    next_calibration_due: str    # Next calibration date
```

### Device Events

```python
class DeviceEvent(Event):
    """Device-related event."""

    device_id: str
    device_type: str
    action: str                  # turn_on, turn_off, set_power_level, etc.
    old_state: str
    new_state: str
    power_level: float
    success: bool
    error_code: str
    error_message: str
    duration: float              # Action duration in seconds

class DeviceErrorEvent(Event):
    """Device error event."""

    device_id: str
    error_code: str              # E_FAN_STALLED, E_PUMP_TIMEOUT, etc.
    error_message: str
    severity: str                # critical, warning, info
    recovery_action: str         # Suggested recovery action
    auto_recovery_attempted: bool
    recovery_success: bool
    error_context: Dict[str, Any]  # Additional error context
```

### Control Events

```python
class ControlEvent(Event):
    """Control system event."""

    control_mode: str
    action_type: str             # mode_change, setpoint_update, device_action
    parameters: Dict[str, Any]
    reason: str
    success: bool
    affected_devices: List[str]
    environmental_impact: Dict[str, float]  # Expected environmental changes

class ModeTransitionEvent(Event):
    """Control mode transition event."""

    from_mode: str
    to_mode: str
    trigger_condition: str
    trigger_value: Any
    transition_delay: int
    hysteresis_applied: float
    success: bool
    rollback_possible: bool
```

## Configuration Data Models

### System Configuration

```python
# Complete system configuration
class SystemConfiguration:
    """Complete OpenGrowBox system configuration."""

    version: str                 # Configuration version
    timestamp: str               # Last modified timestamp

    # Core configuration
    plant_config: PlantConfig
    control_mode: str
    environmental_limits: EnvironmentalLimits

    # Device configuration
    devices: Dict[str, Device]
    device_capabilities: Dict[str, DeviceCapability]

    # Sensor configuration
    sensors: Dict[str, SensorConfig]
    sensor_calibration: Dict[str, CalibrationData]

    # System settings
    notification_settings: NotificationSettings
    performance_settings: PerformanceSettings
    premium_settings: PremiumSettings

    # Validation
    is_valid: bool
    validation_errors: List[str]
    last_validated: str
```

### Calibration Data

```python
class CalibrationData:
    """Sensor or device calibration data."""

    calibration_type: str        # sensor, device
    target_id: str               # Sensor or device ID
    method: str                  # salt_solution, reference_device, etc.
    reference_value: float       # Calibration reference
    measured_value: float        # Measured value
    offset: float                # Calculated offset
    multiplier: float            # Calculated multiplier
    accuracy_before: float       # Accuracy before calibration
    accuracy_after: float        # Accuracy after calibration
    calibration_date: str        # ISO 8601 calibration date
    next_due: str               # Next calibration due date
    technician: str             # Person who performed calibration
    notes: str                   # Calibration notes
    validation_points: List[Dict[str, float]]  # Validation data points
```

## Analytics and Reporting Models

### Analytics Data

```python
class AnalyticsData:
    """Analytics data structure."""

    time_range: TimeRange
    data_type: str               # environmental, device, system
    aggregation_level: str       # raw, hourly, daily, weekly
    metrics: Dict[str, MetricData]

class MetricData:
    """Individual metric data."""

    metric_name: str
    unit: str
    data_points: List[DataPoint]
    statistics: MetricStatistics
    anomalies: List[Anomaly]

class DataPoint:
    """Individual data point."""

    timestamp: str
    value: float
    quality: str
    source: str

class MetricStatistics:
    """Metric statistical summary."""

    count: int
    mean: float
    median: float
    std_dev: float
    min_value: float
    max_value: float
    percentiles: Dict[int, float]  # 25th, 75th, 95th percentiles

class Anomaly:
    """Detected anomaly in data."""

    timestamp: str
    value: float
    expected_value: float
    deviation: float
    severity: str
    description: str
```

### Compliance Data

```python
class ComplianceRecord:
    """Regulatory compliance record."""

    record_id: str
    timestamp: str
    jurisdiction: str            # california, canada, eu, etc.
    regulation: str              # Specific regulation or standard
    compliance_status: str       # compliant, non_compliant, pending
    measured_value: float
    required_value: float
    tolerance: float
    evidence_data: Dict[str, Any]
    auditor: str
    audit_date: str
    notes: str
```

## API Data Models

### REST API Models

```python
class APIResponse:
    """Standard API response structure."""

    status: str                  # success, error
    message: str                 # Human-readable message
    data: Any                    # Response data
    timestamp: str               # Response timestamp
    request_id: str              # Request correlation ID
    pagination: PaginationInfo   # Pagination info if applicable

class PaginationInfo:
    """API pagination information."""

    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool
    next_url: str
    previous_url: str

class APIError:
    """API error response."""

    error_code: str
    message: str
    details: Dict[str, Any]
    timestamp: str
    request_id: str
    suggested_action: str
```

### WebSocket Message Models

```python
class WebSocketMessage:
    """WebSocket message structure."""

    type: str                    # Message type
    data: Dict[str, Any]         # Message data
    timestamp: str               # Message timestamp
    message_id: str              # Unique message ID
    session_id: str              # WebSocket session ID
    compression: str             # Compression used (none, gzip)

class SubscriptionMessage:
    """WebSocket subscription message."""

    action: str                  # subscribe, unsubscribe
    channels: List[str]          # Channels to subscribe to
    filters: Dict[str, Any]      # Message filters
    subscription_id: str         # Subscription identifier

class StreamMessage:
    """Real-time data stream message."""

    channel: str
    event_type: str
    payload: Dict[str, Any]
    sequence_number: int
    compression: bool
```

## Data Validation Schemas

### JSON Schema Definitions

```python
# Plant configuration schema
PLANT_CONFIG_SCHEMA = {
    "type": "object",
    "required": ["type", "stage"],
    "properties": {
        "type": {
            "type": "string",
            "enum": ["Cannabis", "Tomato", "Pepper", "Lettuce", "Herbs"]
        },
        "stage": {
            "type": "string",
            "enum": ["Germination", "EarlyVeg", "MidVeg", "LateVeg",
                    "EarlyFlower", "MidFlower", "LateFlower", "Drying"]
        },
        "strain": {"type": "string"},
        "start_date": {"type": "string", "format": "date"},
        "plant_count": {"type": "integer", "minimum": 1, "maximum": 100},
        "container_size": {"type": "number", "minimum": 1, "maximum": 1000}
    }
}

# Environmental limits schema
ENVIRONMENTAL_LIMITS_SCHEMA = {
    "type": "object",
    "properties": {
        "temperature": {
            "type": "object",
            "properties": {
                "min": {"type": "number", "minimum": 10, "maximum": 40},
                "max": {"type": "number", "minimum": 10, "maximum": 40},
                "target": {"type": "number", "minimum": 10, "maximum": 40}
            },
            "required": ["min", "max"]
        },
        "humidity": {
            "type": "object",
            "properties": {
                "min": {"type": "number", "minimum": 30, "maximum": 90},
                "max": {"type": "number", "minimum": 30, "maximum": 90},
                "target": {"type": "number", "minimum": 30, "maximum": 90}
            },
            "required": ["min", "max"]
        }
    }
}
```

### Data Validation Functions

```python
async def validate_plant_config(config: dict) -> ValidationResult:
    """Validate plant configuration data."""

    result = ValidationResult()

    # Required fields
    required_fields = ["type", "stage"]
    for field in required_fields:
        if field not in config:
            result.add_error(f"Missing required field: {field}")

    # Plant type validation
    valid_types = ["Cannabis", "Tomato", "Pepper", "Lettuce", "Herbs"]
    if config.get("type") not in valid_types:
        result.add_error(f"Invalid plant type: {config.get('type')}")

    # Stage validation
    valid_stages = ["Germination", "EarlyVeg", "MidVeg", "LateVeg",
                   "EarlyFlower", "MidFlower", "LateFlower", "Drying"]
    if config.get("stage") not in valid_stages:
        result.add_error(f"Invalid growth stage: {config.get('stage')}")

    # Plant count validation
    plant_count = config.get("plant_count")
    if plant_count is not None and not (1 <= plant_count <= 100):
        result.add_error("Plant count must be between 1 and 100")

    result.is_valid = len(result.errors) == 0
    return result

class ValidationResult:
    """Data validation result."""

    def __init__(self):
        self.is_valid = False
        self.errors = []
        self.warnings = []

    def add_error(self, error: str):
        self.errors.append(error)

    def add_warning(self, warning: str):
        self.warnings.append(warning)
```

## Data Serialization

### JSON Serialization

```python
import json
from datetime import datetime
from typing import Any

class OGBJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for OpenGrowBox data types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif hasattr(obj, '__dict__'):
            return obj.__dict__
        elif isinstance(obj, set):
            return list(obj)
        else:
            return super().default(obj)

def serialize_data(data: Any) -> str:
    """Serialize data to JSON string."""
    return json.dumps(data, cls=OGBJSONEncoder, indent=2)

def deserialize_data(json_str: str) -> Any:
    """Deserialize JSON string to data."""
    return json.loads(json_str)
```

### Binary Serialization

```python
import pickle
import gzip
from typing import Any

def serialize_binary(data: Any, compress: bool = True) -> bytes:
    """Serialize data to compressed binary format."""
    pickled_data = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)

    if compress:
        return gzip.compress(pickled_data)
    else:
        return pickled_data

def deserialize_binary(data: bytes, compressed: bool = True) -> Any:
    """Deserialize binary data."""
    if compressed:
        decompressed_data = gzip.decompress(data)
    else:
        decompressed_data = data

    return pickle.loads(decompressed_data)
```

---

## Data Models Summary

**Data models and schemas implemented!** OpenGrowBox uses structured data throughout the system.

**Core Data Models:**
- ✅ **Plant Configuration**: Plant types, stages, environmental targets
- ✅ **Sensor Data**: Readings, VPD calculations, quality metrics
- ✅ **Device Models**: Configuration, state, capabilities
- ✅ **Control Systems**: Modes, actions, transitions
- ✅ **Event System**: Structured events with metadata
- ✅ **Configuration**: System-wide settings and validation
- ✅ **Analytics**: Metrics, statistics, compliance data

**Data Features:**
- ✅ **Validation**: JSON schemas and validation functions
- ✅ **Serialization**: JSON and binary serialization support
- ✅ **Type Safety**: Strongly typed data structures
- ✅ **Extensibility**: Modular design for new data types

**Key Relationships:**
- Plant config → Environmental targets → Control modes
- Sensors → VPD calculations → Device actions
- Events → Analytics → Compliance reporting
- Configuration → Validation → System state

**For API usage of these models, see [API Reference](API_REFERENCE.md)**

**For security considerations with data handling, see [Security Guide](SECURITY.md)**