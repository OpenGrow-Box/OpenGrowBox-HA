# FridgeGrow / Plantalytix Integration

## Overview

This document describes the integration of FridgeGrow 2.0 and Plantalytix devices into OpenGrowBox.

FridgeGrow devices are recognized via **Home Assistant labels** and controlled through a dedicated `FridgeGrowDevice` class that is completely isolated from the existing device code.

## Supported Devices

| Device | Sensors | Outputs |
|--------|---------|---------|
| **FridgeGrow 2.0** | Temperature, Humidity, CO2 | Heater, Dehumidifier, Light, CO2, Fan (internal/external/backwall) |
| **Plantalytix AIR** | Temperature, Humidity, Day/Night | Fan |
| **Plantalytix LIGHT** | Temperature, Humidity | Light |
| **Plantalytix Smart Socket** | Temperature, Humidity, CO2 | Relay (Switch) |

## Architecture

### Design Principles

1. **Complete Isolation**: All FridgeGrow code is contained in `OGBDevices/FridgeGrow/` module
2. **Label-based Recognition**: Devices are identified by HA labels, not entity names
3. **Reuse OGB Logic**: Uses existing capability system and event-based control flow
4. **Minimal Changes**: Only 2-3 lines added to existing files

### File Structure

```
OGBController/OGBDevices/FridgeGrow/
├── __init__.py              # Module exports
└── FridgeGrowDevice.py      # Main device class
```

### Changes to Existing Files

| File | Change |
|------|--------|
| `OGBParams.py` | Add `"FridgeGrow"` to `DEVICE_TYPE_MAPPING` |
| `OGBDeviceManager.py` | Add import + entry in `get_device_class()` |

## How It Works

### 1. Device Recognition Flow

```
HA Entity: number.fridgegrow_abc123_heater
Labels: ["fridgegrow", "heater"]
Area: "GrowRoom"
        │
        ▼
RegistryListener.get_filtered_entities_with_value()
        │
        ▼
OGBDeviceManager.identify_device()
        │
        ├─ Checks labels against DEVICE_TYPE_MAPPING
        ├─ Finds "fridgegrow" label → detected_type = "FridgeGrow"
        │
        ▼
FridgeGrowDevice instantiated
        │
        ├─ _identify_output_type() finds "heater" label
        ├─ output_type = "heater"
        │
        ▼
deviceInit()
        │
        ├─ identifyCapabilities() → registers "canHeat"
        ├─ _register_event_listeners() → listens for "Increase Heater"
        │
        ▼
Device ready for control
```

### 2. Control Flow (Runtime)

```
VPD too low (needs heat)
        │
        ▼
VPDManager → ActionManager
        │
        ▼
emit("Increase Heater")
        │
        ▼
FridgeGrowDevice.increaseAction()
        │
        ├─ Calculates new value (0-100%)
        │
        ▼
turn_on(percentage=X)
        │
        ├─ Converts to FridgeGrow range (0-1)
        │
        ▼
hass.services.async_call("number", "set_value", value=X/100)
```

## User Setup Guide

### Step 1: Get FridgeGrow Entities into Home Assistant

FridgeGrow devices communicate via MQTT. You need to get them into HA as entities first:

**Option A: Manual MQTT Configuration (configuration.yaml)**
```yaml
mqtt:
  sensor:
    - name: "FridgeGrow Temperature"
      unique_id: "fridgegrow_ABC123_temperature"
      state_topic: "devices/ABC123/sensors"
      value_template: "{{ value_json.temperature }}"
      device_class: temperature
      unit_of_measurement: "°C"
    
    - name: "FridgeGrow Humidity"
      unique_id: "fridgegrow_ABC123_humidity"
      state_topic: "devices/ABC123/sensors"
      value_template: "{{ value_json.humidity }}"
      device_class: humidity
      unit_of_measurement: "%"

  number:
    - name: "FridgeGrow Heater"
      unique_id: "fridgegrow_ABC123_heater"
      command_topic: "devices/ABC123/control/heater"
      state_topic: "devices/ABC123/outputs"
      value_template: "{{ value_json.heater }}"
      min: 0
      max: 1
      step: 0.01

  switch:
    - name: "FridgeGrow Dehumidifier"
      unique_id: "fridgegrow_ABC123_dehumidifier"
      command_topic: "devices/ABC123/control/dehumidifier"
      state_topic: "devices/ABC123/outputs"
      value_template: "{{ value_json.dehumidifier }}"
      payload_on: "1"
      payload_off: "0"
```

**Option B: Use Node-RED Flow** (from FridgeGrow repository)

**Option C: Use a Community HA Add-on** (if available)

### Step 2: Assign Labels in Home Assistant

Each FridgeGrow output entity needs **two labels**:

1. `fridgegrow` - Identifies it as a FridgeGrow device
2. Output type label - `heater`, `dehumidifier`, `light`, `co2`, `exhaust`, `ventilation`

**Example Labels:**

| Entity | Labels |
|--------|--------|
| `number.fridgegrow_abc123_heater` | `fridgegrow`, `heater` |
| `switch.fridgegrow_abc123_dehumidifier` | `fridgegrow`, `dehumidifier` |
| `number.fridgegrow_abc123_light` | `fridgegrow`, `light` |
| `switch.fridgegrow_abc123_co2` | `fridgegrow`, `co2` |
| `number.fridgegrow_abc123_fan_backwall` | `fridgegrow`, `exhaust` |
| `number.fridgegrow_abc123_fan_internal` | `fridgegrow`, `ventilation` |

**How to add labels in HA:**
1. Go to Settings → Devices & Services → Entities
2. Find your FridgeGrow entity
3. Click on it → Edit (pencil icon)
4. Add labels: `fridgegrow` and the output type (e.g., `heater`)
5. Save

### Step 3: Assign to Room/Area

Assign the FridgeGrow device/entities to your grow room area in HA.

### Step 4: Restart OGB

After labeling, restart OpenGrowBox. The FridgeGrow devices will be automatically recognized.

## Output Type Configuration

### Supported Output Types

| Label | OGB Capability | Entity Type | Value Range | Events |
|-------|---------------|-------------|-------------|--------|
| `heater` | canHeat | number | 0-1 | Increase/Reduce Heater |
| `dehumidifier` | canDehumidify | switch | on/off | Increase/Reduce Dehumidifier |
| `humidifier` | canHumidify | switch | on/off | Increase/Reduce Humidifier |
| `light` | canLight | number | 0-1 | toggleLight |
| `co2` | canCO2 | switch | on/off | Increase/Reduce CO2 |
| `exhaust` | canExhaust | number | 0-1 | Increase/Reduce Exhaust |
| `ventilation` | canVentilate | number | 0-1 | Increase/Reduce Ventilation |
| `intake` | canIntake | number | 0-1 | Increase/Reduce Intake |
| `sensor` | - | sensor | read-only | - |

### Value Scaling

FridgeGrow uses `0.0 - 1.0` range, OGB uses `0 - 100%`.

The `FridgeGrowDevice` class automatically converts:
- **OGB → FridgeGrow**: `percentage / 100.0`
- **FridgeGrow → OGB**: `value * 100`

## MQTT Control Mode (mqttcontrol)

FridgeGrow devices have two control modes:

1. **Internal Control**: Device regulates itself based on internal settings
2. **MQTT Control**: External control via MQTT (what OGB needs)

To enable MQTT control, send `{"mqttcontrol": true}` to `devices/{device_id}/configuration` **every 60 seconds**.

### Keepalive Implementation

The `FridgeGrowDevice` class can optionally manage the keepalive:

```python
# Enable direct control (starts keepalive)
await device.enable_mqtt_control()

# Disable direct control (stops keepalive, device returns to internal control)
await device.disable_mqtt_control()
```

**Note**: If keepalive stops, FridgeGrow falls back to internal control after 60 seconds.

## Troubleshooting

### Device not recognized

1. **Check labels**: Entity must have both `fridgegrow` AND output type label
2. **Check area**: Entity/device must be assigned to your grow room area
3. **Check logs**: Look for `FridgeGrow` in OGB logs

### Device not responding

1. **Check MQTT connection**: Is FridgeGrow connected to your MQTT broker?
2. **Check mqttcontrol mode**: Is keepalive running?
3. **Check entity state**: Is the entity available in HA?

### Wrong value range

FridgeGrow outputs expect `0-1`, not `0-100`. The `FridgeGrowDevice` class handles this conversion automatically. If values seem wrong, check that you're using the correct entity type (number vs switch).

## Technical Reference

### FridgeGrowDevice Class

```python
class FridgeGrowDevice(Device):
    """
    FridgeGrow/Plantalytix device handler.
    
    Attributes:
        isFridgeGrowDevice (bool): Always True
        output_type (str): "heater", "light", etc.
        fridgegrow_device_id (str): Device ID for keepalive
        mqtt_control_enabled (bool): MQTT control mode active
    
    Methods:
        turn_on(**kwargs): Turn on output with optional percentage
        turn_off(**kwargs): Turn off output
        increaseAction(data): Handle increase event
        reduceAction(data): Handle reduce event
        enable_mqtt_control(): Start keepalive for direct control
        disable_mqtt_control(): Stop keepalive
    """
```

### OUTPUT_CONFIG Reference

```python
OUTPUT_CONFIG = {
    "heater": {
        "capability": "canHeat",
        "event_increase": "Increase Heater",
        "event_reduce": "Reduce Heater",
        "entity_type": "number",
        "value_range": (0, 1),
    },
    # ... see source code for all outputs
}
```

## Related Documentation

- [Device Management](./device_management.md)
- [Modbus Integration](./MODBUS_INTEGRATION.md)
- [Supported Devices](./supported_devices_hardware.md)

## External Resources

- [FridgeGrow 2.0 Repository](https://github.com/plantalytix/fridgegrow2.0)
- [FridgeGrow MQTT Documentation](https://github.com/plantalytix/fridgegrow2.0/wiki/mqtt)
- [FridgeGrow Configuration](https://github.com/plantalytix/fridgegrow2.0/wiki/config)
