from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)

class Heater(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)

        ## Events Register
        self.eventManager.on("Increase Heater", self.increaseAction)
        self.eventManager.on("Reduce Heater", self.reduceAction)

        if self.isAcInfinDev:
            self.steps = 10 
            self.maxDuty = 100
            self.minDuty = 0

    #Actions Helpers
    
            
    async def increaseAction(self, data):
        """Schaltet Heater an"""
        if self.isRunning == True:
            self.log_action("Allready in Desired State ")
        else:
            self.log_action("TurnON ")
            await self.turn_on()

    async def reduceAction(self, data):
        """Schaltet Heater aus"""
        if self.isRunning == True:
            self.log_action("TurnOff ")
            await self.turn_off()
        else:
            self.log_action("Allready in Desired State ")


    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName}"
        _LOGGER.warn(f"{action_name}: {log_message}")

