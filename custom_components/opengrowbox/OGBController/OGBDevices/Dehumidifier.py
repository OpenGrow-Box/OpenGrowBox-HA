from .Device import Device

from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)

class Dehumidifier(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)
        self.realHumidifier = False  # Erkennung eines echten Luftentfeuchters
        self.isSimpleSwitch = False  # Standardmäßig ein einfacher Schalter
        self.hasModes = False  # Erkennung von Modi
        self.currentMode = None  # Aktueller Modus des Luftentfeuchters

        if self.isAcInfinDev:
            self.steps = 10    

        ## Events Register
        self.eventManager.on("Increase Dehumidifier", self.increaseAction)
        self.eventManager.on("Reduce Dehumidifier", self.reduceAction)

    #Actions Helpers
    async def increaseAction(self, data):
        """Schaltet Entfeuchter an"""
        
        if self.isRunning == True:
            self.log_action("Allready in Desired State ")
        else:
            self.log_action("TurnON ")
            await self.turn_on()
                
    async def reduceAction(self, data):
        """Schaltet Entfeuchter aus"""
        if self.isRunning == True:
            self.log_action("TurnOff ")
            await self.turn_off()

        else:
            self.log_action("Allready in Desired State ")
                 
    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName}"
        _LOGGER.warn(f"{action_name}: {log_message}")
