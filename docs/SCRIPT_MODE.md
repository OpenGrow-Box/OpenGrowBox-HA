# OpenGrowBox Script Mode

## Overview

Script Mode allows advanced users to create fully customizable automation scripts for their grow environment. It supports both a simple Domain-Specific Language (DSL) and Python scripting.

## Features

- **Full DataStore Access**: Read and write all OpenGrowBox data
- **Device Control**: Control all devices (exhaust, heater, lights, etc.)
- **Condition Logic**: IF/THEN/ELSE statements
- **Time-based Triggers**: Schedule actions based on time
- **Variable Support**: Store and use custom variables
- **Python Support**: Full Python scripting for advanced users
- **Templates**: Start with pre-built templates
- **Safety Limits**: Built-in protection against runaway scripts

## Configuration

### Option 1: YAML Configuration

Add to your `configuration.yaml`:

```yaml
opengrowbox:
  rooms:
    - name: "MyGrowRoom"
      script_mode:
        enabled: true
        type: "dsl"  # or "python"
        cycle_interval: 30  # seconds between executions
        
        # Direct script code
        script: |
          // Read sensor data
          READ vpd FROM vpd.current
          READ temp FROM tentData.temperature
          READ humidity FROM tentData.humidity
          
          // VPD Control
          IF vpd > 1.2 THEN
            LOG "VPD too high"
            CALL exhaust.increase
            CALL dehumidifier.increase
          ENDIF
          
          IF vpd < 0.8 THEN
            LOG "VPD too low"
            CALL exhaust.reduce
            CALL humidifier.increase
          ENDIF
          
          // Temperature safety
          IF temp > 28 THEN
            LOG "Temperature high!" LEVEL=warning
            CALL cooler.increase
            CALL exhaust.increase
          ENDIF
          
          IF temp < 20 THEN
            LOG "Temperature low"
            CALL heater.increase
          ENDIF
```

### Option 2: Python Script

For advanced users who need full control:

```yaml
opengrowbox:
  rooms:
    - name: "MyGrowRoom"
      script_mode:
        enabled: true
        type: "python"
        script: |
          # Read current values
          vpd = READ("vpd.current")
          temp = READ("tentData.temperature")
          humidity = READ("tentData.humidity")
          is_light_on = READ("isPlantDay.islightON")
          
          # Get capabilities
          caps = READ("capabilities")
          
          # VPD Control with safety checks
          if vpd > 1.2:
              LOG("VPD too high, activating exhaust")
              if caps.get("canExhaust", {}).get("state"):
                  CALL("exhaust", "increase")
          
          elif vpd < 0.8:
              LOG("VPD too low")
              if caps.get("canHumidify", {}).get("state"):
                  CALL("humidifier", "increase")
          
          # Temperature control
          if temp > 28:
              LOG("Temperature critical!", level="warning")
              CALL("cooler", "increase", priority="high")
          
          elif temp < 20 and vpd < 1.0:  # Only heat if VPD not too high
              CALL("heater", "increase")
          
          # Day/Night cycle
          if is_light_on:
              CALL("light", "on")
          else:
              CALL("light", "off")
          
          # Store custom value
          SET("workData.myCalculation", temp * 0.8)
```

### Option 3: Terminal Configuration (Interactive)

Configure Script Mode directly via the OGB Terminal:

#### Enable Script Mode
```
ogb$ tentmode Script Mode
[SUCCESS] Mode changed to Script Mode
```

#### Check Script Status
```
ogb$ script status
[RESPONSE] Script Mode: Active
           Type: dsl
           Last execution: 14:32:15
           Status: Running
```

#### Edit Script
```
ogb$ script edit
Entering script editor. Type 'END' on a new line to finish.
> READ vpd FROM vpd.current
> IF vpd > 1.2 THEN
>   CALL exhaust.increase
> ENDIF
> END
[RESPONSE] ✓ Script captured (4 lines). Use 'script save' to store.
```

#### Load Template
```
ogb$ script template basic_vpd_control
[RESPONSE] ✓ Template 'basic_vpd_control' loaded.
           Use 'script show' to view.
```

Available templates:
- `basic_vpd_control` - Simple VPD management
- `advanced_environment` - Full environmental control with safety checks

#### Save Script
```
ogb$ script save
[RESPONSE] ✓ Script saved to DataStore.
           Script Mode will use this script on next cycle.
```

#### Show Current Script
```
ogb$ script show
[RESPONSE] Current Script:
-----------
READ vpd FROM vpd.current
READ vpd_max FROM vpd.perfectMax
IF vpd > vpd_max THEN
  CALL exhaust.increase
ENDIF
-----------
```

#### Validate Script
```
ogb$ script validate
[RESPONSE] ✓ Script validation passed.
           Syntax: OK
           Commands: 5
           Warnings: 0
```

#### Test Run (One-time execution)
```
ogb$ script run
[RESPONSE] Executing script...
           LOG: VPD check: 1.25
           CALL: exhaust.increase
           ✓ Execution completed in 0.3s
```

#### Clear Script
```
ogb$ script clear
[RESPONSE] ⚠ Script cleared. Script Mode disabled until new script saved.
```

#### Debug DataStore
```
ogb$ datastore list
[RESPONSE] Available DataStore paths:
           - vpd.current
           - vpd.perfection
           - tentData.temperature
           - tentData.humidity
           - isPlantDay.islightON
           - capabilities.canHeat.state
           ... (50+ more paths)

ogb$ datastore get vpd.current
[RESPONSE] Value: 1.15 kPa
```

### Option 4: Load from File

```yaml
opengrowbox:
  rooms:
    - name: "MyGrowRoom"
      script_mode:
        enabled: true
        type: "dsl"
        file: "/config/opengrowbox_scripts/my_grow_script.txt"
```

## How Script Mode Works

### Execution Model (Like VPD Perfection)

Script Mode uses a **stateless, cyclic execution model**:

1. **Script is stored** in DataStore (`controlOptions.scriptMode`)
2. **ModeManager calls** `handle_script_mode()` cyclically (every 30s by default)
3. **Script is loaded** from DataStore on each execution
4. **Script executes** completely (all commands)
5. **Fresh start** on next cycle (no state persistence between runs)

```
Cycle 1: Load Script → Execute → Done
   ↓ (30 seconds)
Cycle 2: Load Script → Execute → Done
   ↓ (30 seconds)
Cycle 3: Load Script → Execute → Done
```

### Why Stateless?

- **Reliability**: No background threads that can crash
- **Predictability**: Each run starts fresh
- **Safety**: Max execution time enforced per cycle
- **Debugging**: Easy to test individual executions

### Variable Lifecycle

Variables are **not persisted** between cycles:

```dsl
// Cycle 1
SET counter = 0          // counter = 0
SET counter = counter + 1  // counter = 1
// End of cycle: counter is lost

// Cycle 2
// counter doesn't exist anymore!
READ counter FROM workData.counter  // Must read from DataStore
```

**To persist values**, use SET/READ with DataStore:
```dsl
// Save value
SET workData.myCounter = counter

// Later cycle: read it back
READ counter FROM workData.myCounter
```

## DSL Syntax Reference

### Commands

#### READ
Read a value from the DataStore:
```
READ <variable> FROM <datastore.path>
```
Example:
```
READ vpd FROM vpd.current
READ temp FROM tentData.temperature
```

#### SET
Write a value to the DataStore:
```
SET <datastore.path> = <value>
```
Example:
```
SET workData.myValue = 25.5
SET controlOptions.mySetting = "enabled"
```

#### CALL
Execute a device action:
```
CALL <device>.<action> [WITH <parameters>]
```
Devices: exhaust, intake, ventilation, heater, cooler, humidifier, dehumidifier, light, co2, climate
Actions: increase, reduce, on, off, set_value, eval

Example:
```
CALL exhaust.increase
CALL heater.increase WITH priority=high
CALL light.on WITH brightness=80
```

#### IF/THEN/ELSE/ENDIF
Conditional logic:
```
IF <condition> THEN
  ...
ELSEIF <condition> THEN
  ...
ELSE
  ...
ENDIF
```

Example:
```
IF vpd > 1.2 THEN
  CALL exhaust.increase
ELSEIF vpd < 0.8 THEN
  CALL exhaust.reduce
ELSE
  LOG "VPD in range"
ENDIF
```

#### EMIT
Emit an event:
```
EMIT <event_name> [WITH <data>]
```
Example:
```
EMIT MyCustomEvent WITH data=vpd
```

#### LOG
Log a message:
```
LOG "<message>" [LEVEL=<level>]
```
Levels: debug, info, warning, error

Example:
```
LOG "Starting ventilation" LEVEL=debug
LOG "Critical temperature!" LEVEL=error
```

### Variables

Store values in variables:
```
READ vpd FROM vpd.current
SET my_threshold = 1.2

IF vpd > my_threshold THEN
  ...
ENDIF
```

### Time-based Conditions

```
IF TIME > "08:00" THEN
  CALL light.on
ENDIF

IF TIME BETWEEN "20:00" AND "08:00" THEN
  CALL light.off
ENDIF
```

### Mathematical Operations

```
SET avg_temp = (temp_max + temp_min) / 2
SET vpd_threshold = 1.0 + 0.2
```

## Available DataStore Paths

### Sensor Data (Read-Only)
- `vpd.current` - Current VPD value
- `vpd.perfection` - Target VPD value
- `vpd.perfectMin` - Minimum acceptable VPD
- `vpd.perfectMax` - Maximum acceptable VPD
- `tentData.temperature` - Current temperature
- `tentData.humidity` - Current humidity
- `tentData.maxTemp` - Maximum temperature setting
- `tentData.minTemp` - Minimum temperature setting
- `tentData.AmbientTemp` - Ambient temperature
- `tentData.AmbientHum` - Ambient humidity

### State Information (Read-Only)
- `isPlantDay.islightON` - Boolean, true if lights are on
- `isPlantDay.lightOnTime` - Light on time (HH:MM)
- `isPlantDay.lightOffTime` - Light off time (HH:MM)
- `plantStage` - Current plant stage (EarlyVeg, MidVeg, LateVeg, etc.)
- `tentMode` - Current mode (VPD Perfection, Script Mode, etc.)
- `mainControl` - Control mode (HomeAssistant, Premium, Disabled)

### Control Options (Read/Write)
- `controlOptions.co2Control` - Boolean, CO2 control enabled
- `controlOptions.vpdLightControl` - Boolean, VPD controls lights
- `controlOptions.nightVPDHold` - Boolean, maintain VPD at night
- `controlOptions.ownWeights` - Boolean, use custom weights
- `controlOptionData.weights.temp` - Temperature weight
- `controlOptionData.weights.hum` - Humidity weight

### Device Capabilities (Read-Only)
- `capabilities.canHeat.state` - Heater available
- `capabilities.canCool.state` - Cooler available
- `capabilities.canExhaust.state` - Exhaust available
- `capabilities.canHumidify.state` - Humidifier available
- `capabilities.canDehumidify.state` - Dehumidifier available
- `capabilities.canLight.state` - Light available
- `capabilities.canCO2.state` - CO2 available

### Crop Steering (Read/Write)
- `CropSteering.CropPhase` - Current phase (p0, p1, p2, p3)
- `CropSteering.vwc_current` - Current VWC
- `CropSteering.ec_current` - Current EC
- `CropSteering.Active` - Boolean, crop steering active

## Python API Reference

When using Python scripts, you have access to these helper functions:

### READ(path: str) -> Any
Read a value from DataStore:
```python
vpd = READ("vpd.current")
temp = READ("tentData.temperature")
```

### SET(path: str, value: Any)
Write a value to DataStore:
```python
SET("workData.myValue", 25.5)
```

### CALL(device: str, action: str, **kwargs)
Execute a device action:
```python
CALL("exhaust", "increase")
CALL("heater", "increase", priority="high")
```

### EMIT(event: str, data: dict = None)
Emit an event:
```python
EMIT("MyEvent", {"value": vpd})
```

### LOG(message: str, level: str = "info")
Log a message:
```python
LOG("Message")
LOG("Warning!", level="warning")
LOG("Error!", level="error")
```

### TIME -> str
Current time as "HH:MM":
```python
current_time = TIME
```

### VARS -> dict
Access script variables:
```python
VARS["my_var"] = 123
value = VARS.get("my_var", 0)
```

## Templates

### Template 1: Basic VPD Control
```yaml
script_mode:
  template: basic_vpd_control
```

Automatically handles VPD control with exhaust and humidity devices.

### Template 2: Advanced Environment
```yaml
script_mode:
  template: advanced_environment
```

Full environmental control with safety checks and day/night cycle.

## Safety Limits

Script Mode has built-in safety limits:

- **Max Execution Time**: 5 seconds per run
- **Max Instructions**: 1000 per execution
- **Cycle Interval**: Minimum 10 seconds between executions
- **Forbidden Operations**: File I/O, network access, system commands blocked

## Best Practices

1. **Always check device availability** before calling actions:
   ```
   READ caps FROM capabilities
   IF caps.canExhaust.state THEN
     CALL exhaust.increase
   ENDIF
   ```

2. **Use proper logging** for debugging:
   ```
   LOG "VPD check: {vpd}" LEVEL=debug
   ```

3. **Handle edge cases**:
   ```
   IF vpd IS NOT NULL THEN
     ...
   ENDIF
   ```

4. **Test in dry-run mode** first (coming soon)

5. **Start simple** and add complexity gradually

## Examples

### Example 1: Simple VPD Controller
```
// Simple VPD control
READ vpd FROM vpd.current
READ vpd_max FROM vpd.perfectMax
READ vpd_min FROM vpd.perfectMin

IF vpd > vpd_max THEN
  LOG "Reducing VPD"
  CALL exhaust.increase
  CALL dehumidifier.increase
ENDIF

IF vpd < vpd_min THEN
  LOG "Increasing VPD"
  CALL exhaust.reduce
  CALL humidifier.increase
ENDIF
```

### Example 2: Temperature Safety with VPD Check
```
READ temp FROM tentData.temperature
READ vpd FROM vpd.current
READ temp_min FROM tentData.minTemp

// Only heat if VPD is not too high
IF temp < temp_min THEN
  IF vpd < 1.2 THEN
    CALL heater.increase
  ELSE
    LOG "Cannot heat - VPD too high" LEVEL=warning
  ENDIF
ENDIF
```

### Example 3: Day/Night Cycle with CO2
```
READ is_light_on FROM isPlantDay.islightON
READ co2_control FROM controlOptions.co2Control

IF is_light_on THEN
  CALL light.on
  
  IF co2_control THEN
    CALL co2.increase
  ENDIF
ELSE
  CALL light.off
  
  IF co2_control THEN
    CALL co2.reduce
  ENDIF
ENDIF
```

## Troubleshooting

### Script not executing
- Check if `enabled: true` is set
- Verify script syntax
- Check logs for compilation errors

### Variables not working
- Ensure variables are defined before use
- Check variable scope (per execution)

### Actions not executing
- Verify device capabilities exist
- Check if devices are available in HA
- Review action logs for blocked actions

### Performance issues
- Reduce script complexity
- Increase cycle_interval
- Remove unnecessary calculations

## Support

For help with Script Mode:
1. Check the logs for error messages
2. Start with a template and modify gradually
3. Test conditions in Developer Tools first
4. Use LOG statements for debugging

## Roadmap

Coming soon:
- Visual script editor in OGB Terminal
- Dry-run mode for testing
- Import/export scripts
- Community script sharing
- Performance metrics
