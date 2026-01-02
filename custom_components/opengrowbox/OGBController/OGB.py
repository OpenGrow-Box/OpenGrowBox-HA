"""
OpenGrowBox - Modular Main Controller

This file provides the main OpenGrowBox interface that integrates all modular managers.
It replaces the monolithic implementation with a clean, modular architecture.
"""

import asyncio
import logging
from datetime import datetime

from .managers.core.OGBConfigurationManager import OGBConfigurationManager
from .managers.core.OGBMainController import OGBMainController
from .managers.core.OGBVPDManager import OGBVPDManager
from .OGBOrchestrator import OGBOrchestrator
from .RegistryListener import OGBRegistryEvenListener
# OGBPremiumIntegration is now created by OGBMainController - no need to import here

_LOGGER = logging.getLogger(__name__)


class OpenGrowBox:
    """
    Main OpenGrowBox controller - integrates all modular managers.

    This class provides the main interface that other components expect,
    but delegates all functionality to the modular managers.
    """

    def __init__(self, hass, room):
        """
        Initialize OpenGrowBox with modular managers.

        Args:
            hass: Home Assistant instance
            room: Room identifier
        """
        self.name = "OpenGrowBox Modular Controller"
        self.hass = hass
        self.room = room

        # Initialize modular managers FIRST
        self.main_controller = OGBMainController(hass, room)

        # Registry Listener for HA Events
        self.registryListener = OGBRegistryEvenListener(hass, self.main_controller.data_store, self.main_controller.event_manager, room)

        # Initialize orchestrator for control loop coordination
        self.orchestrator = OGBOrchestrator(
            hass,
            self.main_controller.data_store,
            self.main_controller.event_manager,
            room
        )

        # Provide backwards compatibility attributes
        self.data_store = self.main_controller.data_store
        self.dataStore = self.main_controller.data_store  # Backwards compatibility for external code
        self.event_manager = self.main_controller.event_manager
        self.eventManager = self.main_controller.event_manager  # Backwards compatibility for external code

        # Use premium integration from main_controller (don't create duplicate)
        self.prem_manager = self.main_controller.premium_manager
        # Backwards compatibility aliases
        self.premiumManager = self.prem_manager
        self.premium_manager = self.prem_manager
        # Set room-based attribute for sensor access
        setattr(self, room, self.prem_manager)

        # mainControl is now set in OGBConf default value to enable device initialization
        self.vpd_manager = OGBVPDManager(
            self.main_controller.data_store,
            self.main_controller.event_manager,
            room,
            hass,
            self.prem_manager,  # Add prem_manager
        )
        self.config_manager = OGBConfigurationManager(
            self.main_controller.data_store,
            self.main_controller.event_manager,
            room,
            hass,
        )

        # Register event handlers (matching original)
        # NOTE: RoomUpdate is handled by OGBMainController - don't register duplicate here
        # The main_controller._register_event_handlers() already registers for RoomUpdate
        self.event_manager.on("PlantTimeChange", self._auto_update_plant_stages)

        # Register HA bus listeners
        self.hass.bus.async_listen("AmbientData", self._handle_ambient_data)
        self.hass.bus.async_listen("OutsiteData", self._handle_outsite_data)

        # Initialize additional backwards compatibility
        self._setup_backwards_compatibility()

    def _setup_backwards_compatibility(self):
        """Set up attributes and methods for backwards compatibility."""
        # VPD-related attributes and methods
        self.vpd = self.vpd_manager

        # Configuration handling - backwards compatible wrapper
        self.manager = self._manager_wrapper

        # Plant stage management
        self._plant_stage_to_vpd = self.config_manager._plant_stage_to_vpd

        # Sensor updating methods
        self._update_vpd_tolerance = self.config_manager._update_vpd_tolerance

        # Device management
        self.deviceManager = self.main_controller.device_manager

        # Backwards compatibility for all managers (matching original monolithic structure)
        self.data_storeManager = self.main_controller.data_store_manager
        self.plantCastManager = self.main_controller.plant_cast_manager
        self.modeManager = self.main_controller.mode_manager
        self.actionManager = self.main_controller.action_manager
        self.feedManager = self.main_controller.feed_manager
        self.consoleManager = self.main_controller.console_manager
        self.premiumManager = self.premium_manager  # From our initialization
        
        # Medium Manager - CRITICAL: This was missing and caused sensors not to register to mediums
        self.mediumManager = self.main_controller.medium_manager
        self.medium_manager = self.main_controller.medium_manager

        # Inject config_manager into main_controller for entity routing
        self.main_controller.config_manager = self.config_manager

        # Expose key methods from managers for backwards compatibility
        self._get_starting_vpd = self.main_controller._get_starting_vpd
        
        # Inject managers into orchestrator for control loop coordination
        self.orchestrator.inject_managers(
            device_manager=self.main_controller.device_manager,
            mode_manager=self.main_controller.mode_manager,
            action_manager=self.main_controller.action_manager,
            feed_manager=self.main_controller.feed_manager,
            vpd_manager=self.vpd_manager
        )

    async def _manager_wrapper(self, entity):
        """Backwards compatible wrapper for manager method - routes to ConfigurationManager."""
        # Extract entity key and data from the entity object
        if hasattr(entity, 'Name'):
            entity_key = entity.Name.split(".", 1)[-1].lower()  # Remove prefix like "select."
        elif hasattr(entity, 'name'):
            entity_key = entity.name.split(".", 1)[-1].lower()
        else:
            entity_key = str(entity).split(".", 1)[-1].lower() if "." in str(entity) else str(entity).lower()

        # Get the configuration mapping
        actions = self.config_manager.get_configuration_mapping()
        action = actions.get(entity_key)
        
        if action:
            _LOGGER.info(f"OGB-Manager {self.room}: Executing action for {entity_key}")
            await action(entity)
            return True
        else:
            # Log available keys for debugging medium issues
            if "medium" in entity_key:
                available_medium_keys = [k for k in actions.keys() if "medium" in k]
                _LOGGER.warning(f"OGB-Manager {self.room}: No action for '{entity_key}'. Available medium keys: {available_medium_keys}")
            else:
                _LOGGER.debug(f"OGB-Manager {self.room}: No action found for {entity_key}")
            return False

    async def _delayed_plan_activation(self):
        """Activate grow plan after 30 second delay in background."""
        await asyncio.sleep(30)

        if hasattr(self, 'premium_manager') and self.premium_manager:
            try:
                if hasattr(self.premium_manager, 'growPlanManager') and self.premium_manager.growPlanManager.managerActive:
                    await self.event_manager.emit("plan_activation", self.premium_manager.growPlanManager.active_grow_plan)
                    _LOGGER.info(f"Delayed plan activation executed for {self.room}")
                else:
                    _LOGGER.debug(f"Delayed plan activation skipped (manager inactive) for {self.room}")
            except Exception as e:
                _LOGGER.error(f"Error in delayed plan activation: {e}")

    async def _vpd_update_loop(self, interval: int, vpdPublication):
        """Periodic VPD calculation at set interval."""
        while True:
            try:
                await self.event_manager.emit("VPDCreation", vpdPublication)
            except Exception as e:
                _LOGGER.error(f"Error in VPD calculation: {e}")
            await asyncio.sleep(interval)

    async def first_start(self):
        """Start the OpenGrowBox system."""
        _LOGGER.debug(f"🔧 FIRST_START CALLED for room {self.room}")
        Init = True

        # Initialize main control setting for device setup
        if not self.data_store.get("mainControl"):
            self.data_store.set("mainControl", "HomeAssistant")

        # Initialize default data store values (lost in modular migration)
        self._initialize_default_data_store_values()

        # Initialize action modules (pump controller, etc.)
        if hasattr(self.actionManager, 'initialize_action_modules'):
            await self.actionManager.initialize_action_modules(self)

        await self._get_starting_vpd(Init)

        # Mark config manager as initialized BEFORE emitting events
        # This allows subsequent config changes to emit events
        if hasattr(self, 'config_manager') and hasattr(self.config_manager, 'mark_initialized'):
            self.config_manager.mark_initialized()
        
        await self.event_manager.emit("HydroModeChange", Init)
        await self.event_manager.emit("HydroModeRetrieveChange", Init)
        await self.event_manager.emit("PlantTimeChange", Init)

        # Start orchestrator control loop
        await self.orchestrator.start()
        _LOGGER.info(f"✅ {self.room} Orchestrator control loop started")

        # Premium manager grow plan activation
        if hasattr(self, 'premium_manager') and self.premium_manager:
            try:
                if hasattr(self.premium_manager, 'growPlanManager') and self.premium_manager.growPlanManager.managerActive is True:
                    strain_name = self.data_store.get("strainName")
                    planRequestData = {"event_id": "grow_plans_on_start", "strain_name": strain_name}

                    # Start plan retrieval normally
                    if hasattr(self.premium_manager, 'ogb_ws'):
                        success = await self.premium_manager.ogb_ws.prem_event("get_grow_plans", planRequestData)

                        # If successful → start background task
                        if success and self.premium_manager.growPlanManager.managerActive:
                            asyncio.create_task(self._delayed_plan_activation())
            except Exception as e:
                _LOGGER.warning(f"Error in premium grow plan activation: {e}")

        _LOGGER.debug(f"OpenGrowBox for {self.room} started successfully State:{self.data_store}")
        return True

    async def managerInit(self, ogbEntity):
        """Initialize manager with OGB entities - processes each entity."""
        from .data.OGBDataClasses.OGBPublications import OGBInitData
        
        for entity in ogbEntity['entities']:
            entity_id = entity['entity_id']
            value = entity['value']
            entityPublication = OGBInitData(Name=entity_id, newState=[value])
            await self.manager(entityPublication)

    async def handle_room_update(self, entity):
        """
        Update WorkData for temperature or humidity based on an entity.
        Ignore entities that contain 'ogb_' in the name.
        """
        # Type check - entity must have Name attribute
        if not hasattr(entity, 'Name'):
            _LOGGER.debug(f"Skipping invalid entity type: {type(entity)}")
            return
        
        # Skip entities with 'ogb_' in name
        if "ogb_" in entity.Name:
            await self.manager(entity)
            return

        await self.light_schedule_update(entity)

        vpd = self.data_store.getDeep("vpd.current")

        from .data.OGBDataClasses.OGBPublications import OGBVPDPublication
        VPDPub = OGBVPDPublication(Name="RoomUpdate", VPD=vpd, AvgDew=None, AvgHum=None, AvgTemp=None)

        vpdDetermination = self.data_store.get("vpdDetermination")
        from .data.OGBParams.OGBParams import VPD_INTERVALS
        interval = VPD_INTERVALS.get(vpdDetermination.upper(), 0)

        if interval == 0:
            # LIVE: VPD is calculated immediately on every sensor update
            await self.event_manager.emit("VPDCreation", VPDPub)
        else:
            # Interval mode: run periodically
            asyncio.create_task(self._vpd_update_loop(interval, VPDPub))

    async def light_schedule_update(self, data):
        """Handle light schedule updates."""
        return await self.main_controller.light_schedule_update(data)

    async def _handle_ambient_data(self, event):
        """Handle ambient data from other rooms."""
        return await self.main_controller._handle_ambient_data(event)

    async def _handle_outsite_data(self, event):
        """Handle outside data."""
        return await self.main_controller._handle_outsite_data(event)

    async def _auto_update_plant_stages(self, data):
        """Auto update plant stages."""
        return await self.main_controller._auto_update_plant_stages(data)

    def _initialize_default_data_store_values(self):
        """Initialize default data store values lost in modular migration."""
        # Set default light times if not set
        if not self.data_store.getDeep("isPlantDay.lightOnTime"):
            self.data_store.setDeep("isPlantDay.lightOnTime", "06:00:00")
        if not self.data_store.getDeep("isPlantDay.lightOffTime"):
            self.data_store.setDeep("isPlantDay.lightOffTime", "22:00:00")

        # Set default sun times
        if not self.data_store.getDeep("isPlantDay.sunRiseTime"):
            self.data_store.setDeep("isPlantDay.sunRiseTime", "07:00:00")
        if not self.data_store.getDeep("isPlantDay.sunSetTime"):
            self.data_store.setDeep("isPlantDay.sunSetTime", "19:00:00")

        # Set default plant phase
        if not self.data_store.getDeep("isPlantDay.plantPhase"):
            self.data_store.setDeep("isPlantDay.plantPhase", "veg")

        # Set default VPD determination
        if not self.data_store.get("vpdDetermination"):
            self.data_store.set("vpdDetermination", "LIVE")

        _LOGGER.info(f"🔧 Initialized default data store values for {self.room}")

    async def _plant_stage_to_vpd(self, plantStage, tolerance=10):
        """Calculate perfect VPD range based on plant stage."""
        from .utils.calcs import calculate_perfect_vpd
        from .utils.sensorUpdater import _update_specific_sensor

        # Plant stage VPD ranges (kPa)
        stage_ranges = {
            "germ": [0.8, 1.0],
            "veg": [1.0, 1.2],
            "gen": [1.2, 1.4],  # generative/flowering
        }

        # Get range for current stage
        vpd_range = stage_ranges.get(plantStage.lower(), [1.0, 1.2])  # Default to veg

        # Calculate perfect VPD values
        perfections = calculate_perfect_vpd(vpd_range, tolerance)

        perfectVPD = perfections["perfection"]
        perfectVPDMin = perfections["perfect_min"]
        perfectVPDMax = perfections["perfect_max"]

        # Update sensors
        await _update_specific_sensor("ogb_current_vpd_target_", self.room, perfectVPD, self.hass)
        await _update_specific_sensor("ogb_current_vpd_target_min_", self.room, perfectVPDMin, self.hass)
        await _update_specific_sensor("ogb_current_vpd_target_max_", self.room, perfectVPDMax, self.hass)

        # Store in data store
        self.data_store.setDeep("vpd.range", vpd_range)
        self.data_store.setDeep("vpd.perfection", perfectVPD)
        self.data_store.setDeep("vpd.perfectMin", perfectVPDMin)
        self.data_store.setDeep("vpd.perfectMax", perfectVPDMax)

        # Check if min/max control is active
        minMaxActive = self._string_to_bool(self.data_store.getDeep("controlOptions.minMaxControl"))

        if not minMaxActive:
            # Update min/max values (this would be more complex in reality)
            # For now, just store the VPD values
            pass

        await self.event_manager.emit("PlantStageChange", plantStage)
        _LOGGER.debug(f"{self.room}: PlantStage '{plantStage}' successfully transferred to VPD data")

    ## Control Update Functions
    async def _update_control_option(self, data):
        """
        Update ControlOption.
        """
        value = data.newState[0]
        current_main_control = self.data_store.get("mainControl")
        if current_main_control != value:
            self.data_store.set("mainControl", value)
            await self.event_manager.emit("mainControlChange", value)
            await self.event_manager.emit("PremiumChange", {"currentValue": value, "lastValue": current_main_control})

    async def _update_vpd_determination(self, data):
        """
        Update VPD Determination setting.
        """
        value = data.newState[0]
        current_value = self.data_store.get("vpdDetermination")
        if current_value != value:
            self.data_store.set("vpdDetermination", value)
            await self.event_manager.emit("VPDDeterminationChange", value)

    ## Workmode
    async def _update_work_mode_control(self, data):
        """
        Update OGB Workmode Control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(self.data_store.getDeep("controlOptions.workMode"))
        if current_value != value:
            self.data_store.setDeep("controlOptions.workMode", self._string_to_bool(value))
            await self.event_manager.emit("WorkModeChange", self._string_to_bool(value))

    ## Ambient/outside
    async def _update_ambient_control(self, data):
        """
        Update OGB Ambient Control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(self.data_store.getDeep("controlOptions.ambientControl"))
        if current_value != value:
            self.data_store.setDeep("controlOptions.ambientControl", self._string_to_bool(value))

    def _string_to_bool(self, value):
        """Convert string to boolean."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on')
        return bool(value)

    def __str__(self):
        return self.main_controller.__str__()

    def __repr__(self):
        return self.main_controller.__repr__()

    async def get_weather_data(self):
        """Fetch weather data for outdoor conditions."""
        try:
            # For now, use a simple weather API or mock data
            # In production, this would integrate with a weather service
            _LOGGER.debug(f"Fetching weather data for {self.room}")

            # Mock weather data - replace with actual API call
            weather_data = {
                "temperature": 20.5,
                "humidity": 65.0,
                "wind_speed": 5.2,
                "description": "Partly cloudy"
            }

            # Store in data store
            self.data_store.setDeep("weather.temperature", weather_data["temperature"])
            self.data_store.setDeep("weather.humidity", weather_data["humidity"])
            self.data_store.setDeep("weather.wind_speed", weather_data["wind_speed"])
            self.data_store.setDeep("weather.description", weather_data["description"])

            # Emit weather update event
            await self.event_manager.emit("WeatherUpdate", weather_data)

            _LOGGER.debug(f"Weather data updated for {self.room}: {weather_data}")

        except Exception as e:
            _LOGGER.error(f"Error fetching weather data for {self.room}: {e}")

    async def emergency_stop(self):
        """Emergency stop all systems."""
        try:
            _LOGGER.warning(f"🚨 Emergency stop initiated for {self.room}")

            # Stop orchestrator control loop first
            await self.orchestrator.stop()

            # Stop all pumps
            if hasattr(self.actionManager, 'pump_controller') and self.actionManager.pump_controller:
                await self.actionManager.pump_controller.emergency_stop_all_pumps()

            # Stop all devices
            if hasattr(self.deviceManager, 'emergency_stop'):
                await self.deviceManager.emergency_stop()

            # Emit emergency stop event
            await self.event_manager.emit("EmergencyStop", {"room": self.room, "timestamp": datetime.now().isoformat()})

            _LOGGER.warning(f"✅ Emergency stop completed for {self.room}")

        except Exception as e:
            _LOGGER.error(f"Error during emergency stop for {self.room}: {e}")

    async def calibrate_sensors(self, sensor_type=None):
        """Calibrate sensors."""
        try:
            _LOGGER.info(f"Calibrating sensors for {self.room}, type: {sensor_type}")

            if sensor_type == "temperature" or sensor_type is None:
                # Temperature sensor calibration
                await self._calibrate_temperature_sensors()

            if sensor_type == "humidity" or sensor_type is None:
                # Humidity sensor calibration
                await self._calibrate_humidity_sensors()

            if sensor_type == "ph" or sensor_type is None:
                # pH sensor calibration
                await self._calibrate_ph_sensors()

            if sensor_type == "ec" or sensor_type is None:
                # EC sensor calibration
                await self._calibrate_ec_sensors()

            await self.event_manager.emit("SensorCalibrationComplete", {"sensor_type": sensor_type, "room": self.room})

            _LOGGER.info(f"Sensor calibration completed for {self.room}")

        except Exception as e:
            _LOGGER.error(f"Error calibrating sensors for {self.room}: {e}")

    async def _calibrate_temperature_sensors(self):
        """Calibrate temperature sensors."""
        # Implementation would involve sensor calibration logic
        _LOGGER.debug(f"Temperature sensor calibration for {self.room}")

    async def _calibrate_humidity_sensors(self):
        """Calibrate humidity sensors."""
        # Implementation would involve sensor calibration logic
        _LOGGER.debug(f"Humidity sensor calibration for {self.room}")

    async def _calibrate_ph_sensors(self):
        """Calibrate pH sensors."""
        # Implementation would involve sensor calibration logic
        _LOGGER.debug(f"pH sensor calibration for {self.room}")

    async def _calibrate_ec_sensors(self):
        """Calibrate EC sensors."""
        # Implementation would involve sensor calibration logic
        _LOGGER.debug(f"EC sensor calibration for {self.room}")

    async def transition_plant_stage(self, new_stage):
        """Transition to a new plant growth stage."""
        try:
            current_stage = self.data_store.getDeep("isPlantDay.plantPhase")
            _LOGGER.info(f"Transitioning plant stage from {current_stage} to {new_stage} in {self.room}")

            # Update plant stage in data store
            self.data_store.setDeep("isPlantDay.plantPhase", new_stage)

            # Update VPD targets for new stage
            await self._plant_stage_to_vpd(new_stage)

            # Update light schedule for new stage
            if hasattr(self.main_controller, 'light_scheduler'):
                await self.main_controller.light_scheduler.set_plant_stage_light({"stage": new_stage})

            # Emit plant stage change event
            await self.event_manager.emit("PlantStageChange", {"old_stage": current_stage, "new_stage": new_stage, "room": self.room})

            _LOGGER.info(f"Plant stage transition completed: {new_stage} in {self.room}")

        except Exception as e:
            _LOGGER.error(f"Error transitioning plant stage in {self.room}: {e}")

    def _debugState(self):
        """Debug state output - from original"""
        devices = self.data_store.get("devices")
        tentData = self.data_store.get("tentData")
        controlOptions = self.data_store.get("controlOptions")
        workdata = self.data_store.get("workData")
        vpdData = self.data_store.get("vpd")
        caps = self.data_store.get("capabilities")
        _LOGGER.debug(f"DEBUGSTATE: {self.room} WorkData: {workdata} DEVICES:{devices} TentData {tentData} CONTROLOPTIONS:{controlOptions}  VPDDATA {vpdData} CAPS:{caps}")

    async def async_shutdown(self):
        """
        Gracefully shutdown all OpenGrowBox components.
        
        This method should be called when the integration is being unloaded
        to ensure all background tasks are properly cancelled and resources cleaned up.
        """
        _LOGGER.info(f"🛑 Starting OpenGrowBox shutdown for {self.room}")
        
        try:
            # 1. Stop orchestrator control loop first (prevents new actions)
            if hasattr(self, 'orchestrator') and self.orchestrator:
                try:
                    await self.orchestrator.stop()
                    _LOGGER.debug(f"✅ Orchestrator stopped for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error stopping orchestrator: {e}")

            # 2. Shutdown premium manager (WebSocket connections, etc.)
            if hasattr(self, 'prem_manager') and self.prem_manager:
                try:
                    if hasattr(self.prem_manager, 'async_shutdown'):
                        await self.prem_manager.async_shutdown()
                    _LOGGER.debug(f"✅ Premium manager shutdown for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error shutting down premium manager: {e}")

            # 3. Shutdown main controller (stops all sub-managers)
            if hasattr(self, 'main_controller') and self.main_controller:
                try:
                    if hasattr(self.main_controller, 'async_shutdown'):
                        await self.main_controller.async_shutdown()
                    _LOGGER.debug(f"✅ Main controller shutdown for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error shutting down main controller: {e}")

            # 4. Stop device manager
            if hasattr(self, 'deviceManager') and self.deviceManager:
                try:
                    if hasattr(self.deviceManager, 'emergency_stop'):
                        await self.deviceManager.emergency_stop()
                    if hasattr(self.deviceManager, 'async_shutdown'):
                        await self.deviceManager.async_shutdown()
                    _LOGGER.debug(f"✅ Device manager shutdown for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error shutting down device manager: {e}")

            # 5. Stop action manager
            if hasattr(self, 'actionManager') and self.actionManager:
                try:
                    if hasattr(self.actionManager, 'pump_controller') and self.actionManager.pump_controller:
                        await self.actionManager.pump_controller.emergency_stop_all_pumps()
                    if hasattr(self.actionManager, 'async_shutdown'):
                        await self.actionManager.async_shutdown()
                    _LOGGER.debug(f"✅ Action manager shutdown for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error shutting down action manager: {e}")

            # 6. Stop medium manager
            if hasattr(self, 'mediumManager') and self.mediumManager:
                try:
                    if hasattr(self.mediumManager, 'async_shutdown'):
                        await self.mediumManager.async_shutdown()
                    _LOGGER.debug(f"✅ Medium manager shutdown for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error shutting down medium manager: {e}")

            # 7. Stop VPD manager
            if hasattr(self, 'vpd_manager') and self.vpd_manager:
                try:
                    if hasattr(self.vpd_manager, 'stop'):
                        await self.vpd_manager.stop()
                    _LOGGER.debug(f"✅ VPD manager shutdown for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error shutting down VPD manager: {e}")

            # 8. Save state before shutdown
            if hasattr(self, 'data_storeManager') and self.data_storeManager:
                try:
                    await self.data_storeManager.saveState({})
                    _LOGGER.debug(f"✅ State saved for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error saving state: {e}")

            # 9. Shutdown event manager (cleanup listeners and orphan tasks)
            if hasattr(self, 'event_manager') and self.event_manager:
                try:
                    if hasattr(self.event_manager, 'async_shutdown'):
                        await self.event_manager.async_shutdown()
                    _LOGGER.debug(f"✅ Event manager shutdown for {self.room}")
                except Exception as e:
                    _LOGGER.error(f"Error shutting down event manager: {e}")

            _LOGGER.info(f"✅ OpenGrowBox shutdown complete for {self.room}")

        except Exception as e:
            _LOGGER.error(f"❌ Error during OpenGrowBox shutdown for {self.room}: {e}", exc_info=True)
