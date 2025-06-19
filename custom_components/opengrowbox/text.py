from homeassistant.components.text import TextEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.core import HomeAssistant
from .const import DOMAIN
import logging
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

class OpenGrowBoxAccessToken(TextEntity, RestoreEntity):
    """Custom text entity for OpenGrowBox with state restoration."""

    def __init__(self, name, room_name, coordinator, initial_value=""):
        self._name = name
        self.room_name = room_name
        self.coordinator = coordinator
        self._unique_id = f"{DOMAIN}_access_token"
        self._value = initial_value

        self._attr_min = 0
        self._attr_max = 254
        self._attr_mode = "text"

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def name(self):
        return self._name

    @property
    def native_value(self) -> str:
        return self._value

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": f"Device for {self._name}",
            "model": "Token Device",
            "manufacturer": "OpenGrowBox",
            "suggested_area": self.room_name,
        }

    async def async_set_value(self, value: str) -> None:
        if len(value) > 254:
            _LOGGER.warning(f"Token input too long: {value}")
            return
        self._value = value
        self.async_write_ha_state()
        _LOGGER.info(f"Token '{self._name}' set to {value}")

    async def async_added_to_hass(self):
        """Restore previous value."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "", "unknown", "unavailable"):
            self._value = last_state.state
            _LOGGER.info(f"Restored access token: {self._value}")
        else:
            _LOGGER.info(f"No state to restore for {self.name}")
        self.async_write_ha_state()


class CustomText(TextEntity, RestoreEntity):
    """Custom text entity for OpenGrowBox with state restoration."""

    def __init__(self, name, room_name, coordinator, initial_value=""):
        self._name = name
        self.room_name = room_name
        self.coordinator = coordinator
        self._unique_id = f"{DOMAIN}_{room_name}_{name.lower().replace(' ', '_')}"
        self._value = initial_value

        self._attr_min = 0
        self._attr_max = 254
        self._attr_mode = "text"

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def name(self):
        return self._name

    @property
    def native_value(self) -> str:
        return self._value

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": f"Device for {self._name}",
            "model": "Text Device",
            "manufacturer": "OpenGrowBox",
            "suggested_area": self.room_name,
        }

    async def async_set_value(self, value: str) -> None:
        if len(value) > 254:
            _LOGGER.warning(f"Text input too long: {value}")
            return
        self._value = value
        self.async_write_ha_state()
        _LOGGER.info(f"Text '{self._name}' set to {value}")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state not in (None, "", "unknown", "unavailable"):
            self._value = state.state
            _LOGGER.info(f"Restored text for '{self._name}': {self._value}")
        else:
            _LOGGER.info(f"No state to restore for '{self._name}'")
        self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities):
    """Set up text entities for OpenGrowBox."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    if "texts" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["texts"] = []


    if "access_token_entity" not in hass.data[DOMAIN]:
        access_token_entity = OpenGrowBoxAccessToken(
            name="OGB_AccessToken",
            room_name="Ambient",
            coordinator=coordinator,
            initial_value="AccessToken"
        )
        async_add_entities([access_token_entity])
        hass.data[DOMAIN]["texts"].append(access_token_entity)
        hass.data[DOMAIN]["access_token_entity"] = access_token_entity
        _LOGGER.info("AccessToken entity registered")
    else:
        _LOGGER.debug("AccessToken entity already registered")

    # Räume-spezifische Texte hinzufügen
    texts = [
        CustomText(f"OGB_Notes_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=""),
    ]

    hass.data[DOMAIN]["texts"].extend(texts)
    async_add_entities(texts)


    if not hass.services.has_service(DOMAIN, "update_text"):
        async def handle_update_text(call):
            entity_id = call.data.get("entity_id")
            new_value = call.data.get("text")
            for text_entity in hass.data[DOMAIN]["texts"]:
                if text_entity.entity_id == entity_id:
                    await text_entity.async_set_value(new_value)
                    return
            _LOGGER.warning(f"Text entity {entity_id} not found")

        hass.services.async_register(
            DOMAIN,
            "update_text",
            handle_update_text,
            schema=vol.Schema({
                vol.Required("entity_id"): str,
                vol.Required("text"): str,
            }),
        )
