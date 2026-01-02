# Medium Management System - Growing Medium Coordination

## Overview

The Medium Management System provides comprehensive coordination between growing mediums, sensors, and irrigation systems. It handles medium-specific properties, sensor registration, and ensures optimal growing conditions for different cultivation substrates.

## System Architecture

### Core Components

#### 1. OGBMediumManager (Main Controller)
```python
class OGBMediumManager:
    """Main coordinator for medium operations and sensor management."""
```

#### 2. OGBMediumSensorManager (Sensor Coordination)
```python
class OGBMediumSensorManager:
    """Manages sensor registration and data routing to mediums."""
```

#### 3. OGBMediumPropertiesManager (Medium Properties)
```python
class OGBMediumPropertiesManager:
    """Handles medium-specific properties and calculations."""
```

#### 4. OGBMediumHistoryManager (Data History)
```python
class OGBMediumHistoryManager:
    """Tracks medium performance and historical data."""
```

#### 5. OGBMediumDeviceBindingManager (Device Binding)
```python
class OGBMediumDeviceBindingManager:
    """Manages device-to-medium associations."""
```

## Supported Medium Types

### Organic Mediums

#### Soil
```python
SOIL_PROPERTIES = {
    "drainage_rate": 0.4,      # Slow drainage
    "water_retention": 0.9,    # High retention
    "optimal_vwc_range": [0.5, 0.75],
    "irrigation_frequency": "low",
    "calibration_offset": 0.1,
    "nutrient_availability": "high",
    "buffer_capacity": "high",
    "disease_resistance": "high"
}
```

#### Coco Coir
```python
COCO_PROPERTIES = {
    "drainage_rate": 0.6,      # Moderate drainage
    "water_retention": 0.8,    # Good retention
    "optimal_vwc_range": [0.65, 0.85],
    "irrigation_frequency": "moderate",
    "calibration_offset": 0.03,
    "nutrient_availability": "medium",
    "buffer_capacity": "medium",
    "disease_resistance": "medium"
}
```

### Inert Mediums

#### Rockwool
```python
ROCKWOOL_PROPERTIES = {
    "drainage_rate": 0.8,      # Fast drainage
    "water_retention": 0.6,    # Moderate retention
    "optimal_vwc_range": [0.6, 0.8],
    "irrigation_frequency": "moderate",
    "calibration_offset": 0.05,
    "nutrient_availability": "none",
    "buffer_capacity": "low",
    "disease_resistance": "high"
}
```

#### Perlite/Vermiculite Mix
```python
PERLITE_VERMICULITE_PROPERTIES = {
    "drainage_rate": 0.9,      # Very fast drainage
    "water_retention": 0.5,    # Low retention
    "optimal_vwc_range": [0.55, 0.75],
    "irrigation_frequency": "high",
    "calibration_offset": 0.08,
    "nutrient_availability": "none",
    "buffer_capacity": "low",
    "disease_resistance": "very_high"
}
```

### Hydroponic Systems

#### Hydroponic (Water Culture)
```python
HYDROPONIC_PROPERTIES = {
    "drainage_rate": 1.0,      # Instant drainage
    "water_retention": 0.3,    # Low retention
    "optimal_vwc_range": [0.7, 0.9],
    "irrigation_frequency": "high",
    "calibration_offset": 0.0,
    "nutrient_availability": "controlled",
    "buffer_capacity": "controlled",
    "disease_resistance": "medium"
}
```

## Medium Sensor Registration

### Sensor-to-Medium Association

```python
async def register_sensor_to_medium(self, sensor_id: str, medium_id: str):
    """Register a sensor to a specific growing medium."""

    # Validate sensor and medium exist
    if sensor_id not in self.available_sensors:
        raise ValueError(f"Sensor {sensor_id} not found")

    if medium_id not in self.mediums:
        raise ValueError(f"Medium {medium_id} not found")

    # Register association
    if medium_id not in self.medium_sensors:
        self.medium_sensors[medium_id] = []

    if sensor_id not in self.medium_sensors[medium_id]:
        self.medium_sensors[medium_id].append(sensor_id)

        # Emit registration event
        await self.event_manager.emit("SensorMediumRegistered", {
            "room": self.room,
            "sensor_id": sensor_id,
            "medium_id": medium_id
        })

        _LOGGER.info(f"[{self.room}] Registered sensor {sensor_id} to medium {medium_id}")
```

### Sensor Data Routing

```python
async def route_sensor_data(self, sensor_data: Dict[str, Any]):
    """Route sensor data to appropriate medium handlers."""

    sensor_id = sensor_data.get("sensor_id")
    sensor_type = sensor_data.get("type")
    value = sensor_data.get("value")

    # Find which medium this sensor belongs to
    medium_id = self.find_medium_for_sensor(sensor_id)

    if not medium_id:
        _LOGGER.warning(f"No medium found for sensor {sensor_id}")
        return

    # Route data to medium-specific processing
    await self.process_medium_sensor_data(medium_id, sensor_type, value)
```

## Medium-Specific Calculations

### Water Retention Adjustments

```python
def calculate_water_retention_adjustment(self, medium_type: str) -> float:
    """Calculate water retention adjustment factor."""

    properties = self.get_medium_properties(medium_type)

    # Base adjustment on drainage rate and retention
    drainage_factor = properties["drainage_rate"]
    retention_factor = properties["water_retention"]

    # Higher drainage + lower retention = more frequent irrigation needed
    adjustment = (1 - drainage_factor) + (1 - retention_factor)
    adjustment = adjustment / 2  # Average the factors

    # Convert to irrigation frequency multiplier
    if adjustment > 0.7:
        return 1.5  # 50% more frequent irrigation
    elif adjustment > 0.5:
        return 1.2  # 20% more frequent
    elif adjustment < 0.3:
        return 0.8  # 20% less frequent
    else:
        return 1.0  # No adjustment
```

### Nutrient Availability Adjustments

```python
def adjust_nutrient_targets(self, medium_type: str, base_targets: Dict[str, float]) -> Dict[str, float]:
    """Adjust nutrient targets based on medium properties."""

    properties = self.get_medium_properties(medium_type)
    nutrient_availability = properties["nutrient_availability"]

    adjustment_factors = {
        "none": 1.0,        # Inert medium - nutrients from solution only
        "low": 1.1,         # Low availability - slight increase needed
        "medium": 1.0,      # Medium availability - no adjustment
        "high": 0.9,        # High availability - slight reduction
        "controlled": 1.0   # Hydroponic - precisely controlled
    }

    factor = adjustment_factors.get(nutrient_availability, 1.0)

    adjusted_targets = {}
    for nutrient, target in base_targets.items():
        adjusted_targets[nutrient] = target * factor

    return adjusted_targets
```

## Medium Performance Tracking

### Performance Metrics

```python
class MediumPerformanceTracker:
    """Tracks and analyzes medium performance over time."""

    def __init__(self):
        self.performance_history = {}
        self.current_metrics = {}

    def update_metrics(self, medium_id: str, sensor_data: Dict[str, Any]):
        """Update performance metrics for a medium."""

        if medium_id not in self.current_metrics:
            self.current_metrics[medium_id] = {
                "vwc_stability": 0,
                "irrigation_efficiency": 0,
                "nutrient_retention": 0,
                "root_health_score": 0,
                "last_updated": None
            }

        metrics = self.current_metrics[medium_id]

        # Calculate VWC stability (lower variance = higher stability)
        vwc_history = self.get_vwc_history(medium_id, hours=24)
        if len(vwc_history) > 10:
            vwc_variance = statistics.variance(vwc_history)
            metrics["vwc_stability"] = max(0, 100 - (vwc_variance * 1000))

        # Calculate irrigation efficiency
        irrigation_events = self.get_irrigation_history(medium_id, hours=24)
        vwc_improvement = self.calculate_vwc_improvement(medium_id, hours=24)
        if irrigation_events:
            metrics["irrigation_efficiency"] = vwc_improvement / len(irrigation_events)

        metrics["last_updated"] = datetime.now()
```

### Health Assessment

```python
def assess_medium_health(self, medium_id: str) -> Dict[str, Any]:
    """Assess overall health of a growing medium."""

    metrics = self.current_metrics.get(medium_id, {})
    properties = self.get_medium_properties(medium_id)

    assessment = {
        "overall_health": 0,
        "issues": [],
        "recommendations": [],
        "last_assessment": datetime.now()
    }

    # Check VWC stability
    if metrics.get("vwc_stability", 0) < 50:
        assessment["issues"].append("Poor moisture stability")
        assessment["recommendations"].append("Consider medium amendment or irrigation adjustment")

    # Check irrigation efficiency
    if metrics.get("irrigation_efficiency", 0) < 0.5:
        assessment["issues"].append("Low irrigation efficiency")
        assessment["recommendations"].append("Check irrigation system and medium drainage")

    # Check for compaction indicators
    if self.detect_compaction(medium_id):
        assessment["issues"].append("Possible compaction")
        assessment["recommendations"].append("Consider medium aeration or replacement")

    # Calculate overall health score
    base_score = 100
    penalty_per_issue = 15
    assessment["overall_health"] = max(0, base_score - (len(assessment["issues"]) * penalty_per_issue))

    return assessment
```

## Medium Lifecycle Management

### Medium Initialization

```python
async def initialize_medium(self, medium_config: Dict[str, Any]) -> str:
    """Initialize a new growing medium."""

    medium_id = self.generate_medium_id()
    medium_type = medium_config.get("type", "soil")

    # Set basic properties
    self.mediums[medium_id] = {
        "type": medium_type,
        "properties": self.get_medium_properties(medium_type),
        "created": datetime.now(),
        "status": "active",
        "sensors": [],
        "devices": [],
        "performance_history": []
    }

    # Register sensors
    sensor_ids = medium_config.get("sensors", [])
    for sensor_id in sensor_ids:
        await self.register_sensor_to_medium(sensor_id, medium_id)

    # Register devices
    device_ids = medium_config.get("devices", [])
    for device_id in device_ids:
        await self.bind_device_to_medium(device_id, medium_id)

    # Emit initialization event
    await self.event_manager.emit("MediumInitialized", {
        "room": self.room,
        "medium_id": medium_id,
        "medium_type": medium_type
    })

    _LOGGER.info(f"[{self.room}] Initialized medium {medium_id} of type {medium_type}")

    return medium_id
```

### Medium Maintenance

```python
async def perform_medium_maintenance(self, medium_id: str):
    """Perform routine maintenance on a medium."""

    medium = self.mediums.get(medium_id)
    if not medium:
        return

    # Check for needed maintenance
    if self.needs_ph_adjustment(medium_id):
        await self.adjust_medium_ph(medium_id)

    if self.needs_nutrient_topup(medium_id):
        await self.add_nutrients_to_medium(medium_id)

    if self.needs_aeration(medium_id):
        await self.aerate_medium(medium_id)

    # Update maintenance schedule
    medium["last_maintenance"] = datetime.now()
    medium["next_maintenance"] = datetime.now() + timedelta(days=7)
```

### Medium Replacement

```python
async def replace_medium(self, old_medium_id: str, new_medium_config: Dict[str, Any]):
    """Replace an existing medium with a new one."""

    # Archive old medium data
    await self.archive_medium_data(old_medium_id)

    # Unregister old sensors and devices
    await self.unregister_medium_components(old_medium_id)

    # Initialize new medium
    new_medium_id = await self.initialize_medium(new_medium_config)

    # Transfer applicable settings
    await self.transfer_medium_settings(old_medium_id, new_medium_id)

    # Emit replacement event
    await self.event_manager.emit("MediumReplaced", {
        "room": self.room,
        "old_medium_id": old_medium_id,
        "new_medium_id": new_medium_id
    })

    _LOGGER.info(f"[{self.room}] Replaced medium {old_medium_id} with {new_medium_id}")
```

## Integration with Other Systems

### Crop Steering Integration

```python
async def coordinate_with_crop_steering(self, irrigation_request: Dict[str, Any]):
    """Coordinate medium management with crop steering irrigation."""

    medium_id = irrigation_request.get("medium_id")

    # Get medium-specific irrigation adjustments
    adjustments = self.get_irrigation_adjustments(medium_id)

    # Apply medium properties to irrigation parameters
    adjusted_request = self.apply_medium_adjustments(irrigation_request, adjustments)

    # Check medium health before irrigation
    health_check = await self.assess_medium_health(medium_id)
    if health_check["overall_health"] < 50:
        _LOGGER.warning(f"Medium {medium_id} health poor - irrigation may be ineffective")

    return adjusted_request
```

### Hydroponic System Integration

```python
async def coordinate_with_hydroponics(self, nutrient_solution: Dict[str, Any]):
    """Adjust nutrient solution based on medium properties."""

    medium_type = self.get_current_medium_type()

    # Adjust nutrient concentrations based on medium buffering
    buffer_capacity = self.get_buffer_capacity(medium_type)

    if buffer_capacity == "high":
        # Reduce nutrient concentrations - medium will buffer
        nutrient_solution = self.reduce_nutrient_concentrations(nutrient_solution, 0.8)
    elif buffer_capacity == "low":
        # Increase nutrient concentrations - inert medium needs more
        nutrient_solution = self.increase_nutrient_concentrations(nutrient_solution, 1.2)

    return nutrient_solution
```

## Configuration and Setup

### Medium Configuration

```python
# Example medium configuration
medium_setup = {
    "type": "rockwool",
    "sensors": [
        "sensor.vwc_zone1",
        "sensor.temperature_zone1",
        "sensor.ec_zone1"
    ],
    "devices": [
        "switch.dripper_zone1",
        "switch.pump_nutrient_a"
    ],
    "properties": {
        "volume_liters": 50,
        "plant_count": 4,
        "irrigation_zones": 2
    }
}
```

### Multi-Medium Setup

```python
# Room with multiple medium types
room_mediums = {
    "zone_vegetative": {
        "type": "coco",
        "sensors": ["sensor.vwc_veg", "sensor.temp_veg"],
        "purpose": "vegetative_propagation"
    },
    "zone_flowering": {
        "type": "rockwool",
        "sensors": ["sensor.vwc_flower", "sensor.temp_flower"],
        "purpose": "flowering_production"
    },
    "zone_mother": {
        "type": "soil",
        "sensors": ["sensor.vwc_mother", "sensor.temp_mother"],
        "purpose": "mother_plants"
    }
}
```

---

**Last Updated**: December 24, 2025
**Version**: 2.0 (Multi-Medium Support)
**Status**: Production Ready