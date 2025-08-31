from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)

class CO2(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)
        self.targetCO2 = 0  # Zielwert für CO2 (ppm)
        self.currentCO2 = 0  # Aktueller CO2-Wert (ppm)
        self.autoRegulate = False  # Automatische Steuerung
        
        
        ## Events Register
        self.eventManager.on("NewCO2Publication", self.handleNewCO2Value)

        self.eventManager.on("Increase CO2", self.increaseAction)
        self.eventManager.on("Reduce CO2", self.reduceAction)
   

    #Actions Helpers
    async def handleNewCO2Value(self,co2Publication):
        self.log_action(f" Check  {co2Publication} " )
    
    
    async def increaseAction(self, data):
        """Erhöht den CO2 Wert"""
        self.log_action("IncreaseAction/TurnOn" )
        await self.turn_on()
        
    async def reduceAction(self, data):
        """Reduziertden CO2 Wert"""
        self.log_action("ReduceAction/TurnOff" )
        await self.turn_off()



    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName} PPM-Current:{self.currentCO2} Target-PPM:{self.targetCO2}"
        _LOGGER.warn(f"{action_name}: {log_message}")