import logging
from datetime import datetime, timedelta

from .Device import Device

_LOGGER = logging.getLogger(__name__)


class Pump(Device):
    """
    Generic Pump device class for all pump types.
    
    Supports:
    - Generic Pump (pump, dripper, feedsystem) -> canPump
    - FeedPump (feed_a, feed_b, feed_c, feed_w, feed_x, feed_y, feed_php, feed_phm, feed_water) -> canFeed
    - ReservoirPump (reservoir_pump, reservoirpump, tank_fill, fill_pump, reservoir_fill, water_fill) -> canReservoirFill
    - RetrievePump (retrieve, return, retrieve_pump, return_pump, recovery) -> canRetrieve
    - WateringPump (watering, plant_water, irrigation, watering_pump, irrigate) -> canWatering
    - AeroPump (aero, aeroponic, aero_pump, mist, misting) -> canAero
    - DWCPump (dwc, deep_water, dwc_pump, recirculating) -> canDWC
    - ClonerPump (cloner, clone, cloner_pump, propagation) -> canClone
    """
    
    # Pump type to capability mapping
    PUMP_TYPE_CAPABILITIES = {
        "Pump": "canPump",
        "FeedPump": "canFeed",
        "ReservoirPump": "canReservoirFill",
        "RetrievePump": "canRetrieve",
        "WateringPump": "canWatering",
        "AeroPump": "canAero",
        "DWCPump": "canDWC",
        "ClonerPump": "canClone",
    }
    
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
        self.isRunning = False
        self.Interval = None  # Mindestintervall zwischen Pumpzyklen (in Sekunden)
        self.Duration = None  # Pumpdauer in Sekunden
        self.isAutoRun = False  # Automatikmodus
        self.lastPumpTime = None  # Zeitpunkt des letzten Pumpvorgangs

        self.currentEC = None
        self.minEC = None  # Elektrische Leitfähigkeit
        self.maxEC = None  # Maximaler EC-Wert

        self.soilMoisture = None  # Bodenfeuchtigkeit

        # PLANT FEEDING CLASSIC
        self.minSoilMoisture = 25  # Mindestbodenfeuchte
        self.maxSoilMoisture = 25  # Mindestbodenfeuchte
        
        # Pump type specific attributes
        self.pumpCapability = self.PUMP_TYPE_CAPABILITIES.get(deviceType, "canPump")
        
        _LOGGER.debug(
            f"[{self.inRoom}] Pump '{self.deviceName}' initialized as {deviceType} "
            f"with capability {self.pumpCapability}"
        )

        ## Events Register
        self.event_manager.on("Increase Pump", self.onAction)
        self.event_manager.on("Reduce Pump", self.offAction)
        
        # Register pump-specific events based on type
        self._register_pump_events()

    def _register_pump_events(self):
        """Register pump-type specific events."""
        capability = self.pumpCapability
        
        # Map capabilities to event names
        event_map = {
            "canPump": ["PumpAction", "GenericPump"],
            "canFeed": ["FeedAction", "NutrientPump"],
            "canReservoirFill": ["ReservoirFillAction", "ReservoirPump"],
            "canRetrieve": ["RetrieveAction", "ReturnPump"],
            "canWatering": ["WateringAction", "IrrigationPump"],
            "canAero": ["AeroAction", "AeroPump"],
            "canDWC": ["DWCAction", "DWCPump"],
            "canClone": ["CloneAction", "ClonerPump"],
        }
        
        events = event_map.get(capability, ["PumpAction"])
        for event_name in events:
            self.event_manager.on(event_name, self._handle_pump_event)
            _LOGGER.debug(f"[{self.inRoom}] Pump '{self.deviceName}' registered for event: {event_name}")

    async def _handle_pump_event(self, data):
        """Handle pump-specific events."""
        if isinstance(data, dict):
            target_name = data.get("Device") or data.get("id")
            action = data.get("Action", "on").lower()
        else:
            target_name = getattr(data, "Device", None)
            action = getattr(data, "Action", "on").lower()

        if target_name != self.deviceName:
            return  # Nicht für diese Pumpe bestimmt

        if action in ["on", "start", "activate"]:
            self.log_action(f"TurnON [{self.pumpCapability}]")
            await self.turn_on()
        elif action in ["off", "stop", "deactivate"]:
            self.log_action(f"TurnOFF [{self.pumpCapability}]")
            await self.turn_off()

    # Actions Helpers
    async def onAction(self, data):
        """Start Pump"""
        if isinstance(data, dict):
            target_name = data.get("Device") or data.get("id")
        else:
            target_name = getattr(data, "Device", None)

        if target_name != self.deviceName:
            return  # Nicht für diese Pumpe bestimmt

        self.log_action("TurnON ")
        await self.turn_on()

    async def offAction(self, data):
        """Stop Pump"""
        if isinstance(data, dict):
            target_name = data.get("Device") or data.get("id")
        else:
            target_name = getattr(data, "Device", None)

        if target_name != self.deviceName:
            return  # Nicht für diese Pumpe bestimmt

        self.log_action("TurnOFF ")
        await self.turn_off()

    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion."""
        log_message = f"{self.deviceName} [{self.pumpCapability}]"
        _LOGGER.debug(f"{action_name}: {log_message}")
