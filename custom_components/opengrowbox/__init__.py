import asyncio
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
_CLEANUP_DONE_FLAG = "__cleanup_done__"
_AMBIENT_ENSURE_FLAG = "__ambient_ensure_done__"
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
        content = file.read()

    try:
        loaded = yaml.safe_load(content)
    except yaml.constructor.ConstructorError as err:
        err_msg = str(err)
        if "!include" in err_msg or "tag:yaml.org,2002:python/object" in err_msg:
            return _parse_yaml_with_ha_includes(content)
        raise

    if isinstance(loaded, dict):
        return loaded
    return {}


def _parse_yaml_with_ha_includes(content: str) -> dict[str, Any]:
    """Parse YAML handling Home Assistant !include tags by extracting logger config only."""
    result = {}
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("logger:"):
            result["logger"] = _extract_logger_block(content)
            break
        if stripped.startswith("default_config:"):
            result["default_config"] = {}
    return result


def _extract_logger_block(content: str) -> dict[str, Any]:
    """Extract logger section from YAML content."""
    logger_block = {}
    in_logs = False
    for line in content.split("\n"):
        if line.strip().startswith("logger:"):
            continue
        if "logs:" in line and "logs" not in logger_block:
            in_logs = True
            continue
        if in_logs and line and not line[0].isspace():
            in_logs = False
        if in_logs and ":" in line:
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].strip():
                logger_block["logs"] = logger_block.get("logs", {})
                logger_block["logs"][parts[0].strip()] = parts[1].strip()
        elif "default:" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                logger_block["default"] = parts[1].strip()
    return logger_block


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

    # Create the coordinator
    coordinator = OGBIntegrationCoordinator(hass, config_entry)
    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    # Load all platforms
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    await async_register_frontend(hass)

    await coordinator.startOGB()

    # Ensure matching HA Area exists and attach room hub device to it.
    await _ensure_room_area_and_assign_hub(hass, config_entry)

    # CRITICAL: Update room selector AFTER area is created
    # Small delay to ensure area registry is fully updated
    await asyncio.sleep(0.5)
    await coordinator._update_room_selector_immediate()

    # Run registry cleanup once per HA runtime after platforms are loaded.
    if not hass.data[DOMAIN].get(_CLEANUP_DONE_FLAG):
        await _cleanup_orphaned_entities(hass)
        hass.data[DOMAIN][_CLEANUP_DONE_FLAG] = True

    # Ensure ambient room exists through config flow import (same creation path as UI).
    room_name = str(config_entry.data.get("room_name", "")).strip().lower()
    if room_name != "ambient" and not hass.data[DOMAIN].get(_AMBIENT_ENSURE_FLAG):
        ambient_ok = await _ensure_ambient_room_entry(hass)
        if ambient_ok:
            hass.data[DOMAIN][_AMBIENT_ENSURE_FLAG] = True

    # Service registration happens in sensor.py to access sensor objects directly
    _LOGGER.info(f"✅ Integration setup complete, waiting for sensor platform to register services")

    return True


async def _cleanup_orphaned_entities(hass: HomeAssistant) -> None:
    """
    Clean up orphaned OGB entities from the entity registry.
    
    Removes entities that are retired or tied to removed config entries.
    Also removes legacy orphaned devices for retired strainname entities.
    """
    try:
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er
        
        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)
        active_entry_ids = {
            entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)
        }
        
        # Get all OGB-related entries
        ogb_entities = []
        for entity_id, entry in entity_reg.entities.items():
            if entry.platform == "opengrowbox":
                ogb_entities.append((entity_id, entry))
        
        if not ogb_entities:
            return
        
        # Define retired entity patterns to remove (version-specific)
        retired_patterns = [
            "text.ogb_strainname_",  # Removed in 1.4.2 - strain now via medium
        ]
        
        # Check each entity and remove if orphaned or retired
        removed_count = 0
        for entity_id, entry in ogb_entities:
            # Check for retired pattern match first
            is_retired = any(pattern in entity_id.lower() for pattern in retired_patterns)

            # Remove entities bound to deleted config entries (true orphan)
            config_entry_id = getattr(entry, "config_entry_id", None)
            has_invalid_entry = bool(config_entry_id) and config_entry_id not in active_entry_ids

            # Remove if retired pattern OR orphaned config-entry binding
            should_remove = is_retired or has_invalid_entry
            
            if should_remove:
                try:
                    entity_reg.async_remove(entity_id)
                    removed_count += 1
                    reason = "retired pattern" if is_retired else "orphaned config entry"
                    _LOGGER.warning(f"🧹 Removed orphaned entity ({reason}): {entity_id}")
                except Exception as e:
                    _LOGGER.debug(f"Could not remove {entity_id}: {e}")

        # Remove leftover legacy strainname devices if they no longer have entities.
        removed_devices = 0
        for device in list(device_reg.devices.values()):
            identifiers = getattr(device, "identifiers", set()) or set()
            identifier_strings = [str(value).lower() for _, value in identifiers]
            device_name = str(getattr(device, "name", "") or "")

            is_legacy_strain_device = (
                "device for ogb_strainname_" in device_name.lower()
                or any("ogb_strainname_" in value for value in identifier_strings)
            )

            if not is_legacy_strain_device:
                continue

            linked_entities = er.async_entries_for_device(entity_reg, device.id)
            if linked_entities:
                continue

            try:
                device_reg.async_remove_device(device.id)
                removed_devices += 1
                _LOGGER.warning("🧹 Removed orphaned legacy device: %s", device_name or device.id)
            except Exception as e:
                _LOGGER.debug("Could not remove orphaned device %s: %s", device.id, e)
        
        if removed_count > 0:
            _LOGGER.info(f"✅ Cleaned up {removed_count} orphaned OpenGrowBox entities")
        else:
            _LOGGER.debug("No orphaned entities found")

        if removed_devices > 0:
            _LOGGER.info("✅ Cleaned up %s orphaned OpenGrowBox devices", removed_devices)
            
    except Exception as e:
        _LOGGER.debug(f"Entity cleanup skipped: {e}")


async def _ensure_ambient_room_entry(hass: HomeAssistant) -> bool:
    """Create ambient room via config-flow if missing."""
    await _ensure_area_exists(hass, "ambient")

    existing_entries = hass.config_entries.async_entries(DOMAIN)
    has_ambient = any(
        str(entry.data.get("room_name", "")).strip().lower() == "ambient"
        for entry in existing_entries
    )
    if has_ambient:
        await _ensure_area_exists(hass, "ambient")
        return True

    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={"room_name": "ambient"},
        )

        result_type = result.get("type") if isinstance(result, dict) else None
        if result_type == "form" and result.get("flow_id"):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"room_name": "ambient"},
            )
            result_type = result.get("type") if isinstance(result, dict) else None

        if result_type in {"create_entry", "abort"}:
            _LOGGER.warning("🌿 Auto-created ambient room via user flow: %s", result_type)
            await _ensure_area_exists(hass, "ambient")
            return True

        _LOGGER.error("Failed to auto-create ambient room, unexpected flow result: %s", result_type)
        return False
    except Exception as err:
        _LOGGER.error("Failed to auto-create ambient room: %s", err)
        return False


async def _ensure_area_exists(hass: HomeAssistant, room_name: str) -> str | None:
    """Ensure a Home Assistant area exists for the room name."""
    try:
        from homeassistant.helpers import area_registry as ar

        area_reg = ar.async_get(hass)
        normalized = str(room_name or "").strip()
        if not normalized:
            return None

        for area in area_reg.async_list_areas():
            if str(area.name).strip().lower() == normalized.lower():
                return area.id

        created = area_reg.async_create(normalized)
        _LOGGER.warning("📍 Created HA area for room: %s", normalized)
        return created.id
    except Exception as err:
        _LOGGER.debug("Could not ensure area for room %s: %s", room_name, err)
        return None


async def _ensure_room_area_and_assign_hub(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Create area for room and attach OGB room hub device to it."""
    room_name = str(config_entry.data.get("room_name", "")).strip()
    if not room_name:
        return

    area_id = await _ensure_area_exists(hass, room_name)
    if not area_id:
        return

    try:
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import area_registry as ar

        device_reg = dr.async_get(hass)
        area_reg = ar.async_get(hass)
        entry_devices = dr.async_entries_for_config_entry(device_reg, config_entry.entry_id)

        room_slug = room_name.lower().replace(" ", "_")
        room_identifier = (DOMAIN, f"room_{room_slug}")

        # Ensure ambient area exists
        ambient_area_id = None
        try:
            ambient_area = area_reg.async_get_area_id("ambient")
        except:
            ambient_area_id = None

        if not ambient_area_id:
            try:
                ambient_area = area_reg.async_create("ambient")
                ambient_area_id = ambient_area.id
                _LOGGER.info(f"Created 'ambient' area with ID: {ambient_area_id}")
            except Exception as e:
                _LOGGER.error(f"Failed to create 'ambient' area: {e}")
                return

        for device in entry_devices:
            identifiers = getattr(device, "identifiers", set()) or set()
            device_name = device.name or device.id
            
            # Check if this is the token or room selector (global devices)
            is_token = (DOMAIN, "global_hub") in identifiers
            is_room_selector = (DOMAIN, "room_selector") in identifiers
            
            if is_token or is_room_selector:
                # Force assign token and room selector to ambient area
                if device.area_id != ambient_area_id:
                    device_reg.async_update_device(device.id, area_id=ambient_area_id)
                    _LOGGER.warning(f"🌐 Assigned global device '{device_name}' to 'ambient' area (was: {device.area_id})")
            elif room_identifier in identifiers and device.area_id != area_id:
                # Regular room device - assign to room area
                device_reg.async_update_device(device.id, area_id=area_id)
                _LOGGER.warning("📍 Assigned OGB room hub '%s' to area '%s'", device_name, room_name)
    except Exception as err:
        _LOGGER.debug("Could not assign room hub to area for %s: %s", room_name, err)


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
