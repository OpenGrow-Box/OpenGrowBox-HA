import logging
import asyncio

_LOGGER = logging.getLogger(__name__)

class OGBFallBackManager:
    def __init__(self, hass, dataStore, eventManager,room,regListener):
        self.name = "OGB FallBack Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False
        
        ## Events Register
        self.eventManager.on("LogValidation", self._handleLogForClient)

    async def _handleLogForClient(self, data):
        data.Name = self.room
        await self.eventManager.emit("ClientLogs",data,haEvent=True)          
               
