import logging
from datetime import date, datetime

import voluptuous as vol
from homeassistant.components.date import DateEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

###############################################
# CustomDate – Speichert nur das Datum
###############################################


class CustomDate(DateEntity, RestoreEntity):
    """Custom date entity for storing only the date portion."""

    def __init__(self, name, room_name, coordinator, initial_date=None):
        """Initialize the date entity."""
        self._name = name
        self.room_name = room_name
        self.coordinator = coordinator
        self._unique_id = f"{DOMAIN}_{room_name}_{name.lower().replace(' ', '_')}"
        # Falls kein initial_date angegeben oder ein ungültiger Wert vorliegt, nutze heute
        if initial_date in (None, "", "unknown", "unavailable"):
            self._date = date.today()
        else:
            self._date = self._parse_date(initial_date)

    @staticmethod
    def _parse_date(date_input) -> date:
        if isinstance(date_input, str) and date_input.lower() in (
            "unknown",
            "unavailable",
            "",
        ):
            _LOGGER.debug(f"DateInput was '{date_input}', using default today.")
            return date.today()

        try:
            if isinstance(date_input, date):
                return date_input
            if isinstance(date_input, str):
                return datetime.strptime(date_input, "%Y-%m-%d").date()
            raise ValueError("Unsupported date input type")
        except Exception as e:
            _LOGGER.error(
                f"Invalid date input: {date_input}. Defaulting to today. Error: {e}"
            )
            return date.today()

    @property
    def unique_id(self):
        """Return the unique ID for this entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def native_value(self):
        """Return the date as a date object."""
        return self._date

    @property
    def device_info(self):
        """Device information to link this entity to a device."""
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": f"Device for {self._name}",
            "model": "Date Device",
            "manufacturer": "OpenGrowBox",
            "suggested_area": self.room_name,
        }

    async def async_set_value(self, value):
        """Set a new date value."""
        try:
            if isinstance(value, str) and value.lower() in (
                "unknown",
                "unavailable",
                "",
            ):
                new_date = date.today()
            else:
                new_date = self._parse_date(value)
            self._date = new_date
            self.async_write_ha_state()
            _LOGGER.info(f"Date '{self._name}' set to {new_date}")
        except ValueError as e:
            _LOGGER.error(f"Failed to set date for '{self._name}': {e}")

    async def async_added_to_hass(self):
        """Restore state when the entity is added to Home Assistant."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if (
            state
            and state.state
            and state.state.lower() not in ("unknown", "unavailable", "")
        ):
            try:
                restored_date = self._parse_date(state.state)
                self._date = restored_date
                _LOGGER.info(f"Restored date for '{self._name}': {restored_date}")
            except ValueError:
                _LOGGER.warning(
                    f"Failed to restore date for '{self._name}', using default."
                )


###############################################
# async_setup_entry – Registriert die Entitäten und den Service
###############################################


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up CustomDate entities and register update services."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    dates = [
        CustomDate(
            f"OGB_GrowStartDate_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_date="",
        ),
        CustomDate(
            f"OGB_BloomSwitchDate_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_date="",
        ),
    ]
    if "dates" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["dates"] = []
    hass.data[DOMAIN]["dates"].extend(dates)

    async_add_entities(dates)

    if not hass.services.has_service(DOMAIN, "update_date"):

        async def handle_update_date(call):
            entity_id = call.data.get("entity_id")
            new_date = call.data.get("date")
            _LOGGER.debug(f"Received update_date request for {entity_id} to {new_date}")
            for date_entity in hass.data[DOMAIN]["dates"]:
                if date_entity.entity_id == entity_id:
                    await date_entity.async_set_value(new_date)
                    _LOGGER.info(f"Updated date for {entity_id} to {new_date}")
                    return
            _LOGGER.error(f"Date entity with id {entity_id} not found")

        hass.services.async_register(
            DOMAIN,
            "update_date",
            handle_update_date,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): str,
                    vol.Required("date"): str,
                }
            ),
        )
