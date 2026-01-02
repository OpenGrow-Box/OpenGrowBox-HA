import logging
import os

import voluptuous as vol

from homeassistant.components.frontend import (add_extra_js_url,
                                               async_remove_panel)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import OGBIntegrationCoordinator
from .frontend import async_register_frontend

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "number", "select", "time", "switch", "date", "text"]


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the OpenGrowBox integration via the UI."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    # Check if this entry is already set up to prevent duplicate initialization
    if config_entry.entry_id in hass.data[DOMAIN]:
        _LOGGER.debug(
            f"Entry {config_entry.entry_id} already set up, skipping duplicate setup"
        )
        return True

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

    # Service registration happens in sensor.py to access sensor objects directly
    _LOGGER.info(f"✅ Integration setup complete, waiting for sensor platform to register services")

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload the OpenGrowBox config entry."""
    _LOGGER.info(f"🛑 Unloading OpenGrowBox integration for entry {config_entry.entry_id}")
    
    # Get coordinator before unloading platforms
    coordinator = hass.data[DOMAIN].get(config_entry.entry_id)
    
    # CRITICAL: Shutdown coordinator and all its tasks FIRST
    # This prevents orphaned tasks that can crash HA
    if coordinator:
        try:
            _LOGGER.info(f"🛑 Shutting down coordinator for {coordinator.room_name}")
            await coordinator.async_shutdown()
            _LOGGER.info(f"✅ Coordinator shutdown complete for {coordinator.room_name}")
        except Exception as e:
            _LOGGER.error(f"❌ Error during coordinator shutdown: {e}", exc_info=True)
    
    # Now unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )
    
    if unload_ok:
        hass.data[DOMAIN].pop(config_entry.entry_id, None)

        # Remove the panel from the frontend
        try:
            async_remove_panel(hass, frontend_url_path="opengrowbox")
        except Exception as e:
            _LOGGER.debug(f"Panel already removed or not found: {e}")
        
        _LOGGER.info(f"✅ OpenGrowBox integration unloaded successfully")
    else:
        _LOGGER.warning(f"⚠️ Failed to unload some platforms")

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Reload the HACS config entry."""
    if not await async_unload_entry(hass, config_entry):
        return
    await async_setup_entry(hass, config_entry)
