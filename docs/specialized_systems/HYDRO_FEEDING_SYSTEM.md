# Hydroponic Feeding System - Automated Nutrient Management

## Overview

The Hydroponic Feeding System provides comprehensive automated nutrient delivery for hydroponic cultivation. It manages nutrient solution preparation, pH and EC control, feeding schedules, and integrates with environmental monitoring to ensure optimal plant nutrition.

## System Architecture

### Core Components

#### 1. OGBTankFeedManager (Main Controller)
```python
class OGBTankFeedManager:
    """Main hydroponic feeding system controller."""
```

#### 2. OGBFeedConfiguration (Nutrient Profiles)
```python
class OGBFeedConfiguration:
    """Plant-specific nutrient profiles and feeding schedules."""
```

#### 3. OGBFeedLogicManager (Feeding Logic)
```python
class OGBFeedLogicManager:
    """Intelligent feeding decisions and nutrient adjustments."""
```

#### 4. OGBFeedParameterManager (Parameter Management)
```python
class OGBFeedParameterManager:
    """Manages feeding parameters and target settings."""
```

#### 5. OGBPumpControlManager (Pump Control)
```python
class OGBPumpControlManager:
    """Controls nutrient dosing pumps with precision."""
```

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

#### pH Adjustment Algorithm
```python
async def adjust_ph(self, target_ph: float) -> bool:
    """Adjust solution pH to target value."""

    current_ph = await self.measure_ph()
    tolerance = 0.1  # Acceptable pH range

    if abs(current_ph - target_ph) <= tolerance:
        return True  # Already within tolerance

    # Determine adjustment direction
    if current_ph > target_ph:
        # Too alkaline, add pH down
        adjustment_ml = self.calculate_ph_down_volume(current_ph, target_ph)
        await self.dose_ph_down(adjustment_ml)
    else:
        # Too acidic, add pH up
        adjustment_ml = self.calculate_ph_up_volume(current_ph, target_ph)
        await self.dose_ph_up(adjustment_ml)

    # Mix and re-measure
    await self.activate_mixer(duration_seconds=60)
    await asyncio.sleep(30)

    final_ph = await self.measure_ph()
    return abs(final_ph - target_ph) <= tolerance
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

#### EC Adjustment Algorithm
```python
async def adjust_ec(self, target_ec: float) -> bool:
    """Adjust solution EC to target value."""

    current_ec = await self.measure_ec()
    tolerance = 0.1  # Acceptable EC range (mS/cm)

    if abs(current_ec - target_ec) <= tolerance:
        return True

    # Calculate nutrient adjustment needed
    ec_difference = target_ec - current_ec

    if ec_difference > 0:
        # Need to increase EC - add concentrated nutrients
        nutrient_mix = self.calculate_nutrient_boost(ec_difference)
        await self.add_nutrients(nutrient_mix)
    else:
        # Need to decrease EC - add water or use RO dilution
        dilution_volume = self.calculate_dilution_volume(abs(ec_difference))
        await self.dilute_solution(dilution_volume)

    # Mix and re-measure
    await self.activate_mixer(duration_seconds=120)
    await asyncio.sleep(60)

    final_ec = await self.measure_ec()
    return abs(final_ec - target_ec) <= tolerance
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

#### Pump Calibration
```python
@dataclass
class PumpCalibration:
    """Pump calibration data."""
    pump_type: str
    target_dose_ml: float = 0.0
    actual_dose_ml: float = 0.0
    calibration_factor: float = 1.0
    last_calibration: Optional[datetime] = None

    def calculate_adjustment(self) -> float:
        """Calculate adjustment factor based on calibration results."""
        if self.target_dose_ml <= 0 or self.actual_dose_ml <= 0:
            return 1.0

        # If actual was higher than target, reduce next dose
        adjustment = self.target_dose_ml / self.actual_dose_ml
        return max(0.8, min(1.2, adjustment))  # Limit to ±20%
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

**Last Updated**: December 24, 2025
**Version**: 2.0 (Advanced Nutrient Management)
**Status**: Production Ready