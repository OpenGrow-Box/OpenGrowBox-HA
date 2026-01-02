# Control Modes - VPD Perfection, AI Control, PID, MPC

## Overview

OpenGrowBox implements multiple control modes that determine how environmental parameters are managed. Each mode uses different algorithms and strategies for maintaining optimal growing conditions based on plant stage and user preferences.

**Key Features:**
- **Multiple Control Modes**: VPD Perfection, VPD Target, AI Control, PID, MPC
- **Night VPD Hold**: Energy-saving adaptive control during dark periods
- **Intelligent Device Management**: Context-aware prioritization and dampening
- **Emergency Override**: Safety-first response system
- **Plant Stage Integration**: Growth-phase appropriate control ranges

## Available Control Modes

### 1. VPD Perfection Mode

**Primary Mode**: Automatic environmental control based on plant growth stages with Night VPD Hold energy optimization.

### 2. VPD Target Mode

**Precision Mode**: Maintains VPD within user-defined tolerance around a specific target value for specialized applications.

**Primary Mode**: Automatic environmental control based on plant growth stages.

#### How It Works
- **Input**: Current VPD reading, plant stage, environmental sensors
- **Logic**: Maintains VPD within optimal ranges for current plant stage
- **Actions**: Adjusts humidity, temperature, and air circulation
- **Data Source**: Uses detailed plant stage VPD ranges from datastore

#### Plant Stage Integration
```python
# VPD ranges by plant stage (from plantStages datastore)
plant_stage_ranges = {
    "Germination": [0.35, 0.70],    # Very low VPD for seedlings
    "EarlyVeg": [0.60, 1.20],       # Moderate for initial growth
    "MidVeg": [0.75, 1.45],         # Optimal vegetative development
    "LateVeg": [0.90, 1.65],        # Pre-flowering preparation
    "EarlyFlower": [0.80, 1.55],    # Transition to flowering
    "MidFlower": [0.90, 1.70],      # Peak flowering
    "LateFlower": [0.90, 1.85],     # Late flowering/fruiting
}
```

### Dual-Range VPD Control System

VPD Perfection operates within **two nested VPD ranges** for optimal plant health and precise control:

#### 1. Broad Plant Stage Safety Range
- **Boundaries**: `vpdrange[0]` to `vpdrange[1]` (from plantStages datastore)
- **Purpose**: Absolute safety limits preventing plant stress
- **Example**: EarlyVeg range [0.60, 1.20] kPa
- **Behavior**: System **never** allows VPD outside these limits

#### 2. Narrow Perfection Target Range
- **Boundaries**: `min_perfection` to `max_perfection` (calculated with tolerance)
- **Purpose**: Precision control zone for optimal growth
- **Example**: With 10% tolerance around midpoint 0.90 → [0.81, 0.99] kPa
- **Behavior**: System **actively maintains** VPD within this range

#### 3. Midpoint Targeting
- **Target Value**: Midpoint of the broad plant stage range
- **Example**: Plant stage [0.60, 1.20] → target 0.90 kPa
- **Behavior**: System aims for this optimal value within the perfection range

### Control Hierarchy

```
EMERGENCY: Outside broad range → Immediate correction (highest priority)
PRIMARY: Outside perfection range → Active correction
FINE-TUNE: Within perfection range → Approach midpoint target
STABLE: At midpoint target → Minimal adjustments (lowest priority)
```

### Range Relationship Example

#### Early Vegetative Stage
```python
# Broad safety range (never exceeded)
broad_range = [0.60, 1.20]  # vpdrange[0], vpdrange[1]

# Narrow perfection range (active control zone)
perfection_range = [0.81, 0.99]  # min_perfection, max_perfection (10% tolerance)

# Target midpoint
target_vpd = 0.90  # Midpoint of broad range
```

#### Control Behavior by VPD Level
- **VPD = 0.50**: Below broad minimum → **Emergency increase** (dangerous)
- **VPD = 0.70**: Within broad, below perfection → **Standard increase**
- **VPD = 0.85**: Within perfection, below target → **Fine-tune increase**
- **VPD = 0.90**: At target → **Stable operation** (optimal)
- **VPD = 1.05**: Within perfection, above target → **Fine-tune decrease**
- **VPD = 1.30**: Above broad maximum → **Emergency decrease** (dangerous)

#### Control Algorithm
```python
async def handle_vpd_perfection(self):
    current_vpd = self.data_store.getDeep("vpd.current")
    plant_stage = self.data_store.get("plantStage")

    # Get target range for current plant stage
    target_range = self.get_vpd_range_for_stage(plant_stage)

    if current_vpd < target_range[0]:
        # VPD too low - increase by reducing humidity/increasing temperature
        await self.event_manager.emit("reduce_vpd")
    elif current_vpd > target_range[1]:
        # VPD too high - decrease by increasing humidity/reducing temperature
        await self.event_manager.emit("increase_vpd")
    else:
        # Within range - fine tune if needed
        await self.event_manager.emit("FineTune_vpd")
```

#### When to Use
- **Best for**: Most users, automatic optimization
- **Requirements**: Plant stages properly configured
- **Benefits**: Stage-appropriate environmental control

### 2. VPD Target Mode

**Manual Override Mode**: User-defined VPD targets with direct control.

#### How It Works
- **Input**: User-set VPD target value, tolerance settings
- **Logic**: Maintains VPD at specific user-defined target
- **Actions**: Direct adjustments to hit target VPD
- **Flexibility**: Bypasses automatic stage-based ranges

#### Configuration
```python
# User-configurable settings
vpd_target_config = {
    "target_vpd": 1.2,        # Desired VPD in kPa
    "tolerance": 0.1,         # Acceptable deviation
    "control_sensitivity": 0.05  # How aggressively to adjust
}
```

#### Control Algorithm
```python
async def handle_targeted_vpd(self):
    current_vpd = self.data_store.getDeep("vpd.current")
    target_vpd = self.data_store.getDeep("vpd.target")

    deviation = abs(current_vpd - target_vpd)

    if deviation > self.tolerance:
        if current_vpd < target_vpd:
            await self.event_manager.emit("reduce_vpd")
        else:
            await self.event_manager.emit("increase_vpd")
```

#### When to Use
- **Best for**: Experienced users with specific VPD requirements
- **Requirements**: Manual target configuration
- **Benefits**: Precise VPD control regardless of plant stage

### 3. AI Control Mode (Premium)

**Machine Learning Mode**: AI-driven environmental optimization using Premium API.

#### How It Works
- **Input**: Comprehensive environmental data, plant metrics, historical performance
- **Logic**: Machine learning algorithms analyze patterns and predict optimal actions
- **Actions**: AI-generated device control commands
- **Data Flow**: Sends all data to Premium API for processing

#### AI Processing Pipeline
```
1. Collect comprehensive grow data
2. Send to Premium API via DataRelease event
3. AI analyzes historical patterns and current conditions
4. Receive optimized control recommendations
5. Execute AI-suggested actions
```

#### Key Features
- **Predictive Control**: Anticipates environmental changes
- **Pattern Recognition**: Learns from successful grow cycles
- **Adaptive Learning**: Improves performance over time
- **Multi-variable Optimization**: Considers all environmental factors

#### Requirements
- **Premium Subscription**: Required for AI features
- **API Connectivity**: Active connection to ogb-grow-api
- **Data History**: Sufficient historical data for learning

### 4. PID Control Mode (Premium)

**Proportional-Integral-Derivative Control**: Advanced feedback control system.

#### How It Works
- **Input**: Error between setpoint and current value
- **Logic**: Three-term control algorithm (P+I+D components)
- **Actions**: Continuous adjustments based on control calculations
- **Tuning**: Configurable Kp, Ki, Kd parameters

#### PID Components
- **Proportional (P)**: Immediate response to current error
- **Integral (I)**: Correction for accumulated error over time
- **Derivative (D)**: Prediction based on rate of change

#### PID Implementation
```python
class PIDController:
    def __init__(self, kp, ki, kd, setpoint):
        self.kp = kp  # Proportional gain
        self.ki = ki  # Integral gain
        self.kd = kd  # Derivative gain
        self.setpoint = setpoint

        self.prev_error = 0
        self.integral = 0

    def calculate(self, current_value, dt):
        error = self.setpoint - current_value

        # Proportional term
        p_term = self.kp * error

        # Integral term
        self.integral += error * dt
        i_term = self.ki * self.integral

        # Derivative term
        derivative = (error - self.prev_error) / dt
        d_term = self.kd * derivative

        # Calculate output
        output = p_term + i_term + d_term
        self.prev_error = error

        return output
```

#### Tuning Parameters
- **Kp (Proportional)**: Overall control sensitivity
- **Ki (Integral)**: Eliminates steady-state error
- **Kd (Derivative)**: Improves stability and response speed

### 5. MPC Control Mode (Premium)

**Model Predictive Control**: Advanced predictive control with constraint optimization.

#### How It Works
- **Input**: Current state, future predictions, system constraints
- **Logic**: Optimizes control trajectory over prediction horizon
- **Actions**: Predictive adjustments based on forecasted conditions
- **Optimization**: Considers multiple objectives and constraints

#### MPC Characteristics
- **Prediction Horizon**: Looks ahead multiple time steps
- **Control Horizon**: Plans control actions for future periods
- **Constraints**: Respects device limits and safety boundaries
- **Multi-objective**: Balances competing control goals

#### Key Advantages
- **Anticipatory Control**: Prevents problems before they occur
- **Constraint Handling**: Respects device and environmental limits
- **Disturbance Rejection**: Robust to external disturbances
- **Energy Optimization**: Minimizes resource usage

### 6. Drying Mode

**Harvest/Post-Harvest Mode**: Specialized control for drying and curing phases.

#### How It Works
- **Input**: Drying stage requirements, humidity targets
- **Logic**: Maintains specific conditions for proper drying/curing
- **Actions**: Precise humidity and temperature control
- **Monitoring**: Tracks drying progress and quality

#### Drying Profiles
```python
drying_profiles = {
    "slow_dry": {
        "temperature": 18-22°C,
        "humidity": 55-65%,
        "airflow": "gentle",
        "duration": "7-14 days"
    },
    "fast_dry": {
        "temperature": 20-24°C,
        "humidity": 45-55%,
        "airflow": "moderate",
        "duration": "3-7 days"
    }
}
```

### 7. Closed Environment Mode

**Sealed Chamber Control**: Specialized mode for air-tight grow environments requiring CO2, O2, and humidity management without traditional ventilation.

### CO2 Control Switch - Cross-Mode Automation

**Universal CO2 Control**: Independent switch that enables/disables CO2 automation across all tent modes.

#### Overview & Purpose

Closed Environment mode addresses the unique challenges of **sealed grow chambers** where air exchange is minimized or eliminated. Traditional ventilation-based humidity control becomes ineffective, requiring specialized management of CO2 levels, oxygen safety monitoring, and precise humidity control through dehumidifiers/humidifiers.

**Key Characteristics:**
- **Air-tight operation**: No traditional ventilation (exhaust/intake fans)
- **CO2 supplementation**: Active CO2 level maintenance (800-1500 ppm for photosynthesis)
- **O2 safety monitoring**: Emergency ventilation triggers below 19% O2
- **Humidity precision**: Dehumidifier/humidifier-based control without air exchange
- **Air recirculation**: Optimized circulation for CO2 distribution and thermal uniformity

#### Hardware Requirements

**Required Sensors:**
- **CO2 Sensor**: For active CO2 level monitoring and supplementation
- **O2 Sensor**: Critical for safety monitoring in sealed environments
- **Humidity/Temperature Sensors**: Standard environmental monitoring

**Recommended Devices:**
- **CO2 Controller/Injector**: For maintaining optimal CO2 levels
- **O2 Safety System**: Emergency ventilation capability
- **Dehumidifier**: Precise humidity reduction without ventilation
- **Humidifier**: Controlled humidity addition
- **Air Recirculation Fan**: For CO2 distribution and thermal uniformity

#### Control Logic

Closed Environment mode uses **plant stage-based range control** identical to VPD Perfection, with ambient-enhanced optimization for sealed chambers.

##### Plant Stage Integration (VPD Perfection Style)
```python
# Broad Safety Range (from plantStages datastore - NEVER exceeded)
broad_min_temp = stage_data["minTemp"]  # e.g., 22°C for EarlyVeg
broad_max_temp = stage_data["maxTemp"]  # e.g., 26°C for EarlyVeg

# Narrow Perfection Range (active control zone with ambient enhancement)
midpoint_temp = (broad_min_temp + broad_max_temp) / 2  # e.g., 24°C
perfection_range = (broad_max_temp - broad_min_temp) * 0.1  # 10% tolerance
perfect_min_temp = max(broad_min_temp, midpoint_temp - perfection_range)
perfect_max_temp = min(broad_max_temp, midpoint_temp + perfection_range)

# Ambient enhancement applied to perfection range
ambient_factor = calculate_ambient_temperature_factor()
ambient_adjusted_min = perfect_min_temp + ambient_factor
ambient_adjusted_max = perfect_max_temp + ambient_factor
```

##### Dual-Range Control System
```python
# Control Hierarchy (identical to VPD Perfection)
EMERGENCY: Outside broad range → Immediate correction (highest priority)
PRIMARY: Outside perfection range → Active correction
FINE-TUNE: Within perfection range → Approach midpoint with ambient optimization
STABLE: At optimal midpoint → Minimal adjustments (lowest priority)

# Temperature Control Example (EarlyVeg Stage)
current_temp = 25.2°C
broad_range = [22°C, 26°C]      # Safety bounds
perfection_range = [23.2°C, 24.8°C]  # Active control zone
ambient_enhanced = [22.7°C, 24.3°C]  # With ambient cooling influence

# Result: Fine-tune toward 24°C (midpoint) with cooling bias
```

##### CO2 Management
```python
async def maintain_co2(self, capabilities):
    # Check CO2 control switch - skip if disabled
    co2_control_enabled = self.ogb.dataStore.getDeep("controlOptions.co2Control", False)
    if not co2_control_enabled:
        return

    current_co2 = self.dataStore.getDeep("sensors.co2")

    # Read configurable CO2 targets from datastore (default: 400-1800 ppm)
    co2_min = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.minPPM", 400)
    co2_max = self.ogb.dataStore.getDeep("controlOptionData.co2ppm.maxPPM", 1800)

    if current_co2 < co2_min:  # Below minimum for photosynthesis
        await self._inject_co2(capabilities)
    elif current_co2 > co2_max:  # Above optimal range
        await self._reduce_co2(capabilities)
    # Within range: optimal conditions, no action needed

    # Emergency high CO2 - force ventilation (fixed threshold)
    if current_co2 > 2000:
        await self._emergency_co2_ventilation(capabilities)
```

##### O2 Safety Monitoring
```python
async def monitor_o2_safety(self, capabilities):
    current_o2 = self.data_store.getDeep("sensors.o2")

    if current_o2 < 19.0:  # Critical O2 level
        await self._emergency_o2_ventilation(capabilities)
    elif current_o2 < 20.0:  # Warning level
        _LOGGER.warning(f"O2 level low: {current_o2}%")
```

#### Safety Features

**O2 Emergency Override:**
- **Trigger**: O2 level drops below 19%
- **Action**: Immediate activation of exhaust/intake ventilation
- **Priority**: Highest emergency priority, overrides all other controls

**CO2 High-Limit Protection:**
- **Trigger**: CO2 level exceeds 2000 ppm
- **Action**: Forced ventilation to reduce CO2 levels
- **Purpose**: Prevent CO2 toxicity in sealed environments

**Humidity Bounds:**
- **Upper Limit**: 85% RH triggers emergency dehumidification
- **Lower Limit**: 20% RH (configurable) for plant health protection

#### Control Algorithm

```python
async def execute_closed_environment_cycle(self, capabilities):
    """
    Complete closed environment control cycle with ambient-enhanced range control.
    Uses VPD-perfection-style dual-range logic with plant stage integration.
    """
    # Phase 1: Safety monitoring (highest priority)
    await self.monitor_o2_safety(capabilities)

    # Phase 2: Ambient-enhanced temperature control (range-based)
    await self.control_temperature_ambient_aware(capabilities)

    # Phase 3: Ambient-enhanced humidity control (range-based)
    await self.control_humidity_ambient_aware(capabilities)

    # Phase 4: CO2 maintenance
    await self.maintain_co2(capabilities)

    # Phase 5: Air optimization
    await self.optimize_air_recirculation(capabilities)
```

#### Plant Stage Range Configuration

Closed Environment mode reads temperature and humidity ranges from the **`plantStages` datastore**, identical to VPD Perfection mode:

```python
plantStages: {
  "EarlyVeg": {
    "minTemp": 22,      # Broad safety minimum
    "maxTemp": 26,      # Broad safety maximum
    "minHumidity": 65,  # Broad safety minimum
    "maxHumidity": 75   # Broad safety maximum
  }
}
```

#### Ambient Enhancement Configuration

```json
{
  "closedEnvironment": {
    "ambient_temp_influence": 0.3,      // 30% ambient influence on temperature
    "ambient_humidity_influence": 0.4,  // 40% ambient influence on humidity
    "temp_tolerance": 0.1,              // 10% perfection range tolerance
    "humidity_tolerance": 0.15,         // 15% perfection range tolerance
    "co2TargetMin": 800,
    "co2TargetMax": 1500,
    "co2EmergencyMax": 2000,
    "o2EmergencyMin": 19.0,
    "o2WarningMin": 20.0
  }
}
```

**User-Configurable Parameters:**
- Ambient influence strength (0.0-1.0)
- Tolerance for perfection ranges
- CO2 target range (minPPM/maxPPM from datastore)
- O2 safety thresholds

#### Dual-Range Control System (VPD Perfection Compatible)

Closed Environment mode implements the **same dual-range control system** as VPD Perfection mode:

##### 1. Broad Plant Stage Safety Range
- **Source**: `minTemp/maxTemp` and `minHumidity/maxHumidity` from `plantStages` datastore
- **Purpose**: Absolute safety limits preventing plant stress
- **Example**: EarlyVeg temperature range [22°C, 26°C]
- **Behavior**: System **never** allows conditions outside these limits

##### 2. Narrow Perfection Target Range
- **Calculation**: Midpoint ± tolerance (10% for temp, 15% for humidity)
- **Purpose**: Precision control zone for optimal growth
- **Example**: EarlyVeg perfection range [23.2°C, 24.8°C] around 24°C midpoint
- **Ambient Enhancement**: External conditions adjust this range for energy optimization

##### 3. Ambient Intelligence Layer
- **Temperature**: External conditions influence heating/cooling efficiency
- **Humidity**: Ambient humidity affects dehumidifier/humidifier optimization
- **Energy Savings**: Up to 20-40% reduction through intelligent ambient use

##### Control Hierarchy (Identical to VPD Perfection)
```
EMERGENCY: Outside broad range → Immediate correction (highest priority)
PRIMARY: Outside perfection range → Active correction
FINE-TUNE: Within perfection range → Approach midpoint with ambient optimization
STABLE: At optimal midpoint → Minimal adjustments (lowest priority)
```

##### Range Relationship Example (EarlyVeg Stage)

| Control Level | Temperature Range | Humidity Range | Behavior |
|---------------|-------------------|----------------|----------|
| **Broad Safety** | [22°C, 26°C] | [65%, 75%] | **Never exceeded** (emergency override) |
| **Perfection Zone** | [23.2°C, 24.8°C] | [63.75%, 76.25%] | **Active control** (primary corrections) |
| **Ambient Enhanced** | [22.7°C, 24.3°C] | [68.75%, 81.25%] | **Optimized targeting** (fine-tune) |
| **Target Midpoint** | 24°C | 70% | **Optimal setpoint** (stable operation) |

#### Comparison with VPD Perfection

| Aspect | VPD Perfection | Closed Environment |
|--------|----------------|-------------------|
| **Range Source** | `plantStages` VPD ranges | `plantStages` temp/humidity ranges |
| **Control Logic** | Identical dual-range system | Identical dual-range system |
| **Ambient Integration** | None (ventilation-based) | **Enhanced** (sealed chamber optimization) |
| **Safety Hierarchy** | Emergency → Primary → Fine-tune → Stable | **Same hierarchy** + O2/CO2 monitoring |
| **Plant Stage Support** | All 8 stages | **Same 8 stages** with temp/humidity bounds |

#### When to Use

**Ideal Applications:**
- **Sealed grow tents/chambers**: Air-tight environments with minimal air exchange
- **Vertical farming pods**: Self-contained growing modules
- **Research chambers**: Precise environmental control requirements
- **Climate-controlled shipping containers**: Mobile grow operations

**Not Recommended For:**
- Traditional grow tents with standard ventilation
- Outdoor growing operations
- Environments without CO2/O2 sensors

#### Benefits

**VPD-Perfection-Level Precision:**
- **Plant stage optimization**: Uses same range-based control as VPD Perfection
- **Dual-range safety**: Broad safety bounds + narrow perfection zones
- **Stage-appropriate targets**: 8 plant stages with scientifically-backed ranges

**Ambient-Enhanced Intelligence:**
- **Energy optimization**: External conditions reduce unnecessary climate control
- **Predictive control**: Ambient trends anticipate environmental needs
- **Smart targeting**: Ambient data adjusts perfection ranges for efficiency

**Sealed Environment Specialization:**
- **CO2 supplementation**: Active maintenance (800-1500 ppm optimal range)
- **O2 safety monitoring**: Emergency ventilation below 19% O2
- **Humidity precision**: Dehumidifier/humidifier control without ventilation
- **Air recirculation**: Optimized for CO2 distribution and thermal uniformity

**Operational Advantages:**
- **Pathogen control**: Sealed environment reduces contamination risk
- **Climate consistency**: Stable conditions regardless of external environment
- **Space efficiency**: Compact, self-contained growing systems
- **Energy savings**: 20-40% reduction through ambient intelligence

#### Integration with Other Systems

- **Compatible with VPD calculations**: Uses VPD for humidity targets
- **Emergency override capable**: Respects existing safety systems
- **Premium analytics available**: Detailed environmental tracking
- **Night operation**: Works with Night VPD Hold energy-saving features

#### Monitoring & Logging

**Real-time Status (Range-Based):**
```
Room: Closed Environment - Temp: 24.2°C (Target: 24.0°C, Range: 23.2-24.8°C)
Room: Humidity: 67.5% (Target: 70.0%, Range: 63.8-76.3%)
Room: CO2: 1200 ppm (Optimal: 800-1500 ppm) | O2: 20.5% (Safe: >19%)
Room: Ambient: 22°C/50% | Perfection zone active with ambient cooling influence
```

**Control State Messages:**
```
[DEBUG] Room: Temperature in range: fine-tuning to 24.0°C (midpoint)
[DEBUG] Room: Humidity too low: targeting 68.0% (ambient-adjusted minimum)
[INFO] Room: Broad safety range active - all conditions within plant stage bounds
[INFO] Room: Ambient enhancement: -0.5°C cooling bias (warm external conditions)
```

**Alert Conditions:**
```
[WARNING] Room: O2 level low: 19.5% (approaching emergency threshold)
[CRITICAL] Room: O2 EMERGENCY - Activating ventilation (O2: 18.2%)
[WARNING] Room: Temperature outside perfection range: 26.1°C > 24.8°C (active cooling)
[CRITICAL] Room: Temperature EMERGENCY: 27.5°C > 26°C broad maximum (maximum cooling)
```

#### Troubleshooting

**Plant Stage Range Issues:**
- **Problem**: Control not activating or unexpected behavior
- **Solution**: Verify plant stage is set and plantStages data exists in datastore
- **Debug**: Check `plantStages` datastore and current plant stage setting

**Ambient Data Problems:**
- **Problem**: No ambient influence or unexpected control decisions
- **Solution**: Verify ambient sensors are active and providing data
- **Debug**: Check `tentData.AmbientTemp` and `tentData.AmbientHum` values

**Range Control Issues:**
- **Problem**: Sticking outside perfection range or constant corrections
- **Solution**: Adjust tolerance settings (temp_tolerance, humidity_tolerance)
- **Debug**: Monitor broad vs. perfection range boundaries in logs

**CO2 Issues:**
- **Problem**: CO2 not maintaining target levels
- **Solution**: Check CO2 sensor calibration and injector capacity
- **Debug**: Verify CO2 device capabilities and injection timing

**O2 Safety:**
- **Problem**: False emergency triggers
- **Solution**: Calibrate O2 sensor and check for air leaks
- **Debug**: Monitor O2 levels during normal operation

**Humidity Control:**
- **Problem**: Humidity oscillations or range violations
- **Solution**: Adjust humidity_tolerance or check device capacity
- **Debug**: Enable detailed humidity control logging and ambient influence tracking

### CO2 Control Switch - Cross-Mode Automation

**Universal CO2 Control**: Independent switch that enables/disables CO2 automation across all tent modes.

#### Overview

The CO2 control switch provides **flexible CO2 automation** that works regardless of the selected tent mode. Users can enable CO2 control in VPD-Perfection, Closed Environment, or any other mode where CO2 management is desired.

**Key Features:**
- **Mode-Agnostic**: Works in VPD-Perfection, Closed Environment, and other modes
- **User Choice**: Enable/disable CO2 automation without changing tent mode
- **Safety Integration**: Respects emergency CO2 thresholds regardless of switch state
- **Energy Conscious**: Only activates CO2 systems when explicitly enabled

#### Configuration

```json
{
  "controlOptions": {
    "co2Control": true   // Master CO2 automation switch
  }
}
```

#### Control Logic by Mode

**VPD-Perfection Mode + CO2 Enabled:**
- CO2 adjustments based on VPD optimization
- Increases CO2 when VPD is too low (needs more transpiration)
- Decreases CO2 when VPD is too high (needs less transpiration)

**Closed Environment Mode + CO2 Enabled:**
- CO2 maintenance within configured target range
- Reads targets from `controlOptionData.co2ppm.minPPM/maxPPM`
- Active CO2 supplementation for photosynthesis optimization

**Other Modes + CO2 Enabled:**
- CO2 control available if device capabilities exist
- Mode-specific CO2 logic applied

**Any Mode + CO2 Disabled:**
- No automatic CO2 adjustments
- CO2 devices available for manual control only
- Emergency CO2 ventilation still functions

#### Datastore Integration

**Control Switch:**
```json
{
  "controlOptions": {
    "co2Control": true   // Master enable/disable
  }
}
```

**CO2 Targets (Closed Environment):**
```json
{
  "controlOptionData": {
    "co2ppm": {
      "minPPM": 400,     // Minimum CO2 for photosynthesis
      "maxPPM": 1800,    // Maximum CO2 for efficiency
      "target": 800      // Target CO2 level
    }
  }
}
```

#### UI Integration

**Control Elements:**
- CO2 automation toggle switch (independent of mode selector)
- CO2 target range sliders (when Closed Environment + CO2 enabled)
- Real-time CO2 level monitoring
- CO2 device status indicators

#### Safety Features

**Emergency Override:**
- CO2 > 2000 ppm triggers forced ventilation
- Functions regardless of CO2 control switch state
- Highest priority safety system

**Device Protection:**
- CO2 injection limited by device capabilities
- Automatic shutdown on sensor failure
- Integration with general emergency systems

#### Benefits

**Flexibility:**
- Use CO2 automation in any control mode
- Switch CO2 on/off without changing core control logic
- Adapt to different growing scenarios

**Energy Efficiency:**
- CO2 systems only active when needed
- Prevents unnecessary CO2 supplementation
- Reduces operational costs

**User Control:**
- Simple enable/disable toggle
- Works across all tent modes
- Independent of other control settings

### 9. Disabled Mode

**Safety/Maintenance Mode**: All automatic control disabled.

#### How It Works
- **Input**: None (no automatic processing)
- **Logic**: Manual control only
- **Actions**: No automatic device adjustments
- **Purpose**: Maintenance, troubleshooting, or manual operation

## Mode Selection and Switching

### Mode Configuration
```python
# tentMode options in datastore
tent_modes = [
    "VPD Perfection",  # Default automatic mode
    "VPD Target",      # Manual VPD targeting
    "Closed Environment",  # Sealed chamber control
    "AI Control",      # Premium AI control
    "PID Control",     # Premium PID control
    "MPC Control",     # Premium MPC control
    "Drying",          # Harvest drying mode
    "Disabled"         # Manual/safety mode
]
```

### Mode Switching Logic
```python
async def selectActionMode(self, data):
    """Select appropriate action mode based on tentMode."""
    tentMode = self.data_store.get("tentMode")

    mode_handlers = {
        "VPD Perfection": self.handle_vpd_perfection,
        "VPD Target": self.handle_targeted_vpd,
        "Closed Environment": self.handle_closed_environment,
        "AI Control": lambda: self.handle_premium_modes({"controllerType": "AI"}),
        "PID Control": lambda: self.handle_premium_modes({"controllerType": "PID"}),
        "MPC Control": lambda: self.handle_premium_modes({"controllerType": "MPC"}),
        "Drying": self.handle_drying,
        "Disabled": self.handle_disabled_mode,
    }

    handler = mode_handlers.get(tentMode)
    if handler:
        await handler()
    else:
        _LOGGER.warning(f"Unknown tent mode: {tentMode}")
```

## Premium Feature Integration

### Subscription Requirements
- **AI Control**: Requires "ai_controllers" feature flag
- **PID Control**: Requires "pid_control" feature flag
- **MPC Control**: Requires "mpc_optimization" feature flag

### Feature Flag Checking
```python
async def check_premium_features(self):
    """Verify premium features are available."""
    if not self.premium_manager or not self.premium_manager.is_logged_in:
        # Fall back to basic modes
        await self._fallback_to_basic_mode()
        return

    available_features = await self.premium_manager.get_available_features()

    if "ai_controllers" not in available_features:
        # Disable AI Control option
        await self._disable_ai_mode()
```

## VPD Target Mode

**Direct Control Mode**: Maintains VPD within a user-defined tolerance range around a specific target value.

### Purpose & Use Cases

VPD Target mode provides **precise, user-controlled VPD maintenance** for specific growing requirements:

- **Research applications** requiring exact VPD conditions
- **Specialized growing techniques** needing specific humidity levels
- **Climate chamber control** with precise environmental targets
- **Custom curing/drying processes** with exact VPD requirements

### Configuration

**Target VPD Value:**
```json
{
  "controlOptions": {
    "vpdTarget": 1.2,      // Target VPD value (kPa)
    "vpdTolerance": 10     // Tolerance percentage (±10%)
  }
}
```

**Calculated Control Range:**
- Target: 1.2 kPa
- Tolerance: 10% = ±0.12 kPa
- Control Range: 1.08 - 1.32 kPa

### Control Logic

```python
async def handle_targeted_vpd(self):
    current_vpd = self.data_store.getDeep("vpd.current")
    target_vpd = self.data_store.getDeep("vpd.targeted")
    tolerance_percent = self.data_store.getDeep("vpd.tolerance")

    # Calculate tolerance range
    tolerance_value = target_vpd * (tolerance_percent / 100)
    min_vpd = target_vpd - tolerance_value
    max_vpd = target_vpd + tolerance_value

    # Control actions
    if current_vpd < min_vpd:
        await self.event_manager.emit("increase_vpd")
    elif current_vpd > max_vpd:
        await self.event_manager.emit("reduce_vpd")
    elif current_vpd != target_vpd:
        await self.event_manager.emit("FineTune_vpd")
    # else: within tolerance and at target - no action needed
```

### Behavior Characteristics

#### Precision Control
- **Exact targeting**: Aims for specific VPD value, not range midpoint
- **User-defined tolerance**: Customizable precision vs stability balance
- **Fine-tuning capability**: Approaches target value within tolerance

#### Control Hierarchy
- **Outside tolerance range**: Active correction (increase/decrease VPD)
- **Within tolerance, off target**: Fine-tune toward exact target
- **At exact target**: Stable operation (minimal adjustments)

### Comparison with VPD Perfection

| Aspect | VPD Perfection | VPD Target |
|--------|----------------|------------|
| **Target Source** | Plant stage ranges | User-defined value |
| **Range Type** | Dual-range system | Single tolerance band |
| **Control Focus** | Plant health optimization | Precision maintenance |
| **Use Case** | Standard growing | Research/specialized |

### Configuration Interface

**Web Interface Controls:**
- Target VPD slider/input (0.5 - 3.0 kPa typical range)
- Tolerance percentage selector (5-25%)
- Real-time VPD monitoring display

**API Configuration:**
```json
{
  "mode": "VPD Target",
  "settings": {
    "targetVPD": 1.2,
    "tolerancePercent": 10
  }
}
```

### Monitoring & Logging

**Real-time Status:**
- Current VPD vs target display
- Tolerance range indicators
- Control action history

**Log Output:**
```
[DEBUG] Room: Current VPD (1.15) is below minimum (1.08). Increasing VPD.
[DEBUG] Room: Current VPD (1.25) is within range but not at Target (1.20). Fine-tuning.
[DEBUG] Room: Current VPD (1.20) is at exact target. No action required.
```

### Integration Notes

- **Works with all dampening features** (adaptive cooldown, buffer zones)
- **Compatible with Night VPD Hold** (respects energy-saving settings)
- **Emergency override capable** (safety systems take precedence)
- **Premium analytics available** (detailed VPD tracking and reporting)

---

## Night VPD Hold - Energy-Saving Control

**Adaptive Control Feature**: Intelligent energy conservation during non-photosynthetic periods.

### Purpose & Logic

Night VPD Hold addresses the biological reality that **VPD control is less critical at night** when plants aren't actively photosynthesizing. This feature provides energy-efficient operation while maintaining plant health.

**Key Insight**: During darkness, plants enter a rest phase where precise VPD optimization is unnecessary, allowing significant energy savings.

### Configuration

```json
{
  "controlOptions": {
    "nightVPDHold": false  // Default: disabled (energy-saving mode)
  }
}
```

### Decision Matrix

| Lighting Condition | nightVPDHold Setting | Behavior |
|-------------------|---------------------|----------|
| **Lights ON** | Any | Normal VPD control (full optimization) |
| **Lights OFF** | `true` (enabled) | **Continuous VPD control** (for special lighting) |
| **Lights OFF** | `false` (disabled) | **Energy-saving fallback** (recommended) |

### Energy-Saving Fallback Logic

When `nightVPDHold = false` and lights are off, the system executes intelligent energy conservation:

#### ✅ Maintains (Essential Functions)
- **Ventilation**: `canExhaust`, `canIntake`, `canVentilate` - Basic air exchange for CO2/O2 balance
- **Basic Monitoring**: Environmental sensors remain active

#### 🔽 Reduces (Energy Conservation)
- **Heating**: `canHeat` → "Reduce" to minimum safe level
- **Cooling**: `canCool` → "Reduce" to prevent over-cooling
- **Humidity**: `canHumidify`, `canDehumidify` → "Reduce" to baseline
- **Climate Control**: `canClimate` → "Reduce" complex climate systems
- **CO2**: `canCO2` → "Reduce" CO2 supplementation

#### ❌ Suspends (Unnecessary)
- **Lighting**: `canLight` - Obviously disabled at night
- **Complex VPD Manipulation**: Advanced humidity/temperature adjustments

### Implementation Details

```python
async def _night_hold_fallback(self, action_map: List):
    """Execute energy-saving night mode fallback."""

    # Define device behavior categories
    excluded_caps = {"canHeat", "canCool", "canHumidify", "canDehumidify",
                    "canLight", "canCO2", "canClimate"}
    reduce_caps = {"canHeat", "canCool", "canHumidify", "canDehumidify",
                  "canCO2", "canClimate"}

    # Allow only essential ventilation actions
    filtered_actions = [
        action for action in action_map
        if action.capability not in excluded_caps
    ]

    # Create reduction actions for climate devices
    reduction_actions = [
        self._create_reduced_action(action)
        for action in action_map
        if action.capability in reduce_caps
    ]

    # Execute combined actions
    final_actions = filtered_actions + reduction_actions
    await self._execute_actions(final_actions)
```

### Benefits

#### ⚡ Energy Conservation
- **30-50% reduction** in climate control energy usage during dark periods
- **Smart device management** prevents unnecessary operation
- **Automatic adaptation** to plant activity cycles

#### 🛡️ Plant Protection
- **Stable environment** with minimum climate intervention
- **Prevents temperature extremes** during rest periods
- **Maintains air quality** through basic ventilation

#### 🎛️ User Control
- **Flexible configuration** for different growing setups
- **Special lighting support** via enable/disable setting
- **Transparent operation** with comprehensive logging

### Use Cases

#### 🌱 Standard Growing (Recommended)
```json
{"nightVPDHold": false}  // Energy-saving mode
```
- Perfect for traditional light/dark cycles
- Maximizes energy efficiency
- Maintains plant health during rest

#### 🔬 Research/Special Lighting
```json
{"nightVPDHold": true}   // Continuous control
```
- For 24/7 lighting setups
- Continuous environmental optimization
- Higher energy consumption but precise control

### Monitoring & Logging

The system provides comprehensive logging of night hold operations:

```
[INFO] Room: VPD Night Hold Not Active - Executing energy-saving fallback
[DEBUG] Room: Executing 3 actions (2 ventilation, 1 reductions)
[INFO] Room: Buffer zones blocked 2 actions to prevent oscillation
```

### Integration with Other Systems

- **Works with all control modes** (VPD Perfection, VPD Target, AI, PID, MPC)
- **Respects emergency overrides** (safety always takes precedence)
- **Compatible with dampening system** (prevents device wear)
- **Premium feature integration** (advanced analytics available)

---

## VPD Tolerance Configuration

### Understanding VPD Tolerance

VPD tolerance determines how precisely the system maintains target Vapor Pressure Deficit ranges. Smaller tolerances provide more precise control but may cause device oscillation. Larger tolerances provide stable operation but allow more VPD variation.

### Recommended Settings

#### By Environment Type
- **Home Grow Tents (1-4 plants)**: 8-12% tolerance
- **Small Greenhouses (10-50 plants)**: 10-15% tolerance
- **Commercial Greenhouses**: 12-18% tolerance
- **Research/Lab**: 5-10% tolerance
- **Outdoor/Field**: 15-25% tolerance

#### By Plant Stage
- **Germination/Cuttings**: 5-8% (most sensitive to VPD stress)
- **Early Veg**: 8-12% (building resilience)
- **Mid/Late Veg**: 10-15% (more tolerant)
- **Early Flower**: 8-12% (bud development sensitivity)
- **Mid/Late Flower**: 12-18% (fruit swelling tolerance)
- **Late Flower/Harvest**: 15-20% (least sensitive)

### Default Setting
- **System Default**: 10% tolerance
- **Why 10%**: Balances precision with stability for most environments

### Warning Signs of Incorrect Tolerance

#### Too Low (< 5%)
- Devices constantly turning on/off ("always starting" behavior)
- Higher energy consumption
- Premature equipment wear
- Unnecessary climate control activation

#### Too High (> 20%)
- VPD varies significantly from targets
- Potential plant stress or slowed growth
- Reduced control precision

### Adjusting Tolerance

1. **Start with default** (10%)
2. **Monitor for oscillation** - if devices cycle frequently, increase tolerance
3. **Check VPD stability** - if VPD varies too much, decrease tolerance
4. **Consider environment** - larger spaces need higher tolerance
5. **Plant sensitivity** - adjust based on current growth stage

### Advanced Configuration

For precise control, tolerance can be adjusted in the system configuration:

```json
{
  "controlOptions": {
    "vpdTolerance": 10  // Percentage (5-20 recommended)
  }
}
```

---

## Performance and Monitoring

### Mode Performance Metrics
- **VPD Stability**: How well target VPD is maintained
- **Response Time**: Time to correct environmental deviations
- **Device Utilization**: Frequency and duration of device activations
- **Energy Efficiency**: Resource usage optimization

### Mode Health Monitoring
```python
async def monitor_mode_performance(self):
    """Monitor control mode effectiveness."""
    metrics = {
        "vpd_target_achieved": self.calculate_vpd_accuracy(),
        "response_time": self.measure_control_response_time(),
        "device_stability": self.assess_device_stability(),
        "energy_usage": self.track_energy_consumption()
    }

    # Store metrics for analytics
    await self.store_performance_metrics(metrics)
```

## Troubleshooting Control Modes

### VPD Perfection Issues
- **Problem**: Not maintaining target ranges
- **Solution**: Check plant stage configuration and sensor calibration
- **Debug**: Enable VPD calculation logging

### VPD Target Issues
- **Problem**: Not maintaining user-defined target
- **Solution**: Verify target VPD value and tolerance settings
- **Debug**: Check `vpd.targeted` and `vpd.tolerance` datastore values

### Premium Mode Issues
- **Problem**: Modes not available
- **Solution**: Verify Premium subscription and API connectivity
- **Debug**: Check feature flags and authentication status

### Night VPD Hold Issues
- **Problem**: Unexpected behavior during light/dark transitions
- **Solution**: Check `nightVPDHold` setting and lighting state detection
- **Debug**: Enable dampening action logging to see fallback execution

### Mode Switching Issues
- **Problem**: Mode changes not taking effect
- **Solution**: Verify tentMode datastore value and event processing
- **Debug**: Enable mode manager logging

---

**Last Updated**: December 24, 2025
**Version**: 2.0 (Premium Integration)
**Status**: Production Ready