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
- **Behavior on Switch**:
  - All running tasks are **immediately cancelled**
  - All drippers are turned OFF
  - P1 state tracking is **reset** (irrigation count, last VWC, last irrigation time)
  - Switching back to Automatic will start **fresh** (no waiting for old interval)

### 2. Config Mode
- **Purpose**: Pre-configuration without activation
- **Watering**: None - only settings are adjusted
- **Use Case**: Setting up parameters before going live
- **Behavior on Switch**:
  - All running tasks are **immediately cancelled**
  - All drippers are turned OFF
  - P1 state tracking is **reset** (same as Disabled)
  - User can safely adjust Duration, Interval, Shot Sum, VWC targets
  - Changes are saved to DataStore immediately and used on next Automatic start

### 3. Automatic Mode (Phase-Based)
- **Logic**: Sensor-driven, light-aware 4-phase system
- **Factors**: VWC, EC, light status, growth phase
- **Phases**: P0 (Monitor) → P1 (Saturate) → P2 (Maintain) → P3 (Dryback)
- **Use Case**: Optimal plant health with full automation

### 4. Manual Mode (Manual-P0, P1, P2, P3)
- **Logic**: User selects specific phase to run
- **Control**: Forces system into selected phase
- **Use Case**: Testing, troubleshooting, specific interventions

#### Manual Mode Phase Selection (v3.3)

Manual mode now correctly extracts the phase from the CropPhase selector:

```python
def _extract_phase_from_mode(self, mode: CSMode) -> str:
    """Extract phase identifier from Manual mode enum.

    Handles:
    - Enum value: "Manual-p1" -> "p1"
    - Enum name: "MANUAL_P1" -> "p1"
    """
    mode_value = mode.value
    if "-" in mode_value:
        return mode_value.split("-")[1].lower()

    mode_name = mode.name
    if "_" in mode_name:
        phase = mode_name.split("_")[-1].lower()
        if phase in ["p0", "p1", "p2", "p3"]:
            return phase

    return "p0"  # Default fallback

def _extract_phase_from_value(self, value: str) -> str:
    """Extract phase from stored value (e.g., "P1" -> "p1").

    Handles uppercase, lowercase, and numeric inputs.
    """
    if not value:
        return "p0"

    value_lower = value.lower()
    if value_lower in ["p0", "p1", "p2", "p3"]:
        return value_lower

    # Try extracting from end of string
    if len(value_lower) >= 2:
        possible_phase = value_lower[-2:]
        if possible_phase in ["p0", "p1", "p2", "p3"]:
            return possible_phase

    return "p0"
```

**Manual Mode Flow:**
1. User selects "Manual P1" in UI
2. System reads `CropSteering.CropPhase` from DataStore
3. Phase extraction converts "P1" → "p1"
4. Manual cycle runs with correct phase settings

## Phase System

### Phase Overview

The CropSteering system operates in 4 phases that follow the natural day/night cycle:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        LIGHT ON (Day)                               │
│                                                                     │
│   P0 (Monitor)  ──VWC drops──▶  P1 (Saturate)  ──target──▶  P2     │
│        │                              │                      │      │
│        │                              │                      │      │
│    VWC OK                        irrigating              maintain   │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        LIGHT OFF (Night)                            │
│                                                                     │
│                         P3 (Night Dryback)                          │
│                                                                     │
│              Monitor dryback, emergency irrigation only             │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Phase Definitions

| Phase | Name | Light Status | Purpose | Actions |
|-------|------|--------------|---------|---------|
| **P0** | Monitoring | ON only | Wait for dryback signal | No irrigation, monitor VWC |
| **P1** | Saturation | ON only | Rapid block saturation | Multiple irrigation shots |
| **P2** | Maintenance | ON only | Hold VWC level | Maintenance irrigation |
| **P3** | Night Dryback | OFF only | Controlled dryback | Emergency irrigation only |

### Complete Phase Transition Diagram

```
                              LIGHT ON (Day)
    ┌──────────────────────────────────────────────────────────────┐
    │                                                              │
    │   ┌─────────┐      VWC < VWCMin      ┌─────────┐             │
    │   │         │ ────────────────────► │         │             │
    │   │   P0    │                        │   P1    │             │
    │   │ Monitor │                        │Saturate │             │
    │   │         │ ◄──── VWC >= VWCMin    │         │             │
    │   └────┬────┘       (after P3)       └────┬────┘             │
    │        │                                  │                  │
    │        │ Lights OFF                       │ VWC >= VWCMax    │
    │        │                                  │ OR stagnation    │
    │        │                                  │ OR max_shots     │
    │        │                                  ▼                  │
    │        │                             ┌─────────┐             │
    │        │                             │         │             │
    │        │                             │   P2    │             │
    │        │                             │Maintain │             │
    │        │                             │         │             │
    │        │                             └────┬────┘             │
    │        │                                  │                  │
    │        │                                  │ Lights OFF       │
    │        │                                  │                  │
    └────────┼──────────────────────────────────┼──────────────────┘
             │                                  │
             │         LIGHT OFF (Night)        │
             │                                  │
             ▼                                  ▼
        ┌──────────────────────────────────────────┐
        │                                          │
        │                  P3                      │
        │            Night Dryback                 │
        │                                          │
        │   • Monitor dryback percentage           │
        │   • Emergency irrigation if VWC < 85%    │
        │   • Adjust EC based on dryback rate      │
        │                                          │
        └────────────────────┬─────────────────────┘
                             │
                             │ Lights ON
                             ▼
                        Back to P0
```

### Phase Transition Summary Table

| From | To | Trigger Condition |
|------|-----|-------------------|
| P0 | P1 | VWC < VWCMin (dryback detected) |
| P0 | P3 | Lights OFF |
| P1 | P2 | VWC >= VWCMax (saturation complete) |
| P1 | P2 | Stagnation detected (VWC not increasing after 3+ shots) |
| P1 | P2 | Max irrigation attempts reached |
| P1 | P3 | Lights OFF (interrupts saturation) |
| P2 | P3 | Lights OFF |
| P3 | P0 | Lights ON (new day begins) |

### Light-Based Phase Transitions

**CRITICAL**: The light status is the PRIMARY factor for phase determination.

#### On Startup
```python
# System determines initial phase based on light status FIRST
if not is_light_on:
    # Night time → Always start in P3
    return "p3"
else:
    # Day time → Check VWC to determine P0, P1, or P2
    if vwc >= vwc_max * 0.90:
        return "p2"  # Block full → Maintenance
    elif vwc < vwc_min:
        return "p1"  # Block dry → Saturation
    else:
        return "p0"  # Normal → Monitoring
```

#### During Operation
- **Light turns OFF** → Any phase (P0, P1, P2) immediately transitions to P3
- **Light turns ON** → P3 transitions back to P0 (monitoring)

### Phase Details

#### P0: Monitoring Phase
- **Active during**: Lights ON
- **Purpose**: Wait for natural dryback to trigger saturation
- **Trigger to P1**: VWC drops below VWCMin
- **On light OFF**: Transitions to P3

#### P1: Saturation Phase  
- **Active during**: Lights ON only
- **Purpose**: Rapidly saturate the growing medium
- **Actions**: Multiple irrigation shots with wait periods
- **Completion Conditions**:
  1. VWC reaches target (VWCMax or calibrated max)
  2. Stagnation detected (VWC not increasing after 3+ shots)
  3. Max irrigation attempts reached (Shot Sum)
- **On light OFF**: Immediately stops, transitions to P3
- **Auto-calibration**: Updates VWCMax when saturation detected

##### P1 Stagnation Detection & Calibration Safety

**CRITICAL**: The system includes safety checks to prevent invalid calibration values:

```python
# Stagnation is only accepted as "block full" if VWC >= 40%
min_vwc_for_stagnation = max(40.0, preset_vwc_min)

if stagnation_detected and vwc >= min_vwc_for_stagnation:
    # Valid stagnation - block is actually full
    save_calibration(vwc)
else:
    # VWC too low - something is wrong (sensor, pump, water supply)
    log_warning("VWC stuck at low level - check system!")
    # Continue irrigating, do NOT save bad calibration
```

**Why this matters**: 
- If VWC stagnates at 19% after multiple shots, this indicates a problem (not a full block)
- Without this check, 19% would be saved as VWCMax, causing immediate P1→P2 transitions
- The system now warns about potential issues instead of saving incorrect calibration

#### P2: Day Maintenance Phase
- **Active during**: Lights ON
- **Purpose**: Maintain VWC level during light period
- **Actions**: Small maintenance irrigations when VWC drops below hold threshold
- **Hold threshold**: VWCMax × 0.95 (configurable)
- **On light OFF**: Transitions to P3

#### P3: Night Dryback Phase
- **Active during**: Lights OFF only
- **Purpose**: Allow controlled dryback overnight
- **Actions**: 
  - Monitor dryback percentage
  - Adjust EC target based on dryback rate
  - Emergency irrigation if VWC critically low
- **Emergency threshold**: VWCMax × 0.85
- **On light ON**: Transitions to P0

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

#### VWC Calibration Overview

The CropSteering system requires calibration to understand the VWC (Volumetric Water Content) range of your specific growing medium. There are two types of calibration:

| Type | Purpose | Trigger |
|------|---------|---------|
| **VWC Max** | Find saturation point | `cs_calibrate max` or auto during P1 |
| **VWC Min** | Find safe minimum | `cs_calibrate min` |

#### Console Commands for Calibration

```bash
# Show current calibration status
cs_status

# Start VWC Maximum calibration (saturation point)
cs_calibrate max
cs_calibrate max p1    # Specific phase

# Start VWC Minimum calibration (dryback monitoring)
cs_calibrate min
cs_calibrate min p2    # Specific phase

# Stop running calibration
cs_calibrate stop
```

#### Example Console Output

```
$ cs_status

🌱 CropSteering Status:
==================================================

📊 Mode: Automatic
   Active Mode: Automatic-Generative
   Active: Yes
   Current Phase: p2

📈 Current Readings:
   VWC: 45.2%
   EC: 2.35 mS/cm

🔧 Calibration Values:
   P1:
      VWC Max: 68.5%
      VWC Min: 32.1%
      Last Cal: 2026-01-03T14:30
   P2: Not calibrated
   P3: Not calibrated

==================================================
💡 Use 'cs_calibrate max' or 'cs_calibrate min' to calibrate
```

#### Calibration Manager Architecture

```python
class OGBCSCalibrationManager:
    """
    Dedicated calibration manager for VWC sensors.
    
    Handles all calibration procedures with:
    - Sensor stabilization monitoring
    - Multiple reading averaging
    - Timeout handling
    - Persistent storage of calibrated values
    """

    async def start_vwc_max_calibration(self, phase: str = "p1"):
        """
        Start VWC maximum calibration procedure.
        
        Process:
        1. Irrigate medium progressively
        2. Wait for VWC stabilization after each irrigation
        3. Detect when VWC stops increasing (saturation)
        4. Store calibrated VWCMax value
        5. Persist to disk via SaveState
        """

    async def start_vwc_min_calibration(self, phase: str = "p1"):
        """
        Start VWC minimum calibration through dryback.
        
        Process:
        1. Monitor natural dryback over time
        2. Track minimum VWC observed
        3. Apply 10% safety buffer
        4. Store calibrated VWCMin value
        5. Persist to disk via SaveState
        """

    async def _wait_for_vwc_stabilization(self, timeout=300):
        """
        Wait until VWC reading stabilizes.
        
        Uses moving average of last 3 readings
        and checks if deviation is within tolerance.
        """
```

#### Calibration Data Persistence

Calibration values are stored in the DataStore and persisted to disk:

```python
# Storage structure in CropSteering.Calibration
{
    "p1": {
        "VWCMax": 68.5,      # Maximum VWC (saturation point)
        "VWCMin": 32.1,      # Minimum VWC (safe dryback)
        "timestamp": "2026-01-03T14:30:00"
    },
    "p2": { ... },
    "p3": { ... },
    "LastRun": "2026-01-03T14:30:00"
}
```

**Important**: Calibration values are now persisted across HA restarts.

#### Auto-Calibration During P1 Phase

During the P1 (Saturation) phase, the system automatically calibrates VWCMax when:
- VWC stops increasing after irrigation (stagnation detected)
- Maximum irrigation attempts reached

This is a "passive" calibration that happens as part of normal operation.

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
def _get_drippers(self):
    """Get valid dripper devices from canPump capability.

    Filter returns only devices that contain 'dripper' keyword in name.
    This excludes cloner pumps and other non-irrigation pumps.
    """
    dripperDevices = self.data_store.getDeep("capabilities.canPump")
    if not dripperDevices:
        _LOGGER.warning(f"{self.room} - _get_drippers: No canPump capability found!")
        return []

    devices = dripperDevices.get("devEntities", [])
    if not devices:
        _LOGGER.warning(f"{self.room} - _get_drippers: No pump devices found!")
        return []

    valid_keywords = ["dripper"]

    dripper_devices = [
        dev for dev in devices
        if any(keyword in dev.lower() for keyword in valid_keywords)
    ]

    if not dripper_devices:
        _LOGGER.warning(f"{self.room} - _get_drippers: No dripper devices found in: {devices}")

    _LOGGER.warning(f"{self.room} - _get_drippers: Returning {len(dripper_devices)} dripper(s): {dripper_devices}")
    return dripper_devices
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

### User Settings from DataStore

User-configured values are loaded from the DataStore and merged with defaults. 

#### Medium-Based Configuration (Current)

The modular version uses medium-based paths for user settings:

```python
# Shot Duration (irrigation duration in seconds)
CropSteering.Substrate.{phase}.Shot_Duration_Sec  →  irrigation_duration

# Shot Interval (minutes in UI → converted to seconds internally)
CropSteering.Substrate.{phase}.Shot_Intervall     →  wait_between (converted: value * 60)

# Shot Sum (max irrigation attempts)
CropSteering.Substrate.{phase}.Shot_Sum           →  max_cycles

# VWC Targets
CropSteering.Substrate.{phase}.VWC_Target         →  VWCTarget
CropSteering.Substrate.{phase}.VWC_Min            →  VWCMin  
CropSteering.Substrate.{phase}.VWC_Max            →  VWCMax

# EC Targets
CropSteering.Substrate.{phase}.EC_Target          →  ECTarget
CropSteering.Substrate.{phase}.Min_EC             →  MinEC
CropSteering.Substrate.{phase}.Max_EC             →  MaxEC
```

Where `{phase}` is one of: `p0`, `p1`, `p2`, `p3`

#### Home Assistant Entity Names

Entity names follow this pattern:
```
number.ogb_cropsteering_{phase}_{parameter}_{roomname}

Examples:
- number.ogb_cropsteering_p1_shot_duration_veggitent
- number.ogb_cropsteering_p1_shot_intervall_veggitent  
- number.ogb_cropsteering_p1_shot_sum_veggitent
```

#### Value Loading Priority

1. **User Entity Value** (highest priority) - Values set via HA UI entities
2. **Medium Adjustments** - Applied only to VWC/EC thresholds
3. **Default Presets** (lowest priority) - Fallback values per medium type

**IMPORTANT**: User timing values (duration, interval, shot_sum) are used **exactly as configured** - no drainage_factor adjustments are applied to timing parameters.

#### Automatic Mode Timing Settings (v3.3)

In Automatic mode, the system now uses **user-configurable timing values** while maintaining **preset-based VWC/EC thresholds**:

```python
def _get_automatic_timing_settings(self, phase: str) -> Dict[str, Any]:
    """Get USER timing settings for Automatic Mode.

    Reads Duration/Interval/ShotSum from user settings.
    These are the ONLY user-settable parameters in Automatic mode.
    All other parameters (VWC/EC/etc) come from presets.

    Args:
        phase: Phase identifier (p0, p1, p2, p3)

    Returns:
        Dictionary with timing settings as proper numeric types
    """
    # Reads from CropSteering.Substrate.{phase}.Shot_Duration_Sec
    # Reads from CropSteering.Substrate.{phase}.Shot_Intervall
    # Reads from CropSteering.Substrate.{phase}.Shot_Sum
    # Converts minutes to seconds, validates ranges
```

**Automatic Mode Logic:**
- **Timing**: Uses user settings (duration, interval, shot_sum)
- **VWC/EC Thresholds**: Uses preset values with medium adjustments
- **Logging**: Shows both user timing and preset thresholds

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

These adjustments are applied on top of user settings.

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

#### System Starts in Wrong Phase
- **Symptom**: P1 irrigation shots during night, or P3 during day
- **Cause**: `isPlantDay.islightON` not correctly set
- **Solution**: 
  1. Check that light schedule is configured correctly
  2. Verify `isPlantDay.islightON` in DataStore reflects actual light status
  3. System should always start in P3 if lights are OFF

#### VWC Sensors Reading Incorrectly
- **Symptom**: Irrigations at wrong times or not at all
- **Cause**: Poor calibration or sensor placement
- **Solution**: Recalibrate sensors, check sensor depth

#### Over/Under Watering
- **Symptom**: Plants showing stress despite irrigation
- **Cause**: Wrong VWC targets for plant phase/medium
- **Solution**: Adjust phase-specific VWC ranges

#### P1 Not Stopping When Lights Turn Off
- **Symptom**: Irrigation continues at night
- **Cause**: Light status not updating (older versions)
- **Solution**: Update to latest version - P1 now checks light status each cycle

#### System Not Responding
- **Symptom**: No irrigation despite low VWC
- **Cause**: Emergency stop or calibration issues
- **Solution**: Check system status, recalibrate if needed

#### Irrigation Duration Not Using User Settings
- **Symptom**: Default duration (45s, 20s, 15s) instead of configured value
- **Cause**: DataStore path mismatch or value not set
- **Solution**: 
  1. Check `CropSteering.ShotDuration.{phase}.value` is set
  2. Ensure value is numeric (not string)
  3. Restart integration to reload settings

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

## Console Commands

The CropSteering system provides console commands for monitoring and calibration:

### Available Commands

| Command | Description | Example |
|---------|-------------|---------|
| `cs_status` | Show current CS status and calibration values | `cs_status` |
| `cs_calibrate max` | Start VWC max calibration | `cs_calibrate max p1` |
| `cs_calibrate min` | Start VWC min calibration | `cs_calibrate min p2` |
| `cs_calibrate stop` | Stop running calibration | `cs_calibrate stop` |

### Usage Examples

```bash
# Check current status
$ cs_status

# Start max calibration for P1 phase
$ cs_calibrate max

# Start min calibration for P2 phase  
$ cs_calibrate min p2

# Stop any running calibration
$ cs_calibrate stop

# Get help
$ cs_calibrate -h
```

---

## Implementation Status

### Core Components ✅ **IMPLEMENTED**

| Component | Lines | Status | Description |
|-----------|-------|--------|-------------|
| **OGBCSManager** | ~1450 | ✅ Ready | Main controller, coordinates all subsystems |
| **OGBCSConfigurationManager** | ~320 | ✅ Ready | Settings, presets, medium adjustments |
| **OGBCSIrrigationManager** | ~200 | ✅ Ready | Water delivery, dripper control |
| **OGBCSPhaseManager** | ~150 | ✅ Ready | Phase transitions, timing logic |
| **OGBCSCalibrationManager** | ~400 | ✅ Ready | VWC max/min calibration procedures |
| **OGBAdvancedSensor** | ~300 | ✅ Ready | TDR polynomial calculations |

### Key Features ✅ **FULLY IMPLEMENTED**

- **4-Phase Automatic Mode**: P0-P3 with intelligent transitions
- **Manual Mode**: User-configurable timing per phase
- **Medium-Specific Adjustments**: Rockwool, coco, soil, perlite, aero, water
- **Growth Phase Optimization**: Vegetative vs generative watering strategies
- **VWC Calibration**: Dedicated CalibrationManager with persistence
- **Console Commands**: `cs_status`, `cs_calibrate` for user interaction
- **Advanced Sensor Processing**: TDR-style polynomial calculations
- **EC Management**: Pore water EC with temperature normalization
- **Irrigation Validation**: Effectiveness monitoring and anomaly detection
- **Emergency Systems**: Safety irrigation and dryback protection
- **AI Learning Integration**: Sensor data collection for analytics
- **Calibration Persistence**: Values survive HA restarts

### Integration Points ✅ **CONNECTED**

- **VPD System**: Coordinates with environmental control
- **Premium Analytics**: Sends irrigation data for AI learning
- **Medium Manager**: Syncs growing medium type
- **HA Entities**: Controls pumps, valves, sensors
- **Event System**: Emits irrigation events for monitoring
- **Console Manager**: Exposes `cs_calibrate` and `cs_status` commands
- **DataStore**: Persistent calibration storage

---

**Last Updated**: January 6, 2026
**Version**: 3.3 (Phase Extraction, Pump Filtering & User Timing Fixes)
**Status**: ✅ **PRODUCTION READY** - All critical bugs fixed, enhanced user control

### Changelog v3.3 (January 6, 2026)
- **Fixed**: Manual mode phase extraction bug - now correctly reads from CropPhase selector (P1 vs p0)
- **Fixed**: Pump filtering bug - `_get_drippers()` now filters by "dripper" keyword to exclude pumpcloner
- **Added**: Automatic mode user timing settings - Duration/Interval/ShotSum now use user values instead of hardcoded presets
- **Added**: Phase extraction helper methods `_extract_phase_from_mode()` and `_extract_phase_from_value()`
- **Added**: Automatic timing settings method `_get_automatic_timing_settings()` for user-configurable timing
- **Fixed**: Indentation and syntax errors in `_irrigate()` method try/catch blocks
- **Improved**: Better logging for user timing values vs preset VWC/EC values in Automatic mode

### Changelog v3.2 (January 5, 2026)
- **Fixed**: Config/Disabled mode now properly cancels running tasks
- **Fixed**: P1 state tracking reset when entering Config/Disabled (no stale interval waits)
- **Fixed**: Stagnation detection safety - requires VWC >= 40% before saving calibration
- **Fixed**: Auto-reset of invalid calibration values (< 40% or < preset minimum)
- **Added**: Detailed phase transition diagram
- **Added**: Medium-based DataStore paths documentation
- **Added**: Entity naming convention documentation
- **Improved**: P1 shot logging now includes duration and next interval time