# OpenGrowBox Label System

## Overview

OpenGrowBox uses a **label-based device identification system**. Labels help the system automatically recognize and categorize your devices without manual configuration.

Labels are defined in `OGBParams.py` and used by the `OGBDeviceManager` to identify device types and assign capabilities.

---

## How Labels Work

### Device Configuration Example

```json
{
  "name": "My Exhaust Fan",
  "entity_id": "switch.exhaust_fan_main",
  "deviceType": "Exhaust",
  "labels": [
    {"name": "exhaust"},
    {"name": "main"}
  ],
  "entities": [
    {
      "entity_id": "switch.exhaust_fan_main",
      "capabilities": ["canExhaust"]
    }
  ]
}
```

### Label Priority

1. **Exact match** - Label matches keyword exactly (highest priority)
2. **Contains match** - Label contains keyword
3. **Device type** - Explicit `deviceType` field
4. **Entity ID** - Keywords in entity_id (fallback)

---

## Complete Label Reference

### Climate Control

| Label | Device Type | Description |
|-------|-------------|-------------|
| `heater` | Heater | Heating device |
| `heizung` | Heater | Heating device (German) |
| `cooler` | Cooler | Cooling/AC device |
| `kuehler` | Cooler | Cooling device (German) |
| `climate` | Climate | Climate control unit |
| `klima` | Climate | Climate control (German) |
| `dehumidifier` | Dehumidifier | Removes humidity |
| `entfeuchter` | Dehumidifier | Removes humidity (German) |
| `humidifier` | Humidifier | Adds humidity |
| `befeuchter` | Humidifier | Adds humidity (German) |

**Example:**
```json
{"name": "heater"}
{"name": "dehumidifier"}
```

---

### Ventilation

| Label | Device Type | Description |
|-------|-------------|-------------|
| `exhaust` | Exhaust | Exhaust/outtake fan |
| `abluft` | Exhaust | Exhaust fan (German) |
| `intake` | Intake | Fresh air intake |
| `zuluft` | Intake | Fresh air intake (German) |
| `vent` | Ventilation | General ventilation |
| `vents` | Ventilation | Multiple vents |
| `venti` | Ventilation | Ventilation shorthand |
| `ventilation` | Ventilation | Full word |
| `inlet` | Ventilation | Air inlet |
| `window` | Window | Window opener |
| `fenster` | Window | Window (German) |

**Example:**
```json
{"name": "exhaust"}
{"name": "intake"}
```

---

### Lighting

| Label | Device Type | Description |
|-------|-------------|-------------|
| `light` | Light | Generic light |
| `lamp` | Light | Lamp |
| `led` | Light | LED light |
| `light_fr` | LightFarRed | Far-red spectrum |
| `light_farred` | LightFarRed | Far-red spectrum |
| `farred` | LightFarRed | Far-red |
| `far_red` | LightFarRed | Far-red with underscore |
| `farredlight` | LightFarRed | Far-red light variant |
| `far-red-light` | LightFarRed | Far-red with dashes |
| `lightfarred` | LightFarRed | Far-red combined |
| `light_uv` | LightUV | UV light |
| `light_uvb` | LightUV | UV-B light |
| `light_uva` | LightUV | UV-A light |
| `uvlight` | LightUV | UV light variant |
| `uv-light` | LightUV | UV light with dash |
| `lightuv` | LightUV | UV combined |
| `light_blue` | LightBlue | Blue spectrum |
| `blue_led` | LightBlue | Blue LED |
| `bluelight` | LightBlue | Blue light |
| `blue-light` | LightBlue | Blue light with dash |
| `lightblue` | LightBlue | Blue combined |
| `light_red` | LightRed | Red spectrum |
| `red_led` | LightRed | Red LED |
| `redlight` | LightRed | Red light |
| `red-light` | LightRed | Red light with dash |
| `lightred` | LightRed | Red combined |

**Example:**
```json
{"name": "light"}
{"name": "light_fr"}
```

---

### Irrigation & Reservoir

| Label | Device Type | Description |
|-------|-------------|-------------|
| `pump` | Pump | Generic pump |
| `dripper` | Pump | Drip irrigation |
| `feedsystem` | Pump | Feed system |
| `reservoir_pump` | ReservoirPump | **Reservoir fill pump** |
| `reservoirpump` | ReservoirPump | Reservoir pump variant |
| `tank_fill` | ReservoirPump | Tank filling pump |
| `fill_pump` | ReservoirPump | Fill pump |
| `reservoir_fill` | ReservoirPump | Reservoir fill |
| `water_fill` | ReservoirPump | Water fill pump |
| `feed_a` | FeedPump | **Nutrient pump A** (Veg) |
| `feed_b` | FeedPump | **Nutrient pump B** (Flower) |
| `feed_c` | FeedPump | **Nutrient pump C** (Micro) |
| `feed_w` | FeedPump | **Water pump** |
| `feed_x` | FeedPump | **Custom pump X** |
| `feed_y` | FeedPump | **Custom pump Y** |
| `feed_php` | FeedPump | **pH+ pump** (pH up) |
| `feed_phm` | FeedPump | **pH- pump** (pH down) |
| `feed_water` | FeedPump | **Water feed pump** |
| `retrieve` | RetrievePump | **Return/Recovery pump** |
| `return` | RetrievePump | Return pump variant |
| `retrieve_pump` | RetrievePump | Retrieve pump explicit |
| `return_pump` | RetrievePump | Return pump explicit |
| `recovery` | RetrievePump | Recovery pump |
| `rücklauf` | RetrievePump | Return pump (German) |
| `ruecklauf` | RetrievePump | Return pump (German alt) |
| `watering` | WateringPump | **Plant watering pump** |
| `plant_water` | WateringPump | Plant water pump |
| `irrigation` | WateringPump | Irrigation pump |
| `watering_pump` | WateringPump | Watering pump explicit |
| `irrigate` | WateringPump | Irrigate pump |
| `bewässerung` | WateringPump | Watering (German) |
| `bewaesserung` | WateringPump | Watering (German alt) |
| `aero` | AeroPump | **Aeroponic pump** |
| `aeroponic` | AeroPump | Aeroponic pump full |
| `aero_pump` | AeroPump | Aero pump explicit |
| `mist` | AeroPump | Mist pump |
| `misting` | AeroPump | Misting pump |
| `aeroponik` | AeroPump | Aeroponic (German) |
| `dwc` | DWCPump | **DWC circulation pump** |
| `deep_water` | DWCPump | Deep water culture pump |
| `dwc_pump` | DWCPump | DWC pump explicit |
| `recirculating` | DWCPump | Recirculating pump |
| `cloner` | ClonerPump | **Clone/Propagation pump** |
| `clone` | ClonerPump | Clone pump |
| `cloner_pump` | ClonerPump | Cloner pump explicit |
| `propagation` | ClonerPump | Propagation pump |
| `steckling` | ClonerPump | Cutting/Clone (German) |
| `kloner` | ClonerPump | Cloner (German) |

**Important:** For automatic reservoir filling, you MUST use one of the `ReservoirPump` labels.

Reservoir autofill uses:

- capability-based pump discovery via `canReservoirFill`
- the first matching `ReservoirPump` device/entity
- mobile notifications for low level, fill start, fill progress, completion, and block/error states

Reservoir autofill safety behavior:

- starts when the measured reservoir level drops below `OGB_Feed_Reservoir_Min`
- fills in `5%` steps toward `OGB_Feed_Reservoir_Max`
- uses a maximum of `5 minutes` per cycle
- blocks after repeated sensor errors or unexpected pump state behavior
- a latched autofill block is reset only after switching from `Automatic` to `Config` or `Disabled` and back to `Automatic`

**Important:** For automatic feeding, use `FeedPump` labels to identify your nutrient and pH pumps.

Feed pump behavior:

- nutrient and pH pumps are resolved label-first via `FeedPump`
- if a feed or pH pump flow rate is set to `0`, that pump is treated as disabled and skipped
- this `flowrate = 0` disable behavior applies only to `TankFeedManager` feed/pH pumps, not reservoir pumps

**Important:** For crop steering (retrieve/return), use `RetrievePump` labels.

**Important:** For plant watering systems, use `WateringPump` labels.

**Important:** For aeroponic systems, use `AeroPump` labels.

**Important:** For DWC systems, use `DWCPump` labels.

**Important:** For cloning/propagation systems, use `ClonerPump` labels.

**Example:**
```json
{"name": "reservoir_pump"}
{"name": "feed_a"}
{"name": "feed_php"}
```

---

### Sensors

| Label | Device Type | Description |
|-------|-------------|-------------|
| `sensor` | Sensor | Generic sensor |
| `temperature` | Sensor | Temperature sensor |
| `temp` | Sensor | Temperature shorthand |
| `humidity` | Sensor | Humidity sensor |
| `moisture` | Sensor | Soil moisture |
| `dewpoint` | Sensor | Dew point |
| `illuminance` | Sensor | Light level |
| `ppfd` | Sensor | Photosynthetic photon flux |
| `dli` | Sensor | Daily light integral |
| `reservoir` | Sensor | Reservoir level sensor |
| `ogb` | Sensor | OpenGrowBox sensor |
| `govee` | Sensor | Govee brand sensor |
| `ens160` | Sensor | ENS160 air quality |
| `tasmota` | Sensor | Tasmota-based sensor |
| `watertester` | Sensor | Water tester |
| `wasstertester` | Sensor | Water tester (German typo) |

**Example:**
```json
{"name": "temperature"}
{"name": "reservoir"}
```

---

### Other Devices

| Label | Device Type | Description |
|-------|-------------|-------------|
| `co2` | CO2 | CO2 sensor/controller |
| `carbon` | CO2 | CO2 variant |
| `camera` | Camera | Surveillance camera |
| `kamera` | Camera | Camera (German) |
| `cam` | Camera | Camera shorthand |
| `video` | Camera | Video camera |
| `ipcam` | Camera | IP camera |
| `webcam` | Camera | Web camera |
| `surveillance` | Camera | Surveillance system |
| `fridge` | Fridge | Refrigerator |
| `kuehlschrank` | Fridge | Refrigerator (German) |
| `door` | Door | Door sensor |
| `tuer` | Door | Door (German) |
| `kontakt` | Door | Contact sensor (German) |
| `contact` | Door | Contact sensor |
| `entry` | Door | Entry point |
| `generic` | Switch | Generic switch |
| `switch` | Switch | Switch device |

---

### Special Devices

| Label | Device Type | Description |
|-------|-------------|-------------|
| `fridgegrow` | FridgeGrow | FridgeGrow/Plantalytix device |
| `plantalytix` | FridgeGrow | Plantalytix device |
| `modbus` | ModbusDevice | Modbus RTU/TCP device |
| `modbus_device` | ModbusDevice | Modbus device explicit |
| `modbus_rtu` | ModbusDevice | Modbus RTU |
| `modbus_tcp` | ModbusDevice | Modbus TCP |
| `modbus_sensor` | ModbusSensor | Modbus sensor |
| `modbus_temp` | ModbusSensor | Modbus temperature |
| `modbus_humidity` | ModbusSensor | Modbus humidity |

---

## Device Capabilities (CAP_MAPPING)

Capabilities determine what actions a device can perform:

| Capability | Associated Device Types |
|------------|------------------------|
| `canHeat` | heater |
| `canCool` | cooler |
| `canClimate` | climate |
| `canHumidify` | humidifier |
| `canDehumidify` | dehumidifier |
| `canVentilate` | ventilation, window |
| `canWindow` | window |
| `canDoor` | door |
| `canExhaust` | exhaust |
| `canIntake` | intake |
| `canLight` | light |
| `canCO2` | co2 |
| `canPump` | pump |
| `canFeed` | feedpump |
| `canReservoirFill` | reservoirpump |
| `canRetrieve` | retrievepump |
| `canWatering` | wateringpump |
| `canAero` | aeropump |
| `canDWC` | dwcpump |
| `canClone` | clonerpump |
| `canWatch` | camera |

---

## Configuration Examples

### Reservoir Auto-Fill Setup

**1. Reservoir Level Sensor:**
```json
{
  "name": "Reservoir Level",
  "entity_id": "sensor.reservoir_ultrasonic",
  "deviceType": "Sensor",
  "labels": [
    {"name": "reservoir"},
    {"name": "sensor"}
  ]
}
```

**2. Reservoir Fill Pump:**
```json
{
  "name": "Reservoir Fill Pump",
  "entity_id": "switch.reservoir_pump",
  "deviceType": "ReservoirPump",
  "labels": [
    {"name": "reservoir_pump"}
  ]
}
```

**3. Threshold Numbers (automatically created):**
- `number.ogb_feed_reservoir_min_room1` - Default: 25%
- `number.ogb_feed_reservoir_max_room1` - Default: 85%

---

### Complete Grow Room Setup

```json
[
  {
    "name": "Main Exhaust",
    "entity_id": "fan.exhaust_main",
    "labels": [{"name": "exhaust"}, {"name": "main"}]
  },
  {
    "name": "Intake Fan",
    "entity_id": "fan.intake_side",
    "labels": [{"name": "intake"}]
  },
  {
    "name": "LED Grow Light",
    "entity_id": "light.led_grow_main",
    "labels": [{"name": "light"}]
  },
  {
    "name": "Heater",
    "entity_id": "switch.heater_room",
    "labels": [{"name": "heater"}]
  },
  {
    "name": "Humidifier",
    "entity_id": "switch.humidifier_main",
    "labels": [{"name": "humidifier"}]
  },
  {
    "name": "Reservoir Level",
    "entity_id": "sensor.reservoir_level",
    "labels": [{"name": "reservoir"}]
  },
  {
    "name": "Reservoir Pump",
    "entity_id": "switch.reservoir_fill",
    "labels": [{"name": "reservoir_pump"}]
  },
  {
    "name": "Nutrient Pump A",
    "entity_id": "switch.pump_a",
    "labels": [{"name": "pump"}]
  }
]
```

---

## Troubleshooting

### Device Not Recognized

1. **Check labels:** Ensure device has correct labels from the table above
2. **Check deviceType:** Verify `deviceType` matches expected type
3. **Check logs:** Look for device identification messages in Home Assistant logs
4. **Restart OGB:** After adding labels, reload the OpenGrowBox integration

### Reservoir Not Auto-Filling

1. **Verify pump label:** Must have `reservoir_pump` or similar label
2. **Check thresholds:** Ensure `OGB_Feed_Reservoir_Min` is set correctly
3. **Check sensor:** Reservoir level sensor must be working and updating
4. **Check capability:** The device should appear in `canReservoirFill`
5. **Check logs:** Look for "No reservoir pump found" or fill cycle messages
6. **Check block state:** Autofill can be latched blocked after safety errors until mode reset

### Feed Pumps Not Working

1. **Verify pump labels:** Must use `feed_a`, `feed_b`, `feed_c`, `feed_php`, `feed_phm`, etc.
2. **Check device registration:** Pumps should appear as `FeedPump` device type
3. **Check flow rate:** A flow rate of `0` disables that feed/pH pump intentionally
4. **Check calibration:** Pump flow rates and calibration should be configured in DataStore
5. **Check logs:** Look for pump discovery or "disabled via flow rate 0" messages in Home Assistant logs

Example feed pump configuration:
```json
{
  "name": "Nutrient Pump A",
  "entity_id": "switch.feedpump_a",
  "deviceType": "FeedPump",
  "labels": [{"name": "feed_a"}]
}
```

### Multiple Labels

Devices can have multiple labels. The system uses the **first matching label** to determine type:

```json
{
  "labels": [
    {"name": "exhaust"},     // ← Used: Device type = Exhaust
    {"name": "main"},        // Context label
    {"name": "backup"}       // Context label
  ]
}
```

---

## Adding Custom Labels

To add new labels, edit `OGBParams.py`:

```python
DEVICE_TYPE_MAPPING = {
    # ... existing types ...
    "MyCustomType": ["custom_label", "another_label"],
}
```

Then reload OpenGrowBox or restart Home Assistant.

---

**Last Updated:** April 2025
**Version:** 1.2
**Status:** Production Ready
