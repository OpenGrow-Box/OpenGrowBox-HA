import logging

from .Device import Device

_LOGGER = logging.getLogger(__name__)


class GenericSwitch(Device):
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
        ## Events Register
        self.event_manager.on("Switch ON", self.increaseAction)
        self.event_manager.on("Switch OFF", self.reduceAction)

    # Actions Helpers

    async def increaseAction(self, data):
        _LOGGER.error(f"You got an Gerneric Switch on -> {self.deviceName}.")
        pass

    async def reduceAction(self, data):
        _LOGGER.error(f"You got an Gerneric Switch on -> {self.deviceName}.")
        pass
