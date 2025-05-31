from .Device import Device

from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)

class GenericSwitch(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)
        ## Events Register
        self.eventManager.on("Switch ON", self.increaseAction)
        self.eventManager.on("Switch OFF", self.reduceAction)

    #Actions Helpers
    
    async def increaseAction(self,data):
        _LOGGER.error(f"You got an Gerneric Switch on -> {self.deviceName}.")
        pass
     
    async def reduceAction(self,data):
        _LOGGER.error(f"You got an Gerneric Switch on -> {self.deviceName}.")
        pass
    
