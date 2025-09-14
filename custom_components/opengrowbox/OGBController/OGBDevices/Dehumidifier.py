from .Device import Device

from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)

class Dehumidifier(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)
        self.realHumidifierClass = False  # Erkennung eines echten Luftentfeuchters

 
        ## Events Register
        self.eventManager.on("Increase Dehumidifier", self.increaseAction)
        self.eventManager.on("Reduce Dehumidifier", self.reduceAction)

        if self.isAcInfinDev:
            self.dutyCycle = 0
            self.steps = 10 
            self.maxDuty = 100
            self.minDuty = 0    

    def clamp_duty_cycle(self, duty_cycle):
        """Begrenzt den Duty Cycle auf erlaubte Werte."""
        clamped_value = max(self.minDuty, min(self.maxDuty, duty_cycle))
        _LOGGER.debug(f"{self.deviceName}: Duty Cycle to {clamped_value}% ragend.")
        return clamped_value

    def change_duty_cycle(self, increase=True):
        """
        Ändert den Duty Cycle basierend auf dem Schrittwert.
        Erhöht oder verringert den Duty Cycle und begrenzt den Wert mit clamp.
        """
        if not self.isDimmable:
            _LOGGER.warning(f"{self.deviceName}: Änderung des Duty Cycles nicht möglich, da Device nicht dimmbar ist.")
            return self.dutyCycle

        # Berechne neuen Wert basierend auf Schrittweite
        new_duty_cycle = int(self.dutyCycle) + int(self.steps) if increase else int(self.dutyCycle) - int(self.steps)
        
        # Begrenze den neuen Duty Cycle auf erlaubte Werte
        clamped_duty_cycle = self.clamp_duty_cycle(new_duty_cycle)

        # Setze den begrenzten Wert als neuen Duty Cycle
        self.dutyCycle = int(clamped_duty_cycle)

        _LOGGER.info(f"{self.deviceName}: Duty Cycle changed to {self.dutyCycle}% ")
        return self.dutyCycle
    
    async def increaseAction(self, data):
        """Schaltet Befeuchter an"""
        if self.isDimmable:
            if self.isAcInfinDev:
                newDuty = self.change_duty_cycle(increase=True)
                self.log_action("IncreaseAction")
                await self.turn_on(percentage=newDuty)    
        elif self.realHumidifierClass:
            ## implement the internal humidifier classes with all modes
            return False
        else:
            if self.isRunning == True:
                self.log_action("Allready in Desired State ")
            else:
                self.log_action("TurnON ")
                await self.turn_on()
    
    async def reduceAction(self, data):
        """Schaltet Befeuchter aus"""
        if self.isDimmable:
            if self.isAcInfinDev:
                newDuty = self.change_duty_cycle(increase=False)
                self.log_action("ReduceAction")
                await self.turn_on(percentage=newDuty)    
        elif self.realHumidifierClass:
            ## implement the internal humidifier classes with all modes
            return False
        else:
            if self.isRunning == True:
                self.log_action("TurnOFF ")
                await self.turn_off()
            else:
                self.log_action("Allready in Desired State ")
                                    
    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName}"
        _LOGGER.warn(f"{action_name}: {log_message}")
