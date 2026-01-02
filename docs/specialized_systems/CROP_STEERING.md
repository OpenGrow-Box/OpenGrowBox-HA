# Crop Steering System - Advanced Irrigation Management

## Overview

The Crop Steering system is an advanced irrigation management system that uses soil moisture sensors (Volumetric Water Content - VWC) to provide intelligent, automated watering based on plant needs, growth stages, and environmental conditions. It replaces traditional timer-based irrigation with sensor-driven, precision watering.

## System Architecture

### Core Components

#### 1. OGBCSManager (Main Controller)
```python
class OGBCSManager:
    """Main crop steering controller coordinating all subsystems."""
```

#### 2. OGBCSConfigurationManager (Settings)
```python
class OGBCSConfigurationManager:
    """Manages crop steering configuration and presets."""
```

#### 3. OGBCSIrrigationManager (Watering Logic)
```python
class OGBCSIrrigationManager:
    """Handles irrigation scheduling and execution."""
```

#### 4. OGBCSPhaseManager (Plant Stages)
```python
class OGBCSPhaseManager:
    """Manages plant growth phases and watering adjustments."""
```

#### 5. OGBCSCalibrationManager (Sensor Calibration)
```python
class OGBCSCalibrationManager:
    """Handles VWC sensor calibration and accuracy."""
```

#### 6. OGBAdvancedSensor (TDR Processing)
```python
class OGBAdvancedSensor:
    """Advanced sensor processing with TDR-style calculations."""
```

**Features:**
- Medium-specific VWC polynomial calibration (Teros-12 soilless)
- Pore water EC calculation (Hilhorst model + mass-balance hybrid)
- Temperature normalization for EC readings
- Validation and anomaly detection

## Crop Steering Modes

### 1. Disabled Mode
- **Purpose**: Safety mode, all automation disabled
- **Watering**: Manual or external systems only
- **Use Case**: Maintenance, troubleshooting, manual control

### 2. Basic Mode (VWCMin/VWCMax)
- **Logic**: Simple threshold-based watering
- **Trigger**: When VWC drops below VWCMin
- **Duration**: Water until VWCMax reached
- **Use Case**: Straightforward automation for beginners

### 3. Advanced Mode (Phase-Based)
- **Logic**: Plant stage and environmental adaptation
- **Factors**: Growth phase, temperature, humidity, light
- **Optimization**: Prevents over/under watering
- **Use Case**: Optimal plant health and resource efficiency

## Plant Growth Phases

### Phase Definitions

```python
PLANT_PHASES = {
    "germ": {
        "vwc_min": 0.75,    # 75% moisture for germination
        "vwc_max": 0.90,    # 90% maximum to prevent rot
        "irrigation_interval": 3600,  # Check every hour
        "description": "Germination phase - high moisture needed"
    },
    "veg": {
        "vwc_min": 0.60,    # 60% for vegetative growth
        "vwc_max": 0.80,    # 80% maximum
        "irrigation_interval": 7200,  # Check every 2 hours
        "description": "Vegetative growth - balanced moisture"
    },
    "gen": {
        "vwc_min": 0.50,    # 50% for generative phase
        "vwc_max": 0.75,    # 75% maximum
        "irrigation_interval": 10800, # Check every 3 hours
        "description": "Flowering/fruiting - slightly drier"
    }
}
```

### Phase-Specific Adjustments

#### Vegetative Phase
- **Higher moisture retention** for rapid growth
- **More frequent checks** to prevent drying out
- **Balanced irrigation** to support leaf development

#### Generative Phase (Flowering)
- **Gradually drier conditions** to stress plants for flowering
- **Reduced irrigation frequency** to prevent bud rot
- **Environmental adaptation** based on humidity/temperature

## VWC Sensor Technology

### Volumetric Water Content (VWC)

VWC measures the percentage of water volume in the soil:
- **0%**: Completely dry soil
- **100%**: Saturated soil (not recommended)
- **Optimal Range**: 50-80% depending on plant phase

### Sensor Calibration

#### Automatic VWC Calibration

```python
class OGBCSCalibrationManager:
    """Manages VWC sensor calibration for accuracy."""

    async def start_vwc_max_calibration(self, phase: str = "p1"):
        """Start VWC maximum calibration procedure."""
        # Progressive saturation until no more increase
        # Finds actual maximum VWC for medium

    async def start_vwc_min_calibration(self, phase: str = "p1"):
        """Start VWC minimum calibration through dryback."""
        # Monitors natural dryback to find minimum
        # Applies safety buffer for plant health

    async def _vwc_max_calibration_cycle(self, phase):
        """Main calibration cycle with stabilization monitoring."""
        # Multiple irrigation cycles
        # Wait for stabilization between cycles
        # Detect when medium is fully saturated

    async def _wait_for_vwc_stabilization(self, timeout=300):
        """Wait until VWC reading stabilizes."""
        # Collect readings over time
        # Calculate moving average
        # Check stability tolerance
```

#### Advanced Sensor Processing

```python
class OGBAdvancedSensor:
    """TDR-style sensor processing with polynomial calibration."""

    def calculate_vwc(self, raw_reading: float, medium_type: str) -> float:
        """Calculate VWC using polynomial calibration."""
        # Apply medium-specific polynomial
        # coeffs = self.get_medium_calibration(medium_type)
        # vwc = coeffs[0]*R^3 + coeffs[1]*R^2 + coeffs[2]*R + coeffs[3]

    def calculate_pore_ec(self, bulk_ec: float, vwc: float, temp: float, medium_type: str) -> float:
        """Calculate pore water EC using hybrid model."""
        # Hilhorst model + mass-balance correction
        # Temperature normalization
        # Medium-specific adjustments

    def validate_readings(self, vwc: float, bulk_ec: float, pore_ec: float, temp: float, medium_type: str):
        """Validate sensor readings for reasonableness."""
        # Range checking
        # Rate of change validation
        # Cross-correlation between sensors
        # Anomaly detection
```

### Medium-Specific Calibrations

```python
# Pre-defined calibrations for each medium type
VWC_CALIBRATIONS = {
    "rockwool": {
        "polynomial_coeffs": (6.771e-10, -5.105e-6, 1.302e-2, -10.848),
        "offset": 0.0,
        "scale": 1.0,
        "valid_range": (0.20, 0.80)
    },
    "coco": {
        "polynomial_coeffs": (6.771e-10, -5.105e-6, 1.302e-2, -10.848),
        "offset": 5.0,  # +5% for higher bound water
        "scale": 1.0,
        "valid_range": (0.25, 0.85)
    },
    "soil": {
        "polynomial_coeffs": (4.824e-10, -3.478e-6, 8.502e-3, -7.082),
        "offset": -8.0, # -8% for lower available water
        "scale": 1.0,
        "valid_range": (0.15, 0.75)
    }
}
```

## Irrigation Logic

### Irrigation Triggers

#### 1. VWC Threshold Crossing
```python
def should_irrigate_vwc(self) -> bool:
    """Check if irrigation needed based on VWC levels."""
    current_vwc = self.get_average_vwc()
    vwc_min = self.get_phase_vwc_min()

    return current_vwc < vwc_min
```

#### 2. Time-Based Safety Irrigation
```python
def should_irrigate_safety(self) -> bool:
    """Safety irrigation to prevent complete drying."""
    time_since_last_irrigation = datetime.now() - self.last_irrigation_time
    max_dry_time = timedelta(hours=self.get_max_dry_hours())

    return time_since_last_irrigation > max_dry_time
```

#### 3. Environmental Adaptation
```python
def calculate_environmental_adjustment(self) -> float:
    """Adjust irrigation based on environmental conditions."""
    temperature = self.get_current_temperature()
    humidity = self.get_current_humidity()

    # Higher temperature = more evaporation = more irrigation needed
    temp_factor = (temperature - 20) * 0.02  # 2% more water per °C above 20

    # Lower humidity = more evaporation = more irrigation needed
    humidity_factor = (60 - humidity) * 0.005  # 0.5% more water per % below 60

    return temp_factor + humidity_factor
```

### Irrigation Execution

#### Smart Irrigation Algorithm
```python
async def irrigate(self, duration: int = 30, is_emergency: bool = False) -> bool:
    """Execute intelligent irrigation cycle."""

    # Get available drippers
    drippers = self.get_drippers()
    if not drippers:
        _LOGGER.error(f"{self.room} - No drippers available")
        return False

    # Validate duration
    duration = max(10, min(300, duration))  # 10s to 5min safety limits

    try:
        # Turn on all drippers
        for dripper in drippers:
            entity_id = dripper.get("entity_id")
            if entity_id:
                await self.event_manager.emit("PumpAction", {
                    "Name": self.room,
                    "Action": "on",
                    "Device": entity_id,
                    "Cycle": False
                })

        # Log irrigation start
        await self.event_manager.emit("LogForClient", {
            "Name": self.room,
            "Type": "CSLOG",
            "Message": f"Irrigation started ({duration}s)"
        }, haEvent=True)

        # Wait for irrigation duration
        await asyncio.sleep(duration)

        # Turn off all drippers
        for dripper in drippers:
            entity_id = dripper.get("entity_id")
            if entity_id:
                await self.event_manager.emit("PumpAction", {
                    "Name": self.room,
                    "Action": "off",
                    "Device": entity_id,
                    "Cycle": False
                })

        # Emit AI learning event
        await self.event_manager.emit("CSIrrigation", {
            "room": self.room,
            "duration": duration,
            "is_emergency": is_emergency
        })

        return True

    except Exception as e:
        _LOGGER.error(f"{self.room} - Irrigation error: {e}")
        # Emergency stop all drippers
        await self._emergency_stop_drippers()
        return False
```

#### Dripper Management
```python
def get_drippers(self) -> List[Dict[str, Any]]:
    """Get list of available dripper devices."""

    devices = self.data_store.get("devices") or []
    drippers = []

    for device in devices:
        # Check device type
        device_type = None
        if isinstance(device, dict):
            device_type = device.get("deviceType")
        elif hasattr(device, "device_type"):
            device_type = getattr(device, "device_type", None)

        # Accept Pump and Valve devices
        if device_type in ["Pump", "Valve"]:
            drippers.append(device)

    return drippers
```

## Medium-Specific Logic

### Medium Types and Properties

```python
MEDIUM_PROPERTIES = {
    "rockwool": {
        "drainage_rate": 0.8,      # Fast drainage
        "water_retention": 0.6,    # Moderate retention
        "optimal_vwc_range": [0.6, 0.8],
        "irrigation_frequency": "moderate",
        "calibration_offset": 0.05
    },
    "coco": {
        "drainage_rate": 0.6,      # Moderate drainage
        "water_retention": 0.8,    # Good retention
        "optimal_vwc_range": [0.65, 0.85],
        "irrigation_frequency": "moderate",
        "calibration_offset": 0.03
    },
    "soil": {
        "drainage_rate": 0.4,      # Slow drainage
        "water_retention": 0.9,    # High retention
        "optimal_vwc_range": [0.5, 0.75],
        "irrigation_frequency": "low",
        "calibration_offset": 0.1
    },
    "hydroponic": {
        "drainage_rate": 1.0,      # Instant drainage
        "water_retention": 0.3,    # Low retention
        "optimal_vwc_range": [0.7, 0.9],
        "irrigation_frequency": "high",
        "calibration_offset": 0.0
    }
}
```

### Medium-Based Adjustments

```python
def apply_medium_adjustments(self, irrigation_params):
    """Adjust irrigation parameters based on growing medium."""

    medium_type = self.get_current_medium_type()
    properties = MEDIUM_PROPERTIES.get(medium_type, MEDIUM_PROPERTIES["rockwool"])

    # Adjust VWC targets
    irrigation_params.vwc_min *= (1 + properties["calibration_offset"])
    irrigation_params.vwc_max *= (1 + properties["calibration_offset"])

    # Adjust irrigation frequency
    if properties["irrigation_frequency"] == "high":
        irrigation_params.check_interval *= 0.7  # Check more often
    elif properties["irrigation_frequency"] == "low":
        irrigation_params.check_interval *= 1.3  # Check less often

    return irrigation_params
```

## Safety and Monitoring

### Over-Irrigation Prevention

```python
def prevent_over_irrigation(self) -> bool:
    """Prevent excessive irrigation that could harm plants."""

    # Check recent irrigation history
    recent_irrigation = self.get_recent_irrigation_volume()

    # Maximum irrigation per hour
    max_hourly = self.get_max_irrigation_per_hour()
    if recent_irrigation > max_hourly:
        _LOGGER.warning(f"Over-irrigation detected: {recent_irrigation}L/hr")
        return False

    # Check for runoff (if sensors available)
    if self.has_runoff_sensor():
        runoff_detected = self.check_runoff_level()
        if runoff_detected:
            _LOGGER.warning("Runoff detected - stopping irrigation")
            return False

    return True
```

### Irrigation Effectiveness Validation

```python
async def _validate_irrigation_effectiveness(self):
    """Validate that irrigation achieved desired VWC increase."""

    # Wait for water to soak in
    await asyncio.sleep(300)  # 5 minutes

    # Check VWC improvement
    pre_vwc = self.pre_irrigation_vwc
    post_vwc = self.get_average_vwc()

    improvement = post_vwc - pre_vwc
    expected_improvement = self.expected_vwc_improvement

    if improvement < (expected_improvement * 0.5):
        _LOGGER.warning(f"Poor irrigation effectiveness: "
                       f"Expected {expected_improvement}%, got {improvement}%")

        # Trigger calibration check
        await self.calibration_manager.schedule_calibration_check()
```

## Configuration and Setup

### Medium-Specific Adjustments

The system includes medium-specific adjustments for optimal performance:

```python
MEDIUM_ADJUSTMENTS = {
    "rockwool": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
    "coco": {"vwc_offset": 3, "ec_offset": -0.1, "drainage_factor": 0.9},
    "soil": {"vwc_offset": -5, "ec_offset": 0.2, "drainage_factor": 0.7},
    "perlite": {"vwc_offset": -8, "ec_offset": 0.1, "drainage_factor": 1.2},
    "aero": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
    "water": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0}
}
```

### Phase-Specific Adjustments

Growth phase adjustments optimize watering for plant development:

```python
# Vegetative Phase: Promote growth
veg_adjustments = {
    "vwc_modifier": 2.0,      # +2% moisture
    "dryback_modifier": -2.0, # -2% dryback (less stress)
    "ec_modifier": -0.1       # Slightly lower EC
}

# Generative Phase: Promote flowering
gen_adjustments = {
    "vwc_modifier": -2.0,     # -2% moisture
    "dryback_modifier": 2.0,  # +2% dryback (more stress)
    "ec_modifier": 0.2        # Higher EC
}
```

### Automatic Mode Setup

```python
async def setup_automatic_mode(self):
    """Setup automatic crop steering mode."""

    # Sync medium type
    await self._sync_medium_type()

    # Get plant phase and week
    plant_phase = self.data_store.getDeep("isPlantDay.plantPhase")
    generative_week = self.data_store.getDeep("isPlantDay.generativeWeek")

    # Apply growth phase adjustments
    adjustments = self.get_phase_growth_adjustments(plant_phase, generative_week)

    # Start automatic cycle
    await self._automatic_cycle()
```

### Sensor Configuration
```python
async def _configure_vwc_sensors(self):
    """Configure VWC sensors for crop steering."""

    # Discover available sensors
    available_sensors = await self._discover_vwc_sensors()

    for sensor in available_sensors:
        # Register sensor
        await self.medium_manager.register_sensor_to_medium(
            sensor["entity_id"], sensor["medium_id"]
        )

        # Configure sensor parameters
        await self._configure_sensor_parameters(sensor)

        # Calibrate if needed
        if sensor["needs_calibration"]:
            await self.calibration_manager.calibrate_sensor(sensor["entity_id"])
```

## Integration with Other Systems

### VPD System Integration
```python
async def coordinate_with_vpd_system(self):
    """Coordinate irrigation with VPD-based environmental control."""

    # Get current VPD status
    vpd_status = await self.vpd_manager.get_current_status()

    if vpd_status["too_dry"]:
        # Plants need more water - increase irrigation frequency
        self.adjust_irrigation_frequency(+0.2)  # 20% more frequent

    elif vpd_status["too_humid"]:
        # Environment is humid - reduce irrigation to prevent issues
        self.adjust_irrigation_frequency(-0.1)  # 10% less frequent
```

### Premium Analytics Integration
```python
async def submit_irrigation_analytics(self):
    """Submit irrigation data to premium analytics."""

    if not self.premium_manager or not self.premium_manager.is_logged_in:
        return

    analytics_data = {
        "type": "irrigation",
        "timestamp": datetime.now().isoformat(),
        "room": self.room,
        "irrigation_events": self.irrigation_history[-24:],  # Last 24 hours
        "vwc_trends": self.vwc_history[-168:],  # Last week
        "water_usage": self.calculate_water_usage(),
        "efficiency_score": self.calculate_irrigation_efficiency()
    }

    await self.premium_manager.submit_analytics(analytics_data)
```

## Troubleshooting

### Common Issues

#### VWC Sensors Reading Incorrectly
- **Symptom**: Irrigations at wrong times or not at all
- **Cause**: Poor calibration or sensor placement
- **Solution**: Recalibrate sensors, check sensor depth

#### Over/Under Watering
- **Symptom**: Plants showing stress despite irrigation
- **Cause**: Wrong VWC targets for plant phase/medium
- **Solution**: Adjust phase-specific VWC ranges

#### System Not Responding
- **Symptom**: No irrigation despite low VWC
- **Cause**: Emergency stop or calibration issues
- **Solution**: Check system status, recalibrate if needed

### Diagnostic Tools

#### System Health Check
```python
async def run_system_diagnostics(self):
    """Run comprehensive crop steering diagnostics."""

    diagnostics = {
        "sensor_status": await self._check_sensor_health(),
        "calibration_status": self._check_calibration_validity(),
        "phase_status": self._get_current_phase_status(),
        "irrigation_capability": self._check_irrigation_system(),
        "medium_sync": await self._verify_medium_sync(),
        "performance_metrics": self._calculate_system_performance(),
        "recommendations": self._generate_diagnostic_recommendations()
    }

    return diagnostics

async def _check_sensor_health(self):
    """Check VWC and EC sensor health."""
    sensor_data = await self._get_sensor_averages()

    if not sensor_data:
        return {"status": "error", "message": "No sensor data available"}

    health = {
        "vwc_sensors": len(sensor_data.get("vwc_values", [])),
        "ec_sensors": len(sensor_data.get("ec_values", [])),
        "validation_status": sensor_data.get("validation_valid", False),
        "last_update": sensor_data.get("timestamp")
    }

    return health
```

#### Calibration Validation
```python
def _check_calibration_validity(self):
    """Check if calibrations are current and valid."""

    calibrations = {
        "p1_vwc_max": self.data_store.getDeep("CropSteering.Calibration.p1.VWCMax"),
        "p1_timestamp": self.data_store.getDeep("CropSteering.Calibration.p1.timestamp"),
        "medium_type": self.medium_type
    }

    # Check if calibration exists
    if not calibrations["p1_vwc_max"]:
        return {
            "status": "needs_calibration",
            "message": "No VWC max calibration found",
            "recommendation": "Run automatic calibration"
        }

    # Check calibration age
    if calibrations["p1_timestamp"]:
        import datetime
        cal_date = datetime.fromisoformat(calibrations["p1_timestamp"])
        age_days = (datetime.now() - cal_date).days

        if age_days > 30:
            return {
                "status": "outdated",
                "age_days": age_days,
                "message": f"Calibration is {age_days} days old",
                "recommendation": "Re-run calibration"
            }

    return {
        "status": "valid",
        "vwc_max": calibrations["p1_vwc_max"],
        "medium": calibrations["medium_type"]
    }
```

#### Performance Analytics
```python
def _calculate_system_performance(self):
    """Calculate irrigation system performance metrics."""

    # Get irrigation history
    irrigation_events = self.data_store.getDeep("CropSteering.irrigation_history") or []

    if not irrigation_events:
        return {"status": "no_data", "message": "No irrigation history available"}

    # Calculate metrics
    total_irrigation = sum(event.get("duration", 0) for event in irrigation_events)
    total_events = len(irrigation_events)

    # Calculate efficiency (VWCs achieved per liter)
    vwc_improvements = []
    for event in irrigation_events:
        pre_vwc = event.get("pre_vwc")
        post_vwc = event.get("post_vwc")
        duration = event.get("duration", 0)

        if pre_vwc is not None and post_vwc is not None and duration > 0:
            improvement = post_vwc - pre_vwc
            efficiency = improvement / duration if duration > 0 else 0
            vwc_improvements.append(efficiency)

    avg_efficiency = sum(vwc_improvements) / len(vwc_improvements) if vwc_improvements else 0

    return {
        "total_irrigation_seconds": total_irrigation,
        "total_events": total_events,
        "average_efficiency": avg_efficiency,
        "efficiency_unit": "vwc_percent_per_second"
    }
```

---

## Implementation Status

### Core Components ✅ **IMPLEMENTED**

- **OGBCSManager**: Main controller (1839 lines) - ✅ Production Ready
- **OGBCSConfigurationManager**: Settings & presets (317 lines) - ✅ Production Ready
- **OGBCSIrrigationManager**: Water delivery logic - ✅ Production Ready
- **OGBCSPhaseManager**: Phase transitions & timing - ✅ Production Ready
- **OGBCSCalibrationManager**: VWC calibration procedures - ✅ Production Ready
- **OGBAdvancedSensor**: TDR polynomial calibrations - ✅ Production Ready

### Key Features ✅ **FULLY IMPLEMENTED**

- **4-Phase Automatic Mode**: P0-P3 with intelligent transitions
- **Manual Mode**: User-configurable timing per phase
- **Medium-Specific Adjustments**: Rockwool, coco, soil, perlite, aero, water
- **Growth Phase Optimization**: Vegetative vs generative watering strategies
- **VWC Calibration**: Automatic max/min calibration cycles
- **Advanced Sensor Processing**: TDR-style polynomial calculations
- **EC Management**: Pore water EC with temperature normalization
- **Irrigation Validation**: Effectiveness monitoring and anomaly detection
- **Emergency Systems**: Safety irrigation and dryback protection
- **AI Learning Integration**: Sensor data collection for analytics

### Integration Points ✅ **CONNECTED**

- **VPD System**: Coordinates with environmental control
- **Premium Analytics**: Sends irrigation data for AI learning
- **Medium Manager**: Syncs growing medium type
- **HA Entities**: Controls pumps, valves, sensors
- **Event System**: Emits irrigation events for monitoring

---

**Last Updated**: January 15, 2025
**Version**: 3.0 (Full Implementation)
**Status**: ✅ **PRODUCTION READY** - All managers implemented and integrated