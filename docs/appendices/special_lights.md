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

## Setup Instructions

### Step 1: Label Your Devices in Home Assistant

1. Go to **Settings → Devices & Services → Devices**
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

---

## Far Red Light (`light_fr`)

### Purpose

Far Red light (730nm) is used for:
- **Emerson Effect**: Brief Far Red exposure at lights-on enhances photosynthesis efficiency
- **Phytochrome Conversion**: At lights-off, Far Red accelerates Pfr→Pr conversion, signaling "night" to the plant faster
- **Reduced Stretch**: Proper Far Red timing can reduce internodal stretching

### Default Timing

```
Light ON ─────────────────────────────────────────────── Light OFF
    │                                                        │
    ├── FR ON (15 min) ──┤                    ├── FR ON (15 min) ──┤
    │                    │                    │                    │
 06:00              06:15                  17:45               18:00
```

### Configuration

```yaml
# Via DataStore (set programmatically or via service call)
specialLights:
  farRed:
    startDuration: 15    # Minutes at start of light cycle
    endDuration: 15      # Minutes at end of light cycle
```

### Events

| Event | Description |
|-------|-------------|
| `FarRedSettingsUpdate` | Update settings at runtime |

**Example payload:**
```python
{
    "device": "farred_bar",  # or None for all FR lights
    "startDuration": 20,
    "endDuration": 10
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

### Default Timing

UV activates during the middle portion of the light cycle to:
1. Allow plants to "wake up" before UV stress
2. Provide recovery time before lights-off

```
Light ON ─────────────────────────────────────────────── Light OFF
    │                                                        │
    ├── 2h delay ──├──────── UV ACTIVE ────────├── 2h buffer ──┤
    │              │                           │               │
 06:00          08:00                       16:00           18:00
```

### Configuration

```yaml
specialLights:
  uv:
    delayAfterStart: 120   # Minutes to wait after lights-on (default: 120)
    stopBeforeEnd: 120     # Minutes before lights-off to stop (default: 120)
    maxDuration: 6         # Maximum hours of UV per day (default: 6)
    intensity: 100         # Percent intensity if dimmable (default: 100)
```

### Safety Features

- **Daily Exposure Limit**: Tracks cumulative exposure, stops at `maxDuration`
- **Auto-Off**: Turns off if main lights turn off
- **Buffer Zones**: Never activates immediately at lights-on or lights-off

### Events

| Event | Description |
|-------|-------------|
| `UVSettingsUpdate` | Update settings at runtime |

**Example payload:**
```python
{
    "device": "uv_panel",
    "delayAfterStart": 90,
    "maxDuration": 4,
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

### Default Intensity Profiles

**Blue Spectrum:**
```
100% ┤
 80% ┼───────┐
 60% ┤       └───────────────────────┐
 30% ┤                               └───────
  0% ┼─────────────────────────────────────────
     │ Morning │      Midday        │ Evening │
```

**Red Spectrum:**
```
100% ┤
 80% ┤                               ┌───────
 60% ┤       ┌───────────────────────┘
 30% ┼───────┘
  0% ┼─────────────────────────────────────────
     │ Morning │      Midday        │ Evening │
```

### Phase Distribution

By default:
- **Morning Phase**: First 25% of light cycle
- **Midday Phase**: Middle 50% of light cycle  
- **Evening Phase**: Last 25% of light cycle

### Plant Stage Adjustments

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
      morningIntensity: 80
      middayIntensity: 60
      eveningIntensity: 30
      smoothTransitions: true   # Gradual intensity changes
    red:
      morningIntensity: 30
      middayIntensity: 60
      eveningIntensity: 80
      smoothTransitions: true
```

### Events

| Event | Description |
|-------|-------------|
| `SpectrumSettingsUpdate` | Update settings at runtime |
| `PlantStageChange` | Auto-adjusts profiles |

**Example payload:**
```python
{
    "spectrum": "blue",  # or "red"
    "morningIntensity": 90,
    "eveningIntensity": 20,
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
    "is_active": true,
    "current_phase": "start",  # "start", "end", or null
    "is_running": true,
    "start_duration_minutes": 15,
    "end_duration_minutes": 15,
    "light_on_time": "06:00:00",
    "light_off_time": "18:00:00"
}

# UV status
{
    "device_name": "uv_panel",
    "device_type": "LightUV",
    "is_active": true,
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
    "is_active": true,
    "is_running": true,
    "current_intensity": 65,
    "current_phase": "midday",
    "morning_intensity": 80,
    "midday_intensity": 60,
    "evening_intensity": 30,
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
    Message="FarRed START phase activated",
    Voltage=100,
    Dimmable=false,
    SunRise=false,
    SunSet=false
)
```

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

### Far Red Not Activating

1. Check if main lights are on (`islightON` must be true)
2. Verify `lightOnTime` and `lightOffTime` are set in DataStore
3. Check current time is within activation windows

### UV Hitting Daily Limit

The UV light tracks daily exposure. Check status:
```python
status = uv_light.get_status()
print(f"Today: {status['daily_exposure_minutes']} min")
print(f"Max: {status['max_duration_hours']} hours")
```

Reset happens automatically at midnight.

### Spectrum Intensity Not Changing

1. Verify `smoothTransitions` setting
2. Check if device `isDimmable` is true
3. Look for logs showing intensity adjustments

---

## Example Setup: Full Spectrum Flower Room

```
Devices:
├── main_light (label: light)           → Normal 12/12 schedule
├── farred_bar (label: light_fr)        → 15min at start/end
├── uv_panel (label: light_uv)          → 4h mid-cycle
├── blue_channel (label: light_blue)    → Morning-heavy profile
└── red_channel (label: light_red)      → Evening-heavy profile

Timeline (12h light cycle, 06:00-18:00):
06:00 ─ Main ON, FarRed ON, Blue 80%, Red 30%
06:15 ─ FarRed OFF
08:00 ─ UV ON, Blue 70%, Red 40%
09:00 ─ Blue 60%, Red 60%
14:00 ─ UV OFF (6h limit)
15:00 ─ Blue 50%, Red 70%
17:00 ─ Blue 30%, Red 80%
17:45 ─ FarRed ON
18:00 ─ All OFF
```

---

## File Locations

```
custom_components/opengrowbox/OGBController/
├── OGBDevices/
│   ├── Light.py           # Normal light (existing)
│   ├── LightFarRed.py     # Far Red light
│   ├── LightUV.py         # UV light
│   └── LightSpectrum.py   # Blue/Red spectrum lights
├── OGBParams/
│   └── OGBParams.py       # DEVICE_TYPE_MAPPING with labels
└── OGBDeviceManager.py    # Device class selection
```
