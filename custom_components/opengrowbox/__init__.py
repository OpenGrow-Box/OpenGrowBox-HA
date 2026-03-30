import logging
import os
import shutil
from glob import glob
from typing import Any

import yaml

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
_CONFIG_CHECK_FLAG = "__config_checked__"
_REQUIRED_LOGGER_DEFAULT = "info"
_REQUIRED_LOGGER_LEVEL = "debug"
_REQUIRED_LOGGER_OVERRIDES = {
    "homeassistant.config_entries": _REQUIRED_LOGGER_LEVEL,
    "homeassistant.setup": _REQUIRED_LOGGER_LEVEL,
    "homeassistant.loader": _REQUIRED_LOGGER_LEVEL,
    "custom_components.opengrowbox": _REQUIRED_LOGGER_LEVEL,
    "custom_components.ogb-dev-env": _REQUIRED_LOGGER_LEVEL,
}


def _load_configuration_yaml(path: str) -> dict[str, Any]:
    """Load Home Assistant configuration.yaml as dict."""
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)

    if isinstance(loaded, dict):
        return loaded
    return {}


def _as_level(value: Any) -> str:
    """Normalize logger level value to lowercase string."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _apply_minimal_log_fallback() -> None:
    """Use minimal runtime logging if requested logger config is missing."""
    logging.getLogger("custom_components.opengrowbox").setLevel(logging.WARNING)
    logging.getLogger("custom_components.ogb-dev-env").setLevel(logging.WARNING)


async def _check_required_ha_logging_config(hass: HomeAssistant) -> None:
    """Validate required configuration.yaml entries for default_config and logger."""
    config_path = hass.config.path("configuration.yaml")

    try:
        config = await hass.async_add_executor_job(_load_configuration_yaml, config_path)
    except Exception as err:
        _LOGGER.warning(
            "Could not read configuration.yaml (%s). Using minimal OpenGrowBox log level.",
            err,
        )
        _apply_minimal_log_fallback()
        return

    has_default_config_key = "default_config" in config
    logger_config = config.get("logger")
    logger_default_ok = False
    logger_overrides_ok = False

    if isinstance(logger_config, dict):
        logger_default_ok = _as_level(logger_config.get("default")) == _REQUIRED_LOGGER_DEFAULT
        logs_block = logger_config.get("logs")
        if isinstance(logs_block, dict):
            logger_overrides_ok = all(
                _as_level(logs_block.get(name)) == required_level
                for name, required_level in _REQUIRED_LOGGER_OVERRIDES.items()
            )

    if has_default_config_key and logger_default_ok and logger_overrides_ok:
        return

    missing_reasons = []
    if not has_default_config_key:
        missing_reasons.append("default_config missing")
    if not logger_default_ok:
        missing_reasons.append("logger.default != info")
    if not logger_overrides_ok:
        missing_reasons.append("required logger.logs entries missing")

    _LOGGER.warning(
        "configuration.yaml check failed (%s). "
        "Expected: default_config and logger settings for OpenGrowBox diagnostics. "
        "Applying minimal OpenGrowBox runtime log level (WARNING).",
        ", ".join(missing_reasons),
    )
    _apply_minimal_log_fallback()


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the OpenGrowBox integration via the UI."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    if not hass.data[DOMAIN].get(_CONFIG_CHECK_FLAG):
        await _check_required_ha_logging_config(hass)
        hass.data[DOMAIN][_CONFIG_CHECK_FLAG] = True

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

    # Clean up orphaned/missing OGB entities from entity registry
    await _cleanup_orphaned_entities(hass)

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


async def _cleanup_orphaned_entities(hass: HomeAssistant) -> None:
    """
    Clean up orphaned OGB entities from the entity registry.
    
    Removes entities that are registered to OpenGrowBox but no longer exist
    or are no longer available. This prevents HA warnings about missing entities.
    """
    try:
        from homeassistant.helpers import entity_registry as er
        
        entity_reg = await er.async_get(hass)
        
        # Get all OGB-related entries
        ogb_entities = []
        for entity_id, entry in entity_reg.entities.items():
            if entry.platform == "opengrowbox":
                ogb_entities.append((entity_id, entry))
        
        if not ogb_entities:
            return
            
        # Check each entity and remove if it's orphaned (not available)
        removed_count = 0
        for entity_id, entry in ogb_entities:
            # Try to get the entity state - if it doesn't exist, it's orphaned
            state = hass.states.get(entity_id)
            
            if state is None:
                # Entity doesn't exist - remove from registry
                try:
                    await entity_reg.async_remove(entity_id)
                    removed_count += 1
                    _LOGGER.info(f"🧹 Removed orphaned entity: {entity_id}")
                except Exception as e:
                    _LOGGER.debug(f"Could not remove {entity_id}: {e}")
        
        if removed_count > 0:
            _LOGGER.info(f"✅ Cleaned up {removed_count} orphaned OpenGrowBox entities")
        else:
            _LOGGER.debug("No orphaned entities found")
            
    except Exception as e:
        _LOGGER.debug(f"Entity cleanup skipped: {e}")


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


def _remove_path(path: str) -> bool:
    """Remove file or directory recursively if present."""
    if not os.path.exists(path):
        return False

    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=False)
    else:
        os.remove(path)

    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Remove room-scoped integration data when one config entry is deleted."""
    room_name = str(config_entry.data.get("room_name", "")).strip()
    room_lower = room_name.lower()

    _LOGGER.warning(
        "Removing OpenGrowBox entry %s for room '%s' - deleting room-scoped data",
        config_entry.entry_id,
        room_name or "unknown",
    )

    if not room_name:
        _LOGGER.warning(
            "Room name missing in config entry %s - skip filesystem cleanup to avoid deleting other rooms",
            config_entry.entry_id,
        )
        return

    targets: list[str] = []

    # Room-specific state and media in ogb_data / legacy ogb-data
    for base_name in ("ogb_data", "ogb-data"):
        base_dir = hass.config.path(base_name)
        targets.extend(
            [
                os.path.join(base_dir, f"ogb_{room_lower}_state.json"),
                os.path.join(base_dir, f"{room_name}_img"),
                os.path.join(base_dir, f"{room_lower}_img"),
                os.path.join(base_dir, "scripts", f"{room_lower}_script.yaml"),
                os.path.join(base_dir, "scripts", f"{room_lower}_script_backup.yaml"),
            ]
        )

    # Public timelapse output mirror in www/ogb_data
    targets.extend(
        [
            hass.config.path("www", "ogb_data", f"{room_name}_img"),
            hass.config.path("www", "ogb_data", f"{room_lower}_img"),
        ]
    )

    # Room-specific premium files only (do not remove shared key/other rooms)
    for base_name in (".ogb_premium", ".ogb-premium"):
        base_dir = hass.config.path(base_name)
        targets.extend(
            [
                os.path.join(base_dir, f"ogb_premium_state_{room_lower}.enc"),
                os.path.join(base_dir, f"ogb_{room_name}_room_id.txt"),
                os.path.join(base_dir, f"ogb_{room_lower}_room_id.txt"),
            ]
        )

    # Additional room-id files with special characters normalized differently
    premium_dirs = [hass.config.path(".ogb_premium"), hass.config.path(".ogb-premium")]
    for premium_dir in premium_dirs:
        # Use async glob to avoid blocking event loop
        candidates = await hass.async_add_executor_job(
            glob,
            os.path.join(premium_dir, "ogb_*_room_id.txt")
        )
        for candidate in candidates:
            filename = os.path.basename(candidate)
            if filename.startswith("ogb_") and filename.endswith("_room_id.txt"):
                extracted = filename[len("ogb_"):-len("_room_id.txt")]
                if extracted.lower() == room_lower:
                    targets.append(candidate)

        # Use async glob to avoid blocking event loop
        candidates = await hass.async_add_executor_job(
            glob,
            os.path.join(premium_dir, "ogb_premium_state_*.enc")
        )
        for candidate in candidates:
            filename = os.path.basename(candidate)
            if filename.startswith("ogb_premium_state_") and filename.endswith(".enc"):
                extracted = filename[len("ogb_premium_state_"):-len(".enc")]
                if extracted.lower() == room_lower:
                    targets.append(candidate)

    # Deduplicate while preserving order
    seen = set()
    deduped_targets = []
    for target_path in targets:
        if target_path in seen:
            continue
        seen.add(target_path)
        deduped_targets.append(target_path)

    for target_path in deduped_targets:
        try:
            removed = await hass.async_add_executor_job(_remove_path, target_path)
            if removed:
                _LOGGER.warning("Deleted OpenGrowBox room data path: %s", target_path)
        except FileNotFoundError:
            continue
        except Exception as e:
            _LOGGER.error("Failed to delete room-scoped path %s: %s", target_path, e)
