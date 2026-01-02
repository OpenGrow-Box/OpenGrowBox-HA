# Device Integration Guide

## Overview

OpenGrowBox supports **ANY device that works with Home Assistant**. Since OGB is a HA integration, it can control any HA entity - sensors, switches, lights, climate devices, etc. This guide shows how to integrate devices with OGB through Home Assistant, regardless of the hardware brand or connection method.

## Device Integration with Home Assistant

### Step 1: Connect Devices to Home Assistant

OpenGrowBox works with any device that creates entities in Home Assistant. Here are common integration methods:

#### ESP32/ESP8266 with ESPHome
```yaml
# ESPHome device configuration
esphome:
  name: grow_room_sensor

esp32:
  board: esp32dev

# WiFi connection
wifi:
  ssid: "YourWiFi"
  password: "yourpassword"

# Sensors
sensor:
  - platform: dht
    pin: 4
    model: DHT22
    temperature:
      name: "Grow Room Temperature"
    humidity:
      name: "Grow Room Humidity"
    update_interval: 30s

# Switches for relays
switch:
  - platform: gpio
    pin: 12
    name: "Exhaust Fan"
    inverted: false

  - platform: gpio
    pin: 13
    name: "Heater"
    inverted: false
```

#### Raspberry Pi with GPIO
```yaml
# Raspberry Pi configuration.yaml
sensor:
  - platform: dht
    pin: 4
    model: DHT22
    name: "Grow Room Temperature"
    temperature_offset: 0.5

switch:
  - platform: rpi_gpio
    ports:
      12: Exhaust Fan
      13: Heater
      14: Humidifier
```

#### Commercial Smart Devices
```yaml
# Shelly devices
shelly:
  devices:
    - id: shelly1-123456
      name: "Exhaust Fan Controller"

# Tuya devices
tuya:
  username: your_email@example.com
  password: your_password
  country_code: 1

# Zigbee devices
zha:
  usb_path: /dev/ttyUSB0
  radio_type: ezsp
```

#### Cloud-Connected Devices
```yaml
# Google Nest
nest:
  client_id: your_client_id
  client_secret: your_client_secret

# Philips Hue
hue:
  bridges:
    - host: 192.168.1.100

# Sonoff devices
sonoff:
  username: your_email@example.com
  password: your_password
```

### Step 2: Verify HA Entity Creation

After connecting devices, check that entities are created in HA:

1. **Go to HA Developer Tools → States**
2. **Look for your device entities:**
   - `sensor.grow_room_temperature`
   - `switch.exhaust_fan`
   - `light.led_grow_light`
   - `climate.grow_room_ac`

3. **Test entities work:**
   - Toggle switches in HA UI
   - Check sensor values update
   - Verify device control works

### Step 3: Configure Devices in OpenGrowBox

Once devices are working in HA, add them to OGB:

#### OGB Device Configuration

**Basic Device Setup:**
```json
{
  "name": "Grow Room Sensor",
  "type": "Sensor",
  "entities": [
    {
      "entity_id": "sensor.grow_room_temperature",
      "capabilities": ["canMeasureTemp"]
    },
    {
      "entity_id": "sensor.grow_room_humidity",
      "capabilities": ["canMeasureHum"]
    }
  ]
}
```

**Device with Labels (Auto-Detection):**
```json
{
  "name": "Exhaust Fan",
  "entities": [
    {
      "entity_id": "switch.exhaust_fan"
    }
  ],
  "labels": [
    {
      "id": "exhaust",
      "name": "Exhaust Fan",
      "scope": "device"
    }
  ]
}
```

**Complete Device Configuration:**
```json
{
  "name": "LED Grow Light",
  "type": "Light",
  "entities": [
    {
      "entity_id": "light.led_grow_light",
      "capabilities": ["canLight", "canSpectrum"]
    }
  ],
  "settings": {
    "min_voltage": 20,
    "max_voltage": 100,
    "calibration_factor": 1.0
  },
  "labels": [
    {
      "id": "grow_light",
      "name": "LED Grow Light",
      "scope": "device"
    }
  ]
}
```

## Device Type Auto-Detection

### Label-Based Detection

OpenGrowBox automatically detects device types using labels in your HA entities:

**Sensor Labels:**
```yaml
# Add labels to your HA entities
sensor:
  - platform: dht
    pin: 4
    model: DHT22
    temperature:
      name: "Grow Room Temperature"
    humidity:
      name: "Grow Room Humidity"
    # Add labels for OGB detection
    labels:
      - "temperature"
      - "grow_room"
```

**Device Labels:**
```yaml
switch:
  - platform: gpio
    pin: 12
    name: "Exhaust Fan"
    labels:
      - "exhaust"
      - "ventilation"
      - "fan"
```

### Common Label Keywords

**Lighting:**
- `light`, `led`, `grow_light`, `spectrum`, `dli`

**Climate:**
- `heater`, `cooler`, `ac`, `hvac`, `climate`, `temperature`

**Humidity:**
- `humidifier`, `dehumidifier`, `humidity`

**Ventilation:**
- `exhaust`, `intake`, `fan`, `ventilation`

**Irrigation:**
- `pump`, `irrigation`, `water`, `nutrient`

**Sensors:**
- `temperature`, `humidity`, `co2`, `ph`, `ec`, `moisture`

### Manual Device Configuration

If auto-detection doesn't work, manually configure devices:

## OGB Device Configuration Examples

### Sensor Arrays

**Environmental Monitoring:**
```json
{
  "name": "Grow Room Sensors",
  "type": "Sensor",
  "entities": [
    {"entity_id": "sensor.temperature", "capabilities": ["canMeasureTemp"]},
    {"entity_id": "sensor.humidity", "capabilities": ["canMeasureHum"]},
    {"entity_id": "sensor.co2", "capabilities": ["canMeasureCO2"]},
    {"entity_id": "sensor.light_ppfd", "capabilities": ["canMeasureLight"]}
  ]
}
```

### Actuator Configurations

**Climate Control:**
```json
{
  "name": "Climate Controller",
  "type": "Climate",
  "entities": [
    {"entity_id": "climate.grow_room_ac", "capabilities": ["canHeat", "canCool", "canDehumidify"]}
  ]
}
```

**Lighting System:**
```json
{
  "name": "LED Grow Lights",
  "type": "Light",
  "entities": [
    {"entity_id": "light.main_grow_light", "capabilities": ["canLight", "canSpectrum"]},
    {"entity_id": "light.side_lights", "capabilities": ["canLight"]}
  ],
  "settings": {
    "min_voltage": 10,
    "max_voltage": 100
  }
}
```

**Irrigation System:**
```json
{
  "name": "Nutrient System",
  "type": "Pump",
  "entities": [
    {"entity_id": "switch.nutrient_pump_a", "capabilities": ["canPump"]},
    {"entity_id": "switch.nutrient_pump_b", "capabilities": ["canPump"]},
    {"entity_id": "switch.ph_pump", "capabilities": ["canPump"]},
    {"entity_id": "sensor.ph_level", "capabilities": ["canMeasurePH"]},
    {"entity_id": "sensor.ec_level", "capabilities": ["canMeasureEC"]}
  ]
}
```

## Device Capability Mapping

### Understanding Capabilities

OpenGrowBox uses capabilities to understand what each device can do:

**Climate Capabilities:**
- `canHeat` - Can increase temperature
- `canCool` - Can decrease temperature
- `canHumidify` - Can increase humidity
- `canDehumidify` - Can decrease humidity

**Lighting Capabilities:**
- `canLight` - Basic on/off lighting
- `canSpectrum` - Can adjust light spectrum
- `canUV` - UV light control
- `canFarRed` - Far-red spectrum control

**Irrigation Capabilities:**
- `canPump` - Can pump liquids
- `canIrrigate` - Irrigation control
- `canMeasurePH` - pH measurement
- `canMeasureEC` - EC/TDS measurement

**Ventilation Capabilities:**
- `canExhaust` - Exhaust fan control
- `canIntake` - Intake fan control
- `canVentilate` - General ventilation

### Auto-Capability Detection

OGB automatically detects capabilities based on HA entity types:

**Entity Type → Capabilities:**
- `climate.*` → `canHeat`, `canCool`, `canHumidify`, `canDehumidify`
- `light.*` → `canLight`, `canSpectrum`
- `switch.*` → `canSwitch` (generic on/off)
- `fan.*` → `canExhaust`, `canVentilate`
- `sensor.temperature` → `canMeasureTemp`
- `sensor.humidity` → `canMeasureHum`
- `sensor.*co2*` → `canMeasureCO2`

## Device Integration Examples

### Complete Grow Room Setup

**ESP32 with Multiple Sensors:**
```yaml
# ESPHome configuration for grow room
esphome:
  name: grow_room_controller

esp32:
  board: esp32dev

wifi:
  ssid: "YourWiFi"
  password: "yourpassword"

# Sensors
sensor:
  - platform: dht
    pin: 4
    model: DHT22
    temperature:
      name: "Temperature"
      id: temp_sensor
    humidity:
      name: "Humidity"
      id: hum_sensor
    update_interval: 30s

  - platform: adc
    pin: A0
    name: "Soil Moisture"
    update_interval: 60s

# Switches for devices
switch:
  - platform: gpio
    pin: 12
    name: "Exhaust Fan"
    inverted: false

  - platform: gpio
    pin: 13
    name: "Heater"
    inverted: false

  - platform: gpio
    pin: 14
    name: "Humidifier"
    inverted: false

  - platform: gpio
    pin: 15
    name: "Water Pump"
    inverted: false

# Light control
light:
  - platform: rgb
    name: "LED Grow Light"
    red: 16
    green: 17
    blue: 18
    white: 19
```

**OGB Configuration for Above Setup:**
```json
[
  {
    "name": "Environmental Sensors",
    "type": "Sensor",
    "entities": [
      {"entity_id": "sensor.temperature", "capabilities": ["canMeasureTemp"]},
      {"entity_id": "sensor.humidity", "capabilities": ["canMeasureHum"]},
      {"entity_id": "sensor.soil_moisture", "capabilities": ["canMeasureVWC"]}
    ]
  },
  {
    "name": "Exhaust Fan",
    "type": "Exhaust",
    "entities": [
      {"entity_id": "switch.exhaust_fan", "capabilities": ["canExhaust"]}
    ]
  },
  {
    "name": "Climate Control",
    "type": "Heater",
    "entities": [
      {"entity_id": "switch.heater", "capabilities": ["canHeat"]}
    ]
  },
  {
    "name": "Humidity Control",
    "type": "Humidifier",
    "entities": [
      {"entity_id": "switch.humidifier", "capabilities": ["canHumidify"]}
    ]
  },
  {
    "name": "Irrigation",
    "type": "Pump",
    "entities": [
      {"entity_id": "switch.water_pump", "capabilities": ["canPump", "canIrrigate"]}
    ]
  },
  {
    "name": "LED Grow Light",
    "type": "Light",
    "entities": [
      {"entity_id": "light.led_grow_light", "capabilities": ["canLight", "canSpectrum"]}
    ],
    "settings": {
      "min_voltage": 10,
      "max_voltage": 100
    }
  }
]
```

## Device Discovery and Auto-Configuration

### Automatic Device Discovery

OpenGrowBox automatically discovers devices from your Home Assistant setup:

1. **Entity Scanning**: OGB scans all HA entities when started
2. **Label Detection**: Uses entity labels for device type identification
3. **Capability Mapping**: Maps HA entity types to OGB capabilities
4. **Configuration Generation**: Creates device configurations automatically

**Discovery Process:**
```python
# Automatic discovery workflow
async def discover_devices():
    # 1. Get all HA entities
    ha_entities = await hass.states.async_all()

    # 2. Filter for relevant entities
    relevant_entities = [
        entity for entity in ha_entities
        if is_relevant_for_ogb(entity)
    ]

    # 3. Group by device/location
    device_groups = group_entities_by_device(relevant_entities)

    # 4. Create OGB device configurations
    ogb_devices = []
    for group in device_groups:
        device_config = create_device_config(group)
        ogb_devices.append(device_config)

    return ogb_devices
```

### Manual Device Addition

For devices that need manual configuration:

1. **Via HA UI**: Use the OGB device configuration interface
2. **Via YAML**: Edit OGB device configuration files
3. **Via API**: Use REST API endpoints for device management

## Troubleshooting Device Integration

### Device Not Detected

**Check HA Entity:**
```bash
# Verify entity exists in HA
curl http://homeassistant:8123/api/states/sensor.your_sensor
```

**Check Entity Labels:**
```yaml
# Add labels to HA entity
sensor:
  - platform: dht
    pin: 4
    model: DHT22
    temperature:
      name: "Temperature"
      labels: ["temperature", "grow_room"]
```

**Force OGB Rescan:**
```bash
# Trigger device rescan
curl -X POST http://your-ogb/api/v1/devices/rescan
```

### Device Control Not Working

**Verify HA Control:**
```bash
# Test HA entity control
curl -X POST http://homeassistant:8123/api/services/switch/turn_on \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"entity_id": "switch.your_device"}'
```

**Check OGB Permissions:**
```json
// Verify device capabilities in OGB config
{
  "name": "Your Device",
  "capabilities": ["canSwitch"],
  "permissions": ["read", "write"]
}
```

**Debug OGB Logs:**
```bash
# Check OGB device control logs
tail -f /var/log/opengrowbox/device.log
```

### Sensor Data Issues

**Check HA Sensor Updates:**
```bash
# Monitor sensor value changes
curl http://homeassistant:8123/api/states/sensor.your_sensor
```

**Verify OGB Sensor Reading:**
```bash
# Check if OGB is receiving sensor data
curl http://your-ogb/api/v1/sensors
```

**Calibration Issues:**
```json
// Check sensor calibration settings
{
  "sensor_id": "sensor.your_sensor",
  "calibration": {
    "offset": 0.0,
    "multiplier": 1.0,
    "last_calibrated": "2025-01-15T10:00:00Z"
  }
}
```

## Best Practices for Device Integration

### Entity Naming Conventions

**Consistent Naming:**
```
sensor.grow_room_temperature
sensor.grow_room_humidity
switch.exhaust_fan_main
light.led_grow_light_primary
climate.grow_room_hvac
```

**Label Usage:**
```yaml
# Use descriptive labels for auto-detection
labels:
  - "temperature"
  - "grow_room"
  - "primary"
  - "backup"
```

### Device Organization

**Logical Grouping:**
```json
// Group related devices
{
  "environmental": ["temperature", "humidity", "co2"],
  "climate": ["heater", "cooler", "humidifier"],
  "lighting": ["main_light", "side_lights"],
  "irrigation": ["water_pump", "nutrient_pumps"]
}
```

**Zone-Based Organization:**
```json
// Organize by physical zones
{
  "zone1": {
    "sensors": ["temp1", "hum1"],
    "devices": ["fan1", "light1"]
  },
  "zone2": {
    "sensors": ["temp2", "hum2"],
    "devices": ["fan2", "light2"]
  }
}
```

### Performance Optimization

**Polling Intervals:**
```yaml
# Optimize sensor polling based on needs
sensor:
  - platform: dht
    update_interval: 30s  # Fast for climate control
    # vs
  - platform: soil_moisture
    update_interval: 300s  # Slow for irrigation
```

**Batch Operations:**
```python
# Use batch operations for multiple devices
await ogb.bulk_device_control([
    {"device": "fan1", "action": "turn_on"},
    {"device": "fan2", "action": "turn_on"},
    {"device": "light1", "action": "set_brightness", "value": 80}
])
```

## Supported Device Types

### Any HA-Compatible Device

**Supported Entity Types:**
- `sensor.*` - Temperature, humidity, CO2, pH, EC, light, moisture
- `switch.*` - Relays, smart plugs, pumps, solenoids
- `light.*` - LED grow lights, spectrum lights, dimmable lights
- `climate.*` - HVAC units, mini-splits, climate controllers
- `fan.*` - Exhaust fans, intake fans, ventilation systems
- `humidifier.*` - Humidifiers, dehumidifiers
- `irrigation.*` - Irrigation systems, sprinkler controllers

**Integration Methods:**
- **ESPHome**: Custom firmware for ESP32/RPi
- **Zigbee/Z-Wave**: Wireless mesh networks
- **WiFi**: Shelly, Sonoff, Tuya devices
- **Ethernet**: Modbus, BACnet industrial devices
- **Cloud**: Google Nest, Philips Hue, smart home ecosystems

---

## Integration Summary

**OpenGrowBox supports ANY device that works with Home Assistant!**

**Integration Steps:**
1. ✅ **Connect devices to HA** using any integration method
2. ✅ **Verify HA entities** are working correctly
3. ✅ **Configure in OGB** using labels or manual setup
4. ✅ **Test control** through OGB interface

**Key Advantages:**
- **Universal Compatibility**: Any HA device works
- **Flexible Integration**: ESPHome, Zigbee, WiFi, cloud services
- **Auto-Discovery**: Label-based automatic configuration
- **Scalability**: Add devices without OGB changes

**For device management software, see [Device Management](device_management.md)**

**For configuration examples, see [Configuration Guide](../configuration/CONFIGURATION.md)**</content>
<parameter name="filePath">docs/supported_devices_hardware.md