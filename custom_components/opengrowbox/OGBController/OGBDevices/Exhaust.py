from __future__ import annotations
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

        if self.isAcInfinDev:
            self.steps = 10
            self.maxDuty = 100
            self.minDuty = 0

        ## Events Register
        self.event_manager.on("Increase Exhaust", self.increaseAction)
        self.event_manager.on("Reduce Exhaust", self.reduceAction)


    def clamp_duty_cycle(self, value: int | float | str | None) -> int:
        """Limit the duty cycle to allowed values."""

        if self.isDimmable == False:
            return

        # Convert value safely to float
        if value is None:
            _LOGGER.warning(f"{self.deviceName}:{self.isDimmable} clamp_duty_cycle called with None, using default 50%")
            value = 50.0
        else:
            try:
                value = float(value)
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle got invalid value '{value}', using default 50%")
                value = 50.0

        # Read min/max from dataStore
        min_duty, max_duty = 10.0, 100.0  # Your defaults (10 instead of 0)
        try:
            minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
            if minMaxSets:
                raw_min = minMaxSets.get("minDuty")
                raw_max = minMaxSets.get("maxDuty")
                if raw_min is not None and raw_max is not None:
                    min_duty = float(raw_min)
                    max_duty = float(raw_max)
        except (ValueError, TypeError, AttributeError):
            _LOGGER.warning(f"{self.deviceName}: Error reading DeviceMinMax, using defaults")
            try:
                min_duty = float(self.minDuty) if self.minDuty is not None else 10.0
                max_duty = float(self.maxDuty) if self.maxDuty is not None else 100.0
            except (ValueError, TypeError):
                pass

        clamped = int(max(min_duty, min(max_duty, value)))
        _LOGGER.debug(f"{self.deviceName}: Duty cycle limited to {clamped}% (range: {min_duty}-{max_duty}%)")
        return clamped

    def change_duty_cycle(self, increase=True):
        """
        Change the duty cycle based on the step size.
        Increases or decreases the duty cycle and limits the value with clamp.
        """
        if self.isDimmable == False:
            _LOGGER.warning(
                f"{self.deviceName}: Cannot change duty cycle, device is not dimmable."
            )
            return 

        # Ensure we have valid current duty cycle
        if self.dutyCycle is None:
            _LOGGER.warning(f"{self.deviceName}:{self.isDimmable} Current duty cycle is None, setting to default 50%")
            self.dutyCycle = 50
            
        # Ensure we have valid steps
        try:
            step_value = int(self.steps) if self.steps else 5
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.deviceName}: Invalid step value, using default 5")
            step_value = 5
            
        # Calculate new value based on step size
        new_duty_cycle = (
            int(self.dutyCycle) + step_value
            if increase
            else int(self.dutyCycle) - step_value
        )

        # Limit the new duty cycle to allowed values
        clamped_duty_cycle = self.clamp_duty_cycle(new_duty_cycle)

        # Only update if the value actually changed
        if clamped_duty_cycle != self.dutyCycle:
            self.dutyCycle = clamped_duty_cycle
            _LOGGER.debug(f"{self.deviceName}: Duty Cycle changed from {int(self.dutyCycle) + (step_value if not increase else -step_value)}% to {self.dutyCycle}% (step: {step_value}%)")
        else:
            _LOGGER.debug(f"{self.deviceName}: Duty Cycle unchanged at {self.dutyCycle}% (already at {'max' if increase else 'min'} bound)")
            
        return self.dutyCycle

    # Actions
    async def increaseAction(self, data):
        """Increases the duty cycle."""
        if self.should_block_air_exchange_increase("canExhaust", "Direct Increase Exhaust event"):
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Action": "EnvironmentGuard",
                    "Device": "canExhaust",
                    "From": "Increase",
                    "To": "Reduce",
                    "Message": "Direct Increase Exhaust blocked by EnvironmentGuard",
                },
                haEvent=True,
                debug_type="WARNING",
            )
            await self.reduceAction(data)
            return

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
        """Reduces the duty cycle."""
        
        # Smart Deadband Check - block action when in deadband
        if self._in_smart_deadband:
            _LOGGER.debug(
                f"{self.deviceName}: ReduceAction BLOCKED - device is in Smart Deadband (operating at minimum)"
            )
            return
        
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
        """Log the executed action."""
        log_message = f"{self.deviceName} DutyCycle: {self.dutyCycle}%"
        _LOGGER.debug(f"{action_name}: {log_message}")
