from .Device import Device

from .Device import Device
import logging
from datetime import datetime, timedelta

_LOGGER = logging.getLogger(__name__)

class Pump(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)
        self.isRunning = False
        self.Interval = None  # Mindestintervall zwischen Pumpzyklen (in Sekunden)
        self.Duration = None  # Pumpdauer in Sekunden
        self.isAutoRun = False  # Automatikmodus
        self.lastPumpTime = None  # Zeitpunkt des letzten Pumpvorgangs
        self.soilMoisture = None  # Bodenfeuchtigkeit
        self.currentEC = None
        self.minEC = None  # Elektrische Leitfähigkeit
        self.maxEC = None  # Maximaler EC-Wert

        #PLANT FEEDING CLASSIC
        self.minSoilMoisture = 25  # Mindestbodenfeuchte
        self.maxSoilMoisture = 25  # Mindestbodenfeuchte


        ## Events Register
        self.eventManager.on("Increase Pump", self.onAction)
        self.eventManager.on("Reduce Pump", self.offAction)
       
    #Actions Helpers           
    async def onAction(self,data):
        """Start Pump"""
        self.log_action("TurnON ")
        await self.turn_on()
        
    async def offAction(self,data):
        """Stop Pump"""
        self.log_action("TurnOFF ")
        await self.turn_off()

    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName}"
        _LOGGER.warn(f"{action_name}: {log_message}")
