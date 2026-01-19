import asyncio
import logging
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

# Use modular managers from managers directory
from ..OGBActionManager import OGBActionManager
from ...data.OGBDataClasses.OGBData import OGBConf
from ...OGBDatastore import DataStore
from ..OGBEventManager import OGBEventManager
from ..OGBConsoleManager import OGBConsoleManager
from ..OGBDataCleanupManager import OGBDataCleanupManager
from ..OGBDeviceManager import OGBDeviceManager
from ..OGBDSManager import OGBDSManager
from ..OGBModeManager import OGBModeManager
from ..OGBFallBackManager import OGBFallBackManager
from ..OGBNotifyManager import OGBNotificator
from ..OGBCO2Manager import OGBCO2Manager
from ...premium.OGBPremiumIntegration import OGBPremiumIntegration
from ..hydro.OGBCastManager import OGBCastManager
from ..medium.OGBMediumManager import OGBMediumManager
from ..hydro.tank.OGBTankFeedManager import OGBTankFeedManager
from ...RegistryListener import OGBRegistryEvenListener

_LOGGER = logging.getLogger(__name__)


class OGBMainController:
    """Main controller for OpenGrowBox system - orchestrates all managers and handles system initialization."""

    def __init__(self, hass, room):
        """Initialize the main controller.

        Args:
            hass: Home Assistant instance
            room: Room identifier
        """
        self.name = "OGB Main Controller"
        self.hass = hass
        self.room = room
        
        # Will be injected from OGB.py after construction
        self.config_manager = None

        # Initialize core components
        self.ogb_config = OGBConf(hass=self.hass, room=self.room)
        self.data_store = DataStore(self.ogb_config)
        self.event_manager = OGBEventManager(self.hass, self.data_store)

        # Registry listener for HA events
        self.registry_listener = OGBRegistryEvenListener(
            self.hass, self.data_store, self.event_manager, self.room
        )

        # Initialize optional managers
        self._initialize_managers()

        # Set default data store values if possible
        if self.data_store:
            self._set_default_data_store_values()

        # Register core event handlers if components are available
        if self.event_manager:
            self._register_event_handlers()
        self._register_event_handlers()

    # =================================================================
    # Compatibility Properties for Action Modules
    # Action modules expect ogb.dataStore, ogb.eventManager, ogb.actionManager
    # =================================================================
    
    @property
    def dataStore(self):
        """Alias for data_store - compatibility with action modules."""
        return self.data_store
    
    @property
    def eventManager(self):
        """Alias for event_manager - compatibility with action modules."""
        return self.event_manager
    
    @property
    def actionManager(self):
        """Alias for action_manager - compatibility with action modules."""
        return self.action_manager

    def _initialize_managers(self):
        """Initialize all subsystem managers."""
        # Data management - MUST be first so it can register event handlers
        self.data_store_manager = OGBDSManager(
            self.hass,
            self.data_store,
            self.event_manager,
            self.room,
            self.registry_listener,
        )

        # Medium Manager - CRITICAL for sensor registration to mediums
        # Must be created BEFORE plant_cast_manager so it can be shared
        self.medium_manager = OGBMediumManager(
            self.hass, self.data_store, self.event_manager, self.room
        )
        _LOGGER.info(f"🌱 {self.room}: Medium Manager initialized")

        # Plant cast manager - pass shared medium_manager to avoid duplicate
        self.plant_cast_manager = OGBCastManager(
            self.hass, self.data_store, self.event_manager, self.room,
            medium_manager=self.medium_manager
        )

        # Device and control systems
        self.device_manager = OGBDeviceManager(
            self.hass,
            self.data_store,
            self.event_manager,
            self.room,
            self.registry_listener,
        )

        self.mode_manager = OGBModeManager(
            self.hass, self.data_store, self.event_manager, self.room
        )

        self.action_manager = OGBActionManager(
            self.hass, self.data_store, self.event_manager, self.room
        )

        # Pass action_manager to closedEnvironmentManager after both are created
        if hasattr(self.mode_manager, 'closedEnvironmentManager'):
            self.mode_manager.closedEnvironmentManager.action_manager = self.action_manager

        self.feed_manager = OGBTankFeedManager(
            self.hass, self.data_store, self.event_manager, self.room
        )

        self.co2_manager = OGBCO2Manager(
            self.hass, self.data_store, self.event_manager, self.room
        )

        # Initialize light scheduler
        from ...devices.lighting.OGBLightScheduler import OGBLightScheduler
        self.light_scheduler = OGBLightScheduler(
            f"light_{self.room}", self.data_store, self.event_manager
        )
        self.light_scheduler.initialize_scheduler()

        # self.client_manager = OGBClientManager(
        #     self.hass, self.data_store, self.event_manager, self.room
        # )  # Commented out - OGBClientManager not found after reorganization

        self.console_manager = OGBConsoleManager(
            self.hass, self.data_store, self.event_manager, self.room
        )

        # Notification system
        self.notificator = OGBNotificator(self.hass, self.room)

        # Monitoring and maintenance
        self.fallback_manager = OGBFallBackManager(
            self.hass,
            self.data_store,
            self.event_manager,
            self.room,
            self.registry_listener,
            self.notificator,
        )

        # Data cleanup
        self.data_cleanup_manager = OGBDataCleanupManager(
            self.data_store,
            self.room,
            retention_days=7,  # Keep 7 days of raw sensor data
        )

        # Premium features - using new modular integration
        self.premium_manager = OGBPremiumIntegration(
            self.hass, self.data_store, self.event_manager, self.room
        )

    def _register_event_handlers(self):
        """Register core event handlers."""
        # Room updates
        self.event_manager.on("RoomUpdate", self.handle_room_update)

        # VPD creation
        self.event_manager.on("VPDCreation", self.handle_new_vpd)

        # Plant timing
        self.event_manager.on("PlantTimeChange", self._auto_update_plant_stages)

        # Ambient data handling
        self.hass.bus.async_listen("AmbientData", self._handle_ambient_data)
        self.hass.bus.async_listen("OutsiteData", self._handle_outsite_data)

    async def first_start(self):
        """Perform initial system startup sequence.
        
        NOTE: LoadDataStore and MediumManager.init() are now called in coordinator.startOGB()
        BEFORE managerInit() to ensure saved data is restored before entity values
        trigger MediumChange events.
        """
        _LOGGER.info(f"Starting OpenGrowBox system for room: {self.room}")

        # Initialize action modules (VPD, Emergency, Dampening, Premium actions)
        # Pass 'self' as the ogb reference - we have compatibility properties
        if self.action_manager:
            await self.action_manager.initialize_action_modules(self)
            _LOGGER.info(f"🔥 {self.room}: Action modules initialized")
        else:
            _LOGGER.error(f"❌ {self.room}: Action manager not available")

        # Initialize VPD data
        await self._get_starting_vpd(True)

        # Start monitoring systems
        if self.fallback_manager:
            await self.fallback_manager.start_monitoring()
        if self.data_cleanup_manager:
            await self.data_cleanup_manager.start_cleanup()

        # Emit initial events
        await self.event_manager.emit("HydroModeChange", True)
        await self.event_manager.emit("HydroModeRetrieveChange", True)
        await self.event_manager.emit("PlantTimeChange", True)

        # Handle premium features if available
        # TODO: Implement premium manager integration
        # if (self.premium_manager.grow_plan_manager.manager_active and
        #     self.premium_manager.grow_plan_manager.manager_active is True):
        #     strain_name = self.data_store.get("strainName")
        #     plan_request_data = {"event_id": "grow_plans_on_start", "strain_name": strain_name}
        #     success = await self.premium_manager.ogb_ws.prem_event("get_grow_plans", plan_request_data)
        #     if success and self.premium_manager.grow_plan_manager.manager_active:
        #         asyncio.create_task(self._delayed_plan_activation())

        _LOGGER.debug(
            f"OpenGrowBox for {self.room} started successfully. State: {self.data_store}"
        )
        return True

    async def _delayed_plan_activation(self):
        """Activate grow plan after 30-second delay."""
        await asyncio.sleep(30)

        # TODO: Implement premium manager integration
        # if self.premium_manager.grow_plan_manager.manager_active:
        #     await self.event_manager.emit("plan_activation", self.premium_manager.grow_plan_manager.active_grow_plan)
        #     _LOGGER.info(f"Delayed plan activation executed for {self.room}")
        # else:
        #     _LOGGER.debug(f"Delayed plan activation skipped (manager inactive) for {self.room}")
        _LOGGER.debug(f"Delayed plan activation placeholder for {self.room}")

    async def _get_starting_vpd(self, init_data):
        """Initialize VPD calculations from sensor data."""
        # This will be implemented in OGBVPDManager
        # For now, just emit the event
        if init_data is True:
            await self.event_manager.emit("VPDCreation", init_data)

    async def handle_room_update(self, entity):
        """Handle room update events - main handler for sensor updates."""
        # Type check - entity must have Name attribute
        if not hasattr(entity, 'Name'):
            _LOGGER.debug(f"Skipping invalid entity type: {type(entity)}")
            return
        
        # Check if this is an OGB config entity (select, number, etc.)
        if "ogb_" in entity.Name:
            # Route to configuration manager for handling
            if self.config_manager:
                # Extract entity key from entity name (lowercase, without room suffix in some cases)
                entity_key = entity.Name.lower()
                self.config_manager.handle_configuration_update(entity_key, entity)
            else:
                _LOGGER.warning(f"{self.room} - config_manager not available for entity: {entity.Name}")
            return

        # Handle light scheduling updates
        await self.light_schedule_update(entity)

        # Handle VPD updates - emit VPDCreation event based on determination mode
        vpd = self.data_store.getDeep("vpd.current")

        from ...data.OGBDataClasses.OGBPublications import OGBVPDPublication
        from ...data.OGBParams.OGBParams import VPD_INTERVALS
        
        VPDPub = OGBVPDPublication(Name="RoomUpdate", VPD=vpd, AvgDew=None, AvgHum=None, AvgTemp=None)

        vpd_determination = self.data_store.get("vpdDetermination")
        interval = VPD_INTERVALS.get(vpd_determination.upper() if vpd_determination else "LIVE", 0)

        if interval == 0:
            # LIVE: VPD is calculated immediately on every sensor update
            await self.event_manager.emit("VPDCreation", VPDPub)
        else:
            # Interval mode: run periodically (handled elsewhere)
            pass

    async def update_light_state(self):
        """
        Update light state based on configured light on/off times.
        Returns True if lights should be on, False if off, None if times not configured.
        """
        light_on_time_str = self.data_store.getDeep("isPlantDay.lightOnTime")
        light_off_time_str = self.data_store.getDeep("isPlantDay.lightOffTime")

        try:
            if not light_on_time_str or not light_off_time_str or light_on_time_str == "" or light_off_time_str == "":
                _LOGGER.warning(f"{self.room}: Light times not configured (lightOnTime={light_on_time_str}, lightOffTime={light_off_time_str})")
                return None

            # Convert time strings to time objects
            from datetime import datetime
            light_on_time = datetime.strptime(light_on_time_str, "%H:%M:%S").time()
            light_off_time = datetime.strptime(light_off_time_str, "%H:%M:%S").time()

            # Get current time
            current_time = datetime.now().time()

            # Check if current time is in the "lights on" range
            if light_on_time < light_off_time:
                # Normal cycle (e.g., 08:00 to 20:00)
                is_light_on = light_on_time <= current_time < light_off_time
            else:
                # Over midnight (e.g., 20:00 to 08:00)
                is_light_on = current_time >= light_on_time or current_time < light_off_time

            _LOGGER.debug(f"{self.room}: Light state check - Current: {current_time}, On: {light_on_time}, Off: {light_off_time}, Should be ON: {is_light_on}")
            return is_light_on

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error updating light state: {e}")
            return None

    async def light_schedule_update(self, data):
        """Handle light schedule updates - check if lights should turn on/off based on time."""
        light_by_ogb_control = self.data_store.getDeep(
            "controlOptions.lightbyOGBControl"
        )

        if light_by_ogb_control == False:
            return

        # Calculate desired light state based on current time and schedule
        light_should_be_on = await self.update_light_state()

        if light_should_be_on is None:
            return

        # Update datastore with current desired state
        self.data_store.setDeep("isPlantDay.islightON", light_should_be_on)
        _LOGGER.info(
            f"{self.room}: Light status checked and updated for {self.room} - Light status is {light_should_be_on}"
        )

        # Emit toggleLight event ONLY for normal Light devices (exclude special lights)
        # Special lights (LightFarRed, LightUV, LightBlue, LightRed, LightSpectrum) use their own scheduling
        special_light_types = {"LightFarRed", "LightUV", "LightBlue", "LightRed", "LightSpectrum"}
        normal_light_devices = []

        # Get all registered devices and filter for normal lights only
        if hasattr(self, 'device_manager') and hasattr(self.device_manager, 'devices'):
            for device_name, device in self.device_manager.devices.items():
                device_type = getattr(device, 'deviceType', None) or getattr(device, 'device_type', None)
                if device_type == "Light" and device_name not in special_light_types:
                    normal_light_devices.append(device_name)

        # Emit targeted toggle event only to normal lights
        if normal_light_devices:
            await self.event_manager.emit("toggleLight", {
                "state": light_should_be_on,
                "target_devices": normal_light_devices
            })
            _LOGGER.debug(f"{self.room}: Toggled {len(normal_light_devices)} normal lights to {light_should_be_on}")
        else:
            # Fallback: emit to all lights if no devices filtered (for backward compatibility)
            await self.event_manager.emit("toggleLight", light_should_be_on)

    async def manager(self, data):
        """Route configuration updates to appropriate handlers via ConfigurationManager."""
        # Extract entity key from the data
        if hasattr(data, 'Name'):
            entity_key = data.Name.split(".", 1)[-1].lower()  # Remove prefix like "select."
        elif hasattr(data, 'name'):
            entity_key = data.name.split(".", 1)[-1].lower()
        elif isinstance(data, dict):
            name = data.get("name") or data.get("Name") or ""
            entity_key = name.split(".", 1)[-1].lower() if "." in name else name.lower()
        else:
            entity_key = str(data).split(".", 1)[-1].lower() if "." in str(data) else str(data).lower()
        
        # Check if config_manager is available (injected from OGB.py)
        if hasattr(self, 'config_manager') and self.config_manager:
            actions = self.config_manager.get_configuration_mapping()
            action = actions.get(entity_key)
            
            if action:
                _LOGGER.debug(f"[{self.room}] Manager executing action for: {entity_key}")
                await action(data)
                return True
            else:
                _LOGGER.debug(f"[{self.room}] Manager no action found for: {entity_key}")
                return False
        else:
            _LOGGER.debug(f"[{self.room}] config_manager not available, cannot route: {entity_key}")
            return False

    async def handle_new_vpd(self, data):
        """Handle new VPD data events."""
        # This will be implemented in OGBVPDManager
        control_option = self.data_store.get("mainControl")
        if control_option not in ["HomeAssistant", "Premium"]:
            return

        # VPD processing logic will be in VPD manager

    async def _handle_ambient_data(self, event):
        """Handle ambient data from other rooms."""
        if self.room.lower() == "ambient":
            return

        _LOGGER.debug(f"Received ambient data for {self.room}")

        payload = event.data
        temp = payload.get("AvgTemp")
        hum = payload.get("AvgHum")

        self.data_store.setDeep("tentData.AmbientTemp", temp)
        self.data_store.setDeep("tentData.AmbientHum", hum)

        # Update sensors - this will be in sensor manager
        from ...utils.sensorUpdater import _update_specific_sensor

    def _set_default_data_store_values(self):
        """Set critical default values in data store for proper operation."""
        # Set mainControl default if not set
        if not self.data_store.get("mainControl"):
            self.data_store.set("mainControl", "HomeAssistant")
            _LOGGER.info(f"🔧 Set mainControl to 'HomeAssistant' for room {self.room}")

        _LOGGER.info(f"🔧 Initialized default data store values for room {self.room}")

    async def _handle_outsite_data(self, event):
        """Handle outside weather data."""
        if self.room.lower() == "ambient":
            return

        _LOGGER.debug(f"Received outside data for {self.room}: {event}")

        payload = event.data
        temp = payload.get("temperature")
        hum = payload.get("humidity")

        self.data_store.setDeep("tentData.OutsiteTemp", temp)
        self.data_store.setDeep("tentData.OutsiteHum", hum)

        # Update sensors - this will be in sensor manager
        from ...utils.sensorUpdater import _update_specific_sensor

        await _update_specific_sensor(
            "ogb_outsitetemperature_", self.room, temp, self.hass
        )
        await _update_specific_sensor("ogb_outsitehumidity_", self.room, hum, self.hass)

    async def _auto_update_plant_stages(self, data):
        """Automatically update plant stages periodically."""
        time_now = datetime.now()
        # Plant stage updates will be in plant manager
        await asyncio.sleep(8 * 60 * 60)  # 8 hours
        asyncio.create_task(self._auto_update_plant_stages(time_now))

    def __str__(self):
        return f"{self.name} - Running"

    def __repr__(self):
        return f"{self.name} - Running"
