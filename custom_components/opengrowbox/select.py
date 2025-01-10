from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity
import logging
from .const import DOMAIN
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)


class OpenGrowBoxRoomSelector(SelectEntity, RestoreEntity):
    """A global selector for all Home Assistant rooms with state restoration."""

    def __init__(self, name, options):
        """Initialize the Room Selector."""
        self._attr_name = name  # Der Name der Entität
        self._name = name  # Sicherstellen, dass _name definiert ist
        self._options = options or []
        self._attr_current_option = options[0] if options else None
        self._unique_id = f"{DOMAIN}_room_selector"

    @property
    def unique_id(self):
        """Return the unique ID for this entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the Room Selector."""
        return self._name

    @property
    def options(self):
        """Return the list of available options."""
        return self._options

    @property
    def current_option(self):
        """Return the currently selected option."""
        return self._attr_current_option

    async def async_select_option(self, option: str):
        """Set a new room as selected."""
        if option in self._options:
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.info(f"Room Selector changed to: {option}")
        else:
            _LOGGER.warning(f"Invalid room selection: {option}")

    async def async_added_to_hass(self):
        """Restore the last selected state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in self._options:
            self._attr_current_option = last_state.state
            _LOGGER.info(f"Restored state for '{self._name}': {last_state.state}")
        else:
            _LOGGER.info(f"No valid previous state found for '{self._name}'")

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        return {}

    @property
    def device_info(self):
        """Return device information for the Room Selector."""
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": "Room Selector",
            "model": "Room Selector Device",
            "manufacturer": "OpenGrowBox",
        }

class CustomSelect(SelectEntity, RestoreEntity):
    """Custom select entity with state restoration."""

    def __init__(self, name, hub_name, coordinator, options=None, initial_value=None):
        """Initialize the custom select."""
        self._name = name
        self.hub_name = hub_name
        self._attr_options = options or []  # Home Assistant erwartet _attr_options
        self._attr_current_option = initial_value if initial_value in self._attr_options else None
        self.coordinator = coordinator
        self._unique_id = f"{DOMAIN}_{hub_name}_{name.lower().replace(' ', '_')}"

    async def async_added_to_hass(self):
        """Restore last known state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            _LOGGER.info(f"Restored state for '{self._name}': {last_state.state}")
        else:
            _LOGGER.info(f"No valid previous state found for '{self._name}'")

    @property
    def unique_id(self):
        """Return the unique ID for this entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def options(self):
        """Return the list of available options."""
        return self._attr_options  # Home Assistant nutzt dies im Frontend

    @property
    def current_option(self):
        """Return the currently selected option."""
        return self._attr_current_option

    async def async_select_option(self, option):
        """Set the selected option asynchronously."""
        if option in self._attr_options:
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.info(f"Select '{self._name}' changed to '{option}'")
        else:
            _LOGGER.warning(f"Invalid option '{option}' for select '{self._name}'")

    def add_options(self, new_options):
        """Add new options to the select entity."""
        _LOGGER.info(f"Adding options to '{self._name}': {new_options}")
        unique_new_options = [opt for opt in new_options if opt not in self._attr_options]
        self._attr_options = list(set(self._attr_options + new_options))
        _LOGGER.info(f"Updated options for '{self._name}': {self._attr_options}")
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        return {
            "hub_name": self.hub_name,
            "options": self._attr_options,  # Hinzufügen der aktuellen Optionen
        }
        
    @property
    def device_info(self):
        """Return device information to link this entity to a device."""
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": f"Device for {self._name}",
            "model": "Select Device",
            "manufacturer": "OpenGrowBox",
            "suggested_area": self.hub_name,
        }


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up select entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Add global Room Selector if not already added
    if "room_selector" not in hass.data[DOMAIN]:
        room_selector = coordinator.create_room_selector()
        hass.data[DOMAIN]["room_selector"] = room_selector
        async_add_entities([room_selector])

    # Create hub-specific selects
    selects = [
        CustomSelect(f"OGB_PlantStage_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["Germination", "Clones", "EarlyVeg", "MidVeg", "LateVeg", "EarlyFlower", "MidFlower", "LateFlower",""], initial_value="Germination"),
        CustomSelect(f"OGB_TentMode_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["VPD Perfection", "IN-VPD-Range", "Targeted VPD", "GLBJ-Mode","Drying","Disabled",""], initial_value="Disabled"),
        CustomSelect(f"OGB_HoldVPDNight_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="YES"),
        CustomSelect(f"OGB_AmbientBorrow_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_AmbientControl_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_AutoWatering_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_OwnWeights_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_PIDControl_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_CO2_Control_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_LightControl_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_GLS_Control_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_VPDLightControl_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_GLS_PlantType_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["Sativa", "Indica",""],initial_value=""),
        CustomSelect(f"OGB_OwnDeviceSets_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["YES", "NO",""], initial_value="NO"),
        CustomSelect(f"OGB_DryingModes_{coordinator.hub_name}", coordinator.hub_name, coordinator,
                     options=["ElClassico", "SharkMouse","DewBased",""],initial_value=""),
        ##DEVICES
        CustomSelect(f"OGB_LightSelect_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_LightSelect2_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_LightSelect3_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_ExhaustSelect_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_VentsSelect_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_HumidifierSelect_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_DehumidifierSelect_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_HeaterSelect_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_CoolerSelect_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_ClimateSelect_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_CO2Select_{coordinator.hub_name}", coordinator.hub_name, coordinator, options=[""], initial_value=None),
    ]

    # Register the Selects globally in hass.data
    if "selects" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["selects"] = []

    hass.data[DOMAIN]["selects"].extend(selects)
    
    # Add entities to Home Assistant
    async_add_entities(selects)
    # Register a service to add options to selects
    
    
    if not hass.services.has_service(DOMAIN, "add_select_options"):
        async def handle_add_options(call):
            """Handle the update sensor service."""
            entity_id = call.data.get("entity_id")
            options = call.data.get("options")

            _LOGGER.info(f"Adding options to '{entity_id}': {options}")

            # Find and update the corresponding sensor
            for select in hass.data[DOMAIN]["selects"]:
                if select.entity_id == entity_id:
                    found = True
                    select.add_options(options)
                    _LOGGER.info(f"Updated select'{select.name}' to value: {options}")
                    break
            if not found:
                _LOGGER.error(f"Select entity with id '{entity_id}' not found.")

        # Register the service in Home Assistant
        hass.services.async_register(
            DOMAIN,
            "add_select_options",
            handle_add_options,
            schema=vol.Schema({
                vol.Required("entity_id"): str,
                vol.Required("options"): vol.All(list, [str]),  # Akzeptiert eine Liste von Strings
            }),
        )
