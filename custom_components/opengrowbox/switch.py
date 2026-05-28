import logging

import voluptuous as vol
from homeassistant.helpers.entity import EntityCategory, ToggleEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_AUTO_CONFIGURE_HA, DEFAULT_AUTO_CONFIGURE_HA, DOMAIN
from .naming import display_name_from_raw, legacy_entity_id, room_device_info

_LOGGER = logging.getLogger(__name__)


class CustomSwitch(ToggleEntity, RestoreEntity):
    """Custom switch for multiple hubs with state restoration."""

    _attr_has_entity_name = False

    def __init__(self, name, room_name, coordinator, initial_state=False):
        """Initialize the switch."""
        self._name = name
        self._attr_name = display_name_from_raw(name, room_name)
        self._state = initial_state  # Initial state
        self.room_name = room_name
        self.coordinator = coordinator
        self._unique_id = f"{DOMAIN}_{room_name}_{name.lower().replace(' ', '_')}"
        self.entity_id = legacy_entity_id("switch", name)

    @property
    def unique_id(self):
        """Return the unique ID for this entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._attr_name

    @property
    def is_on(self):
        """Return the current state of the switch."""
        return self._state

    @property
    def device_info(self):
        """Return device information to link this entity to a device."""
        return room_device_info(self.room_name, "Switch Device")

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        self._state = True
        self.async_write_ha_state()
        _LOGGER.debug(f"Switch '{self._name}' turned ON.")

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        self._state = False
        self.async_write_ha_state()
        _LOGGER.debug(f"Switch '{self._name}' turned OFF.")

    async def async_toggle(self, **kwargs):
        """Toggle the state of the switch."""
        self._state = not self._state
        self.async_write_ha_state()
        _LOGGER.debug(
            f"Switch '{self._name}' toggled to: {'ON' if self._state else 'OFF'}."
        )

    async def async_added_to_hass(self):
        """Restore state when the entity is added to Home Assistant."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state is not None:
            self._state = state.state == "on"
            _LOGGER.debug(
                f"Restored state for '{self._name}': {'ON' if self._state else 'OFF'}."
            )


class ConfigEntryOptionSwitch(ToggleEntity):
    """Switch backed by a Home Assistant config-entry option."""

    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:file-cog"

    def __init__(self, name, room_name, coordinator, option_key, default=False):
        """Initialize the config-entry option switch."""
        self._name = name
        self._attr_name = display_name_from_raw(name, room_name)
        self.room_name = room_name
        self.coordinator = coordinator
        self.config_entry = coordinator.config_entry
        self.option_key = option_key
        self.default = default
        self._unique_id = f"{DOMAIN}_{self.config_entry.entry_id}_{option_key}"
        self.entity_id = legacy_entity_id("switch", name)

    @property
    def unique_id(self):
        """Return the unique ID for this entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._attr_name

    @property
    def is_on(self):
        """Return the option state."""
        return bool(
            self.config_entry.options.get(
                self.option_key,
                self.config_entry.data.get(self.option_key, self.default),
            )
        )

    @property
    def device_info(self):
        """Return device information to link this entity to a device."""
        return room_device_info(self.room_name, "Configuration")

    async def async_turn_on(self, **kwargs):
        """Enable the config-entry option."""
        await self._async_set_option(True)

    async def async_turn_off(self, **kwargs):
        """Disable the config-entry option."""
        await self._async_set_option(False)

    async def async_toggle(self, **kwargs):
        """Toggle the config-entry option."""
        await self._async_set_option(not self.is_on)

    async def _async_set_option(self, enabled):
        """Persist the option on the config entry."""
        options = dict(self.config_entry.options)
        options[self.option_key] = bool(enabled)
        self.coordinator.hass.config_entries.async_update_entry(
            self.config_entry,
            options=options,
        )
        self.async_write_ha_state()

        if self.option_key == CONF_AUTO_CONFIGURE_HA:
            from . import _check_required_ha_config

            await _check_required_ha_config(
                self.coordinator.hass,
                auto_update=bool(enabled),
            )


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up switch entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Create switches with placeholders for customization
    switches = [
        ConfigEntryOptionSwitch(
            f"OGB_AutoConfigureHA_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            CONF_AUTO_CONFIGURE_HA,
            DEFAULT_AUTO_CONFIGURE_HA,
        ),
        # TemplateSwitch
        CustomSwitch(
            f"OGB_TemplateSwitch_{coordinator.room_name}",
            coordinator.room_name,
            coordinator,
            initial_state=False,
        ),
    ]

    # Register the switches globally in hass.data
    if "switches" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["switches"] = []

    hass.data[DOMAIN]["switches"].extend(switches)

    # Add entities to Home Assistant
    async_add_entities(switches)

    # Register a global service for toggling switch states if not already registered
    if not hass.services.has_service(DOMAIN, "toggle_switch"):

        async def handle_toggle_switch(call):
            """Handle the toggle switch service."""
            entity_id = call.data.get("entity_id")

            _LOGGER.debug(f"Received request to toggle switch '{entity_id}'")

            for switch in hass.data[DOMAIN]["switches"]:
                if switch.entity_id == entity_id:
                    await switch.async_toggle()
                    _LOGGER.debug(
                        f"Toggled switch '{switch.name}' to state: {'ON' if switch.is_on else 'OFF'}"
                    )
                    return

            _LOGGER.warning(f"Switch with entity_id '{entity_id}' not found.")

        hass.services.async_register(
            DOMAIN,
            "toggle_switch",
            handle_toggle_switch,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): str,
                }
            ),
        )
