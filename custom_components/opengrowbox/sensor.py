from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity
import logging
from .const import DOMAIN
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

class CustomSensor(Entity):
    """Custom sensor for multiple hubs with update capability and graph support."""

    def __init__(self, name, room_name, coordinator, initial_value=None, device_class=None):
        """Initialize the sensor."""
        self._name = name
        self._state = initial_value  # Initial value
        self.room_name = room_name
        self.coordinator = coordinator
        self._device_class = device_class  # e.g., temperature, humidity, light
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
    def device_info(self):
        """Return device information to link this entity to a device."""
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": f"Device for {self._name}",
            "model": "Sensor Device",
            "manufacturer": "OpenGrowBox",
            "suggested_area": self.room_name,
        }


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
        self._state = new_state
        self.async_write_ha_state()

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up sensor entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Create all sensors in a single array
    sensors = [
        # VPD Sensors
        CustomSensor(f"OGB_CurrentVPD_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=None, device_class="vpd"),
        CustomSensor(f"OGB_AVGTemperature_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=None, device_class="temperature"),
        CustomSensor(f"OGB_AVGDewpoint_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=None, device_class="temperature"),
        CustomSensor(f"OGB_AVGHumidity_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=None, device_class="humidity"),

        CustomSensor(f"OGB_Current_VPD_Target_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=None, device_class="vpd"),
        CustomSensor(f"OGB_Current_VPD_Target_Min_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=None, device_class="vpd"),
        CustomSensor(f"OGB_Current_VPD_Target_Max_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=None, device_class="vpd"),

        # Ambient Sensors
        CustomSensor(f"OGB_AmbientTemperature_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0.0, device_class="temperature"),
        CustomSensor(f"OGB_AmbientDewpoint_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0.0, device_class="temperature"),
        CustomSensor(f"OGB_AmbientHumidity_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0.0, device_class="humidity"),

        # Outside Sensors
        CustomSensor(f"OGB_OutsiteTemperature_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0.0, device_class="temperature"),
        CustomSensor(f"OGB_OutsiteDewpoint_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0.0, device_class="temperature"),
        CustomSensor(f"OGB_OutsiteHumidity_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0.0, device_class="humidity"),

        # Light Sensors
        CustomSensor(f"OGB_PPFD_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0.0, device_class="ppfd"),
        CustomSensor(f"OGB_DLI_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0.0, device_class="dli"),
        
        # PlantTimeSensors
        CustomSensor(f"OGB_PlantTotalDays_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0, device_class="days"),
        CustomSensor(f"OGB_TotalBloomDays_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0, device_class="days"),
        CustomSensor(f"OGB_ChopChopTime_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0, device_class="days"),
        CustomSensor(f"OGB_PlantFoodNextFeed_{coordinator.room_name}", coordinator.room_name, coordinator, initial_value=0, device_class="Minutes"),

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
            entity_id = call.data.get("entity_id")
            value = call.data.get("value")

            #_LOGGER.debug(f"Received request to update sensor '{entity_id}' with value: {value}")

            # Find and update the corresponding sensor
            for sensor in hass.data[DOMAIN]["sensors"]:
                if sensor.entity_id == entity_id:
                    sensor.update_state(value)
                    #_LOGGER.debug(f"Updated sensor '{sensor.name}' to value: {value}")
                    return


        hass.services.async_register(
            DOMAIN,
            "update_sensor",
            handle_update_sensor,
            schema=vol.Schema({
                vol.Required("entity_id"): str,
                vol.Required("value"): vol.Any(float, int, str),
            }),
        )
