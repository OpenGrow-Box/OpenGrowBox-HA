import logging

import voluptuous as vol
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OGBIntegrationCoordinator
from .premium_entities import should_register_premium_entity

_LOGGER = logging.getLogger(__name__)


class CustomSensor(Entity):
    """Custom sensor for multiple hubs with update capability and graph support."""

    def __init__(
        self, name, room_name, coordinator, initial_value=None, device_class=None
    ):
        """Initialize the sensor."""

        self._name = name
        self._state = initial_value  # Initial value
        self.room_name = room_name
        self.coordinator = coordinator  # Store coordinator reference for premium features
        self._device_class = device_class  # e.g., temperature, humidity, light
        self._unique_id = f"{DOMAIN}_{room_name}_{name.lower().replace(' ', '_')}"
        self._attr_unique_id = self._unique_id

    @property
    def unique_id(self):
        """Return the unique ID for this entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def state(self):
        """Return the current state of the entity."""
        return self._state

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return self._device_class

    @property
    def state_class(self):
        """Return the state class of the sensor."""
        return "measurement"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information to link this entity to a device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._unique_id)},
            name=f"Device for {self._name}",
            model="Sensor Device",
            manufacturer="OpenGrowBox",
            suggested_area=self.room_name,
        )

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement for this sensor."""
        if self._device_class == "temperature":
            return "°C"
        elif self._device_class == "humidity":
            return "%"
        elif self._device_class == "vpd":
            return "kPa"
        elif self._device_class == "ppfd":
            return "μmol/m²/s"
        elif self._device_class == "dli":
            return "mol/m²/day"
        elif self._device_class == "days":
            return "Days"
        elif self._device_class == "minutes":
            return "Minutes"
        return None

    @property
    def extra_state_attributes(self):
        """Return extra attributes for the entity."""
        return {"room_name": self.room_name}

    def update_state(self, new_state):
        """Update the state and notify Home Assistant."""
        old_state = self._state
        self._state = new_state
        _LOGGER.debug(f"🔄 SENSOR UPDATE: {self._name} changed from {old_state} to {new_state}")
        self.async_write_ha_state()


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up sensor entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Check room limits before setting up sensors
    if hasattr(coordinator.OGB, "feature_manager"):
        feature_manager = coordinator.OGB.feature_manager

        # Get current room count for user (simplified for HA - we'll count existing entries)
        existing_rooms = len(
            [entry for entry in hass.data[DOMAIN] if entry != config_entry.entry_id]
        )

        # Check if this room creation is allowed
        room_check = feature_manager.can_create_room(
            existing_rooms, coordinator.user_plan
        )

        if not room_check["allowed"]:
            _LOGGER.warning(f"Room limit exceeded: {room_check['message']}")
            _LOGGER.info(
                f"Upgrade to {room_check.get('required_tier', 'higher tier')} for more rooms"
            )
            # Don't prevent setup, but log the limitation
            # In a full implementation, you might want to fire an event or show UI notification

    # Create all sensors in a single array
    sensors = [
        # VPD Sensors
        CustomSensor(
            f"OGB_CurrentVPD_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="vpd",
        ),
        CustomSensor(
            f"OGB_AVGTemperature_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="temperature",
        ),
        CustomSensor(
            f"OGB_AVGDewpoint_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="temperature",
        ),
        CustomSensor(
            f"OGB_AVGHumidity_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="humidity",
        ),
        CustomSensor(
            f"OGB_Current_VPD_Target_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="vpd",
        ),
        CustomSensor(
            f"OGB_Current_VPD_Target_Min_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="vpd",
        ),
        CustomSensor(
            f"OGB_Current_VPD_Target_Max_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="vpd",
        ),
        # Ambient Sensors
        CustomSensor(
            f"OGB_AmbientTemperature_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="temperature",
        ),
        CustomSensor(
            f"OGB_AmbientDewpoint_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="temperature",
        ),
        CustomSensor(
            f"OGB_AmbientHumidity_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="humidity",
        ),
        # Outside Sensors
        CustomSensor(
            f"OGB_OutsiteTemperature_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="temperature",
        ),
        CustomSensor(
            f"OGB_OutsiteDewpoint_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="temperature",
        ),
        CustomSensor(
            f"OGB_OutsiteHumidity_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="humidity",
        ),
        # Light Sensors
        CustomSensor(
            f"OGB_PPFD_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="ppfd",
        ),
        CustomSensor(
            f"OGB_DLI_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="dli",
        ),
        # PlantTimeSensors
        CustomSensor(
            f"OGB_PlantTotalDays_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0,
            device_class="days",
        ),
        CustomSensor(
            f"OGB_TotalBloomDays_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0,
            device_class="days",
        ),
        CustomSensor(
            f"OGB_ChopChopTime_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0,
            device_class="days",
        ),
        CustomSensor(
            f"OGB_PlantFoodNextFeed_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0,
            device_class="Minutes",
        ),
        # Hydro ORP
        CustomSensor(
            f"OGB_WaterORP_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0,
            device_class="mV",
        ),
    ]

    # Add premium sensors (conditionally based on feature availability)
    room_name = coordinator.room_name

    # Analytics sensors (Basic plan+)
    if should_register_premium_entity(coordinator, room_name, "advanced_analytics"):
        from .premium_sensors import (YieldPredictionSensor, AnomalyScoreSensor,
                                     PerformanceScoreSensor)
        sensors.extend(
            [
                YieldPredictionSensor(room_name, coordinator),
                AnomalyScoreSensor(room_name, coordinator),
                PerformanceScoreSensor(room_name, coordinator),
            ]
        )
        _LOGGER.info(f"✅ {room_name} Registered 3 analytics sensors")

    # Compliance sensors (Professional plan+)
    if should_register_premium_entity(coordinator, room_name, "compliance"):
        from .premium_sensors import (ComplianceStatusSensor, ViolationsCountSensor)
        sensors.extend(
            [
                ComplianceStatusSensor(room_name, coordinator),
                ViolationsCountSensor(room_name, coordinator),
            ]
        )
        _LOGGER.info(f"✅ {room_name} Registered 2 compliance sensors")

    # Research sensors (Professional plan+)
    if should_register_premium_entity(coordinator, room_name, "research_data"):
        from .premium_sensors import (DatasetCountSensor, DataQualitySensor)
        sensors.extend(
            [
                DatasetCountSensor(room_name, coordinator),
                DataQualitySensor(room_name, coordinator),
            ]
        )
        _LOGGER.info(f"✅ {room_name} Registered 2 research sensors")

    # Register premium sensors with OGBPremManager for WebSocket-driven state updates
    prem_manager = getattr(coordinator.OGB, room_name, None)
    if prem_manager:
        # Calculate how many premium sensors were added (count backwards from end)
        analytics_registered = should_register_premium_entity(
            coordinator, room_name, "advanced_analytics"
        )
        compliance_registered = should_register_premium_entity(
            coordinator, room_name, "compliance"
        )
        research_registered = should_register_premium_entity(
            coordinator, room_name, "research_data"
        )

        # Total premium sensors added
        total_premium = 0
        if analytics_registered:
            total_premium += 3
        if compliance_registered:
            total_premium += 2
        if research_registered:
            total_premium += 2

        # Register sensors by counting backwards from total
        current_index = total_premium

        # Register analytics sensors (first 3 premium sensors added)
        if analytics_registered:
            prem_manager.register_premium_sensor(
                "yield_prediction", sensors[-current_index]
            )
            prem_manager.register_premium_sensor(
                "anomaly_score", sensors[-(current_index - 1)]
            )
            prem_manager.register_premium_sensor(
                "performance_score", sensors[-(current_index - 2)]
            )
            current_index -= 3
            _LOGGER.debug(
                f"✅ {room_name} Linked 3 analytics sensors to WebSocket updates"
            )

        # Register compliance sensors (next 2 premium sensors added)
        if compliance_registered:
            prem_manager.register_premium_sensor(
                "compliance_status", sensors[-current_index]
            )
            prem_manager.register_premium_sensor(
                "violations_count", sensors[-(current_index - 1)]
            )
            current_index -= 2
            _LOGGER.debug(
                f"✅ {room_name} Linked 2 compliance sensors to WebSocket updates"
            )

        # Register research sensors (last 2 premium sensors added)
        if research_registered:
            prem_manager.register_premium_sensor(
                "dataset_count", sensors[-current_index]
            )
            prem_manager.register_premium_sensor(
                "data_quality", sensors[-(current_index - 1)]
            )
            _LOGGER.debug(
                f"✅ {room_name} Linked 2 research sensors to WebSocket updates"
            )
    else:
        _LOGGER.warning(
            f"⚠️ {room_name} OGBPremManager not found, premium sensors will not receive WebSocket updates"
        )

    # Register the sensors globally in hass.data
    if "sensors" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["sensors"] = []

    hass.data[DOMAIN]["sensors"].extend(sensors)

    # Add entities to Home Assistant
    async_add_entities(sensors)

    if not hass.services.has_service(DOMAIN, "update_sensor"):

        async def handle_update_sensor(call):
            """Handle the update sensor service."""
            entity_id_requested = call.data.get("entity_id")
            value = call.data.get("value")

            _LOGGER.info(f"🔍 SERVICE CALL: update_sensor for '{entity_id_requested}' = {value}")

            # Find and update the corresponding sensor
            sensor_found = False
            for sensor in hass.data[DOMAIN]["sensors"]:
                # Match by entity_id (if available) OR by name-based entity_id
                sensor_entity_id = getattr(sensor, 'entity_id', None)
                expected_entity_id = f"sensor.{sensor._name.lower().replace(' ', '_')}"
                
                if sensor_entity_id == entity_id_requested or expected_entity_id == entity_id_requested:
                    sensor.update_state(value)
                    _LOGGER.info(f"✅ Updated sensor '{sensor._name}' (matched: {entity_id_requested}) to value: {value}")
                    sensor_found = True
                    return
            
            if not sensor_found:
                _LOGGER.error(f"❌ Sensor '{entity_id_requested}' NOT FOUND in registered sensors")
                # Log first 5 sensors with their entity_ids for debugging
                debug_info = []
                for s in hass.data[DOMAIN]["sensors"][:5]:
                    s_entity_id = getattr(s, 'entity_id', None)
                    s_expected = f"sensor.{s._name.lower().replace(' ', '_')}"
                    debug_info.append(f"{s._name}: entity_id={s_entity_id}, expected={s_expected}")
                _LOGGER.error(f"🔍 Available sensors (first 5): {debug_info}")

        hass.services.async_register(
            DOMAIN,
            "update_sensor",
            handle_update_sensor,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): str,
                    vol.Required("value"): vol.Any(float, int, str),
                }
            ),
        )
        _LOGGER.info(f"✅ Registered {DOMAIN}.update_sensor service with {len(hass.data[DOMAIN]['sensors'])} sensors")
        
        for idx, s in enumerate(hass.data[DOMAIN]['sensors'][:15]):
            s_entity_id = getattr(s, 'entity_id', 'NOT_SET_YET')
            s_expected = f"sensor.{s._name.lower().replace(' ', '_')}"
            _LOGGER.debug(f"  [{idx}] Name: {s._name}, entity_id: {s_entity_id}, expected: {s_expected}")
    else:
        _LOGGER.debug(f"⚠️ Service {DOMAIN}.update_sensor already registered")
    
    # Register medium plant tracking services
    if not hass.services.has_service(DOMAIN, "request_medium_plants_data"):
        async def handle_request_medium_plants_data(call):
            """Handle request for medium plants data - triggers backend to emit MediumPlantsUpdate event."""
            room = call.data.get("room")
            
            _LOGGER.warning(f"🔍 SERVICE CALL: request_medium_plants_data for room '{room}'")
            
            # Get coordinator for this room - check room_name attribute
            coordinator = None
            for entry_id, coord in hass.data[DOMAIN].items():
                if entry_id != "sensors" and hasattr(coord, 'room_name'):
                    if coord.room_name == room:
                        coordinator = coord
                        _LOGGER.warning(f"✅ Found coordinator for room: {room}")
                        break
            
            if not coordinator:
                _LOGGER.error(f"❌ No coordinator found for room: {room}")
                _LOGGER.error(f"❌ Available entries: {[k for k in hass.data[DOMAIN].keys()]}")
                return
            
            # Emit RequestMediumPlantsData event - backend will respond with MediumPlantsUpdate
            try:
                await coordinator.OGB.eventManager.emit("RequestMediumPlantsData", {"room": room}, haEvent=True)
                _LOGGER.warning(f"✅ Emitted RequestMediumPlantsData event for room: {room}")
            except Exception as e:
                _LOGGER.error(f"❌ Failed to emit RequestMediumPlantsData event: {e}", exc_info=True)
        
        hass.services.async_register(
            DOMAIN,
            "request_medium_plants_data",
            handle_request_medium_plants_data,
            schema=vol.Schema({
                vol.Required("room"): str,
            }),
        )
        _LOGGER.info(f"✅ Registered {DOMAIN}.request_medium_plants_data service")
    
    if not hass.services.has_service(DOMAIN, "update_medium_plant_dates"):
        async def handle_update_medium_plant_dates(call):
            """Handle update medium plant dates - updates plant info for a specific medium."""
            room = call.data.get("room")
            medium_index = call.data.get("medium_index")
            
            _LOGGER.warning(f"🔍 SERVICE CALL: update_medium_plant_dates for room '{room}', medium {medium_index}")
            _LOGGER.warning(f"🔍 Full call data: {dict(call.data)}")
            
            # Get coordinator for this room - check room_name attribute
            coordinator = None
            for entry_id, coord in hass.data[DOMAIN].items():
                if entry_id != "sensors" and hasattr(coord, 'room_name'):
                    if coord.room_name == room:
                        coordinator = coord
                        _LOGGER.warning(f"✅ Found coordinator for room: {room}")
                        break
            
            if not coordinator:
                _LOGGER.error(f"❌ No coordinator found for room: {room}")
                _LOGGER.error(f"❌ Available entries: {[k for k in hass.data[DOMAIN].keys()]}")
                return
            
            # Emit UpdateMediumPlantDates event
            try:
                _LOGGER.warning(f"📤 Emitting UpdateMediumPlantDates event with data: {dict(call.data)}")
                await coordinator.OGB.eventManager.emit("UpdateMediumPlantDates", dict(call.data), haEvent=True)
                _LOGGER.warning(f"✅ Emitted UpdateMediumPlantDates event for room: {room}, medium: {medium_index}")
            except Exception as e:
                _LOGGER.error(f"❌ Failed to emit UpdateMediumPlantDates event: {e}", exc_info=True)
        
        hass.services.async_register(
            DOMAIN,
            "update_medium_plant_dates",
            handle_update_medium_plant_dates,
            schema=vol.Schema({
                vol.Required("room"): str,
                vol.Required("medium_index"): int,
                vol.Optional("grow_start"): str,
                vol.Optional("bloom_switch"): str,
                vol.Optional("breeder_bloom_days"): int,
                vol.Optional("plant_stage"): str,
                vol.Optional("plant_name"): str,
                vol.Optional("plant_strain"): str,
                vol.Optional("plant_type"): str,
                # Additional fields from frontend
                vol.Optional("breeder_name"): str,
                vol.Optional("medium_name"): str,
                vol.Optional("display_name"): str,
            }, extra=vol.ALLOW_EXTRA),
        )
        _LOGGER.info(f"✅ Registered {DOMAIN}.update_medium_plant_dates service")
