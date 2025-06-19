from homeassistant.helpers.entity import ToggleEntity
from homeassistant.helpers.restore_state import RestoreEntity
import logging
from .const import DOMAIN
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

class CustomSwitch(ToggleEntity, RestoreEntity):
    """Custom switch for multiple hubs with state restoration."""

    def __init__(self, name, room_name, coordinator, initial_state=False):
        """Initialize the switch."""
        self._name = name
        self._state = initial_state  # Initial state
        self.room_name = room_name
        self.coordinator = coordinator
        self._unique_id = f"{DOMAIN}_{room_name}_{name.lower().replace(' ', '_')}"

    @property
    def unique_id(self):
        """Return the unique ID for this entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def is_on(self):
        """Return the current state of the switch."""
        return self._state

    @property
    def device_info(self):
        """Return device information to link this entity to a device."""
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": f"Device for {self._name}",
            "model": "Switch Device",
            "manufacturer": "OpenGrowBox",
            "suggested_area": self.room_name,
        }

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        self._state = True
        self.async_write_ha_state()
        _LOGGER.info(f"Switch '{self._name}' turned ON.")

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        self._state = False
        self.async_write_ha_state()
        _LOGGER.info(f"Switch '{self._name}' turned OFF.")

    async def async_toggle(self, **kwargs):
        """Toggle the state of the switch."""
        self._state = not self._state
        self.async_write_ha_state()
        _LOGGER.info(f"Switch '{self._name}' toggled to: {'ON' if self._state else 'OFF'}.")

    async def async_added_to_hass(self):
        """Restore state when the entity is added to Home Assistant."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state is not None:
            self._state = state.state == "on"
            _LOGGER.info(f"Restored state for '{self._name}': {'ON' if self._state else 'OFF'}.")

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up switch entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Create switches with placeholders for customization
    switches = [
        #TemplateSwitch
        CustomSwitch(f"OGB_TemplateSwitch_{coordinator.room_name}", coordinator.room_name, coordinator, initial_state=False),
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

            _LOGGER.info(f"Received request to toggle switch '{entity_id}'")


            for switch in hass.data[DOMAIN]["switches"]:
                if switch.entity_id == entity_id:
                    await switch.async_toggle()
                    _LOGGER.info(f"Toggled switch '{switch.name}' to state: {'ON' if switch.is_on else 'OFF'}")
                    return

            _LOGGER.warning(f"Switch with entity_id '{entity_id}' not found.")


        hass.services.async_register(
            DOMAIN,
            "toggle_switch",
            handle_toggle_switch,
            schema=vol.Schema({
                vol.Required("entity_id"): str,
            }),
        )
