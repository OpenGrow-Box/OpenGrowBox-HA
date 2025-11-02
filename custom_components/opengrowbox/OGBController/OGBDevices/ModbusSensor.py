from .Sensor import Sensor
from .ModbusDevice import ModbusDevice
import asyncio

class ModbusSensor(Sensor, ModbusDevice):
    """Kombination aus Sensor und Modbus-Funktionalität."""
    
    def __init__(self, *args, modbus_config=None, **kwargs):
        Sensor.__init__(self, *args, **kwargs)
        self.modbus_config = modbus_config
        asyncio.create_task(self.setup_modbus_polling())
    
    async def setup_modbus_polling(self):
        """Startet automatisches Polling der Modbus-Register."""
        await self.connect_modbus()
        
        while True:
            await self.poll_sensors()
            await asyncio.sleep(self.modbus_config.get("poll_interval", 30))