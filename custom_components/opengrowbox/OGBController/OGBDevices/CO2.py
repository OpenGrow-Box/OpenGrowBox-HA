import logging

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
        self.targetCO2 = 0  # Zielwert für CO2 (ppm)
        self.currentCO2 = 0  # Aktueller CO2-Wert (ppm)
        self.autoRegulate = False  # Automatische Steuerung

        ## Events Register
        self.event_manager.on("NewCO2Publication", self.handleNewCO2Value)
        self.event_manager.on("Increase CO2", self.increaseAction)
        self.event_manager.on("Reduce CO2", self.reduceAction)

        _LOGGER.info(f"CO2 Device {self.deviceName} initialized with {len(self.switches)} switches, {len(self.sensors)} sensors, {len(self.options)} options")

    def deviceInit(self, entitys):
        """Override deviceInit to add CO2-specific debugging."""
        _LOGGER.info(f"CO2 Device {self.deviceName} starting initialization with {len(entitys)} entities")
        for entity in entitys:
            entity_id = entity.get("entity_id", "unknown")
            entity_value = entity.get("value", "unknown")
            _LOGGER.debug(f"CO2 Device {self.deviceName} processing entity: {entity_id} = {entity_value}")

        # Call parent initialization
        super().deviceInit(entitys)

        _LOGGER.info(f"CO2 Device {self.deviceName} initialization complete. isInitialized: {self.isInitialized}, switches: {len(self.switches)}, sensors: {len(self.sensors)}")

    # Actions Helpers
    async def handleNewCO2Value(self, co2Publication):
        self.log_action(f" Check  {co2Publication} ")

    async def increaseAction(self, data):
        """Erhöht den CO2 Wert"""
        logging.warning("INCREASE ACTION START")
        self.log_action("IncreaseAction/TurnOn")
        await self.turn_on()

    async def reduceAction(self, data):
        """Reduziertden CO2 Wert"""
        logging.warning("REDUCE ACTION START")
        self.log_action("ReduceAction/TurnOff")
        await self.turn_off()

    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName} PPM-Current:{self.currentCO2} Target-PPM:{self.targetCO2}"
        _LOGGER.warn(f"{action_name}: {log_message}")
