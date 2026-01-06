# OpenGrowBox Modbus Integration

## Overview

The OpenGrowBox integration supports Modbus devices for industrial-grade control of grow room equipment. **After recent improvements, Modbus devices now function identically to native OGB devices**, including full integration with the action system and capabilities management.

Modbus is commonly used for:
- Commercial HVAC systems
- Industrial fans and ventilation
- Professional EC/pH controllers
- Temperature/humidity controllers
- CO2 controllers and sensors
- Custom industrial equipment

### Key Improvements (v2.0)
- ✅ **Full Action System Integration**: Modbus devices register with capabilities and respond to OGB actions
- ✅ **Improved Error Handling**: Automatic reconnection and graceful failure recovery
- ✅ **Proper Initialization**: Fixed inheritance issues and async task safety
- ✅ **Capabilities Registration**: Automatic integration with ActionManager for device control
- ✅ **Enhanced Polling**: Configurable intervals with proper cancellation support

## Supported Protocols

| Protocol | Description | Use Case |
|----------|-------------|----------|
| **Modbus TCP** | Ethernet-based | Network-connected devices |
| **Modbus RTU** | Serial (RS-485/RS-232) | Local serial connections |

---

## Recent Bug Fixes & Improvements

### v2.0 Critical Fixes
**Fixed in December 2025** - These fixes ensure Modbus devices work exactly like native OGB devices:

#### 1. Multiple Inheritance Fix
**Problem**: `class ModbusSensor(Sensor, OGBModbusDevice)` caused initialization conflicts
**Solution**: Changed to `class ModbusSensor(OGBModbusDevice, Sensor)` with proper method resolution order

#### 2. Async Task Safety
**Problem**: `asyncio.create_task()` called in `__init__` before object fully initialized
**Solution**: Moved polling startup to `deviceInit()` method after complete initialization

#### 3. Infinite Loop Prevention
**Problem**: Polling loops ran forever with no exit condition
**Solution**: Added proper `asyncio.CancelledError` handling and task cancellation support

#### 4. Missing Configuration
**Problem**: Register mappings never loaded from device configuration
**Solution**: Added automatic parsing of `registers` config from entity data

#### 5. Action System Integration
**Problem**: Modbus devices invisible to ActionManager and capabilities system
**Solution**: Added `_configure_capabilities()` to register devices with OGB control system

#### 6. Error Resilience
**Problem**: Single connection failure crashed entire device
**Solution**: Added try/catch with automatic retry and brief pause on errors

### Compatibility Verification
Modbus devices now support the same features as native devices:
- ✅ **Event Handling**: Respond to all standard OGB action events
- ✅ **Capabilities**: Registered in ActionManager for device targeting
- ✅ **Work Modes**: Proper work mode switching and polling control
- ✅ **Sensor Updates**: Same event emission patterns as native sensors
- ✅ **Error Recovery**: Automatic reconnection with exponential backoff

---

## Setup Instructions

### Step 1: Add pymodbus Dependency

The OpenGrowBox integration already includes `pymodbus>=3.11.2,<4.0.0` as a dependency in `manifest.json`. No additional installation required.

### Step 2: Label Your Modbus Device in Home Assistant

1. Go to **Settings -> Devices & Services -> Devices**
2. Find your device (or create a helper entity to represent it)
3. Click on it and select **Add Label**
4. Add the appropriate label:

| Device Type | Labels to Use |
|-------------|---------------|
| Generic Modbus Device | `modbus`, `modbus_device` |
| Modbus TCP Device | `modbus_tcp` |
| Modbus RTU Device | `modbus_rtu` |
| Modbus Sensor | `modbus_sensor`, `modbus_temp`, `modbus_humidity` |

### Step 3: Configure Modbus Connection

Modbus devices require additional configuration. This is done through entity attributes or a configuration helper.

#### Modbus TCP Configuration

```yaml
# Example configuration in HA
modbus_config:
  type: tcp
  host: 192.168.1.100    # IP address of Modbus device
  port: 502              # Modbus TCP port (default: 502)
  slave_id: 1            # Modbus slave address (1-247)
  control_register: 0    # Coil address for ON/OFF control
  value_register: 1      # Holding register for value setting
  scale_factor: 1        # Multiply value before writing
  capabilities:          # NEW: Action system integration
    - "canHeat"          # Register as heater device
    - "canCool"          # Register as cooler device
```

#### Capabilities Configuration
For full OGB integration, configure device capabilities:

```yaml
# Heater device
modbus_config:
  capabilities: ["canHeat"]

# Fan device
modbus_config:
  capabilities: ["canVentilate"]

# Pump device
modbus_config:
  capabilities: ["canPump"]

# Multi-capability device (e.g., climate controller)
modbus_config:
  capabilities: ["canHeat", "canCool", "canHumidify", "canDehumidify"]
```

#### Modbus RTU Configuration

```yaml
# Example configuration in HA
modbus_config:
  type: rtu
  port: /dev/ttyUSB0     # Serial port path
  baudrate: 9600         # Baud rate (default: 9600)
  slave_id: 1            # Modbus slave address
  control_register: 0    # Coil address for ON/OFF control
  value_register: 1      # Holding register for value setting
```

### Step 4: Define Register Mapping (For Sensors)

For Modbus sensors, define the register mapping:

```yaml
registers:
  temperature:
    address: 0           # Register address
    type: holding        # Register type: holding, input, or coil
    scale: 0.1           # Multiply raw value by this factor
    offset: 0            # Add this after scaling
    context: air         # Sensor context: air, water, soil
  humidity:
    address: 1
    type: input
    scale: 1
    offset: 0
    context: air
  ec:
    address: 2
    type: holding
    scale: 0.01
    offset: 0
    context: water
```

---

## Modbus Device Classes

### OGBModbusDevice

Base class for controllable Modbus devices (fans, pumps, valves, etc.)

**Features:**
- ✅ **Full OGB Integration**: Registered with capabilities system for ActionManager control
- ✅ **ON/OFF control** via coil registers
- ✅ **Value setting** via holding registers with scale factor support
- ✅ **Automatic reconnection** with exponential backoff
- ✅ **Error recovery** with graceful failure handling
- ✅ **Work mode support** for conditional operation

**Example Usage:**

```python
# Device is automatically created when labeled with 'modbus' in HA
# Control is done through standard OGB actions:

# Turn ON (writes 1 to control_register coil)
await device.turn_on()

# Turn OFF (writes 0 to control_register coil)
await device.turn_off()

# Set value (writes to value_register with scale_factor)
await device.set_value(75)  # e.g., 75% fan speed

# Device is automatically registered with capabilities for ActionManager
# Example: If configured with capabilities: ["canHeat"], device responds to:
await event_manager.emit("increase_vpd", capabilities)  # VPD actions
await event_manager.emit("checkLimitsAndPublicate", action_map)  # Direct actions
```

### ModbusSensor

Specialized class for Modbus sensors with automatic polling and full OGB compatibility.

**Features:**
- ✅ **Automatic periodic polling** with configurable intervals (default 30s)
- ✅ **Multi-register support** with individual scaling/offset per sensor
- ✅ **Full Sensor compatibility** - same event emission and data handling as native sensors
- ✅ **Context-aware readings** (air, water, soil, light contexts)
- ✅ **Robust error handling** with connection retry and polling continuation
- ✅ **Proper initialization** with safe async task startup
- ✅ **Work mode integration** for conditional polling control

**Example Register Configuration:**

```python
registers = {
    "temperature": {
        "address": 0,
        "type": "holding",
        "scale": 0.1,
        "offset": 0,
        "context": "air",
    },
    "humidity": {
        "address": 1,
        "type": "input", 
        "scale": 1,
        "offset": 0,
        "context": "air",
    },
}
```

---

## Register Types

| Type | Description | Read Method | Write Method |
|------|-------------|-------------|--------------|
| `holding` | Holding Registers (R/W) | `read_holding_registers()` | `write_register()` |
| `input` | Input Registers (R/O) | `read_input_registers()` | N/A |
| `coil` | Coil Registers (R/W) | `read_coils()` | `write_coil()` |

---

## Connection Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Home Assistant                                     │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    OpenGrowBox Integration                              │ │
│  │                                                                         │ │
│  │  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐  │ │
│  │  │   OGBDevice     │     │ OGBModbusDevice │     │  ModbusSensor   │  │ │
│  │  │   Manager       │────>│                 │     │                 │  │ │
│  │  │                 │     │ - connect()     │     │ - poll_sensors()│  │ │
│  │  │                 │     │ - turn_on()     │     │ - read_register │  │ │
│  │  │                 │     │ - turn_off()    │     │                 │  │ │
│  │  │                 │     │ - set_value()   │     │                 │  │ │
│  │  └────────┬────────┘     └────────┬────────┘     └────────┬────────┘  │ │
│  │           │                       │                       │           │ │
│  └───────────┼───────────────────────┼───────────────────────┼───────────┘ │
└──────────────┼───────────────────────┼───────────────────────┼─────────────┘
               │                       │                       │
    ┌──────────▼──────────┐   ┌────────▼────────┐   ┌──────────▼──────────┐
    │   Standard HA       │   │  Modbus TCP     │   │   Modbus RTU        │
    │   Entities          │   │  (Ethernet)     │   │   (RS-485/Serial)   │
    │   (WiFi/Zigbee/etc) │   │  Port 502       │   │   /dev/ttyUSB0      │
    └─────────────────────┘   └─────────────────┘   └─────────────────────┘
```

---

## Common Modbus Devices

### Industrial EC/pH Controllers

Many professional EC/pH controllers use Modbus. Common register mappings:

```yaml
# Example: Generic EC/pH Controller
registers:
  ec:
    address: 0
    type: input
    scale: 0.001    # Raw value in microsiemens, convert to mS/cm
    context: water
  ph:
    address: 1
    type: input
    scale: 0.01     # Raw value x100
    context: water
  temperature:
    address: 2
    type: input
    scale: 0.1
    context: water
```

### Industrial Fans/Ventilation

```yaml
# Example: VFD-controlled exhaust fan
modbus_config:
  type: tcp
  host: 192.168.1.50
  port: 502
  slave_id: 1
  control_register: 0      # Coil 0 = Start/Stop
  value_register: 100      # Holding register 100 = Speed setpoint
  scale_factor: 100        # 0-100% maps to 0-10000

registers:
  speed_feedback:
    address: 101           # Actual speed feedback
    type: input
    scale: 0.01
```

### Temperature Controllers

```yaml
# Example: PID Temperature Controller
registers:
  process_value:
    address: 0             # Current temperature
    type: input
    scale: 0.1
  setpoint:
    address: 1             # Temperature setpoint
    type: holding
    scale: 0.1
  output_percent:
    address: 2             # PID output %
    type: input
    scale: 1
```

---

## Troubleshooting

### Connection Issues

**Problem:** "Modbus connection failed"

**Solutions:**
1. Verify IP address/port (TCP) or serial port (RTU)
2. Check network connectivity: `ping <modbus_ip>`
3. Verify slave ID matches device configuration
4. Check firewall allows port 502 (TCP)

**Problem:** "Register read error"

**Solutions:**
1. Verify register addresses match device documentation
2. Check register type (holding vs input vs coil)
3. Ensure slave ID is correct
4. Try reading single register to isolate issue

### Device Not Detected

**Problem:** Device not appearing in OGB

**Solutions:**
1. Verify label is exactly: `modbus`, `modbus_device`, `modbus_tcp`, or `modbus_rtu`
2. **NEW**: Ensure `capabilities` are configured for device control
3. Restart Home Assistant after adding labels
4. Check OGB logs for device identification messages
5. **NEW**: Verify device appears in capabilities: `data_store.get("capabilities")`

### Action System Issues

**Problem:** Modbus device not responding to OGB actions

**Solutions:**
1. Check capabilities configuration includes required capabilities
2. Verify device registered in ActionManager: Check `capabilities[cap]["devEntities"]`
3. Ensure proper `modbus_config` with `control_register` and `value_register`
4. Check device logs for connection errors

**Problem:** Modbus sensor not updating values

**Solutions:**
1. Verify `registers` configuration in entity attributes
2. Check polling is active: Look for "Modbus polling started" in logs
3. Verify register addresses and types match device documentation
4. Check for connection errors in device logs

### Value Scaling Issues

**Problem:** Values are incorrect (too large/small)

**Solutions:**
1. Check device documentation for register format
2. Adjust `scale` factor (e.g., 0.1 for tenths, 0.01 for hundredths)
3. Apply `offset` if device uses non-zero baseline

---

## API Reference

### OGBModbusDevice Methods

```python
# Connect to Modbus device
await device.connect_modbus()

# Read register(s)
values = await device.read_register(
    address=0,           # Register address
    count=1,             # Number of registers
    register_type="holding"  # "holding", "input", or "coil"
)

# Write to register
success = await device.write_register(
    address=0,           # Register address
    value=100,           # Value to write
    register_type="holding"  # "holding" or "coil"
)

# Turn ON (uses control_register)
await device.turn_on()

# Turn OFF (uses control_register)
await device.turn_off()

# Set value (uses value_register with scale_factor)
await device.set_value(75)

# Poll all configured sensors
await device.poll_sensors()
```

### ModbusSensor Methods

```python
# Setup automatic polling
await sensor.setup_modbus_polling()

# Manual poll
await sensor.poll_sensors()
```

---

## Best Practices

1. **Configure Capabilities**: Always specify `capabilities` array for ActionManager integration
2. **Document Your Registers**: Keep detailed mapping of Modbus registers with addresses and scaling
3. **Use Unique Slave IDs**: On RS-485 buses, ensure each device has a unique slave ID (1-247)
4. **Test Manually First**: Use Modbus tools (QModMaster, Modbus Poll) to verify connectivity
5. **Monitor Polling Interval**: Default 30 seconds is usually sufficient; adjust based on device response time
6. **Handle Disconnections**: Automatic reconnection is built-in, but monitor logs for persistent issues
7. **Scale Factor Calibration**: Test scale_factor values to ensure proper value mapping
8. **Register Type Verification**: Confirm register types (holding/input/coil) match device documentation

---

## File Locations

```
custom_components/opengrowbox/OGBController/
├── OGBDevices/
│   ├── ModbusDevice.py    # Base Modbus device class
│   └── ModbusSensor.py    # Modbus sensor with polling
├── managers/
│   └── OGBDeviceManager.py # Device registration (lines 337-340)
└── data/OGBParams/
    └── OGBParams.py       # Device type mapping (lines 79-81)
```

---

## Changelog

### v2.0.0 - Critical Bug Fixes & Full OGB Integration (December 2025)
- ✅ **Fixed Multiple Inheritance**: Proper initialization order for ModbusSensor
- ✅ **Async Task Safety**: Moved polling startup to deviceInit() method
- ✅ **Infinite Loop Prevention**: Added proper task cancellation and cleanup
- ✅ **Configuration Loading**: Automatic register parsing from entity data
- ✅ **Action System Integration**: Capabilities registration for ActionManager control
- ✅ **Error Resilience**: Try/catch blocks with automatic retry logic
- ✅ **Work Mode Support**: Proper polling control based on work mode
- ✅ **Documentation Updates**: Comprehensive setup and troubleshooting guides

### v1.0.0 - Initial Modbus Support
- Added `OGBModbusDevice` base class
- Added `ModbusSensor` for automatic polling
- Support for Modbus TCP and RTU protocols
- Integration with OGB device manager
- Automatic reconnection handling
