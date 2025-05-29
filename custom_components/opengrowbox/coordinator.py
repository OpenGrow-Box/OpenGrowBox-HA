from datetime import timedelta
import logging
import json
import asyncio

from homeassistant.helpers.area_registry import async_get as async_get_area_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .select import OpenGrowBoxRoomSelector
from .text import OpenGrowBoxAccessToken
from .const import DOMAIN
from .OGBController.RegistryListener import OGBRegistryEvenListener
from .OGBController.OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)

class OGBIntegrationCoordinator(DataUpdateCoordinator):
    """Manage data for multiple hubs and global entities."""

    def __init__(self, hass, config_entry):
        """Initialize the coordinator."""
        self.hass = hass
        self.config_entry = config_entry
        self.room_name = config_entry.data["room_name"]

        
        self.OGB = OpenGrowBox(hass,config_entry.data["room_name"])
        self.is_ready = False 
        
        # Entitäten nach Typ initialisieren
        self.entities = {
            "sensor": [],
            "number": [],
            "switch": [],
            "select": [],
            "time": [],
            "date":[],
            "text":[],
        }
        
        self.room_selector = None  # Store the Room Selector instance
        self.long_live_token = None # Store the Long Live Token for UI 
        
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.room_name}",
            update_interval=timedelta(seconds=15),
        )

    def create_room_selector(self):
        """Create a new global Room Selector."""
        area_registry = async_get_area_registry(self.hass)
        areas = area_registry.async_list_areas()
        room_names = [area.name for area in areas]

        self.room_selector = OpenGrowBoxRoomSelector(
            name="OGB Rooms",
            options=room_names
        )
        return self.room_selector
       
    async def update_room_selector(self):
        """Update the Room Selector with current Home Assistant rooms."""
        area_registry = async_get_area_registry(self.hass)
        areas = area_registry.async_list_areas()
        room_names = [area.name for area in areas]

        if self.room_selector:
            # Preserve the current selected room
            current_option = self.room_selector.current_option
            self.room_selector._options = room_names
            if current_option in room_names:
                self.room_selector._attr_current_option = current_option
            else:
                self.room_selector._attr_current_option = room_names[0] if room_names else None
            self.room_selector.async_write_ha_state()
            _LOGGER.debug(f"Updated Room Selector with rooms: {room_names} (current: {self.room_selector._attr_current_option})")

    async def startOGB(self):
        """
        Startet die OpenGrowBox-Initialisierung und stellt sicher, 
        dass Events erst nach Abschluss der Initialisierung verarbeitet werden.
        """
        _LOGGER.debug("Starting OpenGrowBox initialization.")
        self.is_ready = False  # Verhindert die Verarbeitung von Events während der Initialisierung

        try:
            # Abrufen und Verarbeiten der Raum-Entitäten
            room = self.room_name.lower()
            groupedRoomEntities = await self.OGB.registryListener.get_filtered_entities_with_value(room)

            #_LOGGER.warning(f"All Groups {groupedRoomEntities} in {self.room_name}")

            # Filtern der Gruppen
            ogbGroup = [group for group in groupedRoomEntities if "ogb" in group["name"].lower()]
            realDevices = [group for group in groupedRoomEntities if "ogb" not in group["name"].lower()]

            #_LOGGER.warning(f"OGB group {ogbGroup} in {self.room_name}")
            #_LOGGER.warning(f"Real Devices {realDevices} in {self.room_name}")

            # Verarbeite zuerst die OGB-Gruppen
            if ogbGroup:
                _LOGGER.debug(f"Starting OGB initialization for {len(ogbGroup)} groups.")
                ogbTasks = [self.OGB.managerInit(group) for group in ogbGroup]
                await asyncio.gather(*ogbTasks)  # Warte, bis alle OGB-Tasks abgeschlossen sind
            else:
                _LOGGER.warning(f"No OGB groups found in room {self.room_name}. Proceeding with device initialization.")

            # Danach die anderen Geräte verarbeiten
            if realDevices:
                _LOGGER.debug(f"Starting device initialization for {len(realDevices)} groups.")
                deviceTasks = [self.OGB.deviceManager.addDevice(deviceGroup) for deviceGroup in realDevices]
                await asyncio.gather(*deviceTasks)  # Warte, bis alle Geräte-Tasks abgeschlossen sind
            else:
                _LOGGER.warning(f"No devices found in room {self.room_name}.")

            # Abschließende Initialisierungen
            await self.OGB.firstInit()

            _LOGGER.debug(f"OpenGrowBox initialization completed in {self.room_name}.")
        except Exception as e:
            _LOGGER.error(f"Error during OpenGrowBox initialization: {e}")
        finally:
            self.is_ready = True  # Initialisierung abgeschlossen

        # Starte das Monitoring
        asyncio.create_task(self.wait_until_ready_and_start_monitoring())

    async def wait_until_ready_and_start_monitoring(self):
        """Wartet, bis die Initialisierung abgeschlossen ist, und startet dann das Monitoring."""
        _LOGGER.debug("Waiting for OpenGrowBox to be ready...")
        attempt = 0
        while not self.is_ready:
            attempt += 1
            if attempt % 10 == 0:  # Alle 10 Versuche loggen
                _LOGGER.debug("Still waiting for OpenGrowBox to be ready...")
            await asyncio.sleep(0.1)
        _LOGGER.debug("OpenGrowBox is ready. Starting monitoring...")
        await self.startAllMonitorings()

    # OGB Monitorings
    async def startAllMonitorings(self):
       await self.subEventMonitoring()
       #await self.subDeviceMonitoring()

    async def subEventMonitoring(self):
        """Starte das Event Monitoring."""
        await self.OGB.registryListener.monitor_filtered_entities(self.room_name)
        
        # Device monitoring für adding and removing Devices and Sensors.
        #await self.OGB.registryListener.monitor_device_and_entity_changes(self.room_name)          
        
    async def subDeviceMonitoring(self):
        await asyncio.sleep(0.1)
        """Starte das Event Monitoring."""
        #await self.OGB.registryListener.monitor_filtered_entities(self.room_name)

