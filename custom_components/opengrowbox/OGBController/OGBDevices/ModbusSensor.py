import asyncio
import logging

from .ModbusDevice import OGBModbusDevice
from .Sensor import Sensor

_LOGGER = logging.getLogger(__name__)


class ModbusSensor(OGBModbusDevice, Sensor):
    """Kombination aus Sensor und Modbus-Funktionalität."""

    def __init__(self, *args, modbus_config=None, **kwargs):
        # Initialize OGBModbusDevice first (Device parent)
        OGBModbusDevice.__init__(self, *args, modbus_config=modbus_config, **kwargs)

        # Override Sensor-specific attributes
        self.sensorReadings = {"air": {}, "water": {}, "soil": {}, "light": {}}
        self._entity_to_config = {}
        self._alert_active = False

        # Initialize Sensor functionality
        self._translation_cache = self._build_translation_cache()
        self.medium_label = self._extract_medium_label(self.deviceLabel)
        self.ppfdDLI_label = None

        # Register Sensor events
        self.event_manager.on("ReadSensor", self.readSensor)
        self.event_manager.on("ReadAllSensors", self.readAllSensors)
        self.event_manager.on("GetSensorValue", self.getSensorValue)
        self.event_manager.on("CheckSensor", self.checkSensor)

        # Start Modbus polling after full initialization
        self._polling_task = None

    async def setup_modbus_polling(self):
        """Startet automatisches Polling der Modbus-Register."""
        try:
            success = await self.connect_modbus()
            if not success:
                _LOGGER.error(f"Failed to connect Modbus for {self.deviceName}")
                return

            self._polling_task = asyncio.create_task(self._polling_loop())
            _LOGGER.info(f"Modbus polling started for {self.deviceName}")

        except Exception as e:
            _LOGGER.error(f"Error setting up Modbus polling for {self.deviceName}: {e}")

    async def _polling_loop(self):
        """Main polling loop with proper error handling and shutdown support."""
        try:
            while self.isRunning or self.isRunning is None:
                try:
                    if self.isRunning or self.isRunning is None:  # Poll if running or not set
                        await self.poll_sensors()
                except Exception as e:
                    _LOGGER.error(f"Error polling Modbus sensors for {self.deviceName}: {e}")
                    # Brief pause on error before retry
                    await asyncio.sleep(5)

                await asyncio.sleep(self.modbus_config.get("poll_interval", 30))

        except asyncio.CancelledError:
            _LOGGER.info(f"Modbus polling stopped for {self.deviceName}")
        except Exception as e:
            _LOGGER.error(f"Unexpected error in Modbus polling loop for {self.deviceName}: {e}")

    async def stop_polling(self):
        """Stop the Modbus polling loop."""
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        _LOGGER.info(f"Modbus polling stopped for {self.deviceName}")

    async def poll_sensors(self):
        """Liest Sensor-Daten über Modbus aus und updates sensorReadings."""
        if not self.registers:
            _LOGGER.warning(f"No registers configured for Modbus sensor {self.deviceName}")
            return

        for sensor_name, register_info in self.registers.items():
            try:
                address = register_info["address"]
                reg_type = register_info.get("type", "holding")

                values = await self.read_register(address, 1, reg_type)
                if values is not None:
                    raw_value = values[0]
                    scale = register_info.get("scale", 1.0)
                    offset = register_info.get("offset", 0.0)
                    actual_value = (raw_value * scale) + offset

                    # Determine context (air, water, etc.) based on sensor_name or config
                    context = register_info.get("context", "air")
                    if context in self.sensorReadings:
                        old_value = self.sensorReadings[context].get(sensor_name)
                        self.sensorReadings[context][sensor_name] = actual_value

                        # Emit Sensor-Update Event
                        await self.event_manager.emit(
                            "DeviceStateUpdate",
                            {
                                "entity_id": f"sensor.{self.deviceName}_{sensor_name}",
                                "newValue": actual_value,
                                "oldValue": old_value,
                            },
                        )

                        # Update entity_to_config for Sensor class compatibility
                        self._entity_to_config[f"sensor.{self.deviceName}_{sensor_name}"] = register_info

                        _LOGGER.debug(f"Modbus sensor {self.deviceName}_{sensor_name}: {actual_value}")
                else:
                    _LOGGER.warning(f"Failed to read Modbus register {address} for {sensor_name}")

            except Exception as e:
                _LOGGER.error(f"Error polling Modbus sensor {sensor_name} on {self.deviceName}: {e}")

    def deviceInit(self, entitys):
        """Initialize device with proper Modbus configuration."""
        # Call parent initialization first
        OGBModbusDevice.deviceInit(self, entitys)

        # Configure registers from entity data
        if "modbus" in entitys and "registers" in entitys["modbus"]:
            self.registers = entitys["modbus"]["registers"]
            _LOGGER.info(f"Configured {len(self.registers)} Modbus registers for {self.deviceName}")

        # Start polling after configuration
        if self.registers:
            asyncio.create_task(self.setup_modbus_polling())
        else:
            _LOGGER.warning(f"No Modbus registers configured for {self.deviceName}")

    async def deviceUpdate(self, updateData):
        """Handle device updates with Modbus reconnection if needed."""
        # Check if we need to reconnect
        if not self.modbus_client or not self.modbus_client.is_socket_open():
            await self.connect_modbus()

        # Call parent update
        await OGBModbusDevice.deviceUpdate(self, updateData)

    async def WorkMode(self, workmode):
        """Handle work mode changes with polling control."""
        self.inWorkMode = workmode.get("workMode", False)

        # Start/stop polling based on work mode
        if self.inWorkMode and not self._polling_task:
            await self.setup_modbus_polling()
        elif not self.inWorkMode and self._polling_task:
            await self.stop_polling()

    # Sensor class compatibility methods are inherited from Sensor parent
