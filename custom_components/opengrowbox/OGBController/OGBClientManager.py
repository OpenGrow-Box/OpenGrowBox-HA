import logging
_LOGGER = logging.getLogger(__name__)

class OGBClientManager:
    def __init__(self, hass, dataStore, eventManager,room):
        self.name = "OGB Device Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False

        self.init()

        
  
    def init(self):
        """initialized Device Manager."""
        self._setup_event_listeners()
        self.is_initialized = True
        _LOGGER.info("OGBDeviceManager initialized with event listeners.")

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self.hass.bus.async_listen("need_targets", self.provide_targets)

    async def provide_targets(self):
        vpd_targets = self.dataStore.get("vpd")
        await self.eventManager.emit("target_values",vpd_targets)