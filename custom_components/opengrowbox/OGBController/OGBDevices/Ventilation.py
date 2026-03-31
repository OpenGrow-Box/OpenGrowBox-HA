from .Device import Device
import logging
import asyncio

_LOGGER = logging.getLogger(__name__)


class Ventilation(Device):
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
        self.steps = 5  # DutyCycle Steps
        self.minDuty = 0  # Class default
        self.maxDuty = 100  # Class default

        if self.isAcInfinDev:
            self.steps = 10
            self.maxDuty = 100
            self.minDuty = 0

        # Events registrieren
        self.event_manager.on("Increase Ventilation", self.increaseAction)
        self.event_manager.on("Reduce Ventilation", self.reduceAction)

    def clamp_duty_cycle(self, value: int | float | str | None) -> int:
        """Begrenzt den Duty Cycle auf erlaubte Werte."""

        # value sicher zu float konvertieren
        if value is None:
            _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle called with None, using default 50%")
            value = 50.0
        else:
            try:
                value = float(value)
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle got invalid value '{value}', using default 50%")
                value = 50.0

        # Min/Max aus dataStore lesen
        min_duty, max_duty = 10.0, 100.0  # ← deine Defaults (10 statt 0)
        try:
            minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
            if minMaxSets:
                raw_min = minMaxSets.get("minDuty")
                raw_max = minMaxSets.get("maxDuty")
                if raw_min is not None and raw_max is not None:
                    min_duty = float(raw_min)
                    max_duty = float(raw_max)
        except (ValueError, TypeError, AttributeError):
            _LOGGER.warning(f"{self.deviceName}: Fehler beim Lesen von DeviceMinMax, nutze Defaults")
            try:
                min_duty = float(self.minDuty) if self.minDuty is not None else 10.0
                max_duty = float(self.maxDuty) if self.maxDuty is not None else 100.0
            except (ValueError, TypeError):
                pass

        clamped = int(max(min_duty, min(max_duty, value)))
        _LOGGER.debug(f"{self.deviceName}: Duty Cycle auf {clamped}% begrenzt (range: {min_duty}-{max_duty}%)")
        return clamped

    def change_duty_cycle(self, increase=True):
        """
        Ändert den Duty Cycle basierend auf dem Schrittwert.
        Erhöht oder verringert den Duty Cycle und begrenzt den Wert mit clamp.
        """
        if not self.isDimmable:
            _LOGGER.warning(f"{self.deviceName}: Änderung des Duty Cycles nicht möglich, da Gerät nicht dimmbar ist.")
            return float(self.dutyCycle)

        # Ensure we have valid current duty cycle
        if self.dutyCycle is None:
            _LOGGER.warning(f"{self.deviceName}: Current duty cycle is None, setting to default 50%")
            self.dutyCycle = 50.0
            
        # Konvertiere dutyCycle zu float für Berechnungen
        current_duty = float(self.dutyCycle)
        
        # Ensure we have valid steps
        try:
            step_value = float(self.steps) if self.steps else 5.0
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.deviceName}: Invalid step value, using default 5.0")
            step_value = 5.0
        
        # Berechne neuen Wert basierend auf Schrittweite
        new_duty_cycle = current_duty + step_value if increase else current_duty - step_value
        
        # Begrenze den neuen Duty Cycle auf erlaubte Werte
        clamped_duty_cycle = self.clamp_duty_cycle(new_duty_cycle)

        # Only update if the value actually changed
        if clamped_duty_cycle != current_duty:
            self.dutyCycle = clamped_duty_cycle
            _LOGGER.info(f"{self.deviceName}: Duty Cycle changed from {current_duty + (step_value if not increase else -step_value)}% to {self.dutyCycle}% (step: {step_value}%)")
        else:
            _LOGGER.info(f"{self.deviceName}: Duty Cycle unchanged at {current_duty}% (already at {'max' if increase else 'min'} bound)")
            
        return float(self.dutyCycle)

    async def increaseAction(self, data):
        """Erhöht den Duty Cycle."""
        if self.should_block_air_exchange_increase("canVentilate", "Direct Increase Ventilation event"):
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Action": "AirExchangeColdGuard",
                    "Device": "canVentilate",
                    "From": "Increase",
                    "To": "Reduce",
                    "Message": "Direct Increase Ventilation blocked by cold ambient guard",
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
        _LOGGER.debug(f"{action_name}: {log_message}")
