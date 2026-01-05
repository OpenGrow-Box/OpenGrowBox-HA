# OpenGrowBox Special Lights System

## Overview

The OpenGrowBox integration supports specialized light control beyond standard on/off scheduling. Special lights have unique timing behaviors optimized for plant biology.

## Light Types

| Type | Label | Purpose | Timing Behavior |
|------|-------|---------|-----------------|
| **Normal** | `light` | Main grow light | Full light cycle (existing behavior) |
| **Far Red** | `light_fr` | Phytochrome control | Start + End of light cycle only |
| **UV** | `light_uv` | Stress response | Middle of light cycle only |
| **Blue Spectrum** | `light_blue` | Vegetative growth | Variable intensity throughout day |
| **Red Spectrum** | `light_red` | Flowering promotion | Variable intensity throughout day |

---

## Operation Modes

All special lights now support **four operation modes** that can be configured via Home Assistant entities:

| Mode | Description | Use Case |
|------|-------------|----------|
| **Schedule** | Time-based automatic control (default) | Standard operation with optimized timing |
| **Always On** | ON whenever main lights are ON | Continuous supplemental lighting |
| **Always Off** | Disabled, never activates automatically | Temporarily disable without removing device |
| **Manual** | Only responds to manual commands | Full manual control via HA automations |

### Mode Selection Entities

Each special light type has a corresponding select entity in Home Assistant:

- `select.ogb_light_farred_mode_{room}`
- `select.ogb_light_uv_mode_{room}`
- `select.ogb_light_blue_mode_{room}`
- `select.ogb_light_red_mode_{room}`

---

## Setup Instructions

### Step 1: Label Your Devices in Home Assistant

1. Go to **Settings -> Devices & Services -> Devices**
2. Find your light device
3. Click on it and select **Add Label**
4. Add the appropriate label:

| Light Type | Label to Add |
|------------|--------------|
| Main grow light | `light` |
| Far Red LED bar | `light_fr` or `light_farred` |
| UV/UVB light | `light_uv` or `light_uvb` or `light_uva` |
| Blue spectrum channel | `light_blue` |
| Red spectrum channel | `light_red` |

### Step 2: Restart Home Assistant

After labeling, restart HA or reload the OpenGrowBox integration. The devices will be detected and initialized with the correct timing behavior.

### Step 3: Verify Detection

Check your HA logs for messages like:
```
LightFarRed('farred_bar' in FlowerTent) - Far Red scheduler started
LightUV('uv_panel' in FlowerTent) - UV scheduler started
```

### Step 4: Configure Mode (Optional)

By default, all special lights use **Schedule** mode. To change:

1. Find the mode select entity in HA (e.g., `select.ogb_light_farred_mode_flowertent`)
2. Set to your desired mode:
   - **Schedule** - Default timing behavior
   - **Always On** - Continuous operation with main lights
   - **Always Off** - Disabled
   - **Manual** - No automatic control

---

## Far Red Light (`light_fr`)

### Purpose

Far Red light (730nm) is used for:
- **Emerson Effect**: Brief Far Red exposure at lights-on enhances photosynthesis efficiency
- **Phytochrome Conversion**: At lights-off, Far Red accelerates Pfr->Pr conversion, signaling "night" to the plant faster
- **Reduced Stretch**: Proper Far Red timing can reduce internodal stretching

### Mode Behaviors

| Mode | Behavior |
|------|----------|
| **Schedule** | ON for configured duration at start/end of light cycle |
| **Always On** | ON continuously while main lights are ON |
| **Always Off** | Never activates automatically |
| **Manual** | Only responds to manual commands |

### Default Timing (Schedule Mode)

```
Light ON ---------------------------------------------------------------- Light OFF
    |                                                                        |
    |-- FR ON (15 min) --|                              |-- FR ON (15 min) --|
    |                    |                              |                    |
  06:00              06:15                          17:45                18:00
```

### Configuration

```yaml
# Via DataStore (set programmatically or via service call)
specialLights:
  farRed:
    mode: "Schedule"        # "Schedule", "Always On", "Always Off", "Manual"
    startDurationMinutes: 15    # Minutes at start of light cycle
    endDurationMinutes: 15      # Minutes at end of light cycle
    intensity: 100              # Percent intensity
```

### Home Assistant Entities

| Entity | Type | Description |
|--------|------|-------------|
| `select.ogb_light_farred_mode_{room}` | Select | Operation mode |
| `select.ogb_light_farred_enabled_{room}` | Select | Enable/Disable |
| `number.ogb_light_farred_start_duration_{room}` | Number | Start phase duration (min) |
| `number.ogb_light_farred_end_duration_{room}` | Number | End phase duration (min) |
| `number.ogb_light_farred_intensity_{room}` | Number | Intensity (%) |

### Events

| Event | Description |
|-------|-------------|
| `FarRedSettingsUpdate` | Update settings at runtime |

**Example payload:**
```python
{
    "device": "farred_bar",  # or None for all FR lights
    "mode": "Always On",
    "startDurationMinutes": 20,
    "endDurationMinutes": 10,
    "intensity": 100
}
```

---

## UV Light (`light_uv`)

### Purpose

UV light (UVA 315-400nm, UVB 280-315nm) is used for:
- **Trichome Production**: UV stress increases resin/trichome development
- **Pathogen Control**: UV exposure can reduce mold and mildew
- **Compact Growth**: Prevents excessive stretching
- **Flavonoid Production**: Enhances terpene and flavonoid profiles

### Mode Behaviors

| Mode | Behavior |
|------|----------|
| **Schedule** | ON during middle of light cycle with delay/buffer |
| **Always On** | ON continuously while main lights are ON |
| **Always Off** | Never activates automatically |
| **Manual** | Only responds to manual commands |

### Default Timing (Schedule Mode)

UV activates during the middle portion of the light cycle to:
1. Allow plants to "wake up" before UV stress
2. Provide recovery time before lights-off

```
Light ON ---------------------------------------------------------------- Light OFF
    |                                                                        |
    |-- 2h delay --|-------------- UV ACTIVE --------------|-- 2h buffer --|
    |              |                                       |               |
  06:00          08:00                                   16:00           18:00
```

### Configuration

```yaml
specialLights:
  uv:
    mode: "Schedule"           # "Schedule", "Always On", "Always Off", "Manual"
    delayAfterStartMinutes: 120   # Minutes to wait after lights-on (default: 120)
    stopBeforeEndMinutes: 120     # Minutes before lights-off to stop (default: 120)
    maxDurationHours: 6           # Maximum hours of UV per day (default: 6)
    intensity: 100                # Percent intensity if dimmable (default: 100)
```

### Home Assistant Entities

| Entity | Type | Description |
|--------|------|-------------|
| `select.ogb_light_uv_mode_{room}` | Select | Operation mode |
| `select.ogb_light_uv_enabled_{room}` | Select | Enable/Disable |
| `number.ogb_light_uv_delay_start_{room}` | Number | Delay after light-on (min) |
| `number.ogb_light_uv_stop_before_end_{room}` | Number | Stop before light-off (min) |
| `number.ogb_light_uv_max_duration_{room}` | Number | Max daily duration (hours) |
| `number.ogb_light_uv_intensity_{room}` | Number | Intensity (%) |

### Safety Features

- **Daily Exposure Limit**: Tracks cumulative exposure, stops at `maxDurationHours` (Schedule mode only)
- **Auto-Off**: Turns off if main lights turn off
- **Buffer Zones**: Never activates immediately at lights-on or lights-off (Schedule mode)

### Events

| Event | Description |
|-------|-------------|
| `UVSettingsUpdate` | Update settings at runtime |

**Example payload:**
```python
{
    "device": "uv_panel",
    "mode": "Schedule",
    "delayAfterStartMinutes": 90,
    "maxDurationHours": 4,
    "intensity": 75
}
```

---

## Spectrum Lights (`light_blue`, `light_red`)

### Purpose

Spectrum-specific lights allow mimicking natural sunlight color temperature changes:

| Spectrum | Morning | Midday | Evening | Plant Effect |
|----------|---------|--------|---------|--------------|
| **Blue** (400-500nm) | High (80%) | Medium (60%) | Low (30%) | Compact growth, thick stems |
| **Red** (600-700nm) | Low (30%) | Medium (60%) | High (80%) | Flowering, stretching |

This mimics natural conditions where:
- Morning sun has more blue (cooler)
- Evening sun has more red (warmer)

### Mode Behaviors

| Mode | Behavior |
|------|----------|
| **Schedule** | Time-based intensity profiles (morning/midday/evening) |
| **Always On** | ON at fixed intensity while main lights are ON |
| **Always Off** | Never activates automatically |
| **Manual** | Only responds to manual commands |

### Default Intensity Profiles (Schedule Mode)

**Blue Spectrum:**
```
100% |
 80% |-------+
 60% |       +-------------------+
 30% |                           +-------
  0% +-----------------------------------
     | Morning |      Midday      | Evening |
```

**Red Spectrum:**
```
100% |
 80% |                           +-------
 60% |       +-------------------+
 30% +-------+
  0% +-----------------------------------
     | Morning |      Midday      | Evening |
```

### Phase Distribution

By default:
- **Morning Phase**: First 25% of light cycle
- **Midday Phase**: Middle 50% of light cycle  
- **Evening Phase**: Last 25% of light cycle

### Plant Stage Adjustments (Schedule Mode)

The system automatically adjusts intensity based on plant stage:

| Stage | Blue Adjustment | Red Adjustment |
|-------|-----------------|----------------|
| Vegetative | +10% all phases | -10% evening |
| Flowering | -10% morning | +10% midday/evening |

### Configuration

```yaml
specialLights:
  spectrum:
    blue:
      mode: "Schedule"            # "Schedule", "Always On", "Always Off", "Manual"
      morningIntensity: 80
      middayIntensity: 60
      eveningIntensity: 30
      alwaysOnIntensity: 100      # Intensity for Always On mode
      smoothTransitions: true     # Gradual intensity changes
    red:
      mode: "Schedule"
      morningIntensity: 30
      middayIntensity: 60
      eveningIntensity: 80
      alwaysOnIntensity: 100
      smoothTransitions: true
```

### Home Assistant Entities

| Entity | Type | Description |
|--------|------|-------------|
| `select.ogb_light_blue_mode_{room}` | Select | Blue operation mode |
| `select.ogb_light_blue_enabled_{room}` | Select | Enable/Disable |
| `select.ogb_light_red_mode_{room}` | Select | Red operation mode |
| `select.ogb_light_red_enabled_{room}` | Select | Enable/Disable |

### Events

| Event | Description |
|-------|-------------|
| `SpectrumSettingsUpdate` | Update settings at runtime |
| `PlantStageChange` | Auto-adjusts profiles (Schedule mode) |

**Example payload:**
```python
{
    "blue": {
        "mode": "Always On",
        "alwaysOnIntensity": 75
    }
}
# Or for Schedule mode:
{
    "spectrum": "red",
    "mode": "Schedule",
    "morningIntensity": 40,
    "eveningIntensity": 90,
    "smoothTransitions": false
}
```

---

## API Reference

### Getting Light Status

Each special light class provides a `get_status()` method:

```python
# Far Red status
{
    "device_name": "farred_bar",
    "device_type": "LightFarRed",
    "mode": "Schedule",           # NEW: Current mode
    "is_active": true,
    "current_phase": "start",     # "start", "end", "always_on", or null
    "is_running": true,
    "intensity": 100,
    "start_duration_minutes": 15,
    "end_duration_minutes": 15,
    "light_on_time": "06:00:00",
    "light_off_time": "18:00:00"
}

# UV status
{
    "device_name": "uv_panel",
    "device_type": "LightUV",
    "mode": "Schedule",           # NEW: Current mode
    "is_active": true,
    "current_phase": "schedule",  # "schedule", "always_on", or null
    "is_running": true,
    "daily_exposure_minutes": 180,
    "max_duration_hours": 6,
    "delay_after_start_minutes": 120,
    "stop_before_end_minutes": 120,
    "intensity_percent": 100
}

# Spectrum status
{
    "device_name": "blue_channel",
    "device_type": "LightBlue",
    "spectrum_type": "blue",
    "mode": "Schedule",           # NEW: Current mode
    "is_active": true,
    "is_running": true,
    "current_intensity": 65,
    "current_phase": "midday",    # "morning", "midday", "evening", "always_on"
    "morning_intensity": 80,
    "midday_intensity": 60,
    "evening_intensity": 30,
    "always_on_intensity": 100,   # NEW: Intensity for Always On mode
    "smooth_transitions": true
}
```

### Event Logging

All special lights emit `LogForClient` events with `OGBLightAction` data:

```python
OGBLightAction(
    Name="FlowerTent",
    Device="farred_bar",
    Type="LightFarRed",
    Action="ON",  # or "OFF"
    Message="FarRed activated (Always On mode)",  # NEW: Mode in message
    Voltage=100,
    Dimmable=false,
    SunRise=false,
    SunSet=false
)
```

---

## Use Cases

### Use Case 1: Far Red Always On

For growers who want continuous Far Red supplementation during the entire light period:

1. Set `select.ogb_light_farred_mode_{room}` to **"Always On"**
2. Far Red will now be ON whenever main lights are ON
3. No start/end timing - continuous supplementation

### Use Case 2: Disable UV During Veg

To temporarily disable UV lights during vegetative growth:

1. Set `select.ogb_light_uv_mode_{room}` to **"Always Off"**
2. UV will never activate automatically
3. Change back to "Schedule" when entering flower

### Use Case 3: Manual Spectrum Control

For integration with custom automations:

1. Set `select.ogb_light_blue_mode_{room}` to **"Manual"**
2. OGB will not control the light automatically
3. Use HA automations to control `light.blue_channel` directly

### Use Case 4: Continuous Blue During Clone/Seedling Stage

For clones and seedlings that benefit from high blue light:

1. Set `select.ogb_light_blue_mode_{room}` to **"Always On"**
2. Set intensity via configuration
3. Blue light runs continuously during light hours

---

## Troubleshooting

### Light Not Detected as Special Type

1. Verify the label is exactly correct (case-insensitive):
   - `light_fr`, `light_farred`, `farred`, `far_red`
   - `light_uv`, `light_uvb`, `light_uva`, `uvlight`
   - `light_blue`, `blue_led`, `bluelight`
   - `light_red`, `red_led`, `redlight`

2. Check logs for device identification:
   ```
   Device 'my_light' identified via label as LightFarRed
   ```

3. Restart HA after adding labels

### Far Red Not Activating (Schedule Mode)

1. Check if main lights are on (`islightON` must be true)
2. Verify `lightOnTime` and `lightOffTime` are set in DataStore
3. Check current time is within activation windows
4. Verify mode is set to "Schedule" (not "Always Off" or "Manual")

### UV Hitting Daily Limit

The UV light tracks daily exposure (Schedule mode only). Check status:
```python
status = uv_light.get_status()
print(f"Today: {status['daily_exposure_minutes']} min")
print(f"Max: {status['max_duration_hours']} hours")
```

Reset happens automatically at midnight.

**Note:** In "Always On" mode, there is NO daily limit - UV runs continuously.

### Spectrum Intensity Not Changing

1. Verify mode is set to "Schedule" (Always On uses fixed intensity)
2. Check `smoothTransitions` setting
3. Check if device `isDimmable` is true
4. Look for logs showing intensity adjustments

### Mode Not Taking Effect

1. Verify the mode entity value is exactly: `"Schedule"`, `"Always On"`, `"Always Off"`, or `"Manual"`
2. Check logs for mode change messages:
   ```
   farred_bar: Mode changed from 'Schedule' to 'Always On'
   ```
3. Wait up to 30 seconds for the scheduler to apply the change

### Entity "Missing or Currently Not Available"

This error occurs when OGB cannot find or communicate with the light entity. Common causes:

1. **Entity is unavailable at startup**: The light device was offline when Home Assistant started
   - Solution: Restart HA after the device comes online
   
2. **Incorrect entity naming**: OGB cannot find a matching entity
   - Solution: Check that your light entity exists in HA with a recognizable name
   - Example: `light.lightred` should exist in HA Developer Tools -> States
   
3. **Entity state is "unavailable" or "unknown"**: Device is disconnected or not responding
   - Check device power and network connection
   - Verify the device appears in HA Integrations
   
4. **Label mismatch**: Device is labeled but entity structure doesn't match expectations
   - Ensure the light entity starts with `light.` or `switch.`
   - Check logs for: `No switches/entities found!`

**Debug Steps:**

1. Check HA logs for these messages:
   ```
   WARNING - light_red: No switches/entities found!
   WARNING - light_red: Entity 'light.light_red' is currently unavailable
   ```

2. Verify entity exists in HA:
   - Go to Developer Tools -> States
   - Search for your light entity (e.g., `light.lightred`, `light.light_red`)
   - Ensure state is not "unavailable"

3. Check OGB identified the device:
   ```
   Device 'lightred' identified via label as LightRed
   ```

4. If entity was recovered automatically, you'll see:
   ```
   INFO - lightred: Found entity 'light.lightred' in HA. Adding to switches list.
   ```

---

## Example Setup: Full Spectrum Flower Room

```
Devices:
+-- main_light (label: light)           -> Normal 12/12 schedule
+-- farred_bar (label: light_fr)        -> Mode: Always On (continuous FR)
+-- uv_panel (label: light_uv)          -> Mode: Schedule (4h mid-cycle)
+-- blue_channel (label: light_blue)    -> Mode: Schedule (morning-heavy)
+-- red_channel (label: light_red)      -> Mode: Schedule (evening-heavy)

Timeline (12h light cycle, 06:00-18:00):
06:00 - Main ON, FarRed ON (Always On), Blue 80%, Red 30%
08:00 - UV ON, Blue 70%, Red 40%
09:00 - Blue 60%, Red 60%
14:00 - UV OFF (6h limit)
15:00 - Blue 50%, Red 70%
17:00 - Blue 30%, Red 80%
18:00 - All OFF (including FarRed)
```

---

## File Locations

```
custom_components/opengrowbox/OGBController/
+-- OGBDevices/
|   +-- Light.py           # Normal light (existing)
|   +-- LightFarRed.py     # Far Red light with mode support
|   +-- LightUV.py         # UV light with mode support
|   +-- LightSpectrum.py   # Blue/Red spectrum lights with mode support
+-- managers/core/
|   +-- OGBConfigurationManager.py   # Mode handlers
+-- data/OGBParams/
|   +-- OGBParams.py       # DEVICE_TYPE_MAPPING with labels
+-- OGBDeviceManager.py    # Device class selection

select.py                  # Mode select entities
number.py                  # Duration/intensity number entities
```

---

## Changelog

### v1.5.1 - Entity Validation Improvements
- Added `_validate_entity_availability()` method to all special light classes
- Automatic entity recovery when switches list is empty
- Improved error messages for missing/unavailable entities
- Better logging for troubleshooting entity issues
- Added troubleshooting section for "missing or not available" errors

### v1.5.0 - Mode System Added
- Added **Mode** select entities for all special light types
- New modes: Schedule, Always On, Always Off, Manual
- Far Red: Can now run continuously with "Always On" mode
- UV: Can run without daily limits in "Always On" mode
- Spectrum: Fixed intensity option with "Always On" mode
- Improved logging with mode information
- Updated documentation with mode configuration
