import logging
import asyncio

from .Device import Device

_LOGGER = logging.getLogger(__name__)


class Door(Device):
    """Door contact device for room-entry awareness notifications."""

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
        self._door_state_listener_registered = False
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
        _LOGGER.info("%s: Door device initialized", self.deviceName)

    def deviceUpdater(self):
        """Register generic updater and dedicated door-state notifications."""
        super().deviceUpdater()

        if self._door_state_listener_registered:
            return

        door_entity_ids = {
            entity.get("entity_id")
            for entity in self.switches
            if isinstance(entity, dict) and str(entity.get("entity_id", "")).startswith("binary_sensor.")
        }
        if not door_entity_ids:
            return

        async def door_state_listener(event):
            data = event.data or {}
            entity_id = data.get("entity_id")
            if entity_id not in door_entity_ids:
                return

            old_state = data.get("old_state")
            new_state = data.get("new_state")
            if not new_state:
                return

            old_value = str(getattr(old_state, "state", "")).lower()
            new_value = str(getattr(new_state, "state", "")).lower()
            if old_value == new_value:
                return

            for entity in self.switches:
                if entity.get("entity_id") == entity_id:
                    entity["value"] = new_value
                    break

            is_open = new_value in ("on", "open", "opening")
            self.isRunning = is_open

            if is_open:
                _LOGGER.warning("🚪 %s: Door OPEN detected on %s", self.room, entity_id)
                asyncio.create_task(
                    self.event_manager.emit(
                        "LogForClient",
                        {
                            "Name": self.room,
                            "Type": "WARNING",
                            "Device": self.deviceName,
                            "Entity": entity_id,
                            "Action": "DoorOpen",
                            "Message": f"Door opened in room {self.room} ({self.deviceName}).",
                        },
                        haEvent=True,
                        debug_type="WARNING",
                    )
                )
            else:
                _LOGGER.info("🚪 %s: Door CLOSED on %s", self.room, entity_id)
                asyncio.create_task(
                    self.event_manager.emit(
                        "LogForClient",
                        {
                            "Name": self.room,
                            "Type": "INFO",
                            "Device": self.deviceName,
                            "Entity": entity_id,
                            "Action": "DoorClose",
                            "Message": f"Door closed in room {self.room} ({self.deviceName}).",
                        },
                        haEvent=True,
                        debug_type="INFO",
                    )
                )

        self.hass.bus.async_listen("state_changed", door_state_listener)
        self._door_state_listener_registered = True
