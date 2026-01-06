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
if abs(deviation) < 0.03:  # 3% EC tolerance
    return {'nutrients_needed': False}

if abs(deviation) < 0.1:   # 0.1 pH tolerance
    return {'ph_down_needed': False}
```

#### Proportional Dose Calculation
```python
# Scale dose amount with deviation severity
base_dose_per_5_percent = 2.5  # ml per nutrient type
dose_multiplier = deviation / 0.05  # Normalize to 5% deviation
nutrient_dose_ml = min(base_dose_per_5_percent * dose_multiplier, 10.0)
```

#### Adjustment Examples
| Deviation | Old System | New Proportional |
|-----------|------------|------------------|
| EC 2% | No action | No action (dead zone) |
| EC 5% | Full 5ml dose | 2.5ml dose |
| EC 10% | Full 5ml dose | 5.0ml dose |
| pH 0.1 | No action | No action (dead zone) |
| pH 0.3 | Full pH dose | 1.5ml pH adjuster |

### Feeding Frequency Optimization

- **Checks**: Every 10 minutes (reduced from 5 minutes)
- **Actual Dosing**: Every 30+ minutes (increased safety margin)
- **Daily Limit**: 12 doses maximum (allows frequent small adjustments)

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
    if abs(deviation) < 0.1:  # 0.1 pH tolerance
        return {'ph_down_needed': False, 'ph_up_needed': False}

    # Proportional dosing: more deviation = more pH adjustment
    base_dose_per_0_2_ph = 1.0  # ml for 0.2 pH deviation
    dose_multiplier = abs(deviation) / 0.2
    ph_dose_ml = min(base_dose_per_0_2_ph * dose_multiplier, 3.0)  # Cap at 3ml

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
    if deviation < 0.03:  # 3% tolerance
        return {'nutrients_needed': False, 'nutrient_dose_ml': 0.0}

    # Proportional dosing: more deviation = more nutrients
    base_dose_per_5_percent = 2.5  # ml per nutrient type for 5% deviation
    dose_multiplier = deviation / 0.05  # Normalize to 5% deviation
    nutrient_dose_ml = min(base_dose_per_5_percent * dose_multiplier, 10.0)  # Cap at 10ml

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
    calibration_factor: float = 1.0
    last_calibration: Optional[datetime] = None
    is_calibrated: bool = False
    accuracy_score: float = 0.0  # Percentage accuracy
    test_volume: float = 10.0  # ml calibration test volume

    def calculate_adjustment(self) -> float:
        """Calculate flow rate adjustment factor."""
        if not self.is_calibrated or self.calibration_factor <= 0:
            return 1.0
        return self.calibration_factor

    def is_calibration_valid(self) -> bool:
        """Check if calibration is still valid (30 days)."""
        if not self.last_calibration:
            return False
        max_age = timedelta(days=30)
        return (datetime.now() - self.last_calibration) < max_age
```

#### Automatic Calibration Management
```python
class OGBFeedCalibrationManager:
    """Manages pump calibration with auto-recalibration."""

    async def start_pump_calibration(self, pump_type: str) -> bool:
        """Start calibration for specific pump with accuracy validation."""

    async def start_daily_calibration(self):
        """Enable automatic daily recalibration at 2 AM."""

    async def validate_all_calibrations(self) -> bool:
        """Validate all pumps have current, accurate calibrations."""
```

#### Pump Operation
```python
async def dose_nutrient(self, pump_id: str, volume_ml: float) -> bool:
    """Dose precise volume of nutrient using calibrated pump."""

    # Get pump calibration data
    calibration = self.get_pump_calibration(pump_id)

    # Apply calibration adjustment
    adjusted_volume = volume_ml * calibration.calibration_factor

    # Calculate dosing time
    ml_per_second = self.get_pump_flow_rate(pump_id)
    dosing_time_seconds = adjusted_volume / ml_per_second

    # Execute dosing
    success = await self.activate_pump(pump_id, dosing_time_seconds)

    if success:
        # Record actual dosing for future calibration
        self.record_dosing_event(pump_id, volume_ml, adjusted_volume)

    return success
```

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
    """Calibrate a dosing pump for accuracy."""

    # Step 1: Prepare calibration container
    calibration_volume = 10.0  # 10ml test dose

    # Step 2: Execute test dose
    await self.dose_nutrient(pump_id, calibration_volume)

    # Step 3: Measure actual volume dispensed
    actual_volume = await self.measure_dispensed_volume()

    # Step 4: Calculate calibration factor
    calibration_factor = calibration_volume / actual_volume

    # Step 5: Update pump configuration
    self.update_pump_calibration(pump_id, calibration_factor)

    _LOGGER.info(f"Pump {pump_id} calibrated: factor = {calibration_factor:.3f}")
```

---

## New Proportional Features Summary

### ✅ Implemented Features
- **Proportional Dosing**: Dose amounts scale with deviation severity
- **Dead Zone Logic**: Prevents unnecessary micro-adjustments (< 3% EC, < 0.1 pH)
- **Frequent Small Doses**: 30-minute minimum intervals vs 2-hour batches
- **Advanced Calibration**: Auto-recalibration with accuracy scoring
- **Parameter Validation**: Comprehensive validation with plant stage adaptation

### 🔧 Key Improvements
- **Prevents Over-Fertilization**: Small doses instead of large batches
- **Responsive Control**: Adjusts based on real-time deviations
- **Equipment Protection**: Reduces pump cycling and wear
- **Calibration Accuracy**: Automatic validation and adjustment

### 📊 Performance Metrics
- **Adjustment Frequency**: Every 30+ minutes (vs 2+ hours)
- **Dose Precision**: 0.1ml increments with calibration factors
- **Safety Limits**: 10ml nutrient cap, 3ml pH adjuster cap
- **Daily Capacity**: 12 adjustments (vs 8 batch preparations)

---

**Last Updated**: December 24, 2025
**Version**: 3.0 (Proportional Nutrient Management)
**Status**: Production Ready with Advanced Features