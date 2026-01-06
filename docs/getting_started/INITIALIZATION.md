# 🔄 OpenGrowBox Initialization Sequence

**How OpenGrowBox starts up and becomes operational**

---

## 📋 Overview

OpenGrowBox follows a structured initialization sequence that transforms HA entities into an intelligent grow control system. The process occurs in phases, with each phase building on the previous one to ensure reliable operation.

### Initialization Flow

```
HA Startup → OGB Integration Load → Room Discovery → Device Recognition
       ↓              ↓              ↓              ↓
   Service Reg. → Capability Mapping → VPD Setup → Automation Start
```

---

## 🚀 Phase 1: Home Assistant Integration Loading

### 1.1 Component Registration

When HA starts, it discovers and loads the OpenGrowBox custom component:

```python
# custom_components/opengrowbox/__init__.py
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenGrowBox from a config entry."""

    # Create data coordinator for sensor data management
    coordinator = OGBDataCoordinator(hass, entry)

    # Store coordinator in HA data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward setup to sensor/switch/select platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register OGB-specific services
    await async_register_services(hass)

    return True
```

**Key Actions:**
- ✅ Data coordinator initialized for sensor synchronization
- ✅ Platforms (sensor, switch, select, number) set up
- ✅ OGB services registered with HA

### 1.2 Platform Initialization

OGB forwards to these HA platforms:

| Platform | Purpose | Example Entities |
|----------|---------|------------------|
| **Sensor** | Environmental data | `sensor.ogb_temperature` |
| **Switch** | Device control | `switch.ogb_heater` |
| **Select** | Mode selection | `select.ogb_control_mode` |
| **Number** | Adjustable values | `number.ogb_vpd_target` |

---

## 🏠 Phase 2: Room & Area Discovery

### 2.1 HA Area Scanning

OGB scans all HA areas to identify grow rooms:

```python
# RegistryListener discovers areas with OGB devices
async def get_filtered_entities_with_value(self, room_name: str):
    """Find all entities in a specific HA area."""

    # Get all entities from HA registry
    all_entities = await self.hass.states.async_all()

    # Filter for entities in target area
    room_entities = [
        entity for entity in all_entities
        if getattr(entity, 'area_id', None) == room_name
    ]

    return room_entities
```

### 2.2 Room Configuration Loading

For each discovered room, OGB loads configuration:

```python
# Load room-specific settings
room_config = {
    "name": "Main Grow Tent",
    "plant_type": "cannabis",
    "stage": "vegetative",
    "vpd_target": 1.2,
    "temperature_target": 25.0,
    "humidity_target": 60.0,
    "co2_target": 800
}
```

**Validation:**
- ✅ Plant type recognized
- ✅ Growth stage valid
- ✅ Environmental targets within safe ranges

---

## 🔍 Phase 3: Device Recognition & Setup

### 3.1 Entity Discovery

OGB scans entities in each room using label-based detection:

```python
# DeviceManager.identify_device() - Label-based recognition
def identify_device(self, device_name: str, device_data: List[Dict],
                   device_labels: List[Dict] = None) -> Device:
    """Identify device type from HA entity labels."""

    if device_labels:
        label_names = [lbl.get("name", "").lower() for lbl in device_labels]

        # Check for FridgeGrow devices
        if any(kw in label_names for kw in ["fridgegrow", "plantalytix"]):
            return FridgeGrowDevice(...)

        # Check for standard device types
        for label in label_names:
            if label in ["heater", "temperature", "humidity"]:
                device_type = self._map_label_to_device_type(label)
                return self.get_device_class(device_type)(...)
```

### 3.2 Device Instantiation

Recognized devices are instantiated with their configurations:

```python
# Example: Heater device creation
heater = Heater(
    deviceName="switch.grow_tent_heater",
    deviceData=[{"entity_id": "switch.grow_tent_heater"}],
    eventManager=event_manager,
    dataStore=data_store,
    deviceType="Heater",
    inRoom="grow_tent",
    hass=hass
)

# Device registers capabilities
await heater.identifyCapabilities()  # Registers "canHeat"
```

### 3.3 Capability Registration

Devices register their capabilities with the system:

```python
# DataStore capability tracking
capabilities = {
    "canHeat": {
        "state": True,
        "count": 2,
        "devEntities": ["switch.heater_1", "switch.heater_2"]
    },
    "canMeasureTemp": {
        "state": True,
        "count": 1,
        "devEntities": ["sensor.temperature"]
    }
}
```

**Device Status:**
- ✅ **Heaters**: 2 devices registered
- ✅ **Sensors**: Temperature, humidity, CO₂ active
- ✅ **Lights**: LED controllers configured
- ✅ **FridgeGrow**: MQTT devices connected

---

## 🎯 Phase 4: Control System Activation

### 4.1 VPD Engine Initialization

VPD calculations require sensor data to be available:

```python
# VPDManager initialization
async def initialize_vpd_engine(self):
    """Set up VPD calculations with available sensors."""

    # Find temperature and humidity sensors
    temp_sensors = self._find_sensors_by_capability("canMeasureTemp")
    hum_sensors = self._find_sensors_by_capability("canMeasureHum")

    if not temp_sensors or not hum_sensors:
        _LOGGER.warning("Insufficient sensors for VPD calculation")
        return

    # Initialize VPD tracking
    self.vpd_history = []
    self.current_vpd = await self.calculateVPD(
        temperature=self._get_average_reading(temp_sensors),
        humidity=self._get_average_reading(hum_sensors)
    )

    _LOGGER.info(f"VPD engine initialized: {self.current_vpd:.2f} kPa")
```

### 4.2 Control Mode Setup

Based on configuration, OGB activates the appropriate control mode:

```python
# ModeManager setup
async def initialize_control_mode(self, mode: str):
    """Activate specified control mode."""

    if mode == "vpd_perfection":
        await self.activate_vpd_perfection_mode()
    elif mode == "ai_control":
        await self.activate_ai_control_mode()
    elif mode == "manual":
        await self.activate_manual_mode()

    _LOGGER.info(f"Control mode activated: {mode}")
```

### 4.3 Event System Registration

Devices register for control events:

```python
# Device event registration during deviceInit
async def deviceInit(self, entitys: List[Dict]):
    """Initialize device and register event handlers."""

    # Register for capability-specific events
    if self.deviceType == "Heater":
        self.eventManager.on("Increase Heater", self.increaseAction)
        self.eventManager.on("Reduce Heater", self.reduceAction)

    elif self.deviceType == "FridgeGrow":
        # FridgeGrow specific events
        self.eventManager.on("Increase Heater", self.increaseAction)
        self.eventManager.on("Increase VPD", self._handle_vpd_adjustment)
```

### 4.4 Automation Startup

Final phase activates automated control:

```python
# ActionManager startup
async def start_automation(self):
    """Begin automated grow control."""

    # Start VPD monitoring loop
    asyncio.create_task(self.vpd_monitoring_loop())

    # Start action execution loop
    asyncio.create_task(self.action_execution_loop())

    # Enable device control
    await self.enable_device_control()

    _LOGGER.info("OpenGrowBox automation started successfully")
```

---

## 📊 Phase 5: Operational Verification

### 5.1 System Health Check

OGB performs startup health verification:

```python
# Health check after initialization
async def perform_startup_health_check(self):
    """Verify all systems are operational."""

    checks = {
        "sensors": await self._check_sensor_health(),
        "devices": await self._check_device_health(),
        "vpd": await self._check_vpd_calculation(),
        "automation": await self._check_automation_status()
    }

    failed_checks = [k for k, v in checks.items() if not v]

    if failed_checks:
        _LOGGER.warning(f"Health check failed: {failed_checks}")
        await self._enter_safe_mode()
    else:
        _LOGGER.info("All systems operational")
        await self._enter_normal_operation()
```

### 5.2 Safe Mode Activation

If issues detected, OGB enters safe mode:

```python
async def _enter_safe_mode(self):
    """Activate safe mode with manual control only."""

    # Disable automated control
    await self.disable_automation()

    # Send alerts
    await self.send_alert("System entered safe mode due to initialization issues")

    # Enable manual override
    await self.enable_manual_control()

    _LOGGER.warning("Safe mode activated - manual control only")
```

---

## 🔍 Initialization Monitoring

### Debug Logging

Enable detailed initialization logging:

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.opengrowbox: debug
    custom_components.opengrowbox.OGBController: debug
```

### Common Initialization Issues

#### "No devices found"
```
Cause: Entities not assigned to HA areas or missing labels
Fix: Assign entities to correct areas and add device labels
```

#### "VPD calculation failed"
```
Cause: Missing temperature/humidity sensors
Fix: Add sensors with proper labels ("temperature", "humidity")
```

#### "FridgeGrow not recognized"
```
Cause: Missing FridgeGrow labels
Fix: Add "fridgegrow" + output type labels to entities
```

#### "Capability not registered"
```
Cause: Device type not recognized
Fix: Check device labels match expected patterns
```

---

## ⏱️ Initialization Timeline

| Phase | Duration | Status Indicators |
|-------|----------|-------------------|
| **HA Integration** | < 5s | Integration loaded |
| **Room Discovery** | < 2s | Rooms found |
| **Device Setup** | 5-15s | Devices initialized |
| **VPD Engine** | < 2s | VPD calculating |
| **Automation** | < 1s | Events registered |
| **Health Check** | < 5s | System operational |

**Total initialization time: 15-30 seconds**

---

## 🚨 Emergency Procedures

### Force Restart
If initialization hangs:

```bash
# Restart HA
ha core restart

# Check logs
tail -f /config/home-assistant.log | grep opengrowbox
```

### Reset Configuration
If configuration corrupted:

```bash
# Backup current config
cp -r /config/.storage/opengrowbox* /config/backup/

# Reset OGB data
rm -rf /config/.storage/opengrowbox*

# Restart HA for clean initialization
ha core restart
```

---

## 📋 Initialization Checklist

- [ ] **HA Integration**: OGB appears in HA integrations
- [ ] **Rooms**: Grow areas discovered and configured
- [ ] **Devices**: Sensors and actuators recognized
- [ ] **Capabilities**: Device capabilities registered
- [ ] **VPD Engine**: Environmental calculations active
- [ ] **Events**: Control events registered
- [ ] **Automation**: Grow control loops running
- [ ] **Health**: All systems passing checks

**✅ System ready for automated plant cultivation!**