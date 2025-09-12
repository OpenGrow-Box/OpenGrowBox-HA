from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)

class Cooler(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)
        ## Events Register
        self.eventManager.on("Increase Cooler", self.increaseAction)
        self.eventManager.on("Reduce Cooler", self.reduceAction)

        if self.isAcInfinDev:
            self.steps = 10    

    #Actions Helpers
    async def increaseAction(self, data):
        """Reduziert die klima oder Kühlgerät"""
        if self.isRunning == True:
            self.log_action("Allready in Desired State ")
        else:
            await self.turn_on()
            self.log_action("TurnON ")


    async def reduceAction(self, data):
        """Erhöht die klima oder Kühlgerät"""
        
        if self.isRunning == True:
            self.log_action("TurnOFF ")
            await self.turn_off()
        else:
            self.log_action("Allready in Desired State ")          


    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName}"
        _LOGGER.warn(f"{action_name}: {log_message}")
