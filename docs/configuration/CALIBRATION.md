# Sensor and Device Calibration Guide

## Overview

Proper calibration of sensors and devices is essential for accurate environmental control and optimal plant growth. This guide covers calibration procedures for all OpenGrowBox sensors and devices.

## Calibration Architecture

### Calibration Data Storage

Calibration data is stored in the device configuration and persists across restarts:

```python
calibration_data = {
    "sensors": {
        "temperature": {
            "offset": 0.5,              # Temperature offset (°C)
            "multiplier": 1.02,         # Calibration multiplier
            "last_calibrated": "2025-01-15T10:00:00Z",
            "reference_reading": 25.0    # Reference temperature used
        },
        "humidity": {
            "offset": 2.0,              # Humidity offset (%)
            "multiplier": 0.98,         # Calibration multiplier
            "last_calibrated": "2025-01-15T10:00:00Z",
            "reference_reading": 60.0    # Reference humidity used
        }
    },
    "devices": {
        "exhaust_fan": {
            "voltage_offset": 5,        # Voltage offset
            "flow_calibration": 1.05,   # Airflow calibration factor
            "last_calibrated": "2025-01-15T10:00:00Z"
        }
    }
}
```

## Sensor Calibration

### Temperature Sensor Calibration

Temperature sensors can drift over time due to environmental factors. Regular calibration ensures accurate temperature readings.

#### Required Equipment
- Certified digital thermometer (accuracy ±0.1°C)
- Calibration chamber or stable environment
- Access to Home Assistant configuration interface

#### Calibration Procedure

1. **Prepare Calibration Environment**
   ```python
   # Create stable calibration environment
   calibration_setup = {
       "target_temperature": 25.0,      # Target calibration temperature (°C)
       "stabilization_time": 3600,      # 1 hour stabilization period
       "measurement_interval": 60       # Take readings every minute
   }
   ```

2. **Take Reference Reading**
   - Place certified thermometer next to OGB sensor
   - Allow 1 hour for temperature stabilization
   - Record certified thermometer reading: `T_reference = 25.2°C`

3. **Record OGB Sensor Reading**
   - Access sensor data through HA: `sensor.ogb_zone1_temperature`
   - Record current reading: `T_sensor = 24.8°C`

4. **Calculate Calibration Offset**
   ```python
   # Calculate temperature calibration
   temperature_calibration = {
       "reference_reading": 25.2,       # Certified thermometer (°C)
       "sensor_reading": 24.8,          # OGB sensor reading (°C)
       "offset": 25.2 - 24.8,          # = 0.4°C offset
       "multiplier": 1.0               # Default multiplier for temperature
   }
   ```

5. **Apply Calibration**
   ```yaml
   # Configuration update
   sensor_calibration:
     temperature:
       offset: 0.4                    # Add 0.4°C to readings
       multiplier: 1.0                # No scaling needed
       last_calibrated: "2025-01-15T10:00:00Z"
   ```

#### Calibration Verification
- Monitor sensor readings for 24 hours
- Verify readings match reference thermometer within ±0.2°C
- Check for reading stability during environmental changes

### Humidity Sensor Calibration

Humidity sensors are particularly sensitive to calibration due to their operating principles.

#### Salt Solution Calibration Method

1. **Prepare Saturated Salt Solutions**
   ```python
   # Standard humidity references using saturated salt solutions
   humidity_references = {
       "lithium_chloride": 11.3,        # 11.3% RH at 25°C
       "magnesium_chloride": 32.8,      # 32.8% RH at 25°C
       "sodium_chloride": 75.3,         # 75.3% RH at 25°C
       "potassium_sulfate": 97.3        # 97.3% RH at 25°C
   }
   ```

2. **Calibration Procedure**
   - Create sealed chambers with each salt solution
   - Allow 24 hours for equilibrium
   - Place OGB sensor in each chamber for 2 hours
   - Record sensor readings at each reference humidity

3. **Calculate Humidity Calibration**
   ```python
   # Example calibration data
   humidity_calibration_data = [
       {"reference": 32.8, "sensor": 30.5},  # Magnesium chloride
       {"reference": 75.3, "sensor": 77.1},  # Sodium chloride
       {"reference": 97.3, "sensor": 95.8}   # Potassium sulfate
   ]

   # Calculate linear calibration curve
   # y = mx + b, where y = corrected_reading, x = sensor_reading
   calibration_curve = calculate_linear_regression(humidity_calibration_data)
   ```

4. **Apply Humidity Calibration**
   ```yaml
   sensor_calibration:
     humidity:
       offset: -2.1                    # From regression calculation
       multiplier: 0.98                # From regression calculation
       last_calibrated: "2025-01-15T14:00:00Z"
       method: "salt_solution"         # Calibration method used
   ```

### VPD Sensor Calibration

VPD (Vapor Pressure Deficit) calibration requires accurate temperature and humidity calibration first.

#### VPD Calibration Procedure

1. **Verify Base Sensors**
   - Ensure temperature and humidity sensors are calibrated
   - Verify sensor accuracy within ±0.2°C and ±2% RH

2. **Create Known VPD Environment**
   ```python
   # Create controlled environment for VPD calibration
   vpd_calibration_environment = {
       "temperature": 25.0,             # °C
       "humidity": 60.0,               # %
       "expected_vpd": 1.25            # kPa (calculated from temp/humidity)
   }
   ```

3. **Calculate Expected VPD**
   ```python
   # VPD calculation formula
   def calculate_vpd(temperature_c, humidity_percent):
       # Convert temperature to Kelvin
       temp_k = temperature_c + 273.15

       # Calculate saturation vapor pressure
       svp = 0.6108 * math.exp((17.27 * temperature_c) / (temp_k - 35.85))

       # Calculate actual vapor pressure
       avp = svp * (humidity_percent / 100)

       # Calculate VPD
       vpd = svp - avp
       return vpd

   expected_vpd = calculate_vpd(25.0, 60.0)  # Should be ~1.25 kPa
   ```

4. **Apply VPD Calibration**
   ```yaml
   sensor_calibration:
     vpd:
       offset: 0.0                    # VPD is calculated from T/H
       multiplier: 1.0                # VPD scaling factor
       last_calibrated: "2025-01-15T16:00:00Z"
       base_sensor_accuracy_verified: true
   ```

### VWC (Volumetric Water Content) Sensor Calibration

VWC sensors are used by the CropSteering system for precision irrigation. Proper calibration ensures accurate watering.

#### VWC Calibration Types

| Calibration | Purpose | When to Use |
|-------------|---------|-------------|
| **VWC Max** | Find saturation point | New medium, changed medium type |
| **VWC Min** | Find safe minimum | After VWC Max, or for dryback tuning |

#### Console Commands for VWC Calibration

```bash
# Check current calibration status
cs_status

# Start VWC Maximum calibration (saturation point)
cs_calibrate max
cs_calibrate max p1    # For specific phase

# Start VWC Minimum calibration (dryback monitoring)
cs_calibrate min
cs_calibrate min p2    # For specific phase

# Stop running calibration
cs_calibrate stop
```

#### VWC Max Calibration Procedure

The system automatically calibrates VWCMax through progressive saturation:

1. **Start Calibration**: Run `cs_calibrate max`
2. **Progressive Irrigation**: System irrigates in cycles
3. **Stabilization Check**: Waits for VWC to stabilize after each irrigation
4. **Stagnation Detection**: When VWC stops increasing → saturation reached
5. **Store Result**: VWCMax saved and persisted to disk

```python
# Calibration result stored in:
CropSteering.Calibration.p1.VWCMax = 68.5    # Saturation point
CropSteering.Calibration.p1.timestamp = "2026-01-03T14:30:00"
```

#### VWC Min Calibration Procedure

VWCMin calibration monitors natural dryback:

1. **Start Calibration**: Run `cs_calibrate min`
2. **Dryback Monitoring**: System monitors VWC decrease over ~2 hours
3. **Minimum Detection**: Tracks lowest VWC observed
4. **Safety Buffer**: Applies 10% safety buffer above observed minimum
5. **Store Result**: VWCMin saved and persisted to disk

```python
# Calibration result stored in:
CropSteering.Calibration.p1.VWCMin = 32.1    # Safe minimum with buffer
CropSteering.Calibration.p1.timestamp = "2026-01-03T16:30:00"
```

#### Auto-Calibration During P1 Phase

The CropSteering system also performs automatic VWCMax calibration during the P1 (Saturation) phase. This happens when:
- VWC stops increasing after irrigation (stagnation)
- Maximum irrigation attempts reached

This "passive" calibration updates VWCMax as part of normal operation.

#### VWC Calibration Data Persistence

**Important**: VWC calibration values are now persisted across HA restarts:

```python
# Storage structure
CropSteering: {
    "Calibration": {
        "p1": {"VWCMax": 68.5, "VWCMin": 32.1, "timestamp": "..."},
        "p2": {"VWCMax": null, "VWCMin": null, "timestamp": null},
        "p3": {"VWCMax": null, "VWCMin": null, "timestamp": null},
        "LastRun": "2026-01-03T14:30:00"
    }
}
```

#### Medium-Specific VWC Calibration

Different growing mediums have different VWC characteristics:

| Medium | Typical VWCMax | Typical VWCMin | Notes |
|--------|----------------|----------------|-------|
| Rockwool | 65-75% | 30-40% | Fast drainage |
| Coco | 70-80% | 35-45% | Good retention |
| Soil | 55-70% | 25-35% | Slow drainage |
| Perlite | 50-65% | 20-30% | Very fast drainage |

**Always calibrate for your specific medium** - these are just guidelines.

### CO2 Sensor Calibration

CO2 sensors require periodic recalibration, especially in grow environments.

#### Single-Point Calibration

1. **Fresh Air Calibration**
   ```python
   # Calibrate to outdoor air (400 ppm CO2)
   co2_calibration = {
       "reference_co2": 400,           # Outdoor air CO2 (ppm)
       "sensor_reading": 385,          # Current sensor reading
       "offset": 400 - 385,           # = 15 ppm offset
       "last_calibrated": "2025-01-15T18:00:00Z"
   }
   ```

2. **Apply CO2 Calibration**
   ```yaml
   sensor_calibration:
     co2:
       offset: 15                     # Add 15 ppm to readings
       multiplier: 1.0                # Default multiplier
       last_calibrated: "2025-01-15T18:00:00Z"
       method: "fresh_air"
   ```

## Device Calibration

### Fan Speed and Airflow Calibration

Fans require calibration for accurate airflow control and VPD management.

#### RPM Calibration

1. **Measure Actual RPM**
   - Use tachometer to measure fan speed at various voltages
   - Record voltage vs. RPM relationship

2. **Flow Rate Calibration**
   ```python
   # Fan calibration data structure
   fan_calibration = {
       "voltage_steps": [20, 40, 60, 80, 100],  # Voltage percentages
       "measured_rpm": [800, 1450, 2100, 2750, 3200],  # Actual RPM readings
       "measured_cfm": [50, 120, 180, 240, 290],  # Actual CFM readings
       "calibration_factors": [],  # Calculated correction factors
       "last_calibrated": "2025-01-15T20:00:00Z"
   }
   ```

3. **Calculate Calibration Curve**
   ```python
   # Create piecewise linear calibration
   def calculate_fan_calibration(voltage_steps, measured_rpm, target_rpm):
       calibration_curve = []
       for i, voltage in enumerate(voltage_steps):
           expected_rpm = target_rpm[i]  # Expected RPM at this voltage
           actual_rpm = measured_rpm[i]
           factor = actual_rpm / expected_rpm if expected_rpm > 0 else 1.0
           calibration_curve.append({
               "voltage": voltage,
               "factor": factor,
               "offset": 0
           })
       return calibration_curve
   ```

### Pump Flow Rate Calibration

Accurate pump calibration is critical for irrigation and nutrient delivery.

#### Pump Calibration Procedure

1. **Setup Calibration Station**
   ```python
   pump_calibration_setup = {
       "container_volume": 1000,        # ml calibration container
       "pump_runtime": 30,             # seconds per test
       "voltage_setting": 100,         # Full voltage for calibration
       "repetitions": 3               # Number of test runs
   }
   ```

2. **Measure Flow Rate**
   - Run pump for exact time period
   - Measure delivered volume
   - Calculate flow rate: `flow_rate = volume_ml / time_seconds`

3. **Apply Pump Calibration**
   ```python
   # Pump calibration result
   pump_calibration = {
       "measured_flow_rate": 45.2,      # ml/second actual
       "expected_flow_rate": 50.0,      # ml/second expected
       "calibration_factor": 45.2 / 50.0,  # = 0.904
       "voltage": 100,                  # Calibration voltage
       "last_calibrated": "2025-01-15T22:00:00Z"
   }
   ```

4. **Update Configuration**
   ```yaml
   device_calibration:
     irrigation_pump:
       flow_calibration: 0.904         # Multiply flow calculations by 0.904
       voltage_offset: 0               # Voltage adjustment if needed
       last_calibrated: "2025-01-15T22:00:00Z"
   ```

### Humidifier/Dehumidifier Calibration

Humidity control devices need calibration for accurate moisture control.

#### Output Rate Calibration

1. **Measure Humidity Change Rate**
   ```python
   humidity_device_calibration = {
       "device_type": "humidifier",     # or "dehumidifier"
       "test_duration": 1800,          # 30 minutes test
       "initial_humidity": 50.0,       # % starting humidity
       "final_humidity": 65.0,         # % ending humidity
       "expected_change": 15.0,        # % expected change
       "measured_change": 13.2         # % actual change
   }
   ```

2. **Calculate Efficiency Factor**
   ```python
   efficiency_factor = humidity_device_calibration["measured_change"] / humidity_device_calibration["expected_change"]
   # Result: 13.2 / 15.0 = 0.88 (88% efficiency)
   ```

3. **Apply Device Calibration**
   ```yaml
   device_calibration:
     humidifier_main:
       efficiency_factor: 0.88         # Adjust runtime calculations
       last_calibrated: "2025-01-16T08:00:00Z"
       calibration_method: "humidity_change_rate"
   ```

### Heater/Cooler Calibration

Temperature control devices require calibration for accurate climate control.

#### Power Output Calibration

1. **Measure Temperature Change**
   ```python
   thermal_device_calibration = {
       "device_type": "heater",         # or "cooler"
       "test_duration": 3600,          # 1 hour test
       "initial_temp": 22.0,           # °C starting temperature
       "final_temp": 25.0,             # °C ending temperature
       "expected_rise": 3.0,           # °C expected change
       "measured_rise": 2.7            # °C actual change
   }
   ```

2. **Calculate Calibration Factor**
   ```python
   calibration_factor = thermal_device_calibration["measured_rise"] / thermal_device_calibration["expected_rise"]
   # Result: 2.7 / 3.0 = 0.90 (90% efficiency)
   ```

## Automated Calibration System

### Calibration Scheduling

The system includes automated calibration reminders and scheduling:

```python
calibration_schedule = {
    "sensors": {
        "temperature": {
            "interval_days": 90,           # Calibrate every 90 days
            "last_calibrated": "2025-01-15T10:00:00Z",
            "next_due": "2025-04-15T10:00:00Z",
            "auto_reminder": true
        },
        "humidity": {
            "interval_days": 60,           # Calibrate every 60 days
            "last_calibrated": "2025-01-15T14:00:00Z",
            "next_due": "2025-03-16T14:00:00Z",
            "auto_reminder": true
        }
    },
    "devices": {
        "fans": {
            "interval_days": 180,          # Calibrate every 6 months
            "last_calibrated": "2025-01-15T20:00:00Z",
            "next_due": "2025-07-14T20:00:00Z"
        },
        "pumps": {
            "interval_days": 30,           # Calibrate monthly
            "last_calibrated": "2025-01-15T22:00:00Z",
            "next_due": "2025-02-14T22:00:00Z"
        }
    }
}
```

### Calibration Status Monitoring

```python
def check_calibration_status():
    """Check if any calibrations are due or overdue."""
    current_time = datetime.now()
    alerts = []

    for sensor_type, config in calibration_schedule["sensors"].items():
        days_overdue = (current_time - config["last_calibrated"]).days - config["interval_days"]
        if days_overdue > 0:
            alerts.append({
                "type": "sensor",
                "sensor": sensor_type,
                "days_overdue": days_overdue,
                "severity": "warning" if days_overdue < 30 else "critical"
            })

    return alerts
```

## Calibration Validation and Testing

### Post-Calibration Validation

After applying calibration, validate the changes:

1. **Sensor Validation Tests**
   ```python
   validation_tests = {
       "temperature_stability": {
           "test_duration": 3600,          # 1 hour test
           "acceptable_variance": 0.1,     # °C maximum variance
           "sample_interval": 60          # Sample every minute
       },
       "humidity_accuracy": {
           "reference_points": [30, 50, 70, 90],  # % RH test points
           "acceptable_error": 2.0         # % maximum error
       }
   }
   ```

2. **Device Validation Tests**
   ```python
   device_validation = {
       "fan_speed_accuracy": {
           "test_voltages": [25, 50, 75, 100],
           "acceptable_error": 50          # RPM tolerance
       },
       "pump_flow_accuracy": {
           "test_duration": 60,            # 1 minute test
           "acceptable_error": 5.0         # ml tolerance
       }
   }
   ```

### Calibration History and Trending

Track calibration history for quality assurance:

```python
calibration_history = {
    "sensor_temperature": [
        {"date": "2024-10-15", "offset": 0.2, "accuracy": 0.1},
        {"date": "2025-01-15", "offset": 0.4, "accuracy": 0.05},
    ],
    "device_pump_main": [
        {"date": "2024-12-15", "factor": 0.95, "accuracy": 2.1},
        {"date": "2025-01-15", "factor": 0.904, "accuracy": 1.8},
    ]
}
```

## Safety Considerations

### Calibration Safety Guidelines

1. **Electrical Safety**
   - Always disconnect power before physical sensor access
   - Use insulated tools when working with electrical components
   - Never perform calibration during electrical storms

2. **Environmental Safety**
   - Ensure proper ventilation when working with calibration gases
   - Use appropriate PPE for chemical calibration solutions
   - Maintain stable environmental conditions during calibration

3. **Plant Protection**
   - Schedule calibrations during low-stress periods
   - Monitor plants closely during calibration procedures
   - Have backup environmental control ready

### Emergency Calibration Procedures

For urgent calibration needs when readings are dangerously inaccurate:

```python
emergency_calibration = {
    "temperature_critical": {
        "fallback_offset": 0,           # Use raw readings
        "notification_required": true,
        "immediate_recalibration": true
    },
    "device_failure": {
        "safe_mode_enabled": true,      # Disable automated control
        "manual_override": true,        # Allow manual device control
        "alert_level": "critical"
    }
}
```

## Troubleshooting Calibration Issues

### Common Calibration Problems

1. **Drifting Sensor Readings**
   - **Cause**: Environmental contamination or aging sensors
   - **Solution**: Clean sensors and recalibrate, replace if necessary

2. **Inconsistent Device Performance**
   - **Cause**: Worn components or power supply issues
   - **Solution**: Check power quality and replace worn parts

3. **Failed Validation Tests**
   - **Cause**: Incorrect calibration procedure or environmental factors
   - **Solution**: Repeat calibration with stricter environmental control

### Calibration Data Recovery

If calibration data is lost or corrupted:

```python
def recover_calibration_data():
    """Attempt to recover calibration data from backups or defaults."""
    recovery_options = {
        "backup_restore": True,          # Restore from last backup
        "default_fallback": True,        # Use factory defaults
        "minimal_calibration": True      # Quick single-point calibration
    }

    # Attempt recovery in order of preference
    if backup_available():
        restore_from_backup()
    elif quick_calibration_possible():
        perform_minimal_calibration()
    else:
        apply_factory_defaults()
```

---

## Calibration Summary

**Calibration completed!** Your sensors and devices are now properly calibrated for accurate environmental control.

**Calibration Areas Covered:**
- ✅ Temperature, humidity, and VPD sensors
- ✅ Fan speed and airflow devices
- ✅ Pump flow rate and irrigation systems
- ✅ Humidity control devices
- ✅ Thermal control systems
- ✅ Automated calibration scheduling
- ✅ Validation and testing procedures

**Recommended Calibration Schedule:**
- **Sensors**: Every 60-90 days or when accuracy is questionable
- **Devices**: Every 30-180 days depending on usage and criticality
- **Critical Systems**: Immediate recalibration if readings seem inaccurate

**For additional configuration options, see the [Configuration Guide](CONFIGURATION.md)**

**For troubleshooting calibration issues, see the [Troubleshooting Guide](../../appendices/TROUBLESHOOTING.md)**</content>
<parameter name="filePath">docs/configuration/CALIBRATION.md