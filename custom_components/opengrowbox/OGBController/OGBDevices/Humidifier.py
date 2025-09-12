from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)

class Humidifier(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)
        self.currentHumidity = 0
        self.targetHumidity = 0
        self.minHumidity = 30
        self.maxHumidity = 70
        self.steps = 5
        self.realHumidifier = False
        self.hasModes = False
        self.isSimpleSwitch = True
        self.modes = {"interval": True, "small": False, "large": False}
        ## Events Register
        self.eventManager.on("Increase Humidifier", self.increaseAction)
        self.eventManager.on("Reduce Humidifier", self.reduceAction)

        if self.isAcInfinDev:
            self.steps = 10    
        
    async def increaseAction(self, data):
        """Schaltet Befeuchter an"""
        if self.isRunning == True:
            self.log_action("Allready in Desired State ")
        else:
            self.log_action("TurnON ")
            await self.turn_on()


        
        
    async def reduceAction(self, data):
        """Schaltet Befeuchter aus"""
        
        if self.isRunning == True:
            self.log_action("TurnOFF ")
            await self.turn_off()
        else:
            self.log_action("Allready in Desired State ")
            
    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName} "
        _LOGGER.warn(f"{action_name}: {log_message}")
