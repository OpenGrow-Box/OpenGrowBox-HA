### Hydroponic Tank Feeding System

**Precision Nutrient Dosing**: Automated nutrient delivery system with configurable pump calibration and plant-stage-based feeding schedules.

#### Overview

The Hydroponic Tank Feeding System provides sophisticated nutrient dosing for hydroponic grow operations. It features:

- **Precise Pump Control**: ML-per-second calibration for accurate dosing
- **Plant Stage Integration**: Automatic nutrient adjustments based on growth phases
- **Multi-Nutrient Support**: Separate pumps for different nutrients (A, B, C, pH up/down)
- **Reservoir Management**: Volume tracking and automatic top-off
- **Calibration System**: Self-learning pump accuracy optimization

#### System Architecture

**Core Components:**
- **OGBTankFeedManager**: Main controller for feeding operations
- **Pump Controllers**: Individual pump activation and calibration
- **Feed Logic Manager**: Plant-stage-based nutrient calculations
- **Calibration Manager**: Pump accuracy testing and adjustment

**Data Flow:**
```
Plant Stage → Nutrient Requirements → Dose Calculations → Pump Activation → Calibration Feedback
```

#### Pump Configuration and Calibration

**Pump Speed Configuration:**
```python
@dataclass
class PumpConfig:
    ml_per_second: float = 0.5    # Pump flow rate (ml/s)
    min_dose_ml: float = 0.5      # Minimum dose volume
    max_dose_ml: float = 25.0     # Maximum dose volume
```

**Dose Time Calculation:**
```python
def calculate_dose_time(ml_amount: float) -> float:
    """Calculate pump run time for desired ml amount"""
    if ml_amount < config.min_dose_ml:
        return 0.0
    ml_amount = min(ml_amount, config.max_dose_ml)  # Cap at maximum
    return ml_amount / config.ml_per_second  # Time = volume / flow rate
```

**Example Calculations:**
- **0.5 ml** at 0.5 ml/s = **1 second** run time
- **5.0 ml** at 0.5 ml/s = **10 seconds** run time
- **25.0 ml** at 0.5 ml/s = **50 seconds** run time (maximum)

#### Plant Stage Nutrient Profiles

**Automatic Feeding Configurations:**
```python
plant_stage_nutrients = {
    "Germination": FeedConfig(
        ph_target=6.2,
        ec_target=0.6,
        nutrients={"A": 0.5, "B": 0.5, "C": 0.3}  # ml per liter
    ),
    "EarlyVeg": FeedConfig(
        ph_target=5.8,
        ec_target=1.2,
        nutrients={"A": 3.0, "B": 1.0, "C": 2.0}
    ),
    "MidVeg": FeedConfig(
        ph_target=5.8,
        ec_target=1.5,
        nutrients={"A": 4.0, "B": 2.0, "C": 3.0}
    )
}
```

#### Pump Activation Sequence

**Standard Nutrient Dosing:**
```python
async def activate_nutrient_feeding(feed_config, reservoir_volume):
    # Calculate nutrient doses based on reservoir volume
    for nutrient_type, ml_per_liter in feed_config.nutrients.items():
        total_ml = ml_per_liter * reservoir_volume
        
        # Convert ml to run time
        run_time = calculate_dose_time(total_ml)
        
        # Activate appropriate pump
        pump_entity = f"switch.nutrient_pump_{nutrient_type.lower()}"
        await activate_pump(pump_entity, run_time, total_ml)
```

**pH Correction Integration:**
```python
# After nutrient dosing, check and correct pH
current_ph = sensor_data["ph"]
target_ph = feed_config.ph_target

if current_ph > target_ph + 0.1:
    # Add pH down
    ph_down_ml = calculate_ph_correction(current_ph, target_ph, "down")
    await activate_pump("switch.ph_down_pump", 
                       calculate_dose_time(ph_down_ml), ph_down_ml)
elif current_ph < target_ph - 0.1:
    # Add pH up
    ph_up_ml = calculate_ph_correction(current_ph, target_ph, "up")
    await activate_pump("switch.ph_up_pump", 
                       calculate_dose_time(ph_up_ml), ph_up_ml)
```

#### Pump Calibration System

**Calibration Process:**
1. **Test Dose**: Dispense known volume (e.g., 10ml)
2. **Measure Result**: Actual vs expected volume
3. **Calculate Factor**: Adjustment multiplier for accuracy
4. **Apply Correction**: Update pump calibration factor

**Calibration Data Structure:**
```python
@dataclass
class PumpCalibration:
    pump_type: str
    calibration_factor: float = 1.0      # Accuracy multiplier
    last_calibration: datetime = None
    accuracy_score: float = 0.0          # Percentage accuracy
```

#### Safety and Validation

**Dose Limits:**
- **Minimum Dose**: Prevents micro-dosing errors
- **Maximum Dose**: Prevents reservoir over-dosing
- **Time Limits**: Maximum pump run time protection

**Error Handling:**
- **Pump Failure Detection**: Timeout and retry logic
- **Calibration Validation**: Automatic recalibration triggers
- **Reservoir Monitoring**: Low volume warnings

#### Integration with Other Systems

**VPD Perfection Compatibility:**
- Nutrient dosing can trigger based on VPD readings
- pH/EC adjustments integrated with environmental control

**Plant Stage Synchronization:**
- Automatic feeding schedule updates with plant growth
- Nutrient ratio adjustments for different growth phases

**Monitoring and Logging:**
- Detailed dosing logs with timestamps
- Calibration history tracking
- Reservoir volume monitoring

#### Configuration Interface

**HA Number Entities:**
- `number.ogb_pump_ml_per_second_{room}`: Pump flow rate calibration
- `number.ogb_reservoir_volume_{room}`: Tank capacity setting
- `number.ogb_feed_interval_{room}`: Automatic feeding frequency

**Plant Stage Configuration:**
```json
{
  "hydroConfig": {
    "plantStages": {
      "EarlyVeg": {
        "nutrients": {"A": 3.0, "B": 1.0, "C": 2.0},
        "phTarget": 5.8,
        "ecTarget": 1.2
      }
    }
  }
}
```

#### Benefits

**Precision Nutrition:**
- Exact dosing prevents nutrient deficiencies or toxicities
- Consistent nutrient ratios across reservoir volume

**Automation:**
- Scheduled feeding eliminates manual dosing
- pH/EC correction integrated with nutrient delivery

**Adaptability:**
- Plant-stage-specific nutrient profiles
- Pump calibration ensures long-term accuracy

**Safety:**
- Dose limits prevent over-fertilization
- Calibration system maintains accuracy over time

### Hydroponic Tank Feeding System

**Precision Nutrient Dosing**: Automated nutrient delivery system with configurable pump calibration and plant-stage-based feeding schedules.

#### Overview

The Hydroponic Tank Feeding System provides sophisticated nutrient dosing for hydroponic grow operations. It features:

- **Precise Pump Control**: ML-per-second calibration for accurate dosing
- **Plant Stage Integration**: Automatic nutrient adjustments based on growth phases
- **Multi-Nutrient Support**: Separate pumps for different nutrients (A, B, C, pH up/down)
- **Reservoir Management**: Volume tracking and automatic top-off
- **Calibration System**: Self-learning pump accuracy optimization

#### System Architecture

**Core Components:**
- **OGBTankFeedManager**: Main controller for feeding operations
- **Pump Controllers**: Individual pump activation and calibration
- **Feed Logic Manager**: Plant-stage-based nutrient calculations
- **Calibration Manager**: Pump accuracy testing and adjustment

**Data Flow:**
```
Plant Stage → Nutrient Requirements → Dose Calculations → Pump Activation → Calibration Feedback
```

#### Pump Configuration and Calibration

**Pump Speed Configuration:**
```python
@dataclass
class PumpConfig:
    ml_per_second: float = 0.5    # Pump flow rate (ml/s)
    min_dose_ml: float = 0.5      # Minimum dose volume
    max_dose_ml: float = 25.0     # Maximum dose volume
```

**Dose Time Calculation:**
```python
def calculate_dose_time(ml_amount: float) -> float:
    """Calculate pump run time for desired ml amount"""
    if ml_amount < config.min_dose_ml:
        return 0.0
    ml_amount = min(ml_amount, config.max_dose_ml)  # Cap at maximum
    return ml_amount / config.ml_per_second  # Time = volume / flow rate
```

**Example Calculations:**
- **0.5 ml** at 0.5 ml/s = **1 second** run time
- **5.0 ml** at 0.5 ml/s = **10 seconds** run time
- **25.0 ml** at 0.5 ml/s = **50 seconds** run time (maximum)

#### Plant Stage Nutrient Profiles

**Automatic Feeding Configurations:**
```python
plant_stage_nutrients = {
    "Germination": FeedConfig(
        ph_target=6.2,
        ec_target=0.6,
        nutrients={"A": 0.5, "B": 0.5, "C": 0.3}  # ml per liter
    ),
    "EarlyVeg": FeedConfig(
        ph_target=5.8,
        ec_target=1.2,
        nutrients={"A": 3.0, "B": 1.0, "C": 2.0}
    ),
    "MidVeg": FeedConfig(
        ph_target=5.8,
        ec_target=1.5,
        nutrients={"A": 4.0, "B": 2.0, "C": 3.0}
    )
}
```

#### Pump Activation Sequence

**Standard Nutrient Dosing:**
```python
async def activate_nutrient_feeding(feed_config, reservoir_volume):
    # Calculate nutrient doses based on reservoir volume
    for nutrient_type, ml_per_liter in feed_config.nutrients.items():
        total_ml = ml_per_liter * reservoir_volume

        # Convert ml to run time
        run_time = calculate_dose_time(total_ml)

        # Activate appropriate pump
        pump_entity = f"switch.nutrient_pump_{nutrient_type.lower()}"
        await activate_pump(pump_entity, run_time, total_ml)
```

**pH Correction Integration:**
```python
# After nutrient dosing, check and correct pH
current_ph = sensor_data["ph"]
target_ph = feed_config.ph_target

if current_ph > target_ph + 0.1:
    # Add pH down
    ph_down_ml = calculate_ph_correction(current_ph, target_ph, "down")
    await activate_pump("switch.ph_down_pump",
                       calculate_dose_time(ph_down_ml), ph_down_ml)
elif current_ph < target_ph - 0.1:
    # Add pH up
    ph_up_ml = calculate_ph_correction(current_ph, target_ph, "up")
    await activate_pump("switch.ph_up_pump",
                       calculate_dose_time(ph_up_ml), ph_up_ml)
```

#### Pump Calibration System

**Calibration Process:**
1. **Test Dose**: Dispense known volume (e.g., 10ml)
2. **Measure Result**: Actual vs expected volume
3. **Calculate Factor**: Adjustment multiplier for accuracy
4. **Apply Correction**: Update pump calibration factor

**Calibration Data Structure:**
```python
@dataclass
class PumpCalibration:
    pump_type: str
    calibration_factor: float = 1.0      # Accuracy multiplier
    last_calibration: datetime = None
    accuracy_score: float = 0.0          # Percentage accuracy
```

#### Safety and Validation

**Dose Limits:**
- **Minimum Dose**: Prevents micro-dosing errors
- **Maximum Dose**: Prevents reservoir over-dosing
- **Time Limits**: Maximum pump run time protection

**Error Handling:**
- **Pump Failure Detection**: Timeout and retry logic
- **Calibration Validation**: Automatic recalibration triggers
- **Reservoir Monitoring**: Low volume warnings

#### Integration with Other Systems

**VPD Perfection Compatibility:**
- Nutrient dosing can trigger based on VPD readings
- pH/EC adjustments integrated with environmental control

**Plant Stage Synchronization:**
- Automatic feeding schedule updates with plant growth
- Nutrient ratio adjustments for different growth phases

**Monitoring and Logging:**
- Detailed dosing logs with timestamps
- Calibration history tracking
- Reservoir volume monitoring

#### Configuration Interface

**HA Number Entities:**
- `number.ogb_pump_ml_per_second_{room}`: Pump flow rate calibration
- `number.ogb_reservoir_volume_{room}`: Tank capacity setting
- `number.ogb_feed_interval_{room}`: Automatic feeding frequency

**Plant Stage Configuration:**
```json
{
  "hydroConfig": {
    "plantStages": {
      "EarlyVeg": {
        "nutrients": {"A": 3.0, "B": 1.0, "C": 2.0},
        "phTarget": 5.8,
        "ecTarget": 1.2
      }
    }
  }
}
```

#### Benefits

**Precision Nutrition:**
- Exact dosing prevents nutrient deficiencies or toxicities
- Consistent nutrient ratios across reservoir volume

**Automation:**
- Scheduled feeding eliminates manual dosing
- pH/EC correction integrated with nutrient delivery

**Adaptability:**
- Plant-stage-specific nutrient profiles
- Pump calibration ensures long-term accuracy

**Safety:**
- Dose limits prevent over-fertilization
- Calibration system maintains accuracy over time