import asyncio
import json
import logging
from datetime import timedelta
from typing import Any
import voluptuous as vol

from homeassistant.helpers.area_registry import \
    async_get as async_get_area_registry
from homeassistant.helpers.update_coordinator import (DataUpdateCoordinator,
                                                      UpdateFailed)

from .const import DOMAIN
from .OGBController.OGB import OpenGrowBox
from .OGBController.RegistryListener import OGBRegistryEvenListener
from .premium_services import register_premium_services
from .select import OpenGrowBoxRoomSelector
from .text import OpenGrowBoxAccessToken

_LOGGER = logging.getLogger(__name__)


class OGBIntegrationCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage data for multiple hubs and global entities."""

    def __init__(self, hass, config_entry):
        """Initialize the coordinator."""
        self.hass = hass
        self.config_entry = config_entry
        self.room_name = config_entry.data["room_name"]

        self.OGB = OpenGrowBox(hass, config_entry.data["room_name"])
        self.is_ready = False
        self._started = False

        # Track background tasks for proper cleanup
        self._background_tasks: set[asyncio.Task] = set()

        # Entities by type
        self.entities = {
            "sensor": [],
            "number": [],
            "switch": [],
            "select": [],
            "time": [],
            "date": [],
            "text": [],
        }

        self.room_selector = None  # Store the Room Selector instance
        self.long_live_token = None  # Store the Long Live Token for UI

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.room_name}",
            update_interval=timedelta(seconds=15),
        )

        # Track the room selector update task
        self._create_background_task(self.update_room_selector())

    def _create_background_task(self, coro) -> asyncio.Task:
        """Create and track a background task for proper cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from OpenGrowBox controller.

        This method is required by DataUpdateCoordinator but not actively used.
        Sensors update their data through other mechanisms.
        """
        # Return minimal data structure for coordinator compatibility
        return {
            "ready": self.is_ready,
            "room": self.room_name,
            "sensors": {},
            "devices": {},
            "mode": None,
            "premium": False,
        }

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup all resources."""
        _LOGGER.info(f"🛑 Shutting down coordinator for {self.room_name}")

        # 1. Shutdown the OGB controller and all its managers
        if hasattr(self, 'OGB') and self.OGB:
            try:
                _LOGGER.info(f"🛑 Shutting down OGB controller for {self.room_name}")
                await self.OGB.async_shutdown()
                _LOGGER.info(f"✅ OGB controller shutdown complete for {self.room_name}")
            except Exception as e:
                _LOGGER.error(f"❌ Error shutting down OGB controller: {e}", exc_info=True)

        # 2. Cancel all background tasks
        _LOGGER.debug(f"Cancelling {len(self._background_tasks)} background tasks")
        for task in self._background_tasks:
            if not task.done():
                task.cancel()

        # 3. Wait for tasks to complete with timeout
        if self._background_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._background_tasks, return_exceptions=True),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(f"⚠️ Some tasks did not complete within timeout for {self.room_name}")

        self._background_tasks.clear()
        self.is_ready = False
        self._started = False
        _LOGGER.info(f"✅ Coordinator shutdown complete for {self.room_name}")

    def create_room_selector(self):
        """Create a new global Room Selector."""
        area_registry = async_get_area_registry(self.hass)
        areas = area_registry.async_list_areas()
        room_names = [area.name for area in areas] if areas else ["No rooms configured"]

        self.room_selector = OpenGrowBoxRoomSelector(
            name="OGB Rooms", options=room_names
        )
        # Set the entity_id to match what the frontend expects
        self.room_selector._unique_id = f"{DOMAIN}_room_selector"
        return self.room_selector

    async def update_room_selector(self):
        """Update the Room Selector with current Home Assistant rooms."""
        area_registry = async_get_area_registry(self.hass)
        areas = area_registry.async_list_areas()
        room_names = [area.name for area in areas] if areas else ["No rooms configured"]

        if self.room_selector:
            # Preserve the current selected room
            current_option = self.room_selector.current_option
            self.room_selector._options = room_names
            if current_option in room_names:
                self.room_selector._attr_current_option = current_option
            else:
                self.room_selector._attr_current_option = (
                    room_names[0] if room_names else None
                )
            self.room_selector.async_write_ha_state()
            _LOGGER.debug(
                f"Updated Room Selector with rooms: {room_names} (current: {self.room_selector._attr_current_option})"
            )

    async def startOGB(self):
        _LOGGER.info(f"🚀 ============ STARTING OGB INTEGRATION FOR {self.room_name} ============")
        """
        Start the OpenGrowBox-Init.
        """
        if self._started:
            return
        self._started = True
        self.is_ready = False
        await asyncio.sleep(3)
        try:
            room = self.room_name.lower()
            
            # CRITICAL: Load saved datastore state FIRST before managerInit
            # This ensures plant names, dates, mediums are restored BEFORE
            # entity values trigger MediumChange events that would create new empty mediums
            # NOTE: Uses async_init() to avoid blocking I/O in event loop
            try:
                await self.OGB.data_storeManager.async_init()
                _LOGGER.info(f"✅ {self.room_name}: Restored saved state from disk (async)")
            except Exception as e:
                _LOGGER.warning(f"⚠️ {self.room_name}: Could not load saved state: {e}")
            
            # NOW initialize MediumManager with restored data BEFORE managerInit
            # This way when MediumChange comes, it can properly sync instead of creating new
            # CRITICAL: Must call init() which loads from growMediums via _load_mediums_from_store()
            # NOT initialize_mediums_from_config() which loads from wrong path!
            if self.OGB.mediumManager:
                await self.OGB.mediumManager.init()
                _LOGGER.info(f"🌱 {self.room_name}: MediumManager initialized with {len(self.OGB.mediumManager.media)} restored mediums")
            
            groupedRoomEntities = (
                await self.OGB.registryListener.get_filtered_entities_with_value(room)
            )

            ogbGroup = [
                group for group in groupedRoomEntities if "ogb" in group["name"].lower()
            ]
            realDevices = [
                group
                for group in groupedRoomEntities
                if "ogb" not in group["name"].lower()
            ]

            if not realDevices:
                _LOGGER.warning(f"No devices found in room {self.room_name}")

            if ogbGroup:
                _LOGGER.info(
                    f"✅ {self.room_name}: Found {len(ogbGroup)} OGB configuration groups"
                )
                for group in ogbGroup:
                    entity_count = len(group.get('entities', []))
                    _LOGGER.info(f"  📦 {group['name']}: {entity_count} entities")
                ogbTasks = [self.OGB.managerInit(group) for group in ogbGroup]
                await asyncio.gather(*ogbTasks)
                _LOGGER.info(f"✅ {self.room_name}: OGB configuration complete")
            else:
                _LOGGER.error(
                    f"❌ {self.room_name}: No OGB groups found. Proceeding with device initialization."
                )

            if realDevices:
                self.OGB.dataStore.setDeep("workData.Devices", realDevices)
                _LOGGER.info(
                    f"✅ {self.room_name}: Found {len(realDevices)} device groups"
                )
                for group in realDevices[:5]:  # Log first 5
                    entity_count = len(group.get('entities', []))
                    _LOGGER.info(f"  🔌 {group['name']}: {entity_count} entities")
                if len(realDevices) > 5:
                    _LOGGER.info(f"  ... and {len(realDevices) - 5} more device groups")
                deviceTasks = [
                    self.OGB.deviceManager.setupDevice(deviceGroup)
                    for deviceGroup in realDevices
                ]
                await asyncio.gather(*deviceTasks)
                _LOGGER.info(f"✅ {self.room_name}: Device initialization complete")
            else:
                _LOGGER.warning(f"⚠️ {self.room_name}: No devices found.")

            _LOGGER.info(f"🎉 {self.room_name}: OpenGrowBox initialization completed!")

            # CRITICAL: Signal orchestrator that initialization is complete
            # This allows the orchestrator's control loop to start safely
            if hasattr(self.OGB, 'orchestrator') and self.OGB.orchestrator:
                self.OGB.orchestrator.mark_initialization_complete()
                _LOGGER.info(f"✅ {self.room_name}: Orchestrator signaled initialization complete")

        except Exception as e:
            _LOGGER.error(f"Error during OpenGrowBox initialization: {e}")
        finally:
            self.is_ready = True

        # Register core services (only once for first coordinator)
        if not self.hass.data[DOMAIN].get("_core_services_registered", False):
            try:
                await self._register_core_services()
                self.hass.data[DOMAIN]["_core_services_registered"] = True
                _LOGGER.info("✅ Core services registered successfully")
            except Exception as e:
                _LOGGER.error(
                    f"Failed to register core services: {e}", exc_info=True
                )

        # Register premium services (only once for first coordinator)
        if not self.hass.data[DOMAIN].get("_premium_services_registered", False):
            try:
                await register_premium_services(self.hass, self)
                self.hass.data[DOMAIN]["_premium_services_registered"] = True
                _LOGGER.info("✅ Premium services registered successfully")
            except Exception as e:
                _LOGGER.error(
                    f"Failed to register premium services: {e}", exc_info=True
                )

        # Start the monitoring with tracked task
        self._create_background_task(self.wait_until_ready_and_start_monitoring())

    async def wait_until_ready_and_start_monitoring(self):
        _LOGGER.debug("Waiting for OpenGrowBox to be ready...")
        attempt = 0
        while not self.is_ready:
            attempt += 1
            if attempt % 10 == 0:  # Alle 10 Versuche loggen
                _LOGGER.debug("Still waiting for OpenGrowBox to be ready...")
            await asyncio.sleep(0.1)
        _LOGGER.debug("OpenGrowBox is ready. Starting monitoring...")

        await self.OGB.first_start()
        await self.startAllMonitorings()

    # OGB Monitorings
    async def startAllMonitorings(self):
        await self.subEventMonitoring()

    async def subEventMonitoring(self):
        await self.OGB.registryListener.monitor_filtered_entities(self.room_name)

    async def _register_core_services(self) -> None:
        """Register core OGB services like set_mode and set_setpoints."""
        from homeassistant.core import ServiceCall
        import voluptuous as vol

        async def handle_set_mode(call: ServiceCall) -> None:
            """Handle set_mode service call."""
            mode = call.data['mode']

            # Get all coordinators and set mode on each
            for entry_id, coordinator in self.hass.data[DOMAIN].items():
                if hasattr(coordinator, 'OGB') and coordinator.OGB:
                    await coordinator.OGB.async_set_mode(mode)
                    await coordinator.async_request_refresh()

        async def handle_set_setpoints(call: ServiceCall) -> None:
            """Handle set_setpoints service call."""
            setpoints = {
                k: v for k, v in call.data.items()
                if v is not None
            }

            # Get all coordinators and set setpoints on each
            for entry_id, coordinator in self.hass.data[DOMAIN].items():
                if hasattr(coordinator, 'OGB') and coordinator.OGB:
                    await coordinator.OGB.async_set_setpoints(setpoints)
                    await coordinator.async_request_refresh()

        # Register services
        self.hass.services.async_register(
            DOMAIN,
            'set_mode',
            handle_set_mode,
            schema=vol.Schema({
                vol.Required('mode'): vol.In([
                    'VPD-Perfection', 'VPD-Target', 'Closed-Environment',
                    'Drying', 'Disabled'
                ]),
            }),
        )

        self.hass.services.async_register(
            DOMAIN,
            'set_setpoints',
            handle_set_setpoints,
            schema=vol.Schema({
                vol.Optional('temperature'): vol.Coerce(float),
                vol.Optional('humidity'): vol.Coerce(float),
                vol.Optional('vpd'): vol.Coerce(float),
            }),
        )
