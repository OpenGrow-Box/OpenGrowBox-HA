# Hydroponic Feeding System - Automated Nutrient Management

## Overview

The Hydroponic Feeding System provides **intelligent, proportional nutrient delivery** for hydroponic cultivation. It features **automated small-dose adjustments** rather than large batch preparations, ensuring precise nutrient management and preventing over-fertilization.

**Key Innovation**: **Proportional Dosing** - Instead of large, infrequent nutrient batches, the system delivers small, calculated doses based on real-time deviations from target values.

## System Architecture

### Core Components

#### 1. OGBTankFeedManager (Main Controller)
```python
class OGBTankFeedManager:
    """Main hydroponic feeding system controller with proportional dosing."""
```
**Responsibilities**:
- Event handling for proportional dosing requests
- Sensor data processing and normalization
- Coordination between logic, calibration, and parameter managers

#### 2. OGBFeedLogicManager (Proportional Dosing Logic)
```python
class OGBFeedLogicManager:
    """Intelligent proportional feeding decisions based on deviation analysis."""
```
**Key Features**:
- **Dead Zone Logic**: Prevents micro-adjustments for small deviations
- **Proportional Calculations**: Dose amounts scale with deviation severity
- **Rate Limiting**: Minimum 30-minute intervals between doses
- **Event Emission**: Triggers `DoseNutrients`, `DosePHDown`, `DosePHUp` events

#### 3. OGBFeedCalibrationManager (Pump Calibration)
```python
class OGBFeedCalibrationManager:
    """Advanced pump calibration with accuracy tracking and auto-recalibration."""
```
**Features**:
- Individual pump calibration factors
- Automatic daily recalibration (2 AM)
- Calibration validity checking (30-day expiration)
- Accuracy scoring and adjustment factor calculation

#### 4. OGBFeedParameterManager (Parameter Management)
```python
class OGBFeedParameterManager:
    """Validates and manages feeding parameters with plant stage adaptation."""
```
**Capabilities**:
- Parameter validation with min/max ranges
- Plant stage and category-specific adjustments
- Real-time parameter updates and notifications
- Configuration persistence and history

## Proportional Dosing System

### Intelligent Adjustment Algorithm

The system uses **proportional dosing** to deliver precise nutrient amounts based on real-time sensor deviations:

#### Dead Zone Logic
```python
# Prevent unnecessary micro-adjustments
if abs(deviation) < 0.08:  # 8% EC tolerance (conservative)
    return {'nutrients_needed': False}

if abs(deviation) < 0.2:   # 0.2 pH tolerance (conservative)
    return {'ph_down_needed': False}
```

#### Proportional Dose Calculation
```python
# Scale dose amount with deviation severity (conservative dosing)
base_dose_per_5_percent = 1.5  # ml per nutrient type (reduced for safety)
dose_multiplier = deviation / 0.05  # Normalize to 5% deviation
nutrient_dose_ml = min(base_dose_per_5_percent * dose_multiplier, 5.0)  # Cap at 5ml
```

#### Adjustment Examples (Conservative Settings)
| Deviation | System Response | Dose Amount |
|-----------|----------------|-------------|
| EC < 8% | No action (dead zone) | 0ml |
| EC 8% | Small correction | 1.2ml per nutrient |
| EC 15% | Medium correction | 2.25ml per nutrient |
| EC 25%+ | Maximum correction | 5.0ml per nutrient (cap) |
| pH < 0.2 | No action (dead zone) | 0ml |
| pH 0.3 | Small correction | 0.75ml pH adjuster |
| pH 0.5 | Medium correction | 1.25ml pH adjuster |
| pH 0.6+ | Maximum correction | 1.5ml pH adjuster (cap) |

### Feeding Frequency (Conservative Settings)

- **Sensor Check Interval**: Every 15 minutes
- **Minimum Dosing Interval**: 60 minutes between doses
- **Daily Limit**: Maximum 6 doses per day
- **Emergency Threshold**: 25% deviation triggers immediate correction

## Hydroponic Modes

### 1. Disabled Mode
- **Purpose**: Safety mode, all feeding automation disabled
- **Control**: Manual nutrient management only
- **Use Case**: Maintenance, troubleshooting, manual operation

### 2. Automatic Mode
- **Logic**: Plant stage-based feeding with environmental adaptation
- **Schedule**: Automatic feeding cycles based on plant needs
- **Adjustment**: Real-time pH/EC corrections
- **Use Case**: Full automated hydroponic operation

### 3. Own-Plan Mode
- **Logic**: Custom user-defined feeding schedules
- **Flexibility**: User-programmed nutrient ratios and timings
- **Control**: Manual override of automatic calculations
- **Use Case**: Specialized feeding regimens

### 4. Config Mode
- **Logic**: Configuration and testing mode
- **Function**: System setup, calibration, and diagnostics
- **Safety**: Limited operation for setup purposes
- **Use Case**: Initial setup and system testing

## Nutrient Management

### Plant-Specific Nutrient Profiles

#### Cannabis Nutrient Profile
```python
CANNABIS_PROFILE = {
    "germ": {
        "ph_target": 6.0,
        "ec_target": 0.4,
        "nutrients_ml_per_liter": {
            "A": 0.4,  # Nitrogen-rich vegetative
            "B": 0.3,  # Phosphorus/potassium bloom
            "C": 0.2   # Micro nutrients
        }
    },
    "veg": {
        "ph_target": 5.8,
        "ec_target": 1.2,
        "nutrients_ml_per_liter": {
            "A": 2.0,  # High nitrogen for growth
            "B": 1.0,  # Moderate P/K
            "C": 0.8   # Micro nutrients
        }
    },
    "flower": {
        "ph_target": 6.0,
        "ec_target": 2.2,
        "nutrients_ml_per_liter": {
            "A": 1.2,  # Reduced nitrogen
            "B": 3.0,  # High P/K for flowering
            "C": 1.5   # Enhanced micros
        }
    }
}
```

#### Tomato Nutrient Profile
```python
TOMATO_PROFILE = {
    "germ": {
        "ph_target": 6.0,
        "ec_target": 0.5,
        "nutrients_ml_per_liter": {"A": 0.5, "B": 0.3, "C": 0.2}
    },
    "veg": {
        "ph_target": 6.0,
        "ec_target": 2.0,
        "nutrients_ml_per_liter": {"A": 2.5, "B": 1.5, "C": 1.0}
    },
    "flower": {
        "ph_target": 6.2,
        "ec_target": 2.8,
        "nutrients_ml_per_liter": {"A": 1.5, "B": 3.0, "C": 1.5}
    }
}
```

### Nutrient Solution Preparation

#### Automatic Mixing Algorithm
```python
async def prepare_nutrient_solution(self, target_profile: Dict[str, Any]) -> bool:
    """Automatically prepare nutrient solution to target specifications."""

    # 1. Calculate required volumes
    tank_volume_liters = self.get_tank_volume()
    target_ec = target_profile["ec_target"]
    target_ph = target_profile["ph_target"]

    nutrient_volumes = {}
    for nutrient_type, ml_per_liter in target_profile["nutrients_ml_per_liter"].items():
        nutrient_volumes[nutrient_type] = ml_per_liter * tank_volume_liters

    # 2. Start with base water
    await self.fill_tank_with_water(tank_volume_liters)

    # 3. Add nutrients in sequence
    for nutrient_type, volume_ml in nutrient_volumes.items():
        pump_id = self.get_pump_for_nutrient(nutrient_type)
        await self.dose_nutrient(pump_id, volume_ml)

        # Brief mixing time
        await asyncio.sleep(30)

    # 4. Initial mixing and measurement
    await self.activate_mixer(duration_seconds=120)
    await asyncio.sleep(60)  # Settling time

    # 5. Measure and adjust pH
    current_ph = await self.measure_ph()
    ph_adjustment = self.calculate_ph_adjustment(current_ph, target_ph)
    await self.adjust_ph(ph_adjustment)

    # 6. Measure and adjust EC
    current_ec = await self.measure_ec()
    ec_adjustment = self.calculate_ec_adjustment(current_ec, target_ec)
    await self.adjust_ec(ec_adjustment)

    # 7. Final mixing
    await self.activate_mixer(duration_seconds=180)

    # 8. Final verification
    final_ph = await self.measure_ph()
    final_ec = await self.measure_ec()

    success = self.verify_solution_quality(final_ph, final_ec, target_profile)

    if success:
        _LOGGER.info(f"Nutrient solution prepared successfully: "
                    f"pH {final_ph:.1f}, EC {final_ec:.1f}")
    else:
        _LOGGER.error("Nutrient solution preparation failed quality checks")

    return success
```

## pH and EC Control

### pH Management

#### Proportional pH Adjustment
```python
def _calculate_ph_adjustment(self, current_ph: Optional[float], target_ph: float) -> Dict[str, Any]:
    """Calculate proportional pH adjustment needed."""

    if current_ph is None:
        return {'ph_down_needed': False, 'ph_up_needed': False}

    deviation = current_ph - target_ph

    # Dead zone - don't adjust for small deviations
    if abs(deviation) < 0.2:  # 0.2 pH tolerance (increased from 0.1)
        return {'ph_down_needed': False, 'ph_up_needed': False}

    # Proportional dosing: more deviation = more pH adjustment
    base_dose_per_0_2_ph = 0.5  # ml for 0.2 pH deviation (reduced from 1.0)
    dose_multiplier = abs(deviation) / 0.2
    ph_dose_ml = min(base_dose_per_0_2_ph * dose_multiplier, 1.5)  # Cap at 1.5ml

    if deviation > 0:  # pH too high
        return {
            'ph_down_needed': True,
            'ph_down_dose_ml': ph_dose_ml,
            'ph_up_needed': False
        }
    else:  # pH too low
        return {
            'ph_down_needed': False,
            'ph_up_needed': True,
            'ph_up_dose_ml': ph_dose_ml
        }
```

#### pH Adjustment Calculations
```python
def calculate_ph_down_volume(self, current_ph: float, target_ph: float) -> float:
    """Calculate volume of pH down solution needed."""

    ph_difference = current_ph - target_ph

    # Empirical formula: 0.5ml pH down per 0.1 pH unit per 10L
    tank_volume_liters = self.get_tank_volume()
    base_volume_per_ph_unit = 0.5 * (tank_volume_liters / 10)

    adjustment_ml = ph_difference * 10 * base_volume_per_ph_unit

    # Safety limits
    return max(0.1, min(adjustment_ml, 5.0))  # 0.1-5.0ml limit
```

### EC Management

#### Proportional EC Adjustment
```python
def _calculate_ec_adjustment(self, current_ec: Optional[float], target_ec: float) -> Dict[str, Any]:
    """Calculate proportional nutrient adjustment needed for EC correction."""

    if current_ec is None:
        return {'nutrients_needed': False, 'nutrient_dose_ml': 0.0}

    deviation = abs(current_ec - target_ec) / target_ec

    # Dead zone - don't adjust for small deviations
    if deviation < 0.08:  # 8% tolerance (increased from 3%)
        return {'nutrients_needed': False, 'nutrient_dose_ml': 0.0}

    # Proportional dosing: more deviation = more nutrients
    base_dose_per_5_percent = 1.5  # ml per nutrient type for 5% deviation (reduced from 2.5)
    dose_multiplier = deviation / 0.05  # Normalize to 5% deviation
    nutrient_dose_ml = min(base_dose_per_5_percent * dose_multiplier, 5.0)  # Cap at 5ml (reduced from 10ml)

    return {
        'nutrients_needed': True,
        'nutrient_dose_ml': nutrient_dose_ml
    }
```

#### Proportional Feeding Execution
```python
async def _perform_proportional_feeding(self, ec_adjustment: Dict, ph_adjustment: Dict) -> bool:
    """Execute proportional feeding based on calculated adjustments."""

    # Feed nutrients if needed
    if ec_adjustment.get('nutrients_needed', False):
        nutrient_dose = ec_adjustment['nutrient_dose_ml']
        await self.event_manager.emit("DoseNutrients", {'dose_ml': nutrient_dose})

    # Adjust pH if needed
    if ph_adjustment.get('ph_down_needed', False):
        ph_down_dose = ph_adjustment['ph_down_dose_ml']
        await self.event_manager.emit("DosePHDown", {'dose_ml': ph_down_dose})

    if ph_adjustment.get('ph_up_needed', False):
        ph_up_dose = ph_adjustment['ph_up_dose_ml']
        await self.event_manager.emit("DosePHUp", {'dose_ml': ph_up_dose})

    return True
```

## Feeding Schedules

### Automatic Feeding Cycles

#### Time-Based Feeding
```python
class FeedingSchedule:
    """Manages automatic feeding schedules."""

    def __init__(self):
        self.feeding_times = []
        self.last_feeding = None

    def set_schedule(self, times: List[str]):
        """Set feeding times (24-hour format)."""
        self.feeding_times = [datetime.strptime(t, "%H:%M").time() for t in times]

    def should_feed_now(self) -> bool:
        """Check if it's time for feeding."""
        now = datetime.now().time()

        for feed_time in self.feeding_times:
            # Check if within 5-minute window of feeding time
            time_diff = abs((datetime.combine(datetime.today(), now) -
                           datetime.combine(datetime.today(), feed_time)).seconds)

            if time_diff <= 300:  # 5 minutes
                # Check if we haven't fed recently
                if not self.last_feeding or \
                   (datetime.now() - self.last_feeding).seconds > 1800:  # 30 min
                    return True

        return False
```

#### Volume-Based Feeding
```python
def calculate_feeding_volume(self, plant_stage: str, plant_count: int) -> float:
    """Calculate nutrient solution volume for feeding."""

    # Base volume per plant per day
    base_volumes = {
        "germ": 0.1,    # 100ml per plant per day
        "veg": 0.5,     # 500ml per plant per day
        "flower": 0.8   # 800ml per plant per day
    }

    base_volume_per_plant = base_volumes.get(plant_stage, 0.5)

    # Environmental adjustments
    temperature_factor = self.get_temperature_factor()
    humidity_factor = self.get_humidity_factor()

    adjusted_volume = base_volume_per_plant * temperature_factor * humidity_factor

    # Total for all plants
    total_volume = adjusted_volume * plant_count

    # Practical limits
    return max(0.5, min(total_volume, 10.0))  # 0.5-10L limit
```

## Pump Control System

### Precision Dosing Pumps

#### Advanced Pump Calibration System
```python
@dataclass
class PumpCalibration:
    """Advanced pump calibration with accuracy tracking."""
    pump_type: str
    calibration_factor: float = 0.5  # ml per second (default flow rate)
    last_calibration: Optional[datetime] = None
    is_calibrated: bool = False
    accuracy_score: float = 0.0  # Percentage accuracy (0-100)
    test_volume: float = 10.0  # ml calibration test volume
    measured_time: float = 0.0  # seconds measured during calibration

    def calculate_adjustment(self) -> float:
        """Calculate flow rate (ml/s) for dosing time calculation."""
        if not self.is_calibrated or self.calibration_factor <= 0:
            return 0.5  # Default: 0.5 ml/s
        return self.calibration_factor

    def update_calibration(self, measured_time: float, target_volume: float = 10.0):
        """Update calibration with new measurement."""
        if measured_time > 0 and measured_time < 120:  # Safety: max 2 minutes
            old_factor = self.calibration_factor if self.is_calibrated else 0.5
            self.calibration_factor = target_volume / measured_time  # ml/s
            self.last_calibration = datetime.now()
            self.is_calibrated = True
            
            # Calculate accuracy: compare expected vs measured time
            expected_time = target_volume / old_factor
            time_ratio = measured_time / expected_time
            self.accuracy_score = min(100.0, max(0.0, (1.0 / time_ratio) * 100))
            self.measured_time = measured_time

    def is_calibration_valid(self) -> bool:
        """Check if calibration is still valid (30 days)."""
        if not self.last_calibration:
            return False
        max_age = timedelta(days=30)
        return (datetime.now() - self.last_calibration) < max_age
```

**Supported Pump Types**:
- `switch.feedpump_a` - Nutrient A (Veg)
- `switch.feedpump_b` - Nutrient B (Flower)
- `switch.feedpump_c` - Nutrient C (Micro)
- `switch.feedpump_w` - Water
- `switch.feedpump_x` - Custom X
- `switch.feedpump_y` - Custom Y
- `switch.feedpump_pp` - pH Down (pH-)
- `switch.feedpump_pm` - pH Up (pH+)

#### Automatic Calibration Management
```python
class OGBFeedCalibrationManager:
    """Manages pump calibration with auto-recalibration for all 8 pump types."""

    async def start_pump_calibration(self, pump_type: str) -> bool:
        """Start calibration for specific pump with accuracy validation.
        
        Measures actual time to dispense test volume (10ml) and calculates
        flow rate: calibration_factor = ml / seconds
        """

    async def start_daily_calibration(self):
        """Enable automatic daily recalibration at 2 AM.
        
        Checks all 8 pump types and recalibrates expired or invalid calibrations.
        """

    async def validate_all_calibrations(self) -> bool:
        """Validate all pumps have current, accurate calibrations.
        
        Returns False if any pump has:
        - No calibration (is_calibrated = False)
        - Expired calibration (>30 days)
        - Low accuracy (<80%)
        """
```

#### Pump Operation
```python
async def dose_nutrient(self, pump_id: str, volume_ml: float) -> bool:
    """Dose precise volume of nutrient using calibrated pump."""

    # Get pump calibration data
    calibration = self.get_pump_calibration(pump_id)

    # Get calibration factor (ml/s flow rate)
    # Uses default 0.5 ml/s if not calibrated
    calibration_factor = calibration.calibration_factor if calibration else 0.5
    
    # Calculate dosing time: volume / flow_rate = seconds
    # Example: 1.0ml / 0.5 ml/s = 2.0 seconds
    dosing_time_seconds = volume_ml / calibration_factor

    # Execute dosing
    success = await self.activate_pump(pump_id, dosing_time_seconds)

    if success:
        # Record actual dosing for future calibration
        self.record_dosing_event(pump_id, volume_ml, dosing_time_seconds)

    return success
```

**Key Principle**: The calibration_factor represents the pump's flow rate in ml/s. To achieve the desired volume, we divide the target volume by the flow rate to get the required run time in seconds. This ensures precise dosing regardless of pump speed variations.

## Environmental Integration

### Climate-Based Adjustments

#### Temperature Compensation
```python
def get_temperature_factor(self) -> float:
    """Calculate feeding adjustment based on temperature."""

    current_temp = self.get_current_temperature()

    if current_temp < 18:
        return 0.7  # Reduce feeding in cold conditions
    elif current_temp > 28:
        return 1.3  # Increase feeding in hot conditions
    else:
        return 1.0  # Normal feeding
```

#### Humidity Compensation
```python
def get_humidity_factor(self) -> float:
    """Calculate feeding adjustment based on humidity."""

    current_humidity = self.get_current_humidity()

    if current_humidity < 40:
        return 1.2  # Increase feeding in dry conditions
    elif current_humidity > 70:
        return 0.8  # Reduce feeding in humid conditions
    else:
        return 1.0  # Normal feeding
```

## Safety and Monitoring

### Solution Quality Monitoring

```python
def verify_solution_quality(self, ph: float, ec: float, target_profile: Dict) -> bool:
    """Verify nutrient solution meets quality standards."""

    ph_target = target_profile["ph_target"]
    ec_target = target_profile["ec_target"]

    # pH quality check
    ph_tolerance = 0.2  # ±0.2 pH units acceptable
    ph_ok = abs(ph - ph_target) <= ph_tolerance

    # EC quality check
    ec_tolerance = 0.2  # ±0.2 mS/cm acceptable
    ec_ok = abs(ec - ec_target) <= ec_tolerance

    # Overall assessment
    if not ph_ok:
        _LOGGER.warning(f"pH out of range: {ph:.1f} (target: {ph_target})")

    if not ec_ok:
        _LOGGER.warning(f"EC out of range: {ec:.1f} (target: {ec_target})")

    return ph_ok and ec_ok
```

### Emergency Safety Systems

```python
async def emergency_shutdown(self):
    """Emergency shutdown of feeding system."""

    _LOGGER.error("Emergency shutdown initiated")

    # Stop all pumps immediately
    await self.stop_all_pumps()

    # Close all valves
    await self.close_all_valves()

    # Disable automatic feeding
    self.set_feed_mode(FeedMode.DISABLED)

    # Alert user
    await self.send_emergency_alert("Feeding system emergency shutdown")

    # Log incident
    self.log_emergency_event("feeding_system_shutdown")
```

## Integration with Other Systems

### VPD System Coordination

```python
async def coordinate_with_vpd_system(self):
    """Adjust feeding based on VPD system feedback."""

    vpd_status = await self.vpd_manager.get_current_status()

    if vpd_status["stress_detected"]:
        # Plants showing stress - increase nutrient concentration
        self.adjust_nutrient_concentration(+0.1)  # 10% increase

    elif vpd_status["over_saturated"]:
        # Plants over-watered - reduce nutrient concentration
        self.adjust_nutrient_concentration(-0.1)  # 10% decrease
```

### Crop Steering Integration

```python
async def coordinate_with_crop_steering(self, irrigation_data: Dict[str, Any]):
    """Coordinate feeding with crop steering irrigation events."""

    irrigation_volume = irrigation_data.get("volume_liters", 0)

    if irrigation_volume > 0:
        # Irrigation event occurred - schedule nutrient top-up
        await self.schedule_nutrient_topup(irrigation_volume)

        # Adjust next feeding based on irrigation
        self.adjust_feeding_schedule_for_irrigation(irrigation_volume)
```

## Configuration and Setup

### System Configuration

```python
# Example hydroponic system configuration
hydroponic_config = {
    "tank": {
        "volume_liters": 50,
        "sensor_ph": "sensor.ph_tank",
        "sensor_ec": "sensor.ec_tank",
        "sensor_temperature": "sensor.temp_tank"
    },
    "pumps": {
        "nutrient_a": {
            "entity_id": "switch.pump_nutrient_a",
            "ml_per_second": 0.8,
            "calibration_factor": 1.05
        },
        "nutrient_b": {
            "entity_id": "switch.pump_nutrient_b",
            "ml_per_second": 0.8,
            "calibration_factor": 0.95
        },
        "ph_down": {
            "entity_id": "switch.pump_ph_down",
            "ml_per_second": 0.5,
            "calibration_factor": 1.0
        }
    },
    "plant_config": {
        "type": "Cannabis",
        "count": 4,
        "stage": "MidFlower"
    },
    "schedule": {
        "feeding_times": ["08:00", "14:00", "20:00"],
        "ph_check_interval": 3600,  # Every hour
        "ec_check_interval": 3600
    }
}
```

### Pump Calibration Procedure

```python
async def calibrate_pump(self, pump_id: str):
    """Calibrate a dosing pump for accuracy.
    
    The calibration_factor represents the pump's flow rate in ml/s.
    This is used to calculate dosing time: time = volume / flow_rate
    """

    # Step 1: Prepare calibration container with measuring scale
    test_volume = 10.0  # 10ml test dose (can be adjusted)

    # Step 2: Record start time and execute test dose
    start_time = datetime.now()
    await self.activate_pump(pump_id, estimated_time)  # Run pump

    # Step 3: Record end time when pump stops
    end_time = datetime.now()
    measured_time = (end_time - start_time).total_seconds()

    # Step 4: Calculate calibration factor (flow rate in ml/s)
    # Formula: calibration_factor = ml / seconds
    # Example: 10ml / 20s = 0.5 ml/s
    calibration_factor = test_volume / measured_time

    # Step 5: Calculate accuracy by comparing to previous calibration
    old_factor = self.get_previous_calibration_factor(pump_id)
    expected_time = test_volume / old_factor if old_factor > 0 else test_volume / 0.5
    accuracy = min(100.0, (expected_time / measured_time) * 100)

    # Step 6: Update pump configuration
    self.update_pump_calibration(pump_id, calibration_factor, accuracy)

    _LOGGER.info(f"Pump {pump_id} calibrated: {calibration_factor:.3f} ml/s, accuracy: {accuracy:.1f}%")
```

**Important Notes**:
- The `calibration_factor` is the **flow rate** (ml/s), NOT a multiplier
- Dosing time is calculated as: `time_seconds = desired_volume_ml / flow_rate_ml_per_s`
- A higher calibration_factor means a faster pump (shorter dosing time)
- All 8 pump types (A, B, C, W, X, Y, pH-, pH+) are calibrated independently

---

## New Proportional Features Summary

### ✅ Implemented Features
- **Proportional Dosing**: Dose amounts scale with deviation severity
- **Dead Zone Logic**: Prevents unnecessary micro-adjustments (< 8% EC, < 0.2 pH)
- **Conservative Dosing**: 60-minute minimum intervals, maximum 6 doses/day
- **Advanced Calibration**: Auto-recalibration with accuracy scoring for all 8 pump types
- **Correct Calibration Math**: Flow rate (ml/s) used for time calculation, not volume multiplier
- **Parameter Validation**: Comprehensive validation with plant stage adaptation

### 🔧 Key Improvements
- **Prevents Over-Fertilization**: Conservative dosing with larger dead zones
- **Stable Control**: Less frequent adjustments prevent oscillation
- **Equipment Protection**: Reduced pump cycling (max 6x/day vs 12x/day)
- **Calibration Accuracy**: Automatic validation and adjustment
- **System Stability**: 8% EC and 0.2 pH dead zones prevent micro-adjustments

### 📊 Performance Metrics (Conservative Settings)
- **Sensor Check Interval**: Every 15 minutes
- **Minimum Dosing Interval**: 60 minutes between doses
- **Dose Precision**: 0.1ml increments with calibration factors
- **Safety Limits**: 5ml nutrient cap, 1.5ml pH adjuster cap
- **Daily Capacity**: Maximum 6 doses per day
- **Dead Zones**: 8% EC tolerance, 0.2 pH tolerance
- **Pump Calibration**: 8 pump types (A, B, C, W, X, Y, pH-, pH+), 30-day validity, 80% accuracy threshold
- **Calibration Factor**: Flow rate in ml/s (default: 0.5 ml/s), used for time calculation

---

**Last Updated**: March 5, 2026
**Version**: 3.1 (Conservative Nutrient Management)
**Status**: Production Ready with Conservative Settings