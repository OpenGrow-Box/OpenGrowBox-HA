import logging
from typing import Optional, Union

from .Device import Device

_LOGGER = logging.getLogger(__name__)


class CO2(Device):
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
        self.targetCO2 = 0  # Target CO2 value in ppm
        self.currentCO2 = 0  # Current CO2 value in ppm
        self.autoRegulate = False  # Automatic control enabled

        # Dimmable CO2 output support (e.g. variable valve / fan speed)
        self.dutyCycle = 0
        self.minDuty = 0
        self.maxDuty = 100

        ## Events Register
        self.event_manager.on("NewCO2Publication", self.handleNewCO2Value)
        self.event_manager.on("Increase CO2", self.increaseAction)
        self.event_manager.on("Reduce CO2", self.reduceAction)
        self.event_manager.on("EmergencyCO2Stop", self.emergencyStop)

        _LOGGER.debug(f"CO2 Device {self.deviceName} initialized with {len(self.switches)} switches, {len(self.sensors)} sensors, {len(self.options)} options")

    def deviceInit(self, entitys):
        """Override deviceInit to add CO2-specific debugging."""
        _LOGGER.debug(f"CO2 Device {self.deviceName} starting initialization with {len(entitys)} entities")
        for entity in entitys:
            entity_id = entity.get("entity_id", "unknown")
            entity_value = entity.get("value", "unknown")
            _LOGGER.debug(f"CO2 Device {self.deviceName} processing entity: {entity_id} = {entity_value}")

        # Call parent initialization
        super().deviceInit(entitys)

        _LOGGER.debug(f"CO2 Device {self.deviceName} initialization complete. isInitialized: {self.isInitialized}, switches: {len(self.switches)}, sensors: {len(self.sensors)}")

    def clamp_duty_cycle(self, value: Union[int, float, str, None]) -> int:
        """Clamp CO2 duty cycle to valid range."""
        if value is None:
            _LOGGER.debug(f"{self.deviceName}: clamp_duty_cycle called with None, using default 0%")
            value = 0.0
        else:
            try:
                value = float(value)
            except (ValueError, TypeError):
                _LOGGER.debug(f"{self.deviceName}: clamp_duty_cycle got invalid value '{value}', using default 0%")
                value = 0.0

        min_duty = float(self.minDuty) if self.minDuty is not None else 0
        max_duty = float(self.maxDuty) if self.maxDuty is not None else 100
        clamped = int(max(min_duty, min(max_duty, value)))
        _LOGGER.debug(f"{self.deviceName}: CO2 duty cycle clamped to {clamped}% (range: {min_duty}-{max_duty}%)")
        return clamped

    def change_duty_cycle(self, increase=True):
        """Change CO2 duty cycle by configured step size."""
        if not self.isDimmable:
            _LOGGER.debug(f"{self.deviceName}: CO2 device is not dimmable, skipping duty cycle change")
            return float(self.dutyCycle)

        if self.dutyCycle is None:
            _LOGGER.debug(f"{self.deviceName}: Current CO2 duty cycle is None, setting to default 0%")
            self.dutyCycle = 0.0

        current_duty = float(self.dutyCycle)

        try:
            step_value = int(self.steps) if self.steps is not None else 5
        except (ValueError, TypeError):
            _LOGGER.debug(f"{self.deviceName}: Invalid CO2 step value, using default 5")
            step_value = 5

        new_duty_cycle = current_duty + step_value if increase else current_duty - step_value
        clamped_duty_cycle = self.clamp_duty_cycle(new_duty_cycle)

        if clamped_duty_cycle != current_duty:
            self.dutyCycle = clamped_duty_cycle
            _LOGGER.debug(f"{self.deviceName}: CO2 duty cycle changed from {current_duty}% to {self.dutyCycle}% (step: {step_value}%)")
        else:
            _LOGGER.debug(f"{self.deviceName}: CO2 duty cycle unchanged at {current_duty}% (already at {'max' if increase else 'min'} bound)")

        return float(self.dutyCycle)

    # Actions Helpers
    async def handleNewCO2Value(self, co2Publication):
        self.log_action(f" Check  {co2Publication} ")

    async def increaseAction(self, data):
        """Increase CO2 output level."""
        logging.debug("CO2 INCREASE ACTION START")
        if self.isDimmable:
            newDuty = self.change_duty_cycle(increase=True)
            self.log_action("IncreaseAction")
            await self.turn_on(percentage=newDuty)
        else:
            self.log_action("IncreaseAction/TurnOn")
            await self.turn_on()

    async def reduceAction(self, data):
        """Reduce CO2 output level."""
        logging.debug("CO2 REDUCE ACTION START")
        if self.isDimmable:
            newDuty = self.change_duty_cycle(increase=False)
            self.log_action("ReduceAction")
            if newDuty <= 0:
                await self.turn_off()
            else:
                await self.turn_on(percentage=newDuty)
        else:
            self.log_action("ReduceAction/TurnOff")
            await self.turn_off()

    def log_action(self, action_name):
        """Log the executed action."""
        log_message = f"{self.deviceName} PPM-Current:{self.currentCO2} Target-PPM:{self.targetCO2} DutyCycle:{self.dutyCycle}%"
        _LOGGER.debug(f"{action_name}: {log_message}")

    async def emergencyStop(self, data):
        """EMERGENCY STOP - Turn off CO2 pump immediately (called by safety system)."""
        _LOGGER.critical(f"EMERGENCY CO2 STOP triggered for {self.deviceName} - Turning OFF immediately!")
        self.log_action("EMERGENCY_STOP/TurnOff")
        self.dutyCycle = 0
        await self.turn_off()
