from .Device import Device
import logging
import asyncio

_LOGGER = logging.getLogger(__name__)


class Exhaust(Device):
    def __init__(
        self,
        deviceName,
        deviceData,
        eventManager,
        dataStore,
        deviceType,
        inRoom,
        hass=None,
        deviceLabel="EMPTY",
        allLabels=[],
    ):
        super().__init__(
            deviceName,
            deviceData,
            eventManager,
            dataStore,
            deviceType,
            inRoom,
            hass,
            deviceLabel,
            allLabels,
        )
        self.steps = 5  # DutyCycle Steps
        self.isDimmable = True
        self.isInitialized = False

        # Initialize min/max to defaults - will be overridden by user settings in checkMinMax
        self.minDuty = 10  # Class default
        self.maxDuty = 100  # Class default
        self.dutyCycle = self.minDuty + ((self.maxDuty - self.minDuty) // 2 // self.steps) * self.steps

        self.init()

        # Delayed initialization - wait for device to come online before turning on
        asyncio.create_task(self._delayed_init())

        ## Events Register
        self.event_manager.on("Increase Exhaust", self.increaseAction)
        self.event_manager.on("Reduce Exhaust", self.reduceAction)

    async def _delayed_init(self):
        """Wait for device to come online, then restore previous state."""
        # Wait up to 10 seconds for device to come online
        for _ in range(20):
            if self._is_device_online():
                break
            await asyncio.sleep(0.5)
        
        # Only turn on if device was previously running (preserve state)
        if self.isRunning:
            await self.turn_on(percentage=self.dutyCycle)
        else:
            _LOGGER.debug(f"{self.deviceName}: Skipping turn_on during init - device was off")

    # Actions Helpers


    def init(self):
        """Initialisiert die Ventilation."""
        if not self.isInitialized:
            # Load min/max from dataStore FIRST
            self.checkMinMax(False)
            
            # Check if dutyCycle needs to be clamped to new min/max range
            if hasattr(self, 'minDuty') and hasattr(self, 'maxDuty') and self.minDuty is not None and self.maxDuty is not None:
                if self.dutyCycle < self.minDuty or self.dutyCycle > self.maxDuty:
                    old_duty = self.dutyCycle
                    self.dutyCycle = max(self.minDuty, min(self.maxDuty, self.dutyCycle))
                    _LOGGER.debug(f"{self.deviceName}: dutyCycle clamped from {old_duty}% to {self.dutyCycle}%")

            self.isInitialized = True

    def __repr__(self):
        return (
            f"DeviceName:'{self.deviceName}' Typ:'{self.deviceType}'RunningState:'{self.isRunning}'"
            f"Dimmable:'{self.isDimmable}' Switches:'{self.switches}' Sensors:'{self.sensors}'"
            f"Options:'{self.options}' OGBS:'{self.ogbsettings}'DutyCycle:'{self.dutyCycle}' "
        )


    def clamp_duty_cycle(self, value):
        """Begrenzt den Duty Cycle auf erlaubte Werte."""
        if value is None:
            _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle called with None, using default 50%")
            value = 50
        
        # Always read min/max from dataStore to get user-defined values
        minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
        if minMaxSets and minMaxSets.get("minDuty") is not None and minMaxSets.get("maxDuty") is not None:
            min_duty = float(minMaxSets.get("minDuty"))
            max_duty = float(minMaxSets.get("maxDuty"))
        else:
            # Fallback to class defaults
            min_duty = float(self.minDuty) if self.minDuty is not None else 10
            max_duty = float(self.maxDuty) if self.maxDuty is not None else 100
        
        duty_cycle = float(value)
        clamped_value = max(min_duty, min(max_duty, duty_cycle))
        clamped_value = int(clamped_value)
        
        _LOGGER.debug(f"{self.deviceName}: Duty Cycle auf {clamped_value}% begrenzt (range: {min_duty}-{max_duty}%)")
        return clamped_value

    def change_duty_cycle(self, increase=True):
        """
        Ändert den Duty Cycle basierend auf dem Schrittwert.
        Erhöht oder verringert den Duty Cycle und begrenzt den Wert mit clamp.
        """
        if not self.isDimmable:
            _LOGGER.warning(
                f"{self.deviceName}: Änderung des Duty Cycles nicht möglich, da Device nicht dimmbar ist."
            )
            return self.dutyCycle

        # Berechne neuen Wert basierend auf Schrittweite
        new_duty_cycle = (
            int(self.dutyCycle) + int(self.steps)
            if increase
            else int(self.dutyCycle) - int(self.steps)
        )

        # Begrenze den neuen Duty Cycle auf erlaubte Werte
        clamped_duty_cycle = self.clamp_duty_cycle(new_duty_cycle)

        # Setze den begrenzten Wert als neuen Duty Cycle
        self.dutyCycle = clamped_duty_cycle

        _LOGGER.info(f"{self.deviceName}: Duty Cycle changed to {self.dutyCycle}% ")
        return self.dutyCycle

    # Actions
    async def increaseAction(self, data):
        """Erhöht den Duty Cycle."""
        if self.isDimmable:
            if self.isSpecialDevice:
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
            if self.isSpecialDevice:
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
