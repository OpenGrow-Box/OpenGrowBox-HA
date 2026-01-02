# Device Management System - Hardware Control & Integration

## Overview

The Device Management System provides comprehensive hardware control and integration for OpenGrowBox. It manages all physical devices including climate control, lighting, irrigation, and sensing equipment through a unified interface with Home Assistant.

## System Architecture

### Core Components

#### 1. OGBDeviceManager (Main Controller)
```python
class OGBDeviceManager:
    """Central device management and coordination."""
```

#### 2. Device Classes (Specialized Controllers)
- **Climate**: HVAC and climate control systems
- **Light**: Lighting control with spectrum management
- **Pump**: Irrigation and nutrient pumps
- **Sensor**: Environmental monitoring devices
- **Exhaust/Intake**: Ventilation systems
- **Heater/Cooler**: Temperature control devices

#### 3. Device Capabilities System
- **Capability Mapping**: Device features and supported operations
- **State Management**: Real-time device state tracking
- **Error Handling**: Device failure detection and recovery

## Device Types and Capabilities

### Climate Control Devices

#### HVAC Climate Controller (`Climate.py`)
```python
class Climate(Device):
    """Advanced climate control with PID integration."""
```

**Capabilities:**
- **Temperature Control**: Heating and cooling with setpoints
- **Humidity Management**: Dehumidification and humidification
- **PID Integration**: Proportional-Integral-Derivative control
- **Mode Switching**: Auto, heat, cool, dry, fan modes

**Configuration:**
```python
climate_device = {
    "name": "Climate Controller",
    "type": "Climate",
    "capabilities": ["canHeat", "canCool", "canDehumidify", "canHumidify"],
    "entities": [
        {
            "entity_id": "climate.grow_room_hvac",
            "capabilities": ["temperature", "humidity", "mode"]
        }
    ]
}
```

### Lighting Systems

#### Advanced Light Controller (`Light.py`)
```python
class Light(Device):
    """Intelligent lighting with spectrum and DLI control."""
```

**Features:**
- **Spectrum Control**: Full spectrum, blue/red, UV, far-red options
- **DLI Management**: Daily Light Integral calculation and control
- **Sunrise/Sunset**: Gradual light transitions
- **Plant Stage Adaptation**: Automatic intensity adjustment

**Supported Light Types:**
- **Full Spectrum**: Balanced white light for all growth stages
- **Blue/Red**: Optimized ratios for vegetative and flowering
- **UV Light**: Enhanced resin production and pathogen control
- **Far-Red**: Stem elongation and flowering enhancement

#### Specialized Light Controllers
- **LightSpectrum**: Spectrum-specific control
- **LightUV**: UV lighting management
- **LightFarRed**: Far-red spectrum control

### Irrigation and Pumping

#### Pump Controller (`Pump.py`)
```python
class Pump(Device):
    """Precision pump control for irrigation and nutrients."""
```

**Capabilities:**
- **Flow Control**: Variable speed and duration control
- **Calibration**: Automated flow rate calibration
- **Scheduling**: Time-based and sensor-triggered operation
- **Safety Features**: Dry-run protection and overflow prevention

**Pump Types:**
- **Nutrient Pumps**: A, B, C nutrient concentrates
- **pH Pumps**: Acid/base adjustment
- **Water Pumps**: Fresh water delivery
- **Recirculation**: Hydroponic system circulation

### Ventilation Systems

#### Exhaust Fan Controller (`Exhaust.py`)
```python
class Exhaust(Device):
    """Exhaust fan control with VPD integration."""
```

**Features:**
- **Variable Speed**: Multi-speed operation
- **VPD Response**: Automatic adjustment based on vapor pressure deficit
- **Temperature Triggered**: High-temperature activation
- **Humidity Control**: Moisture removal for VPD management

#### Intake Fan Controller (`Intake.py`)
```python
class Intake(Device):
    """Intake fan for air exchange and CO2 distribution."""
```

**Capabilities:**
- **Air Exchange**: Fresh air introduction
- **CO2 Distribution**: Uniform CO2 levels
- **Pressure Balance**: Negative pressure prevention
- **Filter Management**: Air quality maintenance

### Sensing Devices

#### Environmental Sensor (`Sensor.py`)
```python
class Sensor(Device):
    """Environmental monitoring with calibration."""
```

**Sensor Types:**
- **Temperature**: Air and media temperature
- **Humidity**: Relative humidity measurement
- **VWC**: Volumetric Water Content (soil moisture)
- **pH**: Solution acidity measurement
- **EC/TDS**: Electrical conductivity
- **Light**: PPFD, lux, spectrum analysis
- **CO2**: Carbon dioxide monitoring

#### Specialized Sensors
- **ModbusSensor**: Industrial sensor integration
- **CO2 Sensor**: Carbon dioxide monitoring
- **Pressure Sensor**: System pressure monitoring

### Temperature Control

#### Heater Controller (`Heater.py`)
```python
class Heater(Device):
    """Heating device control with safety features."""
```

**Safety Features:**
- **Overheat Protection**: Maximum temperature limits
- **Gradual Heating**: Prevents thermal shock
- **Energy Management**: Duty cycle optimization
- **Frost Protection**: Minimum temperature maintenance

#### Cooler Controller (`Cooler.py`)
```python
class Cooler(Device):
    """Cooling device control with humidity consideration."""
```

**Features:**
- **Variable Speed**: Multi-stage cooling
- **Humidity Impact**: Condensation prevention
- **Energy Efficiency**: Adaptive operation
- **Noise Management**: Quiet operation modes

### Humidity Control

#### Humidifier (`Humidifier.py`)
```python
class Humidifier(Device):
    """Humidity control for VPD management."""
```

**Capabilities:**
- **Ultrasonic/Steam**: Different humidification methods
- **Variable Output**: Precise humidity control
- **Water Level Monitoring**: Automatic refill alerts
- **Mineral Deposit Prevention**: Self-cleaning features

#### Dehumidifier (`Dehumidifier.py`)
```python
class Dehumidifier(Device):
    """Moisture removal for humidity and VPD control."""
```

**Features:**
- **Auto Mode**: Continuous humidity monitoring
- **Drain Management**: Automatic condensate removal
- **Filter Maintenance**: Air quality preservation
- **Energy Recovery**: Heat reclamation options

### Atmospheric Control

#### CO2 Controller (`CO2.py`)
```python
class CO2(Device):
    """Carbon dioxide enrichment and monitoring."""
```

**Functions:**
- **CO2 Injection**: Precise gas delivery
- **Level Monitoring**: Real-time CO2 concentration
- **Safety Systems**: Ventilation interlocks
- **Plant Stage Adaptation**: Photosynthesis optimization

#### Ventilation Controller (`Ventilation.py`)
```python
class Ventilation(Device):
    """Advanced air circulation and exchange."""
```

**Capabilities:**
- **Multi-Zone**: Independent zone control
- **Air Quality**: VOC and particulate monitoring
- **Energy Recovery**: Heat exchange systems
- **Pressure Control**: Room pressurization

## Device Capability Mapping

### Capability Definitions

```python
CAPABILITY_MAPPING = {
    # Climate Control
    "canHeat": "Heating capability",
    "canCool": "Cooling capability",
    "canClimate":"Full climate control",
    "canHumidify": "Humidity increase capability",
    "canDehumidify": "Humidity decrease capability",
    "canVentilate": "Ventilation control",

    # Lighting
    "canLight": "Lighting control",
    "canSpectrum": "Spectrum adjustment",
    "canUV": "UV light control",
    "canFarRed": "Far-red spectrum control",

    # Irrigation
    "canPump": "Pumping capability",
    "canIrrigate": "Irrigation control",
    "canMeasure": "Measurement capability",

    # Ventilation
    "canExhaust": "Exhaust fan control",
    "canIntake": "Intake fan control",

    # Sensing
    "canSense": "Sensor capability",
    "canMeasureTemp": "Temperature measurement",
    "canMeasureHum": "Humidity measurement",
    "canMeasureVWC": "Soil moisture measurement",
    "canMeasurePH": "pH measurement",
    "canMeasureEC": "EC measurement",
    "canMeasureLight": "Light measurement",
    "canMeasureCO2": "CO2 measurement"
}
```

### Device Type Classification

```python
DEVICE_TYPE_MAPPING = {
    "Climate": ["canHeat", "canCool", "canHumidify", "canDehumidify"],
    "Light": ["canLight", "canSpectrum"],
    "Pump": ["canPump", "canIrrigate"],
    "Sensor": ["canSense", "canMeasureTemp", "canMeasureHum"],
    "Exhaust": ["canExhaust", "canVentilate"],
    "Heater": ["canHeat"],
    "Humidifier": ["canHumidify"],
    "Dehumidifier": ["canDehumidify"],
    "CO2": ["canMeasureCO2"],
    "GenericSwitch": ["canSwitch"]  # Custom devices
}
```

## Device State Management

### Device Initialization

```python
async def setupDevice(self, device_config):
    """Initialize and configure a device."""

    # 1. Validate device configuration
    await self._validate_device_config(device_config)

    # 2. Create device instance
    device_instance = self._create_device_instance(device_config)

    # 3. Register device capabilities
    await self._register_device_capabilities(device_instance)

    # 4. Initialize device state
    await device_instance.initialize()

    # 5. Start monitoring
    await device_instance.start_monitoring()

    # 6. Register with event system
    self.event_manager.on(f"Device_{device_config['name']}_Update",
                          device_instance.handle_update)
```

### State Synchronization

```python
async def synchronize_device_states(self):
    """Synchronize all device states with Home Assistant."""

    for device in self.devices:
        try:
            # Get current HA state
            ha_state = await self._get_ha_device_state(device.entity_id)

            # Update device internal state
            device.update_state(ha_state)

            # Validate state consistency
            if not device.validate_state():
                await self._correct_device_state(device)

        except Exception as e:
            _LOGGER.error(f"State sync failed for {device.name}: {e}")
            await self._handle_device_error(device, e)
```

## Device Control Interface

### Command Execution

```python
async def execute_device_command(self, device_id, command, parameters=None):
    """Execute a command on a specific device."""

    device = self.get_device_by_id(device_id)
    if not device:
        raise ValueError(f"Device {device_id} not found")

    # Validate command against device capabilities
    if not device.supports_command(command):
        raise ValueError(f"Device {device_id} does not support command {command}")

    # Check device state (powered, operational, etc.)
    if not device.is_operational():
        await self._recover_device(device)
        if not device.is_operational():
            raise RuntimeError(f"Device {device_id} is not operational")

    # Execute command
    result = await device.execute_command(command, parameters)

    # Update device state
    device.update_state_from_command(command, parameters)

    # Emit state change event
    await self.event_manager.emit("DeviceStateChanged", {
        "device_id": device_id,
        "command": command,
        "parameters": parameters,
        "result": result
    })

    return result
```

### Bulk Operations

```python
async def execute_bulk_command(self, device_ids, command, parameters=None):
    """Execute the same command on multiple devices."""

    results = {}
    tasks = []

    for device_id in device_ids:
        task = self.execute_device_command(device_id, command, parameters)
        tasks.append(task)

    # Execute in parallel with concurrency control
    semaphore = asyncio.Semaphore(5)  # Max 5 concurrent operations

    async def execute_with_semaphore(device_id, cmd_task):
        async with semaphore:
            try:
                result = await cmd_task
                results[device_id] = {"success": True, "result": result}
            except Exception as e:
                results[device_id] = {"success": False, "error": str(e)}

    # Execute all commands
    await asyncio.gather(*[
        execute_with_semaphore(device_id, task)
        for device_id, task in zip(device_ids, tasks)
    ])

    return results
```

## Error Handling and Recovery

### Device Failure Detection

```python
async def monitor_device_health(self):
    """Monitor device health and detect failures."""

    while self._monitoring_active:
        for device in self.devices:
            try:
                # Check device responsiveness
                if not await device.is_responsive():
                    await self._handle_unresponsive_device(device)
                    continue

                # Validate device readings
                readings = device.get_current_readings()
                if not self._validate_device_readings(device, readings):
                    await self._handle_invalid_readings(device, readings)
                    continue

                # Check for error conditions
                if device.has_error_condition():
                    await self._handle_device_error(device)

            except Exception as e:
                _LOGGER.error(f"Health check failed for {device.name}: {e}")
                await self._handle_monitoring_error(device, e)

        await asyncio.sleep(self.health_check_interval)
```

### Automatic Recovery

```python
async def _recover_device(self, device):
    """Attempt automatic device recovery."""

    recovery_attempts = device.get_recovery_attempts()

    if recovery_attempts >= self.max_recovery_attempts:
        _LOGGER.error(f"Max recovery attempts exceeded for {device.name}")
        await self._escalate_device_failure(device)
        return

    try:
        # Attempt power cycle
        await device.power_cycle()

        # Wait for device to reinitialize
        await asyncio.sleep(30)

        # Reinitialize device
        await device.reinitialize()

        # Verify recovery
        if await device.is_operational():
            _LOGGER.info(f"Successfully recovered device {device.name}")
            device.reset_recovery_attempts()
        else:
            device.increment_recovery_attempts()
            await self._schedule_recovery_retry(device)

    except Exception as e:
        _LOGGER.error(f"Recovery failed for {device.name}: {e}")
        device.increment_recovery_attempts()
        await self._escalate_device_failure(device)
```

## Configuration Management

### Device Configuration Schema

```python
DEVICE_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "type": {
            "type": "string",
            "enum": ["Climate", "Light", "Pump", "Sensor", "Exhaust", "Heater", "Humidifier", "CO2"]
        },
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "capabilities": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["entity_id"]
            }
        },
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "scope": {"type": "string", "enum": ["device", "entity"]}
                }
            }
        },
        "settings": {
            "type": "object",
            "properties": {
                "min_voltage": {"type": "number"},
                "max_voltage": {"type": "number"},
                "calibration_factor": {"type": "number"},
                "safety_limits": {"type": "object"}
            }
        }
    },
    "required": ["name", "type"]
}
```

### Dynamic Device Discovery

```python
async def discover_devices(self):
    """Automatically discover and configure devices."""

    # Get all HA entities
    ha_entities = await self._get_all_ha_entities()

    # Categorize by domain
    device_candidates = {
        "climate": [],
        "light": [],
        "switch": [],
        "sensor": []
    }

    for entity in ha_entities:
        domain = entity["entity_id"].split(".")[0]
        if domain in device_candidates:
            device_candidates[domain].append(entity)

    # Create device configurations
    discovered_devices = []

    for domain, entities in device_candidates.items():
        for entity in entities:
            device_config = await self._create_device_config_from_entity(domain, entity)
            if device_config:
                discovered_devices.append(device_config)

    return discovered_devices
```

## Performance Optimization

### Device Grouping and Batching

```python
async def execute_grouped_commands(self, command_groups):
    """Execute commands on grouped devices for efficiency."""

    # Group devices by physical location
    location_groups = self._group_devices_by_location(command_groups)

    # Group by command type
    command_batches = self._batch_commands_by_type(location_groups)

    # Execute batches with appropriate delays
    for batch in command_batches:
        await self._execute_command_batch(batch)

        # Allow system to stabilize
        await asyncio.sleep(self.batch_delay)
```

### Resource Management

```python
def optimize_device_resources(self):
    """Optimize device resource usage."""

    # Analyze usage patterns
    usage_patterns = self._analyze_device_usage()

    # Identify underutilized devices
    underutilized = self._find_underutilized_devices(usage_patterns)

    # Suggest power management
    for device in underutilized:
        if device.supports_power_management():
            self._schedule_power_optimization(device)

    # Optimize polling intervals
    self._optimize_polling_intervals(usage_patterns)
```

## Integration with Other Systems

### VPD System Integration

```python
async def coordinate_with_vpd_system(self, vpd_requirements):
    """Adjust device operation based on VPD requirements."""

    for device in self.devices:
        if device.supports_vpd_control():
            vpd_adjustment = self._calculate_vpd_adjustment(device, vpd_requirements)
            await device.apply_vpd_adjustment(vpd_adjustment)
```

### Lighting System Integration

```python
async def coordinate_with_lighting(self, light_schedule):
    """Adjust devices based on lighting schedule."""

    # Adjust ventilation for light periods
    if light_schedule["lights_on"]:
        await self._increase_ventilation_for_light_period()
    else:
        await self._reduce_ventilation_for_dark_period()

    # Adjust climate control for light transitions
    if light_schedule["transition_active"]:
        await self._adjust_climate_for_light_transition()
```

---

## Modbus Device Integration

OpenGrowBox supports Modbus devices (both TCP and RTU/Serial) for industrial-grade sensor and control integration.

### Supported Modbus Device Types

| Device Type | Label Keywords | Description |
|-------------|----------------|-------------|
| `ModbusDevice` | `modbus`, `modbus_device`, `modbus_rtu`, `modbus_tcp` | Generic Modbus control device |
| `ModbusSensor` | `modbus_sensor`, `modbus_temp`, `modbus_humidity` | Modbus sensor device (read-only) |

### Modbus Device Class (`OGBModbusDevice`)

```python
class OGBModbusDevice(Device):
    """Modbus device wrapper for standardized device communication."""
```

**Features:**
- **TCP and RTU Support**: Connect via Ethernet or Serial (RS485/RS232)
- **Register Operations**: Read/write holding, input, and coil registers
- **Auto-reconnection**: Automatic connection recovery
- **Scaling/Offset**: Configurable value transformation
- **Sensor Polling**: Automatic periodic sensor reading

### Configuration

Modbus devices require a `modbus_config` dictionary:

```python
# TCP Configuration
modbus_config = {
    "type": "tcp",              # Connection type
    "host": "192.168.1.100",    # Device IP address
    "port": 502,                # Modbus TCP port (default: 502)
    "slave_id": 1,              # Modbus slave/unit ID
    "control_register": 0,      # Coil address for on/off control
    "value_register": 1,        # Holding register for value setting
    "scale_factor": 1,          # Value scaling multiplier
    "poll_interval": 30,        # Seconds between sensor polls
}

# RTU/Serial Configuration
modbus_config = {
    "type": "rtu",              # Connection type
    "port": "/dev/ttyUSB0",     # Serial port
    "baudrate": 9600,           # Baud rate (default: 9600)
    "slave_id": 1,              # Modbus slave/unit ID
    "control_register": 0,      # Coil address for on/off control
    "value_register": 1,        # Holding register for value setting
}
```

### Register Mapping

Define sensor registers for automatic polling:

```python
device.registers = {
    "temperature": {
        "address": 0,           # Register address
        "type": "holding",      # "holding", "input", or "coil"
        "scale": 0.1,           # Multiply raw value by this
        "offset": 0,            # Add this to scaled value
        "context": "air",       # Sensor context: air, water, soil, light
    },
    "humidity": {
        "address": 1,
        "type": "holding",
        "scale": 0.1,
        "offset": 0,
        "context": "air",
    },
    "ec": {
        "address": 2,
        "type": "input",
        "scale": 0.001,         # Convert µS to mS
        "offset": 0,
        "context": "water",
    },
}
```

### Usage Example

```python
# Create a Modbus temperature/humidity sensor
modbus_sensor = ModbusSensor(
    deviceName="modbus_thsensor",
    deviceData=device_data,
    eventManager=event_manager,
    dataStore=data_store,
    deviceType="ModbusSensor",
    inRoom="GrowRoom",
    hass=hass,
    modbus_config={
        "type": "tcp",
        "host": "192.168.1.50",
        "port": 502,
        "slave_id": 1,
        "poll_interval": 30,
    }
)

# Define registers
modbus_sensor.registers = {
    "temperature": {"address": 0, "type": "holding", "scale": 0.1, "context": "air"},
    "humidity": {"address": 1, "type": "holding", "scale": 0.1, "context": "air"},
}
```

### Modbus API Methods

| Method | Description |
|--------|-------------|
| `connect_modbus()` | Establish Modbus connection |
| `read_register(address, count, type)` | Read register(s) |
| `write_register(address, value, type)` | Write to register |
| `turn_on()` | Write 1 to control coil |
| `turn_off()` | Write 0 to control coil |
| `set_value(value)` | Write scaled value to holding register |
| `poll_sensors()` | Read all configured sensor registers |

### Home Assistant Integration

Label your Modbus devices in Home Assistant for automatic detection:

```yaml
# Example: Label a Modbus sensor device
homeassistant:
  customize:
    sensor.modbus_growroom_temp:
      friendly_name: "Growroom Modbus Temperature"
      # Add label: modbus_sensor
```

Or use the OGB device label system:
- `modbus` - Generic Modbus device
- `modbus_sensor` - Modbus sensor device
- `modbus_tcp` - Modbus TCP device
- `modbus_rtu` - Modbus RTU/Serial device

### Troubleshooting

**Connection Issues:**
```python
# Check connection status
if not device.modbus_client.is_socket_open():
    await device.connect_modbus()
```

**Register Read Errors:**
- Verify slave ID matches device configuration
- Check register addresses in device documentation
- Ensure correct register type (holding vs input)

**Scaling Issues:**
- Raw values typically need scaling (e.g., 0.1 for temperatures)
- Check device documentation for register value format

---

## Device Management Summary

**Device management system implemented!** OpenGrowBox provides comprehensive device control and integration.

**Device Management Features:**
- ✅ **Device Classes**: Specialized controllers for each device type
- ✅ **Capability Mapping**: Feature detection and management
- ✅ **State Management**: Real-time device state tracking
- ✅ **Error Handling**: Device failure detection and recovery
- ✅ **Configuration**: Dynamic device discovery and setup
- ✅ **Performance**: Optimized resource usage and batching
- ✅ **Integration**: Coordination with VPD and lighting systems

**Device Types Supported:**
- ✅ **Climate**: HVAC systems with PID control
- ✅ **Lighting**: Spectrum and DLI management
- ✅ **Pumps**: Irrigation and nutrient delivery
- ✅ **Sensors**: Environmental monitoring
- ✅ **Ventilation**: Exhaust and intake control
- ✅ **Heating/Cooling**: Temperature management
- ✅ **Humidity**: Humidification and dehumidification
- ✅ **CO2**: Atmospheric control
- ✅ **Modbus**: Industrial TCP/RTU device integration

**Management Capabilities:**
- ✅ **Automatic Discovery**: HA entity detection and classification
- ✅ **Bulk Operations**: Concurrent device control
- ✅ **Health Monitoring**: Device failure detection
- ✅ **State Synchronization**: HA integration
- ✅ **Event-Driven**: Real-time response to changes

**For hardware setup guides, see [Supported Devices Hardware Guide](supported_devices_hardware.md)**</content>
<parameter name="filePath">docs/device_management.md