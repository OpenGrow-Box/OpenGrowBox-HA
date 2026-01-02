# Sensor Processing Pipeline and VPD Calculations

## Overview

OpenGrowBox implements a comprehensive sensor processing pipeline that collects environmental data from multiple sensors, validates and aggregates readings, and performs advanced Vapor Pressure Deficit (VPD) calculations to drive intelligent climate control.

## Sensor Architecture

### Sensor Types and Contexts

OpenGrowBox supports multiple sensor types organized by context:

#### Air Context Sensors
- **Temperature**: Leaf and air temperature measurements
- **Humidity**: Relative humidity measurements
- **VPD**: Calculated vapor pressure deficit
- **Dew Point**: Calculated dew point temperature
- **CO2**: Carbon dioxide concentration

#### Water Context Sensors
- **pH**: Water acidity/alkalinity
- **EC/TDS**: Electrical conductivity/Total Dissolved Solids
- **Temperature**: Water temperature
- **ORP**: Oxidation-Reduction Potential

#### Soil Context Sensors
- **Moisture**: Soil moisture content
- **Temperature**: Soil temperature
- **EC**: Soil electrical conductivity

#### Light Context Sensors
- **PPFD**: Photosynthetic Photon Flux Density (μmol/m²/s)
- **DLI**: Daily Light Integral (mol/m²/day)
- **Lux**: Illuminance (rough conversion to PPFD)

### Sensor Data Structure

Each sensor reading includes:
```python
{
    "entity_id": "sensor.ogb_temperature_growroom1",
    "state": "25.5",
    "label": "Main Temperature Sensor",
    "last_updated": "2025-12-24T10:30:00Z",
    "context": "air",
    "type": "temperature"
}
```

## Sensor Processing Pipeline

### 1. Data Collection

#### Sensor Reading Manager (`OGBSensorReadingManager`)

Responsible for:
- **Initialization**: Set up sensor configurations and mappings
- **Reading Coordination**: Coordinate readings from multiple sensors
- **Data Aggregation**: Combine readings from similar sensors
- **Event Handling**: Process sensor update events

```python
async def initialize_sensors(self, sensor_map, config_manager):
    """Initialize sensors from configuration."""
    for context in ["air", "water", "soil", "light"]:
        for sensor_type, sensor_entries in sensor_map["sensors"][context].items():
            await self._initialize_sensor_type(
                sensor_type, sensor_entry, context, config_manager
            )
```

#### Sensor Validation Manager (`OGBSensorValidationManager`)

Ensures data quality:
- **Range Validation**: Check values within expected ranges
- **Outlier Detection**: Identify and filter anomalous readings
- **Data Consistency**: Validate sensor calibration
- **Error Handling**: Graceful degradation on sensor failures

### 2. Data Processing

#### Averaging and Filtering

Multiple sensors of the same type are averaged:
```python
def calculate_avg_value(sensor_readings):
    """Calculate average from multiple sensor readings."""
    valid_readings = []
    for reading in sensor_readings:
        try:
            value = float(reading.get("value", reading.get("state")))
            if not math.isnan(value) and value != "unavailable":
                valid_readings.append(value)
        except (ValueError, TypeError):
            continue

    return sum(valid_readings) / len(valid_readings) if valid_readings else "unavailable"
```

#### Sensor Calibration Manager (`OGBSensorCalibrationManager`)

Applies calibration corrections:
- **Offset Adjustments**: Temperature and humidity offsets
- **Scaling Factors**: Convert between units (lux to PPFD)
- **Calibration Curves**: Non-linear corrections for sensors
- **Drift Compensation**: Long-term accuracy maintenance

### 3. VPD Calculations

#### VPD Fundamentals

Vapor Pressure Deficit (VPD) is the difference between the amount of moisture in the air and how much moisture the air can hold at saturation:

```
VPD = SVPD - AVPD

Where:
- SVPD = Saturation Vapor Pressure Deficit (based on temperature)
- AVPD = Actual Vapor Pressure Deficit (based on humidity)
```

#### VPD Calculation Process

##### Step 1: Saturation Vapor Pressure (SVP)
```python
def calculate_svp(temperature_c):
    """Calculate saturation vapor pressure in kPa."""
    # Magnus-Tetens formula
    a = 17.27
    b = 237.3
    svp = 0.6108 * math.exp((a * temperature_c) / (b + temperature_c))
    return svp
```

##### Step 2: Actual Vapor Pressure (AVP)
```python
def calculate_avp(temperature_c, humidity_percent):
    """Calculate actual vapor pressure in kPa."""
    svp = calculate_svp(temperature_c)
    avp = svp * (humidity_percent / 100)
    return avp
```

##### Step 3: VPD Calculation
```python
def calculate_current_vpd(temperature_c, humidity_percent, leaf_temp_offset=0):
    """Calculate VPD with leaf temperature offset."""
    # Apply leaf temperature offset
    leaf_temp = temperature_c + leaf_temp_offset

    # Calculate saturation vapor pressure at leaf temperature
    svp_leaf = calculate_svp(leaf_temp)

    # Calculate actual vapor pressure at air temperature
    avp_air = calculate_avp(temperature_c, humidity_percent)

    # VPD = SVP(at leaf) - AVP(at air)
    vpd = svp_leaf - avp_air

    return round(vpd, 3) if vpd > 0 else 0.001
```

#### Leaf Temperature Offset

The leaf temperature is often higher than air temperature due to:
- **Transpiration Cooling**: Leaves cool as water evaporates
- **Radiant Heating**: Direct light absorption
- **Boundary Layer**: Microclimate around leaves

```python
# Configuration example
leaf_temp_offset = data_store.getDeep("tentData.leafTempOffset")  # Default: 0.0
```

### 4. Environmental Calculations

#### Dew Point Calculation

```python
def calculate_dew_point(temperature_c, humidity_percent):
    """Calculate dew point temperature."""
    a = 17.27
    b = 237.3

    alpha = ((a * temperature_c) / (b + temperature_c)) + math.log(humidity_percent / 100.0)
    dew_point = (b * alpha) / (a - alpha)

    return round(dew_point, 1)
```

#### Light Calculations

##### PPFD to DLI Conversion
```python
def calc_light_to_ppfd_dli(ppfd_value, light_hours):
    """Convert PPFD to Daily Light Integral."""
    # DLI = PPFD × hours × (3600 seconds/hour) / 1,000,000 μmol/mol
    dli = ppfd_value * light_hours * 3600 / 1000000
    return round(dli, 1)
```

##### Lux to PPFD Approximation
```python
lux_to_ppfd_factor = data_store.getDeep("calibration.luxToPPFDFactor")  # Default: 15.0
ppfd_approximation = lux_value / lux_to_ppfd_factor
```

## VPD Manager Architecture

### OGBVPDManager Responsibilities

The VPD Manager (`OGBVPDManager`) orchestrates the entire VPD calculation pipeline:

1. **Sensor Data Collection**: Gather temperature and humidity readings
2. **Data Validation**: Ensure sensor data quality
3. **VPD Calculation**: Perform VPD calculations with offsets
4. **State Updates**: Update data store with current values
5. **Sensor Publishing**: Publish VPD values to HA entities
6. **Analytics Submission**: Send data to Premium API (if enabled)
7. **Mode Triggering**: Emit events to trigger control actions

### VPD Processing Flow

```
Sensor Update Event
        ↓
VPD Manager.handle_new_vpd()
        ↓
Collect Temperature/Humidity Sensors
        ↓
Validate and Average Readings
        ↓
Apply Leaf Temperature Offset
        ↓
Calculate Current VPD
        ↓
Update Data Store
        ↓
Publish to HA Sensors
        ↓
Submit Analytics (Premium)
        ↓
Emit Mode Selection Event
        ↓
Trigger Control Actions
```

## Control Mode Integration

### VPD-Based Control Modes

#### 1. VPD Perfection Mode
- **Target**: Maintain optimal VPD for plant stage
- **Action**: Adjust humidity/temperature to achieve target VPD
- **Feedback**: Continuous VPD monitoring and adjustment

#### 2. VPD Target Mode
- **Target**: User-defined VPD range
- **Action**: Keep VPD within specified bounds
- **Feedback**: Range-based control with hysteresis

### VPD Targets by Plant Stage

```python
# Actual VPD ranges from datastore (kPa)
PLANT_STAGES_VPD = {
    "Germination": [0.35, 0.70],    # Germination phase
    "Clones": [0.40, 0.85],         # Clone propagation
    "EarlyVeg": [0.60, 1.20],       # Early vegetative growth
    "MidVeg": [0.75, 1.45],         # Mid vegetative growth
    "LateVeg": [0.90, 1.65],        # Late vegetative growth
    "EarlyFlower": [0.80, 1.55],    # Early flowering
    "MidFlower": [0.90, 1.70],      # Mid flowering
    "LateFlower": [0.90, 1.85],     # Late flowering
}
```

The system uses stage-specific VPD ranges with environmental targets:
- **Germination**: 0.35-0.70 kPa (very low for delicate seedlings)
- **Clones**: 0.40-0.85 kPa (low for clone rooting)
- **EarlyVeg**: 0.60-1.20 kPa (moderate for initial growth)
- **MidVeg**: 0.75-1.45 kPa (optimal vegetative development)
- **LateVeg**: 0.90-1.65 kPa (pre-flowering preparation)
- **EarlyFlower**: 0.80-1.55 kPa (transition to flowering)
- **MidFlower**: 0.90-1.70 kPa (peak flowering)
- **LateFlower**: 0.90-1.85 kPa (late flowering/fruiting)

## Sensor Data Publishing

### HA Entity Updates

VPD calculations result in multiple sensor updates:

```python
# VPD Publication
vpd_publication = OGBVPDPublication(
    Name=room,
    VPD=current_vpd,
    AvgTemp=avg_temperature,
    AvgHum=avg_humidity,
    AvgDew=avg_dewpoint
)

# Update HA sensor
await update_sensor_via_service(room, vpd_publication, hass)
```

### Sensor Entities Created

| Entity ID | Type | Description |
|-----------|------|-------------|
| `sensor.ogb_vpd_{room}` | Sensor | Current VPD value |
| `sensor.ogb_temperature_{room}` | Sensor | Average air temperature |
| `sensor.ogb_humidity_{room}` | Sensor | Average relative humidity |
| `sensor.ogb_dewpoint_{room}` | Sensor | Calculated dew point |

## Premium Analytics Integration

### Automatic Data Submission

When Premium features are enabled, VPD data is automatically submitted:

```python
analytics_data = {
    "type": "vpd",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "room": self.room,
    "vpd": current_vpd,
    "temperature": avg_temperature,
    "humidity": avg_humidity,
    "dewpoint": avg_dewpoint,
    "target_vpd": data_store.getDeep("vpd.target"),
}
```

### Analytics Benefits

- **Historical Tracking**: Long-term VPD trends
- **Optimization Insights**: AI-driven VPD recommendations
- **Compliance Reporting**: Environmental data logging
- **Research Contributions**: Anonymized data for plant science

## Error Handling and Resilience

### Sensor Failure Handling

- **Graceful Degradation**: Continue with available sensors
- **Fallback Values**: Use last known good values
- **Error Logging**: Comprehensive error tracking
- **Recovery Mechanisms**: Automatic sensor recovery attempts

### Data Validation

```python
def validate_sensor_reading(self, reading, sensor_type):
    """Validate sensor reading against expected ranges."""
    ranges = {
        "temperature": {"min": -50, "max": 100},
        "humidity": {"min": 0, "max": 100},
        "vpd": {"min": 0, "max": 5.0},
        "ph": {"min": 0, "max": 14},
        "ec": {"min": 0, "max": 10},
    }

    if sensor_type not in ranges:
        return True

    try:
        value = float(reading)
        return ranges[sensor_type]["min"] <= value <= ranges[sensor_type]["max"]
    except (ValueError, TypeError):
        return False
```

## Performance Optimization

### Caching Strategies

- **Sensor Value Caching**: Reduce redundant HA entity queries
- **Calculation Caching**: Cache expensive calculations
- **Batch Updates**: Group sensor updates for efficiency

### Asynchronous Processing

- **Non-blocking Calculations**: All VPD calculations are async
- **Event-driven Updates**: Reactive rather than polling-based
- **Resource Pooling**: Efficient sensor reading coordination

## Configuration and Calibration

### Sensor Configuration

Sensors are configured through the configuration manager:

```python
sensor_config = {
    "sensors": {
        "air": {
            "temperature": [
                {
                    "entity_id": "sensor.temp1",
                    "label": "Main Temp Sensor",
                    "calibration_offset": 0.5
                }
            ],
            "humidity": [
                {
                    "entity_id": "sensor.hum1",
                    "label": "Main Humidity Sensor",
                    "calibration_offset": 2.0
                }
            ]
        }
    }
}
```

### Calibration Procedures

1. **Offset Calibration**: Adjust for sensor bias
2. **Scaling Calibration**: Correct for non-linear responses
3. **Cross-validation**: Compare multiple sensors
4. **Long-term Drift**: Monitor and correct sensor drift

## Troubleshooting

### Common Issues

#### VPD Readings of Zero
- **Cause**: Invalid temperature/humidity data
- **Solution**: Check sensor connectivity and calibration

#### Inconsistent VPD Values
- **Cause**: Sensor averaging issues or calibration problems
- **Solution**: Validate sensor ranges and calibration offsets

#### Missing Sensor Data
- **Cause**: HA entity unavailable or misconfigured
- **Solution**: Check entity IDs and HA integration

#### Premium Analytics Not Working
- **Cause**: WebSocket disconnected or authentication failed
- **Solution**: Check Premium login status and connection

### Debug Logging

Enable detailed sensor logging:

```yaml
# configuration.yaml
logger:
  logs:
    custom_components.opengrowbox.OGBController.sensors: debug
    custom_components.opengrowbox.OGBController.managers.OGBVPDManager: debug
```

---

**Last Updated**: December 24, 2025
**Version**: 2.0 (Modular Architecture)
**Status**: Production Ready