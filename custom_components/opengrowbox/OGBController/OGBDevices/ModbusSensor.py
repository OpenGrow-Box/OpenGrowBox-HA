import asyncio
import logging

from .ModbusDevice import OGBModbusDevice
from .Sensor import Sensor

_LOGGER = logging.getLogger(__name__)


class ModbusSensor(Sensor, OGBModbusDevice):
    """Kombination aus Sensor und Modbus-Funktionalität."""

    def __init__(self, *args, modbus_config=None, **kwargs):
        Sensor.__init__(self, *args, **kwargs)
        self.modbus_config = modbus_config

        # Additional attributes to match Sensor class
        self.sensorReadings = {"air": {}, "water": {}, "soil": {}, "light": {}}
        self._entity_to_config = {}
        self.isRunning = None
        self._alert_active = False
        self.isInitialized = False

        asyncio.create_task(self.setup_modbus_polling())

    async def setup_modbus_polling(self):
        """Startet automatisches Polling der Modbus-Register."""
        await self.connect_modbus()

        while True:
            if self.isRunning or self.isRunning is None:  # Poll if running or not set
                await self.poll_sensors()
            await asyncio.sleep(self.modbus_config.get("poll_interval", 30))

    async def poll_sensors(self):
        """Liest Sensor-Daten über Modbus aus und updates sensorReadings."""
        for sensor_name, register_info in self.registers.items():
            address = register_info["address"]
            reg_type = register_info.get("type", "holding")

            values = await self.read_register(address, 1, reg_type)
            if values:
                raw_value = values[0]
                scale = register_info.get("scale", 1)
                offset = register_info.get("offset", 0)
                actual_value = (raw_value * scale) + offset

                # Determine context (air, water, etc.) based on sensor_name or config
                context = register_info.get("context", "air")
                if context in self.sensorReadings:
                    self.sensorReadings[context][sensor_name] = actual_value

                # Emit Sensor-Update Event
                await self.event_manager.emit(
                    "DeviceStateUpdate",
                    {
                        "entity_id": f"sensor.{self.deviceName}_{sensor_name}",
                        "newValue": actual_value,
                        "oldValue": self.sensorReadings[context].get(sensor_name),
                    },
                )

                # Update entity_to_config if needed
                self._entity_to_config[f"sensor.{self.deviceName}_{sensor_name}"] = (
                    register_info
                )
