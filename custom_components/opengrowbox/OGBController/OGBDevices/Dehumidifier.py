import logging

from .Device import Device

_LOGGER = logging.getLogger(__name__)


class Dehumidifier(Device):
    DEHUMIDIFIER_MODES = ["auto", "eco", "comfort", "dry", "home"]

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
        self.realHumidifierClass = False
        self.hasModes = False
        self.humidifierEntityId = None

        ## Events Register
        self.event_manager.on("Increase Dehumidifier", self.increaseAction)
        self.event_manager.on("Reduce Dehumidifier", self.reduceAction)
        self.isInitialized = False
        self.init()

        
        if self.isAcInfinDev:
            self.dutyCycle = 0
            self.steps = 10
            self.maxDuty = 100
            self.minDuty = 0

    def init(self):

        if not self.isInitialized:

            self.identifyHumidifierClass()

            if self.isAcInfinDev:        
                self.checkMinMax(False)

                self.dutyCycle = 0
                self.steps = 10
                self.maxDuty = 100
                self.minDuty = 0
            self.isInitialized = True



    def identifyHumidifierClass(self):
        for switch in self.switches:
            entity_id = switch.get("entity_id", "")
            if entity_id.startswith("humidifier."):
                self.realHumidifierClass = True
                self.humidifierEntityId = entity_id
                self._checkHumidifierModes()
                return

    def _checkHumidifierModes(self):
        """Check if dehumidifier entity has mode capabilities."""
        if not self.humidifierEntityId or not self.hass:
            return
        
        state = self.hass.states.get(self.humidifierEntityId)
        if state and hasattr(state, 'attributes'):
            modes = state.attributes.get('options') or state.attributes.get('mode_modes')
            if modes and len(modes) > 0:
                self.hasModes = True
                self.modes = modes
                _LOGGER.info(f"{self.deviceName}: Dehumidifier has {len(modes)} modes: {modes}")
            else:
                _LOGGER.debug(f"{self.deviceName}: Dehumidifier has no modes, using simple switch")

    def get_current_mode(self):
        """Get current dehumidifier mode."""
        if not self.humidifierEntityId or not self.hass:
            return None
        
        state = self.hass.states.get(self.humidifierEntityId)
        if state:
            return state.state if hasattr(state, 'state') else None
        return None

    def get_next_mode(self, current_mode, increase=True):
        """Get next mode in sequence."""
        if not self.modes or not isinstance(self.modes, list):
            return None
        
        if current_mode not in self.modes:
            return self.modes[0] if increase else self.modes[-1]
        
        try:
            current_idx = self.modes.index(current_mode)
            if increase:
                next_idx = min(current_idx + 1, len(self.modes) - 1)
            else:
                next_idx = max(current_idx - 1, 0)
            return self.modes[next_idx]
        except (ValueError, IndexError):
            return None

    async def set_dehumidifier_mode(self, mode):
        """Set dehumidifier mode."""
        if not self.humidifierEntityId or not self.hass:
            return
        
        try:
            await self.hass.services.async_call(
                'humidifier',
                'set_mode',
                {'entity_id': self.humidifierEntityId, 'mode': mode}
            )
            _LOGGER.info(f"{self.deviceName}: Set mode to {mode}")
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to set mode {mode}: {e}")


    def clamp_duty_cycle(self, duty_cycle):
        """Begrenzt den Duty Cycle auf erlaubte Werte."""
        if duty_cycle is None:
            _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle called with None, using default 50%")
            duty_cycle = 50
        
        min_duty = float(self.minDuty) if self.minDuty is not None else 0
        max_duty = float(self.maxDuty) if self.maxDuty is not None else 100
        duty_cycle = float(duty_cycle)
        
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
        self.dutyCycle = int(clamped_duty_cycle)

        _LOGGER.info(f"{self.deviceName}: Duty Cycle changed to {self.dutyCycle}% ")
        return self.dutyCycle

    async def increaseAction(self, data):
        """Schaltet Entfeuchter an oder erhöht Modus"""
        if self.isDimmable:
            newDuty = self.change_duty_cycle(increase=True)
            self.log_action("IncreaseAction")
            await self.turn_on(percentage=newDuty)
        elif self.realHumidifierClass:
            if self.hasModes:
                current_mode = self.get_current_mode()
                next_mode = self.get_next_mode(current_mode, increase=True)
                if next_mode:
                    await self.set_dehumidifier_mode(next_mode)
                    self.log_action(f"IncreaseMode: {next_mode}")
            else:
                if self.isRunning == True:
                    self.log_action("Already in Desired State")
                else:
                    self.log_action("TurnON")
                    await self.turn_on()
        else:
            if self.isRunning == True:
                self.log_action("Already in Desired State ")
            else:
                self.log_action("TurnON ")
                await self.turn_on()

    async def reduceAction(self, data):
        """Schaltet Entfeuchter aus oder reduziert Modus"""
        if self.isDimmable:
            newDuty = self.change_duty_cycle(increase=False)
            self.log_action("ReduceAction")
            if newDuty <= 0:
                _LOGGER.info(f"{self.deviceName}: Duty cycle reached 0, turning OFF completely")
                await self.turn_off()
            else:
                await self.turn_on(percentage=newDuty)
        elif self.realHumidifierClass:
            if self.hasModes:
                current_mode = self.get_current_mode()
                next_mode = self.get_next_mode(current_mode, increase=False)
                if next_mode:
                    await self.set_dehumidifier_mode(next_mode)
                    self.log_action(f"ReduceMode: {next_mode}")
            else:
                if self.isRunning == True:
                    self.log_action("TurnOFF")
                    await self.turn_off()
                else:
                    self.log_action("Already in Desired State")
        else:
            if self.isRunning == True:
                self.log_action("TurnOFF ")
                await self.turn_off()
            else:
                self.log_action("Already in Desired State ")

    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName}"
        _LOGGER.debug(f"{action_name}: {log_message}")
