import logging

from .Device import Device

_LOGGER = logging.getLogger(__name__)


class Heater(Device):
    def __init__(
        self,
        deviceName,
        deviceData,
        eventManager,
        dataStore,
        deviceType,
        inRoom,
        hass,
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

        ## Events Register
        self.event_manager.on("Increase Heater", self.increaseAction)
        self.event_manager.on("Reduce Heater", self.reduceAction)
        
        if self.isAcInfinDev:
            self.checkMinMax(False)
            self.dutyCycle = 0
            self.steps = 10
            self.maxDuty = 100
            self.minDuty = 0

    def clamp_duty_cycle(self, duty_cycle):
        """Limit the duty cycle to allowed values."""
        if duty_cycle is None:
            _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle called with None, using default 50%")
            duty_cycle = 50
        
        min_duty = float(self.minDuty) if self.minDuty is not None else 0
        max_duty = float(self.maxDuty) if self.maxDuty is not None else 100
        duty_cycle = float(duty_cycle)
        
        clamped_value = max(min_duty, min(max_duty, duty_cycle))
        clamped_value = int(clamped_value)
        
        _LOGGER.debug(f"{self.deviceName}: Duty cycle limited to {clamped_value}% (range: {min_duty}-{max_duty}%)")
        return clamped_value

    def change_duty_cycle(self, increase=True):
        """
        Change the duty cycle based on the step size.
        Increases or decreases the duty cycle and limits the value with clamp.
        """
        if not self.isDimmable:
            _LOGGER.warning(
                f"{self.deviceName}: Cannot change duty cycle, device is not dimmable."
            )
            return self.dutyCycle

        # Calculate new value based on step size
        new_duty_cycle = (
            int(self.dutyCycle) + int(self.steps)
            if increase
            else int(self.dutyCycle) - int(self.steps)
        )

        # Limit the new duty cycle to allowed values
        clamped_duty_cycle = self.clamp_duty_cycle(new_duty_cycle)

        # Set the clamped value as the new duty cycle
        self.dutyCycle = int(clamped_duty_cycle)

        _LOGGER.debug(f"{self.deviceName}: Duty Cycle changed to {self.dutyCycle}% ")
        return self.dutyCycle

    async def increaseAction(self, data):
        """Turns heater on or increases duty cycle."""
        action_type, target_value = self._extract_action_value(data)
        
        if self.isDimmable:
            if target_value is not None:
                # Dim directly to target value
                await self.set_duty_cycle(target_value, log_action_callback=self.log_action)
            else:
                newDuty = self.change_duty_cycle(increase=True)
                self.log_action("IncreaseAction")
                await self.turn_on(percentage=newDuty)

        else:
            if self.isRunning == True:
                self.log_action("Allready in Desired State ")
            else:
                self.log_action("TurnON ")
                await self.turn_on()

    async def reduceAction(self, data):
        """Turns heater off or reduces mode."""
        
        # Smart Deadband Check - block action when in deadband
        if self._in_smart_deadband:
            _LOGGER.debug(
                f"{self.deviceName}: ReduceAction BLOCKED - device is in Smart Deadband (operating at minimum)"
            )
            return
        
        action_type, target_value = self._extract_action_value(data)
        
        if target_value is not None and self.isDimmable:
            # Dim directly to target value
            await self.set_duty_cycle(target_value, log_action_callback=self.log_action)
        else:
            await self.reduce_or_turn_off(log_action_callback=self.log_action)

    def log_action(self, action_name):
        """Log the executed action."""
        log_message = f"{self.deviceName}"
        _LOGGER.debug(f"{action_name}: {log_message}")
