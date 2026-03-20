# OGBDevices/ModbusDevice.py
import logging

from pymodbus.client import ModbusSerialClient, ModbusTcpClient

from .Device import Device

_LOGGER = logging.getLogger(__name__)


class OGBModbusDevice(Device):
    """Modbus-Gerät als Wrapper für standardisierte Device-Kommunikation."""

    def __init__(
        self,
        deviceName,
        deviceData,
        eventManager,
        dataStore,
        deviceType,
        inRoom,
        hass,
        deviceLabel="EMPTY",
        allLabels=[],
        modbus_config=None,
    ):

        self.modbus_client = None
        self.modbus_config = modbus_config or {}
        self.slave_id = self.modbus_config.get("slave_id", 1)
        self.registers = {}  # Register-Mapping
        self.isModbusDevice = True

        # Additional attributes to match Device class
        self.isSpecialDevice = False
        self.isDimmable = False
        self.isAcInfinDev = False
        self.switches = []
        self.options = []
        self.sensors = []
        self.ogbsettings = []
        self.initialization = False
        self.inWorkMode = False
        self.isInitialized = False

        super().__init__(
            deviceName,
            deviceData,
            eventManager,
            dataStore,
            deviceType,
            inRoom,
            hass,
            deviceLabel,
            allLabels,
        )

        # Register additional events to match Device
        self.event_manager.on("WorkModeChange", self.WorkMode)
        self.event_manager.on("SetDeviceMinMax", self.userSetMinMax)
        self.event_manager.on("MinMaxControlEnabled", self.on_minmax_control_enabled)
        self.event_manager.on("MinMaxControlDisabled", self.on_minmax_control_disabled)

    async def connect_modbus(self):
        """Stellt Modbus-Verbindung her."""
        try:
            if self.modbus_config.get("type") == "tcp":
                self.modbus_client = ModbusTcpClient(
                    host=self.modbus_config["host"],
                    port=self.modbus_config.get("port", 502),
                )
            else:  # RTU/Serial
                self.modbus_client = ModbusSerialClient(
                    port=self.modbus_config["port"],
                    baudrate=self.modbus_config.get("baudrate", 9600),
                )

            if self.modbus_client.connect():
                _LOGGER.info(f"Modbus-Verbindung zu {self.deviceName} erfolgreich")
                return True
            return False
        except Exception as e:
            _LOGGER.error(
                f"Modbus-Verbindung fehlgeschlagen für {self.deviceName}: {e}"
            )
            return False

    async def read_register(self, address, count=1, register_type="holding"):
        """Liest Modbus-Register."""
        if not self.modbus_client or not self.modbus_client.is_socket_open():
            await self.connect_modbus()

        try:
            if register_type == "holding":
                result = self.modbus_client.read_holding_registers(
                    address, count, slave=self.slave_id
                )
            elif register_type == "input":
                result = self.modbus_client.read_input_registers(
                    address, count, slave=self.slave_id
                )
            else:
                result = self.modbus_client.read_coils(
                    address, count, slave=self.slave_id
                )

            if not result.isError():
                return result.registers if hasattr(result, "registers") else result.bits
            else:
                _LOGGER.error(f"Modbus-Lesefehler bei {self.deviceName}: {result}")
                return None
        except Exception as e:
            _LOGGER.error(f"Fehler beim Lesen von Register {address}: {e}")
            return None

    async def write_register(self, address, value, register_type="holding"):
        """Schreibt in Modbus-Register."""
        if not self.modbus_client or not self.modbus_client.is_socket_open():
            await self.connect_modbus()

        try:
            if register_type == "holding":
                result = self.modbus_client.write_register(
                    address, value, slave=self.slave_id
                )
            else:  # Coil
                result = self.modbus_client.write_coil(
                    address, value, slave=self.slave_id
                )

            if not result.isError():
                _LOGGER.debug(f"Modbus-Schreibvorgang erfolgreich: {address} = {value}")
                return True
            return False
        except Exception as e:
            _LOGGER.error(f"Fehler beim Schreiben in Register {address}: {e}")
            return False

    # Override Device-Methoden für Modbus-Steuerung
    async def turn_on(self, **kwargs):
        """Schaltet Modbus-Gerät ein."""
        control_address = self.modbus_config.get("control_register")
        if control_address:
            success = await self.write_register(control_address, 1, "coil")
            if success:
                self.isRunning = True
                return

        # Fallback auf Standard-Methode
        await super().turn_on(**kwargs)

    async def turn_off(self, **kwargs):
        """Schaltet Modbus-Gerät aus."""
        control_address = self.modbus_config.get("control_register")
        if control_address:
            success = await self.write_register(control_address, 0, "coil")
            if success:
                self.isRunning = False
                return

        await super().turn_off(**kwargs)

    async def set_value(self, value):
        """Setzt Wert über Modbus (z.B. Duty Cycle)."""
        value_address = self.modbus_config.get("value_register")
        if value_address:
            # Skalierung anwenden falls nötig
            scaled_value = int(value * self.modbus_config.get("scale_factor", 1))
            await self.write_register(value_address, scaled_value)
        else:
            await super().set_value(value)

    async def poll_sensors(self):
        """Liest Sensor-Daten über Modbus aus."""
        for sensor_name, register_info in self.registers.items():
            address = register_info["address"]
            reg_type = register_info.get("type", "holding")

            values = await self.read_register(address, 1, reg_type)
            if values:
                # Skalierung/Transformation
                raw_value = values[0]
                scale = register_info.get("scale", 1)
                offset = register_info.get("offset", 0)

                actual_value = (raw_value * scale) + offset

                # Emit Sensor-Update Event
                await self.event_manager.emit(
                    "DeviceStateUpdate",
                    {
                        "entity_id": f"sensor.{self.deviceName}_{sensor_name}",
                        "newValue": actual_value,
                        "oldValue": None,
                    },
                )

    # Additional methods to match Device class
    @property
    def option_count(self) -> int:
        """Gibt die Anzahl aller Optionen zurück."""
        return len(self.options)

    @property
    def switch_count(self) -> int:
        """Gibt die Anzahl aller Switches zurück."""
        return len(self.switches)

    @property
    def sensor_count(self) -> int:
        """Gibt die Anzahl aller Sensoren zurück."""
        return len(self.sensors)

    def deviceInit(self, entitys):
        """Initialize device with data, matching Device class."""
        # Parse modbus_config from entitys if available
        if "modbus" in entitys:
            self.modbus_config.update(entitys["modbus"])

        # Configure capabilities based on device type and modbus config
        self._configure_capabilities()

        # Ensure IP is provided or detected
        if not self.modbus_config.get("host"):
            self.modbus_config["host"] = self.detect_modbus_ip()
        super().deviceInit(entitys)

    def _configure_capabilities(self):
        """Configure device capabilities for ActionManager integration."""
        # Map device types to capabilities
        device_type_capabilities = {
            "ModbusDevice": [],  # Generic - configured via modbus config
            "ModbusSensor": ["canSense"],  # Sensors can provide sensor data
        }

        # Get capabilities for this device type
        capabilities = device_type_capabilities.get(self.deviceType, [])

        # Add capabilities from modbus config if specified
        modbus_capabilities = self.modbus_config.get("capabilities", [])
        capabilities.extend(modbus_capabilities)

        # Register with capabilities system (if data_store has capabilities)
        if hasattr(self, 'data_store') and self.data_store:
            current_caps = self.data_store.get("capabilities") or {}

            for cap in capabilities:
                if cap not in current_caps:
                    current_caps[cap] = {"state": False, "count": 0, "devEntities": []}

                if self.deviceName not in current_caps[cap]["devEntities"]:
                    current_caps[cap]["state"] = True
                    current_caps[cap]["count"] += 1
                    current_caps[cap]["devEntities"].append(self.deviceName)

            self.data_store.setDeep("capabilities", current_caps)
            _LOGGER.info(f"Registered Modbus device {self.deviceName} with capabilities: {capabilities}")

    def detect_modbus_ip(self):
        """Detect Modbus device IP (placeholder for auto-detection)."""
        # TODO: Implement IP scanning logic
        _LOGGER.info(f"Detecting IP for Modbus device {self.deviceName}")
        return "192.168.1.100"  # Placeholder

    async def deviceUpdate(self, updateData):
        """Handle device state updates, matching Device class."""
        # Custom handling for Modbus devices
        pass

    async def WorkMode(self, workmode):
        """Handle work mode changes, matching Device class."""
        self.inWorkMode = workmode.get("workMode", False)

    async def userSetMinMax(self, data):
        """Handle min/max settings, matching Device class."""
        # Check if min/max is active during sunphase
        if hasattr(self, 'sunPhaseActive') and self.sunPhaseActive:
            _LOGGER.info(f"{self.deviceName}: Cannot change min/max during active sunphase")
            return

        if not self.isDimmable:
            return
        
        _LOGGER.debug(f"{self.deviceName}: Processing SetMinMax event: {data}")

        # deviceType-Filter – data kann String oder Dict sein
        event_device_type = None
        if isinstance(data, str):
            event_device_type = data
        elif isinstance(data, dict):
            event_device_type = data.get("deviceType", "")
        
        # Case-insensitive device type comparison
        if event_device_type and event_device_type.lower() != self.deviceType.lower():
            _LOGGER.debug(f"{self.deviceName}: ignoring SetMinMax – event for '{event_device_type}', I am '{self.deviceType}'")
            return

        try:
            minMaxSets = self.data_store.getDeep(f"DeviceMinMax.{self.deviceType}")
        except AttributeError:
            _LOGGER.warning(f"{self.deviceName}: dataStore nicht verfügbar in userSetMinMax")
            return

        # Check if min/max is active for this device type
        if not isinstance(minMaxSets, dict):
            _LOGGER.warning(f"{self.deviceName}: minMaxSets is not a dict for {self.deviceType}")
            return
            
        if not minMaxSets.get("active", False):
            _LOGGER.debug(f"{self.deviceName}: min/max control is not active for {self.deviceType}")
            return
            
        _LOGGER.debug(f"{self.deviceName}: min/max control is active for {self.deviceType}")

        # Handle voltage settings for lights or duty settings for other devices
        if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets and self.deviceType == "Light":
            try:
                old_min, old_max = self.minVoltage, self.maxVoltage
                self.minVoltage = float(minMaxSets.get("minVoltage"))
                self.maxVoltage = float(minMaxSets.get("maxVoltage"))
                _LOGGER.info(f"{self.deviceName}: Updated voltage min/max: min={old_min}→{self.minVoltage}%, max={old_max}→{self.maxVoltage}%")
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: Ungültige Voltage-Werte: {minMaxSets.get('minVoltage')}, {minMaxSets.get('maxVoltage')}")
                return

            # Apply voltage settings via modbus if running
            if self.isRunning:
                await self._apply_voltage_settings(self.clamp_voltage(self.voltage))
            else:
                self.voltage = self.clamp_voltage(self.voltage)
                _LOGGER.info(f"{self.deviceName}: Not running - voltage clamped to {self.voltage}% but device NOT turned on")

        elif "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
            try:
                old_min, old_max = self.minDuty, self.maxDuty
                self.minDuty = float(minMaxSets.get("minDuty"))
                self.maxDuty = float(minMaxSets.get("maxDuty"))
                _LOGGER.info(f"{self.deviceName}: Updated duty min/max: min={old_min}→{self.minDuty}%, max={old_max}→{self.maxDuty}%")
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: Ungültige Duty-Werte: {minMaxSets.get('minDuty')}, {minMaxSets.get('maxDuty')}")
                return
            
            # Apply duty settings via modbus
            await self._apply_duty_settings(self.clamp_duty_cycle(self.dutyCycle))

    async def _apply_voltage_settings(self, voltage):
        """Apply voltage settings via modbus."""
        if not hasattr(self, 'voltage_register') or not self.voltage_register:
            _LOGGER.warning(f"{self.deviceName}: No voltage register configured")
            return
            
        try:
            # Scale voltage if needed
            scaled_voltage = int(voltage * self.modbus_config.get("voltage_scale_factor", 1))
            await self.write_register(self.voltage_register, scaled_voltage)
            _LOGGER.debug(f"{self.deviceName}: Applied voltage setting {voltage}% (scaled: {scaled_voltage})")
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error applying voltage settings: {e}")

    async def _apply_duty_settings(self, duty):
        """Apply duty cycle settings via modbus."""
        if not hasattr(self, 'duty_register') or not self.duty_register:
            _LOGGER.warning(f"{self.deviceName}: No duty register configured")
            return
            
        try:
            # Scale duty if needed
            scaled_duty = int(duty * self.modbus_config.get("duty_scale_factor", 1))
            await self.write_register(self.duty_register, scaled_duty)
            _LOGGER.debug(f"{self.deviceName}: Applied duty setting {duty}% (scaled: {scaled_duty})")
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error applying duty settings: {e}")

    def clamp_voltage(self, value):
        """Clamp voltage to min/max range."""
        try:
            v = float(value) if value is not None else 0.0
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.deviceName}: clamp_voltage ungültiger Wert '{value}', nutze 0.0")
            v = 0.0

        try:
            min_v = float(self.minVoltage) if hasattr(self, 'minVoltage') and self.minVoltage is not None else None
            max_v = float(self.maxVoltage) if hasattr(self, 'maxVoltage') and self.maxVoltage is not None else None
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.deviceName}: Ungültige min/max Voltage-Werte, kein Clamping")
            return v

        if min_v is not None and max_v is not None:
            return max(min_v, min(max_v, v))
        return v

    def clamp_duty_cycle(self, value):
        """Clamp duty cycle to min/max range."""
        if value is None:
            _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle None, nutze 50%")
            value = 50.0
        else:
            try:
                value = float(value)
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle ungültiger Wert '{value}', nutze 50%")
                value = 50.0

        try:
            min_duty = float(self.minDuty) if hasattr(self, 'minDuty') and self.minDuty is not None else 0.0
            max_duty = float(self.maxDuty) if hasattr(self, 'maxDuty') and self.maxDuty is not None else 100.0
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.deviceName}: Ungültige min/max Duty-Werte, nutze 0-100")
            min_duty, max_duty = 0.0, 100.0

        return int(max(min_duty, min(max_duty, value)))

    async def on_minmax_control_enabled(self, data) -> None:
        """Handle MinMaxControlEnabled event."""
        minmax_device_types = {"Light", "Exhaust", "Intake", "Ventilation"}
        if self.deviceType not in minmax_device_types:
            _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring MinMaxControlEnabled")
            return

        # Check if this event is for this device type
        if isinstance(data, dict):
            event_device_type = data.get("deviceType", "")
            if event_device_type and event_device_type.lower() != self.deviceType.lower():
                _LOGGER.debug(f"{self.deviceName}: ignoring MinMaxControlEnabled – event for '{event_device_type}', I am '{self.deviceType}'")
                return

        _LOGGER.info(f"{self.deviceName}: MinMax control enabled - applying user-defined min/max values")

        # Re-apply min/max settings
        await self.userSetMinMax(self.deviceType)

    async def on_minmax_control_disabled(self, data) -> None:
        """Handle MinMaxControlDisabled event."""
        minmax_device_types = {"Light", "Exhaust", "Intake", "Ventilation"}
        if self.deviceType not in minmax_device_types:
            _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring MinMaxControlDisabled")
            return

        # Check if this event is for this device type
        if isinstance(data, dict):
            event_device_type = data.get("deviceType", "")
            if event_device_type and event_device_type.lower() != self.deviceType.lower():
                _LOGGER.debug(f"{self.deviceName}: ignoring MinMaxControlDisabled – event for '{event_device_type}', I am '{self.deviceType}'")
                return

        _LOGGER.info(f"{self.deviceName}: MinMax control disabled - resetting to default values")

        # Reset to default values
        if self.deviceType == "Light":
            # Reset voltage to default
            self.minVoltage = float(getattr(self, 'initVoltage', 20))
            self.maxVoltage = 100.0
            _LOGGER.info(f"{self.deviceName}: Reset voltage min/max to defaults: min={self.minVoltage}%, max={self.maxVoltage}%")
            
            if self.isRunning and hasattr(self, 'voltage'):
                self.voltage = self.clamp_voltage(self.voltage)
                await self._apply_voltage_settings(self.voltage)
        else:
            # Reset duty to default
            self.minDuty = 0.0
            self.maxDuty = 100.0
            _LOGGER.info(f"{self.deviceName}: Reset duty min/max to defaults: min={self.minDuty}%, max={self.maxDuty}%")
            
            if self.isRunning and hasattr(self, 'dutyCycle'):
                self.dutyCycle = self.clamp_duty_cycle(self.dutyCycle)
                await self._apply_duty_settings(self.dutyCycle)
