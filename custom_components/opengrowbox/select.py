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

    def __init__(self, name, room_name, coordinator, options=None, initial_value=None):
        """Initialize the custom select."""
        self._name = name
        self.room_name = room_name
        self._attr_options = options or []  # Home Assistant erwartet _attr_options
        self._attr_current_option = initial_value if initial_value in self._attr_options else None
        self.coordinator = coordinator
        self._unique_id = f"{DOMAIN}_{room_name}_{name.lower().replace(' ', '_')}"

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
            "room_name": self.room_name,
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
            "suggested_area": self.room_name,
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
        CustomSelect(f"OGB_PlantStage_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["Germination", "Clones", "EarlyVeg", "MidVeg", "LateVeg", "EarlyFlower", "MidFlower", "LateFlower"], initial_value="Germination"),
        CustomSelect(f"OGB_TentMode_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["VPD Perfection","VPD Target","Drying","Disabled"], initial_value="Disabled"),
        CustomSelect(f"OGB_HoldVpdNight_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["YES", "NO"], initial_value="YES"),
        CustomSelect(f"OGB_OwnWeights_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["YES", "NO"], initial_value="NO"),
        CustomSelect(f"OGB_CO2_Control_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["YES", "NO"], initial_value="NO"),
        CustomSelect(f"OGB_MinMax_Control_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["YES", "NO"], initial_value="NO"),
        CustomSelect(f"OGB_LightControl_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["YES", "NO"], initial_value="NO"),
        CustomSelect(f"OGB_VPDLightControl_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["YES", "NO"], initial_value="NO"),
        CustomSelect(f"OGB_DryingModes_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["ElClassico", "DewBased","5DayDry"],initial_value=""),
        CustomSelect(f"OGB_MainControl_{coordinator.room_name}", coordinator.room_name, coordinator,
                    options=["HomeAssistant", "Node-RED","Self-Hosted","Premium"], initial_value="HomeAssistant"),
        
        ## HYDRO
        CustomSelect(f"OGB_Hydro_Mode_{coordinator.room_name}", coordinator.room_name, coordinator,
                    options=["Hydro","Plant-Watering","OFF"], initial_value="OFF"),
        CustomSelect(f"OGB_Hydro_Cycle_{coordinator.room_name}", coordinator.room_name, coordinator,
                    options=["YES","NO"], initial_value="NO"),
        CustomSelect(f"OGB_Hydro_Retrive_{coordinator.room_name}", coordinator.room_name, coordinator,
                    options=["YES","NO"], initial_value="NO"),
        
        ## FEED
        CustomSelect(f"OGB_Feed_Plan_{coordinator.room_name}", coordinator.room_name, coordinator,
                    options=["Own-Plan","Automatic","Disabled"], initial_value="Disabled"),

        # Ambient
        CustomSelect(f"OGB_AmbientControl_{coordinator.room_name}", coordinator.room_name, coordinator,
                     options=["YES", "NO"], initial_value="NO"),

        
        ##Notifications
        CustomSelect(f"OGB_Notifications_{coordinator.room_name}", coordinator.room_name, coordinator,
                    options=["Enabled", "Disabled"], initial_value="Disabled"),       
        
        #WorkMode
        CustomSelect(f"OGB_WorkMode_{coordinator.room_name}", coordinator.room_name, coordinator,
                    options=["YES","NO"], initial_value="NO"),
        
        ##DEVICES
        CustomSelect(f"OGB_OwnDeviceSets_{coordinator.room_name}", coordinator.room_name, coordinator,
                    options=["YES", "NO"], initial_value="NO"),
        CustomSelect(f"OGB_Light_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_Light_MinMax_{coordinator.room_name}", coordinator.room_name, coordinator, options=["YES", "NO"], initial_value="NO"),
        
        CustomSelect(f"OGB_Exhaust_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_Exhaust_MinMax_{coordinator.room_name}", coordinator.room_name, coordinator, options=["YES", "NO"], initial_value="NO"),
        
        CustomSelect(f"OGB_Intake_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_Intake_MinMax_{coordinator.room_name}", coordinator.room_name, coordinator, options=["YES", "NO"], initial_value="NO"),
         
        CustomSelect(f"OGB_Vents_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_Ventilation_MinMax_{coordinator.room_name}", coordinator.room_name, coordinator, options=["YES", "NO"], initial_value="NO"),
                
        CustomSelect(f"OGB_Humidifier_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_Dehumidifier_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_Heater_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_Cooler_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_Climate_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_CO2_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
        CustomSelect(f"OGB_WaterPump_Device_Select_{coordinator.room_name}", coordinator.room_name, coordinator, options=[""], initial_value=None),
    ]


    if "selects" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["selects"] = []

    hass.data[DOMAIN]["selects"].extend(selects)
    

    async_add_entities(selects)

    
    
    if not hass.services.has_service(DOMAIN, "add_select_options"):
        async def handle_add_options(call):
            """Handle the update sensor service."""
            entity_id = call.data.get("entity_id")
            options = call.data.get("options")

            _LOGGER.info(f"Adding options to '{entity_id}': {options}")


            for select in hass.data[DOMAIN]["selects"]:
                if select.entity_id == entity_id:
                    found = True
                    select.add_options(options)
                    _LOGGER.info(f"Updated select'{select.name}' to value: {options}")
                    break
            if not found:
                _LOGGER.error(f"Select entity with id '{entity_id}' not found.")


        hass.services.async_register(
            DOMAIN,
            "add_select_options",
            handle_add_options,
            schema=vol.Schema({
                vol.Required("entity_id"): str,
                vol.Required("options"): vol.All(list, [str]), 
            }),
        )

    if not hass.services.has_service(DOMAIN, "remove_select_options"):
        async def handle_remove_options(call):
            """Handle the remove options service."""
            entity_id = call.data.get("entity_id")
            options_to_remove = call.data.get("options")
            invalid_modes = ["AI Control", "MCP Control", "PID Control", "OGB Control"]
            fallback_option = "VPD-Perfection"
            found = False

            _LOGGER.warning(f"Removing options from '{entity_id}': {options_to_remove}")

            for select in hass.data[DOMAIN]["selects"]:
                if select.entity_id == entity_id:
                    found = True

                    # Entferne die angegebenen Optionen
                    select._attr_options = [opt for opt in select._attr_options if opt not in options_to_remove]

                    # Prüfe, ob aktuelle Option entfernt wurde oder ein ungültiger Modus ist
                    if (select._attr_current_option in options_to_remove or
                        select._attr_current_option in invalid_modes):

                        # Fallback nur setzen, wenn es verfügbar ist
                        if fallback_option in select._attr_options:
                            select._attr_current_option = fallback_option
                            _LOGGER.warning(f"Set '{select.name}' fallback to '{fallback_option}'")
                        else:
                            select._attr_current_option = None
                            _LOGGER.warning(
                                f"Fallback option '{fallback_option}' not available for '{select.name}', setting to None"
                            )

                    select.async_write_ha_state()
                    _LOGGER.warning(f"Updated options for '{select.name}': {select._attr_options}")
                    break

            if not found:
                _LOGGER.error(f"Select entity with id '{entity_id}' not found.")

        hass.services.async_register(
            DOMAIN,
            "remove_select_options",
            handle_remove_options,
            schema=vol.Schema({
                vol.Required("entity_id"): str,
                vol.Required("options"): vol.All(list, [str]),
            }),
        )
