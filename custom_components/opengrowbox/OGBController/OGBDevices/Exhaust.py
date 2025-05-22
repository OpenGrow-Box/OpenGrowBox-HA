from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)

class Exhaust(Device):
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        super().__init__(deviceName,deviceData,eventManager,dataStore,deviceType,inRoom,hass)
        self.dutyCycle = None  # Initialer Duty Cycle
        self.minDuty = 10    # Minimaler Duty Cycle
        self.maxDuty = 95    # Maximaler Duty Cycle
        self.steps = 5        # DutyCycle Steps
        self.isInitialized = False
        
        self.init()
        
        ## Events Register
        self.eventManager.on("Increase Exhaust", self.increaseAction)
        self.eventManager.on("Reduce Exhaust", self.reduceAction)

    #Actions Helpers
    
    def init(self):
        if not self.isDimmable:
            _LOGGER.warning(f"{self.deviceName}: Device ist nicht dimmbar. Initialisierung übersprungen.")
            return
        
        if not self.isInitialized:
            self.identify_if_tasmota()
            if self.isTasmota == True:
                self.initialize_duty_cycle()
            else:
                self.checkForControlValue()
                if self.dutyCycle == 0 or self.dutyCycle == None:
                    self.initialize_duty_cycle()
            self.isInitialized = True

    def __repr__(self):
        return (f"DeviceName:'{self.deviceName}' Typ:'{self.deviceType}'RunningState:'{self.isRunning}'"
                f"Dimmable:'{self.isDimmable}' Switches:'{self.switches}' Sensors:'{self.sensors}'"
                f"Options:'{self.options}' OGBS:'{self.ogbsettings}'DutyCycle:'{self.dutyCycle}' ")

    def identify_if_tasmota(self):
        """Prüft, ob das Device ein Tasmota-Device ist."""
        self.isTasmota = any(
            switch["entity_id"].startswith("light.") for switch in self.switches
        )
        _LOGGER.info(f"{self.deviceName}: Tasmota-Device Found: {self.isTasmota}")


    def initialize_duty_cycle(self):
        """Initialisiert den Duty Cycle auf 50%."""
        self.dutyCycle = 50  
        _LOGGER.info(f"{self.deviceName}: Duty Cycle initialisiert auf {self.dutyCycle}%.")


    def clamp_duty_cycle(self, duty_cycle):
        """Begrenzt den Duty Cycle auf erlaubte Werte."""
        clamped_value = max(self.minDuty, min(self.maxDuty, duty_cycle))
        _LOGGER.debug(f"{self.deviceName}: Duty Cycle auf {clamped_value}% begrenzt.")
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
        self.dutyCycle = clamped_duty_cycle

        _LOGGER.info(f"{self.deviceName}: Duty Cycle auf {self.dutyCycle}% geändert.")
        return self.dutyCycle

    # Actions
    async def increaseAction(self, data):
        """Erhöht den Duty Cycle."""
        if self.isDimmable:
            if self.isTasmota:
                newDuty = self.change_duty_cycle(increase=True)
                self.log_action("IncreaseAction")
                await self.turn_on(brightness_pct=newDuty)   
            else:          
                newDuty = self.change_duty_cycle(increase=True)
                self.log_action("IncreaseAction")
                await self.turn_on(percentage=newDuty)
        else:
            self.log_action("TurnOn")
            await self.turn_on()
       
    async def reduceAction(self, data):
        """Reduziert den Duty Cycle."""
        if self.isDimmable:
            if self.isTasmota:
                newDuty = self.change_duty_cycle(increase=False)
                self.log_action("ReduceAction")
                await self.turn_on(brightness_pct=newDuty)
            else:
                newDuty = self.change_duty_cycle(increase=False)
                self.log_action("ReduceAction")
                await self.turn_on(percentage=newDuty)
        else:
            self.log_action("TurnOff")
            await self.turn_off()


    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName} DutyCycle: {self.dutyCycle}%"
        _LOGGER.warning(f"{action_name}: {log_message}")
