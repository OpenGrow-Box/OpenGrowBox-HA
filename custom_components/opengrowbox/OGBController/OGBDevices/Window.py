import logging

from .Ventilation import Ventilation

_LOGGER = logging.getLogger(__name__)


class Window(Ventilation):
    """Window actuator device.

    Uses the existing ventilation control/event pipeline so windows can be
    controlled by the same canVentilate capability and safety logic.
    """

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
        _LOGGER.debug("%s: Window device initialized via Ventilation logic", self.deviceName)

    async def reduceAction(self, data):
        """Reduces the duty cycle."""

        # Smart Deadband Check - Block action if in deadband
        if self._in_smart_deadband:
            _LOGGER.debug(
                f"{self.deviceName}: ReduceAction BLOCKED - device is in Smart Deadband (operating at minimum)"
            )
            return

        # Call parent's reduceAction for normal operation
        await super().reduceAction(data)
