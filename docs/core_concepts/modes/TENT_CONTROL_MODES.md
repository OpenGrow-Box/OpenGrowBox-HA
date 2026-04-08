# Tent Control Modes - Complete Implementation Guide

## Overview

The OpenGrowBox system operates under different **tentMode** configurations that determine the primary control logic and automation behavior. Each mode provides different levels of automation, from manual control to advanced AI-driven optimization.

## TentMode Options

### 1. VPD Perfection Mode

**Primary automated mode for optimal plant growth**

#### When Active
- **Trigger**: `tentMode = "VPD Perfection"`
- **Logic**: Maintains optimal VPD ranges based on plant growth stages
- **Automation Level**: High - continuous environmental adaptation

#### Implementation Flow

```mermaid
graph TD
    A[VPD Sensor Reading] --> B[Calculate Current VPD]
    B --> C[Get Plant Stage from DataStore]
    C --> D[Retrieve Stage VPD Ranges]
    D --> E{Compare VPD to Ranges}

    E --> F[VPD < Min Range]
    E --> G[VPD > Max Range]
    E --> H[VPD in Range ≠ Perfect]
    E --> I[VPD = Perfect]

    F --> J[emit 'increase_vpd']
    G --> K[emit 'reduce_vpd']
    H --> L[emit 'FineTune_vpd']
    I --> M[No Action Required]

    J --> N[Dampening & Action Processing]
    K --> N
    L --> N

    N --> O[Device Control Execution]
    O --> P[DataRelease to Premium API]
```

#### Key Code Implementation

```python
# OGBModeManager.handle_vpd_perfection()
async def handle_vpd_perfection(self):
    """VPD Perfection mode - maintain optimal ranges for plant stage."""

    # Retrieve current environmental values
    currentVPD = self.data_store.getDeep("vpd.current")
    plant_stage = self.data_store.get("plantStage")

    # Get stage-specific VPD targets (from plantStages datastore)
    perfectionVPD = self.data_store.getDeep("vpd.perfection")      # Target VPD
    perfectionMinVPD = self.data_store.getDeep("vpd.perfectMin")   # Minimum acceptable
    perfectionMaxVPD = self.data_store.getDeep("vpd.perfectMax")   # Maximum acceptable

    # Validate data availability
    if any(val is None for val in [currentVPD, perfectionMinVPD, perfectionMaxVPD, perfectionVPD]):
        _LOGGER.warning(f"{self.room}: VPD values not initialized. Skipping control.")
        return

    # Determine action based on VPD position
    capabilities = self.data_store.getDeep("capabilities")

    if currentVPD < perfectionMinVPD:
        await self.event_manager.emit("increase_vpd", capabilities)
    elif currentVPD > perfectionMaxVPD:
        await self.event_manager.emit("reduce_vpd", capabilities)
    elif currentVPD != perfectionVPD:
        await self.event_manager.emit("FineTune_vpd", capabilities)
```

### 2. VPD Target Mode

**User-defined VPD control with tolerance bands**

#### When Active
- **Trigger**: `tentMode = "VPD Target"`
- **Logic**: Maintains user-set VPD target with configurable tolerance
- **Automation Level**: Medium-High - user-configured automation

#### Implementation Flow

```mermaid
graph TD
    A[VPD Sensor Reading] --> B[Get Current VPD]
    B --> C[Get User Target VPD]
    C --> D[Get Tolerance Percentage]
    D --> E[Calculate Tolerance Range]
    E --> F{Min ≤ Current ≤ Max?}

    F --> G[Within Tolerance]
    F --> H[Below Minimum]
    F --> I[Above Maximum]

    G --> J{At Exact Target?}
    H --> K[emit 'increase_vpd']
    I --> L[emit 'reduce_vpd']

    J --> M[No Action]
    J --> N[emit 'FineTune_vpd']

    K --> O[Action Processing Pipeline]
    L --> O
    N --> O
```

#### Key Implementation

```python
# OGBModeManager.handle_targeted_vpd()
async def handle_targeted_vpd(self):
    """VPD Target mode with user-defined tolerance."""

    currentVPD_raw = self.data_store.getDeep("vpd.current")
    targetedVPD_raw = self.data_store.getDeep("vpd.targeted")  # User-set target
    tolerance_raw = self.data_store.getDeep("vpd.tolerance")   # Tolerance %

    # Validate inputs
    if any(val is None for val in [currentVPD_raw, targetedVPD_raw, tolerance_raw]):
        return

    currentVPD = float(currentVPD_raw)
    targetedVPD = float(targetedVPD_raw)
    tolerance_percent = float(tolerance_raw)

    # Calculate tolerance range
    tolerance_value = targetedVPD * (tolerance_percent / 100)
    min_vpd = targetedVPD - tolerance_value
    max_vpd = targetedVPD + tolerance_value

    capabilities = self.data_store.getDeep("capabilities")

    if currentVPD < min_vpd:
        await self.event_manager.emit("increase_vpd", capabilities)
    elif currentVPD > max_vpd:
        await self.event_manager.emit("reduce_vpd", capabilities)
    elif currentVPD != targetedVPD:
        await self.event_manager.emit("FineTune_vpd", capabilities)
```

### 3. Closed Environment Mode

**Sealed chamber control without traditional ventilation**

#### When Active
- **Trigger**: `tentMode = "Closed Environment"`
- **Logic**: Temperature and humidity control using dehumidifiers/humidifiers without exhaust/intake
- **Automation Level**: High - ambient-enhanced range control

#### Key Features
- **No ventilation**: Uses dehumidifiers/humidifiers instead of exhaust/intake fans
- **Ambient enhancement**: Adjusts targets based on external conditions
- **CO2 control**: Optional CO2 supplementation (requires `controlOptions.co2Control`)
- **O2 safety**: Emergency ventilation if O2 drops below 19%

#### Implementation

```python
async def handle_closed_environment(self):
    """Closed Environment mode for sealed chambers."""
    
    # Get plant stage ranges (same as VPD Perfection)
    plant_stage = self.data_store.get("plantStage")
    stage_data = self.data_store.getDeep(f"plantStages.{plant_stage}")
    
    # Calculate perfection ranges with ambient enhancement
    ambient_temp = self.data_store.getDeep("tentData.AmbientTemp")
    ambient_hum = self.data_store.getDeep("tentData.AmbientHum")
    
    # Apply ambient influence (30% for temp, 40% for humidity)
    temp_influence = (ambient_temp - stage_data["minTemp"]) * 0.3
    hum_influence = (ambient_hum - stage_data["minHumidity"]) * 0.4
    
    # Adjusted targets
    target_temp_min = stage_data["minTemp"] + temp_influence
    target_temp_max = stage_data["maxTemp"] + temp_influence
    target_hum_min = stage_data["minHumidity"] + hum_influence
    target_hum_max = stage_data["maxHumidity"] + hum_influence
    
    # Control logic (similar to VPD Perfection)
    current_temp = self.data_store.getDeep("tentData.temperature")
    current_hum = self.data_store.getDeep("tentData.humidity")
    
    if current_temp < target_temp_min:
        await self.event_manager.emit("increase_temperature")
    elif current_temp > target_temp_max:
        await self.event_manager.emit("decrease_temperature")
    
    if current_hum < target_hum_min:
        await self.event_manager.emit("increase_humidity")
    elif current_hum > target_hum_max:
        await self.event_manager.emit("decrease_humidity")
    
    # Optional CO2 control
    if self.data_store.getDeep("controlOptions.co2Control"):
        await self.maintain_co2()
```

#### CO2 Control (Optional)

```python
async def maintain_co2(self):
    """Maintain CO2 levels in closed environment."""
    current_co2 = self.data_store.getDeep("sensors.co2")
    co2_min = self.data_store.getDeep("controlOptionData.co2ppm.minPPM", 400)
    co2_max = self.data_store.getDeep("controlOptionData.co2ppm.maxPPM", 1800)
    
    if current_co2 < co2_min:
        await self.inject_co2()
    elif current_co2 > co2_max:
        await self.reduce_co2()
    
    # Emergency high CO2
    if current_co2 > 2000:
        await self.emergency_ventilation()
```

#### O2 Safety Monitoring

```python
async def monitor_o2_safety(self):
    """Monitor O2 levels for safety in sealed environment."""
    current_o2 = self.data_store.getDeep("sensors.o2")
    
    if current_o2 < 19.0:  # Critical level
        await self.emergency_ventilation()
    elif current_o2 < 20.0:  # Warning level
        _LOGGER.warning(f"O2 level low: {current_o2}%")
```

#### When to Use
- **Sealed grow chambers**: Air-tight environments with minimal air exchange
- **Vertical farming pods**: Self-contained growing modules
- **Research chambers**: Precise environmental control requirements
- **Climate-controlled containers**: Mobile grow operations

#### Hardware Requirements
- **Required**: Temperature, humidity sensors
- **Required for CO2**: CO2 sensor, CO2 injector/controller
- **Required for O2 safety**: O2 sensor, emergency ventilation capability
- **Climate control**: Dehumidifier, humidifier (no exhaust/intake)

### 4. Script Mode

**Custom user-defined automation scripts**

#### When Active
- **Trigger**: `tentMode = "Script Mode"`
- **Logic**: Executes user-defined automation scripts
- **Automation Level**: User-defined - complete flexibility

#### Implementation

```python
async def handle_script_mode(self):
    """Script Mode - execute user-defined automation."""
    
    # Get active script configuration
    script_config = self.data_store.get("activeScript")
    
    if not script_config:
        _LOGGER.warning("Script Mode active but no script configured")
        return
    
    # Execute script steps
    for step in script_config.get("steps", []):
        await self.execute_script_step(step)
        
        # Wait for step duration or condition
        if "duration" in step:
            await asyncio.sleep(step["duration"])
        elif "condition" in step:
            await self.wait_for_condition(step["condition"])
```

#### Example Script Configuration

```json
{
  "activeScript": {
    "name": "Custom Light Schedule",
    "steps": [
      {
        "action": "set_light",
        "device": "light.main_grow_light",
        "value": 100,
        "duration": 43200
      },
      {
        "action": "set_light",
        "device": "light.main_grow_light",
        "value": 0,
        "duration": 43200
      }
    ]
  }
}
```

### 5. Drying Mode

**Specialized mode for harvest drying and curing**

#### When Active
- **Trigger**: `tentMode = "Drying"`
- **Logic**: Environmental control optimized for post-harvest processing
- **Automation Level**: High - specialized drying algorithms

#### Drying Mode Variants

#### 3.1 ElClassico Drying
```python
async def handle_ElClassico(self, phaseConfig):
    """Traditional drying with gradual environmental changes."""

    # Phase-based temperature and humidity targets
    days_elapsed = self.get_days_into_drying()

    if days_elapsed < 3:
        # Phase 1: High humidity drying
        target_temp, target_hum = 20, 60
    elif days_elapsed < 7:
        # Phase 2: Medium humidity drying
        target_temp, target_hum = 18, 55
    else:
        # Phase 3: Low humidity curing
        target_temp, target_hum = 16, 50

    await self.apply_drying_conditions(target_temp, target_hum)
```

#### 3.2 DewBased Drying
```python
async def handle_DewBased(self, phaseConfig):
    """Dew point depression-based drying control."""

    current_temp = self.get_current_temperature()
    current_dew = self.get_current_dewpoint()
    dew_depression = current_temp - current_dew

    target_depression = phaseConfig.get("targetDewDepression", 7)

    if dew_depression < target_depression:
        # Increase temperature or decrease humidity
        await self.adjust_dew_depression_up()
    elif dew_depression > target_depression:
        # Decrease temperature or increase humidity
        await self.adjust_dew_depression_down()
```

#### 3.3 5DayDry Mode
```python
async def handle_5DayDry(self, phaseConfig):
    """Accelerated 5-day drying schedule."""

    day = min(self.get_days_into_drying() + 1, 5)

    day_targets = {
        1: {"temp": 22, "hum": 65, "vent": "low"},
        2: {"temp": 20, "hum": 60, "vent": "medium"},
        3: {"temp": 18, "hum": 55, "vent": "medium"},
        4: {"temp": 16, "hum": 50, "vent": "high"},
        5: {"temp": 14, "hum": 45, "vent": "high"}
    }

    targets = day_targets.get(day, day_targets[5])
    await self.apply_day_targets(targets)
```

### 6. AI Control Mode (Premium)

**Machine learning-driven environmental optimization**

#### When Active
- **Trigger**: `tentMode = "AI Control"`
- **Logic**: AI algorithms analyze patterns and predict optimal conditions
- **Automation Level**: Maximum - continuous learning and adaptation

#### Implementation Flow

```mermaid
graph TD
    A[Environmental Data Collection] --> B[Historical Pattern Analysis]
    B --> C[AI Model Prediction]
    C --> D[Generate Control Recommendations]
    D --> E[Validate Safety Constraints]
    E --> F[Apply AI Adjustments]
    F --> G[Monitor Results]
    G --> H[Update Learning Model]
    H --> I[DataRelease for Analytics]
```

#### Key Implementation

```python
async def handle_premium_modes(self, data):
    """Handle premium AI control modes."""

    if not self.premium_manager or not self.premium_manager.is_logged_in:
        # Fallback to VPD Perfection
        await self.handle_vpd_perfection()
        return

    controller_type = data.get("controllerType", "AI")

    if controller_type == "AI":
        # Send comprehensive data to Premium API
        await self.event_manager.emit("DataRelease", True)
        # Request AI control recommendations
        await self.event_manager.emit("AIActions", data)

    elif controller_type == "PID":
        await self.event_manager.emit("PIDActions", data)

    elif controller_type == "MPC":
        await self.event_manager.emit("MPCActions", data)
```

### 7. PID Control Mode (Premium)

**Proportional-Integral-Derivative feedback control**

#### Implementation

```python
class PIDController:
    """PID controller implementation."""

    def __init__(self, kp, ki, kd, setpoint):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.setpoint = setpoint
        self.prev_error = 0
        self.integral = 0

    def calculate(self, current_value, dt):
        error = self.setpoint - current_value

        # P term
        p_term = self.kp * error

        # I term
        self.integral += error * dt
        i_term = self.ki * self.integral

        # D term
        derivative = (error - self.prev_error) / dt
        d_term = self.kd * derivative

        self.prev_error = error
        return p_term + i_term + d_term
```

### 8. MPC Control Mode (Premium)

**Model Predictive Control with optimization horizon**

#### Implementation

```python
async def handle_mpc_mode(self):
    """Model Predictive Control implementation."""

    # Define prediction horizon
    horizon = 12  # Future time steps

    # Get current system state
    current_state = self.get_system_state()

    # Predict future conditions
    predictions = await self.predict_future_conditions(horizon)

    # Optimize control trajectory
    optimal_trajectory = await self.optimize_trajectory(current_state, predictions)

    # Apply first control step
    await self.apply_control_step(optimal_trajectory[0])
```

### 9. Disabled Mode

**Safety mode with all automation disabled**

#### When Active
- **Trigger**: `tentMode = "Disabled"`
- **Logic**: No automatic environmental control
- **Automation Level**: None - manual operation only

#### Implementation

```python
async def handle_disabled_mode(self):
    """Disabled mode - manual control only."""

    # Log mode status
    await self.event_manager.emit(
        "LogForClient",
        {"Name": self.room, "Mode": "Disabled"}
    )

    # Ensure no automated actions are taken
    # System operates in manual mode only
    return None
```

## Mode Selection and Validation

### Mode Transition Logic

```python
async def selectActionMode(self, Publication):
    """Route mode execution based on tentMode."""

    controlOption = self.data_store.get("mainControl")
    if controlOption not in ["HomeAssistant", "Premium"]:
        return False

    tentMode = Publication.currentMode if hasattr(Publication, 'currentMode') else None

    mode_handlers = {
        "VPD Perfection": self.handle_vpd_perfection,
        "VPD Target": self.handle_targeted_vpd,
        "Closed Environment": self.handle_closed_environment,
        "Script Mode": self.handle_script_mode,
        "Drying": self.handle_drying,
        "AI Control": lambda: self.handle_premium_modes({"controllerType": "AI"}),
        "PID Control": lambda: self.handle_premium_modes({"controllerType": "PID"}),
        "MPC Control": lambda: self.handle_premium_modes({"controllerType": "MPC"}),
        "Disabled": self.handle_disabled_mode,
    }

    handler = mode_handlers.get(tentMode)
    if handler:
        await handler()
    else:
        _LOGGER.debug(f"{self.name}: Unknown tentMode '{tentMode}'")
```

### Mode Compatibility Validation

```python
def validate_mode_compatibility(self, tentMode: str) -> bool:
    """Validate if selected mode is compatible with current system state."""

    # Check premium requirements
    premium_modes = ["AI Control", "PID Control", "MPC Control"]
    if tentMode in premium_modes:
        if not self.premium_manager or not self.premium_manager.is_logged_in:
            return False

    # Check sensor requirements
    if tentMode in ["VPD Perfection", "VPD Target"]:
        if not self.has_vpd_sensors():
            return False

    # Check device capabilities
    required_capabilities = self.get_mode_capabilities(tentMode)
    if not self.has_required_capabilities(required_capabilities):
        return False

    return True
```

## Configuration and DataStore Integration

### tentMode Storage

```python
# DataStore structure for tentMode
tent_mode_config = {
    "tentMode": "VPD Perfection",  # Current active mode

    # VPD Perfection mode data
    "vpd": {
        "current": 1.2,           # Current VPD reading
        "perfection": 1.2,        # Target VPD for current stage
        "perfectMin": 1.0,        # Minimum acceptable VPD
        "perfectMax": 1.4,        # Maximum acceptable VPD
        "targeted": 1.2,          # User-set target (VPD Target mode)
        "tolerance": 10           # Tolerance percentage (VPD Target mode)
    },

    # Drying mode data
    "drying": {
        "currentDryMode": "ElClassico",
        "mode_start_time": "2025-12-24T10:00:00Z"
    },

    # Plant stage and species data
    "plantSpecies": "Cannabis",   # Current plant species
    "plantStage": "MidFlower",    # Current plant growth stage
    "plantStages": {              # Detailed stage configurations (species-specific)
        "MidFlower": {
            "vpdRange": [0.90, 1.70],
            "minTemp": 21,
            "maxTemp": 25,
            "minHumidity": 38,
            "maxHumidity": 52,
            "minEC": 1.8,
            "maxEc": 2.4,
            "minPh": 5.8,
            "maxPh": 6.2,
            "minLight": 70,
            "maxLight": 90,
            "minCo2": 1000,
            "maxCo2": 1500
        }
    }
}
```

### Premium Mode Integration

```python
# Premium subscription validation
async def validate_premium_mode_access(self, tentMode: str) -> bool:
    """Validate premium subscription for advanced modes."""

    premium_modes = ["AI Control", "PID Control", "MPC Control"]

    if tentMode not in premium_modes:
        return True  # Non-premium mode

    if not self.premium_manager:
        return False

    # Check subscription status
    subscription = await self.premium_manager.get_subscription_status()

    # Validate feature access
    if tentMode == "AI Control":
        return subscription.get("ai_controllers", False)
    elif tentMode == "PID Control":
        return subscription.get("pid_control", False)
    elif tentMode == "MPC Control":
        return subscription.get("mpc_optimization", False)

    return False
```

## Mode Performance Monitoring

### Mode Effectiveness Tracking

```python
async def track_mode_performance(self, tentMode: str):
    """Track performance metrics for active mode."""

    performance_data = {
        "mode": tentMode,
        "timestamp": datetime.now().isoformat(),
        "vpd_stability": self.calculate_vpd_stability(),
        "environmental_control": self.assess_environmental_control(),
        "energy_efficiency": self.calculate_energy_efficiency(),
        "user_satisfaction": self.get_user_feedback_score()
    }

    # Store performance data
    await self.store_performance_metrics(performance_data)

    # Trigger mode optimization if needed
    if self.should_optimize_mode(performance_data):
        await self.suggest_mode_optimization(tentMode, performance_data)
```

### Automatic Mode Switching

```python
async def evaluate_mode_switching(self):
    """Evaluate if mode switching would improve performance."""

    current_mode = self.data_store.get("tentMode")
    current_performance = self.get_current_performance_metrics()

    # Evaluate alternative modes
    alternative_modes = self.get_available_modes()

    best_alternative = None
    best_score = current_performance["overall_score"]

    for mode in alternative_modes:
        if mode != current_mode and self.validate_mode_compatibility(mode):
            predicted_performance = await self.predict_mode_performance(mode)
            if predicted_performance["overall_score"] > best_score:
                best_alternative = mode
                best_score = predicted_performance["overall_score"]

    # Switch to better performing mode
    if best_alternative:
        await self.initiate_mode_switch(best_alternative, "performance_optimization")
```

## Services

### set_select_options

**Dynamically update select entity options at runtime.**

This service allows updating the available options of any OpenGrowBox select entity. It's primarily used internally when changing plant species to update the PlantStage select options.

#### Service Data

```json
{
  "entity_id": "select.ogb_plantstage_myroom",
  "options": ["Germination", "Clones", "EarlyVeg", "MidVeg", "LateVeg"]
}
```

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `entity_id` | string | Yes | The select entity to update (e.g., `select.ogb_plantstage_room`) |
| `options` | list | Yes | List of new options to set |

#### Behavior

- Replaces all existing options with the new list
- If the current selection is not in the new options, it resets to the first option
- Automatically updates the Home Assistant state

#### Example: Plant Species Change

When switching from Cannabis (8 stages) to Lettuce (5 stages):

```python
# User selects "Lettuce" in OGB_PlantSpecies
# System automatically updates PlantStage options:

await hass.services.async_call(
    "opengrowbox",
    "set_select_options",
    {
        "entity_id": "select.ogb_plantstage_myroom",
        "options": ["Germination", "Clones", "EarlyVeg", "MidVeg", "LateVeg"]
    }
)

# PlantStage select now shows only 5 options instead of 8
```

#### Related Services

- `opengrowbox.add_select_options` - Add options to existing list
- `opengrowbox.remove_select_options` - Remove specific options

---

**Last Updated**: January 2025
**Version**: 2.2 (Added Plant Species, Closed Environment, Script Mode, set_select_options service)
**Status**: Production Ready