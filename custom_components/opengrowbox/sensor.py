import logging

import voluptuous as vol
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OGBIntegrationCoordinator

_LOGGER = logging.getLogger(__name__)


def _handle_missing_coordinator(room: str, available_entries: list) -> bool:
    """
    Handle missing coordinator gracefully.

    Returns True if the room is already removed/unavailable (no error should be logged),
    False if it's a genuine missing coordinator error.
    """
    if room.lower() in ("unavailable", "") or room is None:
        # Room is already removed or not available - clean exit without error
        _LOGGER.debug(f"⚠️ Skipping service for room: {room} (room already removed)")
        return True
    else:
        # Genuine error - log it
        _LOGGER.error(f"❌ No coordinator found for room: {room}")
        _LOGGER.error(f"❌ Available entries: {available_entries}")
        return False


class CustomSensor(RestoreEntity):
    """Custom sensor for multiple hubs with update capability and graph support."""

    def __init__(
        self, name, room_name, coordinator, initial_value=None, device_class=None, should_restore=True
    ):
        """Initialize the sensor."""

        self._name = name
        self._state = initial_value  # Initial value
        self.room_name = room_name
        self.coordinator = coordinator  # Store coordinator reference for premium features
        self._device_class = device_class  # e.g., temperature, humidity, light
        self._unique_id = f"{DOMAIN}_{room_name}_{name.lower().replace(' ', '_')}"
        self._attr_unique_id = self._unique_id
        self._should_restore = should_restore  # Control state restoration

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

    async def async_added_to_hass(self):
        """Restore last known state on startup."""
        await super().async_added_to_hass()

        if not self._should_restore:
            return

        last_state = await self.async_get_last_state()

        if last_state and last_state.state is not None:
            try:
                # Attempt to restore the previous state
                restored_value = last_state.state

                # For numeric sensors, validate and convert
                if self._device_class in ["temperature", "humidity", "vpd", "ppfd", "dli"]:
                    try:
                        # Try to convert to float for numeric sensors
                        restored_value = float(restored_value)
                    except (ValueError, TypeError):
                        _LOGGER.warning(
                            f"Could not convert restored value to float for '{self._name}': {restored_value}"
                        )
                        restored_value = self._state  # Keep initial value

                # Only update if current state is None (initial state)
                if self._state is None:
                    self._state = restored_value
                    _LOGGER.info(
                        f"✅ RESTORED: '{self._name}' state restored to: {restored_value}"
                    )
                else:
                    _LOGGER.debug(
                        f"ℹ️ SKIP: '{self._name}' has initial value {self._state}, not restoring"
                    )
            except Exception as e:
                _LOGGER.error(
                    f"❌ ERROR: Failed to restore state for '{self._name}': {e}"
                )
        else:
            _LOGGER.debug(
                f"ℹ️ FIRST_RUN: No previous state found for '{self._name}' (using initial value: {self._state})"
            )


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
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        CustomSensor(
            f"OGB_AVGTemperature_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="temperature",
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        CustomSensor(
            f"OGB_AVGDewpoint_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="temperature",
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        CustomSensor(
            f"OGB_AVGHumidity_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=None,
            device_class="humidity",
            should_restore=False,  # Live measurement sensor - don't restore
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
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        CustomSensor(
            f"OGB_AmbientDewpoint_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="temperature",
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        CustomSensor(
            f"OGB_AmbientHumidity_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="humidity",
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        # Outside Sensors
        CustomSensor(
            f"OGB_OutsiteTemperature_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="temperature",
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        CustomSensor(
            f"OGB_OutsiteDewpoint_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="temperature",
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        CustomSensor(
            f"OGB_OutsiteHumidity_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="humidity",
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        # Light Sensors
        CustomSensor(
            f"OGB_PPFD_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="ppfd",
            should_restore=False,  # Live measurement sensor - don't restore
        ),
        CustomSensor(
            f"OGB_DLI_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_value=0.0,
            device_class="dli",
            should_restore=False,  # Live measurement sensor - don't restore
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
            
            _LOGGER.debug(f"🔍 SERVICE CALL: request_medium_plants_data for room '{room}'")
            
            # Get coordinator for this room - check room_name attribute
            coordinator = None
            for entry_id, coord in hass.data[DOMAIN].items():
                if entry_id != "sensors" and hasattr(coord, 'room_name'):
                    if coord.room_name == room:
                        coordinator = coord
                        _LOGGER.warning(f"✅ Found coordinator for room: {room}")
                        break

            if not coordinator:
                if _handle_missing_coordinator(room, [k for k in hass.data[DOMAIN].keys()]):
                    return
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
                if _handle_missing_coordinator(room, [k for k in hass.data[DOMAIN].keys()]):
                    return
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
    
    # Register finish_grow service
    if not hass.services.has_service(DOMAIN, "finish_grow"):
        async def handle_finish_grow(call):
            """Handle finish grow service - completes grow cycle for a medium."""
            room = call.data.get("room")
            medium_index = call.data.get("medium_index")
            
            _LOGGER.warning(f"🏁 SERVICE CALL: finish_grow for room '{room}', medium {medium_index}")
            _LOGGER.warning(f"🏁 Full call data: {dict(call.data)}")
            
            # Get coordinator for this room
            coordinator = None
            for entry_id, coord in hass.data[DOMAIN].items():
                if entry_id != "sensors" and hasattr(coord, 'room_name'):
                    if coord.room_name == room:
                        coordinator = coord
                        _LOGGER.warning(f"✅ Found coordinator for room: {room}")
                        break

            if not coordinator:
                if _handle_missing_coordinator(room, [k for k in hass.data[DOMAIN].keys()]):
                    return
                return

            # Emit FinishGrow event - MediumManager will handle the logic
            try:
                _LOGGER.warning(f"📤 Emitting FinishGrow event with data: {dict(call.data)}")
                await coordinator.OGB.eventManager.emit("FinishGrow", dict(call.data), haEvent=True)
                _LOGGER.warning(f"✅ Emitted FinishGrow event for room: {room}, medium: {medium_index}")
            except Exception as e:
                _LOGGER.error(f"❌ Failed to emit FinishGrow event: {e}", exc_info=True)
        
        hass.services.async_register(
            DOMAIN,
            "finish_grow",
            handle_finish_grow,
            schema=vol.Schema({
                vol.Required("room"): str,
                vol.Required("medium_index"): int,
                vol.Optional("medium_name"): str,
                vol.Optional("plant_name"): str,
                vol.Optional("breeder_name"): str,
                vol.Optional("total_days"): vol.Any(int, float),
                vol.Optional("bloom_days"): vol.Any(int, float),
                vol.Optional("notes"): str,
            }, extra=vol.ALLOW_EXTRA),
        )
        _LOGGER.info(f"✅ Registered {DOMAIN}.finish_grow service")
