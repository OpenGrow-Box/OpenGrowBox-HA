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
        hass=None,
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
        self.event_manager.on("SetMinMax", self.userSetMinMax)

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
        # Ensure IP is provided or detected
        if not self.modbus_config.get("host"):
            self.modbus_config["host"] = self.detect_modbus_ip()
        super().deviceInit(entitys)

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
        # Custom handling
        pass
