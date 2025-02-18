from homeassistant.components.number import NumberEntity
from homeassistant.helpers.restore_state import RestoreEntity
import logging
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class CustomNumber(NumberEntity,RestoreEntity):
    """Custom number entity for multiple hubs."""

    def __init__(self, name, room_name, coordinator, min_value, max_value, step, unit, initial_value=None):
        """Initialize the number entity."""
        self._name = name
        self.room_name = room_name
        self._min_value = min_value
        self._max_value = max_value
        self._step = step
        self._unit = unit
        self._value = initial_value or min_value
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
    def native_min_value(self):
        return self._min_value

    @property
    def native_max_value(self):
        return self._max_value

    @property
    def native_step(self):
        return self._step

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def native_value(self):
        """Return the current value of the number."""
        return self._value

    @property
    def device_info(self):
        """Return device information to link this entity to a device."""
        return {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": f"Device for {self._name}",
            "model": "Number Device",
            "manufacturer": "OpenGrowBox",
            "suggested_area": self.room_name,  # Optional: Gibt einen Hinweis für den Bereich
        }
        
    async def async_added_to_hass(self):
        """Restore last known state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state is not None:
            try:
                # Stelle den Wert nur wieder her, wenn er innerhalb des zulässigen Bereichs liegt
                restored_value = float(last_state.state)
                if self._min_value <= restored_value <= self._max_value:
                    self._value = restored_value
                    _LOGGER.info(f"Restored value for '{self._name}': {restored_value}")
                else:
                    _LOGGER.warning(f"Restored value for '{self._name}' out of range: {restored_value}")
            except ValueError:
                _LOGGER.warning(f"Invalid restored value for '{self._name}': {last_state.state}")

    async def async_set_native_value(self, value: float):
        """Set a new value."""
        if self._min_value <= value <= self._max_value:
            self._value = value
            self.async_write_ha_state()
            _LOGGER.info(f"Number '{self._name}' set to {value}")
        else:
            _LOGGER.warning(f"Value {value} out of range for '{self._name}'")

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up number entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Create number entities
    numbers = [
        CustomNumber(f"OGB_LeafTemp_Offset_{coordinator.room_name}", coordinator.room_name, coordinator,
                min_value=0.0, max_value=5.0, step=0.1, unit="°C", initial_value=2.0),
        CustomNumber(f"OGB_VPDTarget_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=5.0, step=0.1, unit="kPa", initial_value=0.0),
        CustomNumber(f"OGB_VPDTolerance_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=25, step=0.1, unit="%", initial_value=0.1),
        
        CustomNumber(f"OGB_TemperatureWeight_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=00.0, max_value=2.0, step=0.1, unit="X", initial_value=1.0),
        CustomNumber(f"OGB_HumidityWeight_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0.0, max_value=2.0, step=0.1, unit="X", initial_value=1.0),
        
        ##Temp/Hum Min/MAX
        CustomNumber(f"OGB_MinTemp_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0.0, max_value=35, step=0.25, unit="°C", initial_value=0),
        CustomNumber(f"OGB_MaxTemp_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0.0, max_value=35, step=0.25, unit="°C", initial_value=0),
        CustomNumber(f"OGB_MinHum_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0.0, max_value=100, step=0.25, unit="%", initial_value=0),
        CustomNumber(f"OGB_MaxHum_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0.0, max_value=100, step=0.25, unit="%", initial_value=0),     
        
        ##CO2
        CustomNumber(f"OGB_CO2MinValue_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0.0, max_value=2000, step=5, unit="ppm", initial_value=400),
        CustomNumber(f"OGB_CO2MaxValue_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0.0, max_value=2000, step=5, unit="ppm", initial_value=400),
        CustomNumber(f"OGB_CO2TargetValue_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0.0, max_value=2000, step=5, unit="ppm", initial_value=400),
        
        
        ## PlantTimes
        CustomNumber(f"OGB_BreederBloomDays_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0, max_value=150, step=1, unit="days", initial_value=0),
        
        CustomNumber(f"OGB_PlantFoodIntervall_{coordinator.room_name}", coordinator.room_name, coordinator,
                     min_value=0, max_value=60000, step=1, unit="minutes", initial_value=60),           

        
        
        # PID VPD
        CustomNumber(f"OGB_ProportionalVPDFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=5.0, step=0.1, unit="X", initial_value=1.0),
        CustomNumber(f"OGB_IntegralVPDFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=0.2, step=0.01, unit="X", initial_value=0.01),
        CustomNumber(f"OGB_DerivativVPDFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=2.0, step=0.1, unit="X", initial_value=0.1),

        # PID TEMP
        CustomNumber(f"OGB_ProportionalTempFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=10.0, step=0.5, unit="X", initial_value=5.0),
        CustomNumber(f"OGB_IntegralTempFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=1.0, step=0.05, unit="X", initial_value=0.05),
        CustomNumber(f"OGB_DerivativTempFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=2.0, step=0.1, unit="X", initial_value=0.5),

        # PID Hum
        CustomNumber(f"OGB_ProportionalHumFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=10.0, step=0.5, unit="X", initial_value=5.0),
        CustomNumber(f"OGB_IntegralHumFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=0.5, step=0.02, unit="X", initial_value=0.02),
        CustomNumber(f"OGB_DerivativHumFaktor_{coordinator.room_name}", coordinator.room_name, coordinator,
                    min_value=0.0, max_value=1.0, step=0.1, unit="X", initial_value=0.1),
   
    ]

    if "numbers" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["numbers"] = []

    hass.data[DOMAIN]["numbers"].extend(numbers)
    async_add_entities(numbers)
