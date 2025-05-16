import logging
import os
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.frontend import async_remove_panel, add_extra_js_url
from homeassistant.loader import async_get_integration
from homeassistant.const import Platform
from .const import DOMAIN
from .coordinator import OGBIntegrationCoordinator
from .frontend import async_register_frontend


_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "number", "select", "time", "switch", "date", "text"]

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the OpenGrowBox integration via the UI."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    # Verify that the frontend integration is available
    try:
        frontend_integration = await async_get_integration(hass, "frontend")
    except Exception as e:
        _LOGGER.error(f"Frontend integration not found: {e}")
        return False

    # Create the coordinator
    coordinator = OGBIntegrationCoordinator(hass, config_entry)
    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    # Load all platforms
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    await async_register_frontend(hass)

    await coordinator.startOGB()

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload the OpenGrowBox config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(config_entry.entry_id)

        # Remove the panel from the frontend
        async_remove_panel(hass, frontend_url_path="opengrowbox")

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Reload the HACS config entry."""
    if not await async_unload_entry(hass, config_entry):
        return
    await async_setup_entry(hass, config_entry)