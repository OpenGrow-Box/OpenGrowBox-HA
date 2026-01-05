# OpenGrowBox Modbus Integration

## Overview

The OpenGrowBox integration supports Modbus devices for industrial-grade control of grow room equipment. Modbus is commonly used for:
- Commercial HVAC systems
- Industrial fans and ventilation
- Professional EC/pH controllers
- Temperature/humidity controllers
- CO2 controllers and sensors

## Supported Protocols

| Protocol | Description | Use Case |
|----------|-------------|----------|
| **Modbus TCP** | Ethernet-based | Network-connected devices |
| **Modbus RTU** | Serial (RS-485/RS-232) | Local serial connections |

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
- ON/OFF control via coil registers
- Value setting via holding registers
- Automatic reconnection handling
- Scale factor support

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
```

### ModbusSensor

Specialized class for Modbus sensors with automatic polling.

**Features:**
- Automatic periodic polling
- Multi-register support
- Value scaling and offset
- Integration with OGB sensor system

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
2. Restart Home Assistant after adding labels
3. Check OGB logs for device identification messages

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

1. **Document Your Registers**: Always keep a mapping of your device's Modbus registers
2. **Use Unique Slave IDs**: On RS-485 buses, ensure each device has a unique slave ID
3. **Test Manually First**: Use a Modbus tool (like QModMaster) to verify connectivity before configuring OGB
4. **Monitor Polling Interval**: Don't poll too frequently - 30 seconds is usually sufficient
5. **Handle Disconnections**: OGBModbusDevice includes automatic reconnection, but monitor logs for connection issues

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

### v1.0.0 - Initial Modbus Support
- Added `OGBModbusDevice` base class
- Added `ModbusSensor` for automatic polling
- Support for Modbus TCP and RTU protocols
- Integration with OGB device manager
- Automatic reconnection handling
