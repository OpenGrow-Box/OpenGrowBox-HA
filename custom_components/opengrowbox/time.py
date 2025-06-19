from datetime import time
from homeassistant.components.time import TimeEntity
from homeassistant.helpers.restore_state import RestoreEntity
import logging
import voluptuous as vol
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class CustomTime(TimeEntity, RestoreEntity):
    """Custom time entity for multiple hubs with state restoration."""

    def __init__(self, name, room_name, coordinator, initial_time="00:00"):
        """Initialize the time entity."""
        self._name = name
        self.room_name = room_name
        self.coordinator = coordinator
        self._unique_id = f"{DOMAIN}_{room_name}_{name.lower().replace(' ', '_')}"
        self._time = self._parse_time(initial_time)

    @staticmethod
    def _parse_time(time_input) -> time:
        """Parse time from a string or return valid time object."""
        try:
            if isinstance(time_input, time):
                return time_input
            if isinstance(time_input, str):
                parts = list(map(int, time_input.split(":")))
                if len(parts) == 2:  # HH:MM
                    hours, minutes = parts
                    seconds = 0
                elif len(parts) == 3:  # HH:MM:SS
                    hours, minutes, seconds = parts
                else:
                    raise ValueError("Invalid time format")

                if 0 <= hours < 24 and 0 <= minutes < 60 and 0 <= seconds < 60:
                    return time(hour=hours, minute=minutes, second=seconds)
                else:
                    raise ValueError("Time values out of range")
            else:
                raise ValueError("Unsupported time input type")
        except (ValueError, AttributeError) as e:
            _LOGGER.error(f"Invalid time input: {time_input}. Defaulting to 00:00. Error: {e}")
            return time(0, 0)

    @property
    def unique_id(self):
        """Return the unique ID for this entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def native_value(self) -> time:
        """Return the current time value."""
        return self._time

    @property
    def device_info(self):
        """Device information to link this entity to a device."""
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": f"Device for {self._name}",
            "model": "Time Device",
            "manufacturer": "OpenGrowBox",
            "suggested_area": self.room_name,
        }

    async def async_set_value(self, value):
        """Set a new time value."""
        try:
            new_time = self._parse_time(value)
            self._time = new_time
            self.async_write_ha_state()
            _LOGGER.info(f"Time '{self._name}' set to {new_time}")
        except ValueError as e:
            _LOGGER.error(f"Failed to set time for '{self._name}': {e}")

    async def async_added_to_hass(self):
        """Restore state when the entity is added to Home Assistant."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state:
            try:
                restored_time = self._parse_time(state.state)
                self._time = restored_time
                _LOGGER.info(f"Restored time for '{self._name}': {restored_time}")
            except ValueError:
                _LOGGER.warning(f"Failed to restore time for '{self._name}', using default.")

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up time entities and register update service."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]


    times = [
        CustomTime(f"OGB_LightOnTime_{coordinator.room_name}", coordinator.room_name, coordinator, initial_time="08:00:00"),
        CustomTime(f"OGB_LightOffTime_{coordinator.room_name}", coordinator.room_name, coordinator, initial_time="20:00:00"),
        CustomTime(f"OGB_SunRiseTime_{coordinator.room_name}", coordinator.room_name, coordinator, initial_time="00:00:00"),
        CustomTime(f"OGB_SunSetTime_{coordinator.room_name}", coordinator.room_name, coordinator, initial_time="00:00:00"),
    ]

    if "times" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["times"] = []

    hass.data[DOMAIN]["times"].extend(times)
    async_add_entities(times)


    if not hass.services.has_service(DOMAIN, "update_time"):
        async def handle_update_time(call):
            """Handle the update_time service call."""
            entity_id = call.data.get("entity_id")
            new_time = call.data.get("time")
            _LOGGER.info(f"Received update_time request for {entity_id} to {new_time}")

            for time_entity in hass.data[DOMAIN]["times"]:
                if time_entity.entity_id == entity_id:
                    await time_entity.async_set_value(new_time)
                    _LOGGER.info(f"Updated time for {entity_id} to {new_time}")
                    return
            _LOGGER.warning(f"Time entity with id {entity_id} not found")

        hass.services.async_register(
            DOMAIN,
            "update_time",
            handle_update_time,
            schema=vol.Schema({
                vol.Required("entity_id"): str,
                vol.Required("time"): str,
            }),
        )
