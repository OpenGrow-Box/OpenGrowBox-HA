import asyncio
import logging
import os
import re
import shutil
from glob import glob

from homeassistant.components.frontend import async_remove_panel
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CONF_AUTO_CONFIGURE_HA, DEFAULT_AUTO_CONFIGURE_HA, DOMAIN
from .coordinator import OGBIntegrationCoordinator
from .frontend import async_register_frontend
from .ha_config_status import (
    REQUIRED_LOGGER_DEFAULT,
    REQUIRED_LOGGER_OVERRIDES,
    get_ha_config_status,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "number", "select", "time", "switch", "date", "text"]
_CONFIG_CHECK_FLAG = "__config_checked__"
_CONFIG_AUTO_UPDATE_FLAG = "__config_auto_update_done__"
_CLEANUP_DONE_FLAG = "__cleanup_done__"
_AMBIENT_ENSURE_FLAG = "__ambient_ensure_done__"
_CONFIG_NOTIFICATION_ID = "opengrowbox_required_ha_config"


def _apply_minimal_log_fallback() -> None:
    """Use minimal runtime logging if requested logger config is missing."""
    logging.getLogger("custom_components.opengrowbox").setLevel(logging.WARNING)
    logging.getLogger("custom_components.ogb-dev-env").setLevel(logging.WARNING)


def _entry_auto_configure_ha(config_entry: ConfigEntry | None) -> bool:
    """Return whether an entry allows OpenGrowBox to update configuration.yaml."""
    if config_entry is None:
        return DEFAULT_AUTO_CONFIGURE_HA

    return bool(
        config_entry.options.get(
            CONF_AUTO_CONFIGURE_HA,
            config_entry.data.get(CONF_AUTO_CONFIGURE_HA, DEFAULT_AUTO_CONFIGURE_HA),
        )
    )


def _should_auto_update_ha_config(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> bool:
    """Return true if any OpenGrowBox entry opted in to automatic YAML updates."""
    entries = list(hass.config_entries.async_entries(DOMAIN))
    if config_entry not in entries:
        entries.append(config_entry)

    return any(_entry_auto_configure_ha(entry) for entry in entries)


async def _check_required_ha_config(
    hass: HomeAssistant,
    *,
    auto_update: bool = DEFAULT_AUTO_CONFIGURE_HA,
) -> None:
    """Validate required configuration.yaml entries and optionally add missing blocks."""
    config_path = hass.config.path("configuration.yaml")

    try:
        status = await hass.async_add_executor_job(get_ha_config_status, config_path)
    except Exception as err:
        _LOGGER.warning(
            "Could not read configuration.yaml (%s). Using minimal OpenGrowBox log level.",
            err,
        )
        _apply_minimal_log_fallback()
        return

    initial_missing_reasons = list(status.missing)
    if status.error:
        initial_missing_reasons.insert(0, status.error)
    initial_missing_text = (
        ", ".join(initial_missing_reasons) or "unknown configuration issue"
    )

    if status.is_complete:
        await _dismiss_ha_config_notification(hass)
        return

    update_attempted = False
    if auto_update:
        # Add all required logger config in one go.
        try:
            update_attempted = await hass.async_add_executor_job(
                _add_required_config_to_yaml,
                config_path,
            )
            if update_attempted:
                _LOGGER.debug("Updated configuration.yaml with required OpenGrowBox config")
            else:
                _LOGGER.debug("No automatic logger config update was applied")
        except Exception as err:
            _LOGGER.warning("Could not update configuration.yaml: %s", err)

        if update_attempted:
            try:
                status = await hass.async_add_executor_job(get_ha_config_status, config_path)
            except Exception as err:
                _LOGGER.warning("Could not re-check configuration.yaml: %s", err)

    if status.is_complete:
        if update_attempted:
            await _async_notify_ha_config_status(
                hass,
                initial_missing_text,
                auto_update=auto_update,
                update_attempted=True,
            )
            return

        await _dismiss_ha_config_notification(hass)
        return

    missing_reasons = list(status.missing)
    if status.error:
        missing_reasons.insert(0, status.error)
    missing_text = ", ".join(missing_reasons) or "unknown configuration issue"

    if auto_update:
        _LOGGER.warning(
            "configuration.yaml check failed (%s). "
            "OpenGrowBox attempted to add missing logger settings; "
            "restart Home Assistant to load them. "
            "Applying minimal OpenGrowBox runtime log level (WARNING).",
            missing_text,
        )
    else:
        _LOGGER.warning(
            "configuration.yaml check failed (%s). "
            "Expected: logger settings for OpenGrowBox diagnostics. "
            "Automatic configuration.yaml updates are disabled; add the entries manually "
            "or enable the OpenGrowBox automatic Home Assistant configuration option. "
            "Applying minimal OpenGrowBox runtime log level (WARNING).",
            missing_text,
        )

    _apply_minimal_log_fallback()

    await _async_notify_ha_config_status(
        hass,
        missing_text,
        auto_update=auto_update,
        update_attempted=update_attempted,
    )


async def _dismiss_ha_config_notification(hass: HomeAssistant) -> None:
    """Dismiss the persistent YAML configuration notification if present."""
    try:
        await _ensure_persistent_notification(hass)
        if not hass.services.has_service("persistent_notification", "dismiss"):
            return
        await hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": _CONFIG_NOTIFICATION_ID},
            blocking=False,
        )
    except Exception as err:
        _LOGGER.debug("Could not dismiss OpenGrowBox config notification: %s", err)


async def _async_notify_ha_config_status(
    hass: HomeAssistant,
    missing_text: str,
    *,
    auto_update: bool,
    update_attempted: bool = False,
) -> None:
    """Create or update a persistent notification for missing HA YAML settings."""
    try:
        await _ensure_persistent_notification(hass)
        if not hass.services.has_service("persistent_notification", "create"):
            return

        if auto_update and update_attempted:
            message = (
                "OpenGrowBox updated `/config/configuration.yaml` with missing "
                f"logger settings: {missing_text}. Restart Home Assistant so the YAML changes "
                "are loaded. A backup was created at "
                "`/config/configuration.yaml.ogb_config_bak`."
            )
        elif auto_update:
            message = (
                "OpenGrowBox is allowed to update `/config/configuration.yaml`, "
                "but required settings are still missing: "
                f"{missing_text}. Check file permissions and the Home Assistant log."
            )
        else:
            message = (
                "OpenGrowBox detected missing required Home Assistant YAML "
                f"settings: {missing_text}. Add them manually, or turn on the "
                "`OGB Auto Configure HA` switch for one OpenGrowBox room so "
                "OpenGrowBox can create a backup and add the missing logger entries."
            )

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "OpenGrowBox configuration required",
                "message": message,
                "notification_id": _CONFIG_NOTIFICATION_ID,
            },
            blocking=False,
        )
    except Exception as err:
        _LOGGER.debug("Could not create OpenGrowBox config notification: %s", err)


async def _ensure_persistent_notification(hass: HomeAssistant) -> None:
    """Load persistent_notification if it is not already available."""
    if hass.services.has_service("persistent_notification", "create"):
        return

    try:
        from homeassistant.setup import async_setup_component

        await async_setup_component(hass, "persistent_notification", {})
    except Exception as err:
        _LOGGER.debug("Could not set up persistent_notification: %s", err)


def _add_required_config_to_yaml(config_path: str) -> bool:
    """Add required logger settings to configuration.yaml if missing.

    IMPORTANT: Only prepend missing blocks, do NOT overwrite existing content including
    !include directives (automations.yaml, scripts.yaml, scenes.yaml, themes, etc.)
    """
    if not os.path.exists(config_path):
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    status = get_ha_config_status(config_path)

    if status.is_complete:
        _LOGGER.debug("All required logger config already present in configuration.yaml")
        return False

    new_content = _merge_required_logger_config(content)

    if new_content == content:
        return False

    backup_path = config_path + ".ogb_config_bak"
    shutil.copy(config_path, backup_path)

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    _LOGGER.debug("Updated configuration.yaml logger config (backup at %s)", backup_path)
    return True


def _required_logger_yaml() -> str:
    """Return the required logger YAML block."""
    lines = [
        "logger:",
        f"  default: {REQUIRED_LOGGER_DEFAULT}",
        "  logs:",
        *(
            f"    {name}: {level}"
            for name, level in REQUIRED_LOGGER_OVERRIDES.items()
        ),
    ]
    return "\n".join(lines)


def _merge_required_logger_config(content: str) -> str:
    """Merge required logger settings into configuration.yaml content."""
    lines = content.splitlines()
    logger_index = _find_top_level_key(lines, "logger")

    if logger_index is None:
        if not content:
            return _required_logger_yaml() + "\n"
        ending = "" if content.endswith("\n") else "\n"
        return f"{_required_logger_yaml()}\n\n{content}{ending}"

    if ":" in lines[logger_index] and lines[logger_index].split(":", 1)[1].strip():
        _LOGGER.warning(
            "Could not auto-update logger config because logger uses an inline value: %s",
            lines[logger_index].strip(),
        )
        return content

    end_index = _find_top_level_block_end(lines, logger_index + 1)
    block = lines[logger_index:end_index]
    block = _merge_logger_block(block)
    merged = lines[:logger_index] + block + lines[end_index:]
    ending = "\n" if content.endswith("\n") else ""
    return "\n".join(merged) + ending


def _merge_logger_block(block: list[str]) -> list[str]:
    """Return logger block lines with required default and logs settings."""
    result = list(block)

    default_index = _find_child_key(result, "default")
    if default_index is None:
        result.insert(1, f"  default: {REQUIRED_LOGGER_DEFAULT}")
    else:
        result[default_index] = f"  default: {REQUIRED_LOGGER_DEFAULT}"

    logs_index = _find_child_key(result, "logs")
    if logs_index is None:
        result.append("  logs:")
        logs_index = len(result) - 1
    elif ":" in result[logs_index] and result[logs_index].split(":", 1)[1].strip():
        _LOGGER.warning(
            "Could not auto-update logger logs because logger.logs uses an inline value: %s",
            result[logs_index].strip(),
        )
        return result

    logs_end = _find_child_block_end(result, logs_index + 1, parent_indent=2)
    existing_log_lines = {
        _line_key(line): index
        for index, line in enumerate(result[logs_index + 1:logs_end], start=logs_index + 1)
        if _line_key(line)
    }

    insert_lines = []
    for name, level in REQUIRED_LOGGER_OVERRIDES.items():
        line = f"    {name}: {level}"
        if name in existing_log_lines:
            result[existing_log_lines[name]] = line
        else:
            insert_lines.append(line)

    if insert_lines:
        result[logs_end:logs_end] = insert_lines

    return result


def _find_top_level_key(lines: list[str], key: str) -> int | None:
    """Find a top-level YAML key line."""
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    for index, line in enumerate(lines):
        if pattern.match(line):
            return index
    return None


def _find_top_level_block_end(lines: list[str], start_index: int) -> int:
    """Find the end of a top-level YAML block."""
    for index in range(start_index, len(lines)):
        line = lines[index]
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            return index
    return len(lines)


def _find_child_key(lines: list[str], key: str) -> int | None:
    """Find a two-space indented child key."""
    pattern = re.compile(rf"^  {re.escape(key)}\s*:")
    for index, line in enumerate(lines):
        if pattern.match(line):
            return index
    return None


def _find_child_block_end(
    lines: list[str],
    start_index: int,
    *,
    parent_indent: int,
) -> int:
    """Find the end of a child YAML block."""
    for index in range(start_index, len(lines)):
        line = lines[index]
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            return index
    return len(lines)


def _line_key(line: str) -> str | None:
    """Extract a YAML key from a line."""
    stripped = line.strip()
    if ":" not in stripped or stripped.startswith("#"):
        return None
    return stripped.split(":", 1)[0].strip()


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the OpenGrowBox integration via the UI."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    auto_update_config = _should_auto_update_ha_config(hass, config_entry)
    needs_config_check = not hass.data[DOMAIN].get(_CONFIG_CHECK_FLAG)
    needs_auto_update = (
        auto_update_config
        and not hass.data[DOMAIN].get(_CONFIG_AUTO_UPDATE_FLAG)
    )

    if needs_config_check or needs_auto_update:
        await _check_required_ha_config(hass, auto_update=auto_update_config)
        hass.data[DOMAIN][_CONFIG_CHECK_FLAG] = True
        if auto_update_config:
            hass.data[DOMAIN][_CONFIG_AUTO_UPDATE_FLAG] = True

    # Check if this entry is already set up to prevent duplicate initialization
    if config_entry.entry_id in hass.data[DOMAIN]:
        _LOGGER.debug(
            f"Entry {config_entry.entry_id} already set up, skipping duplicate setup"
        )
        return True

    config_entry.async_on_unload(config_entry.add_update_listener(_async_options_updated))

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
    _LOGGER.debug(f"✅ Integration setup complete, waiting for sensor platform to register services")

    return True


async def _async_options_updated(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Reload an entry after options change so configuration checks rerun."""
    if DOMAIN in hass.data:
        hass.data[DOMAIN].pop(_CONFIG_CHECK_FLAG, None)
        hass.data[DOMAIN].pop(_CONFIG_AUTO_UPDATE_FLAG, None)

    await hass.config_entries.async_reload(config_entry.entry_id)


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
            "text.ogb_strainname_",     # Removed in 1.4.2 - strain now via medium
            "OGB_Feed_Nutrient_",       # Removed in 3.2 - concentration-based dosing
            "OGB_Feed_Tolerance_",      # Removed in 3.2 - concentration-based dosing
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
            _LOGGER.debug(f"✅ Cleaned up {removed_count} orphaned OpenGrowBox entities")
        else:
            _LOGGER.debug("No orphaned entities found")

        if removed_devices > 0:
            _LOGGER.debug("✅ Cleaned up %s orphaned OpenGrowBox devices", removed_devices)
            
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

        # Get room area ID
        room_slug = room_name.lower().replace(" ", "_")
        room_identifier = (DOMAIN, f"room_{room_slug}")

        # Get ambient area ID
        ambient_area_id = None
        try:
            ambient_area = area_reg.async_get_area_id("ambient")
            ambient_area_id = ambient_area.id
        except:
            ambient_area_id = None

        if not ambient_area_id:
            try:
                # Check if area already exists by name
                existing = None
                for area in area_reg.areas.values():
                    if area.name.lower() == "ambient":
                        existing = area
                        break
                if existing:
                    ambient_area_id = existing.id
                    _LOGGER.debug(f"Found existing 'ambient' area with ID: {ambient_area_id}")
                else:
                    ambient_area = area_reg.async_create("ambient")
                    ambient_area_id = ambient_area.id
                    _LOGGER.debug(f"Created 'ambient' area with ID: {ambient_area_id}")
            except Exception as e:
                _LOGGER.warning(f"Could not create/get 'ambient' area: {e}")
                # Don't return - try to continue with room assignment anyway

        # Small delay to ensure devices are fully registered in the device registry
        await asyncio.sleep(0.3)

        # CRITICAL: Get ALL OGB devices from entire registry, not just current config_entry
        # This ensures global devices (global_hub, room_selector) get assigned to ambient
        # even when they were created as part of a room's config_entry
        all_ogb_devices = []
        for device in device_reg.devices.values():
            identifiers = getattr(device, "identifiers", set()) or set()
            if any(ident[0] == DOMAIN for ident in identifiers):
                all_ogb_devices.append(device)

        # Get devices for current config entry
        entry_devices = dr.async_entries_for_config_entry(device_reg, config_entry.entry_id)

        for device in all_ogb_devices:
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
            elif room_identifier in identifiers:
                # Regular room device - assign to room area (only if this is the room's own config_entry)
                if device in entry_devices and device.area_id != area_id:
                    device_reg.async_update_device(device.id, area_id=area_id)
                    _LOGGER.warning("📍 Assigned OGB room hub '%s' to area '%s'", device_name, room_name)
    except Exception as err:
        _LOGGER.debug("Could not assign room hub to area for %s: %s", room_name, err)


async def _ensure_global_devices_in_ambient(hass: HomeAssistant) -> None:
    """Ensure all global OGB devices (token, room_selector) are in ambient area."""
    try:
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import area_registry as ar

        device_reg = dr.async_get(hass)
        area_reg = ar.async_get(hass)

        # Get or create ambient area
        ambient_area_id = None
        try:
            ambient_area = area_reg.async_get_area_id("ambient")
            ambient_area_id = ambient_area.id
        except:
            # Area doesn't exist, try to create it
            try:
                existing = None
                for area in area_reg.areas.values():
                    if area.name.lower() == "ambient":
                        existing = area
                        break
                if existing:
                    ambient_area_id = existing.id
                else:
                    created = area_reg.async_create("ambient")
                    ambient_area_id = created.id
                    _LOGGER.debug("Created ambient area in _ensure_global_devices_in_ambient")
            except Exception as create_err:
                _LOGGER.warning(f"Could not create ambient area: {create_err}")

        if not ambient_area_id:
            _LOGGER.warning("No ambient area ID available, skipping global device assignment")
            return

        # Get ALL OGB devices with detailed logging
        _LOGGER.debug(f"Searching for global OGB devices in device registry...")
        ogb_devices_updated = 0
        for device in device_reg.devices.values():
            identifiers = getattr(device, "identifiers", set()) or set()
            
            is_token = (DOMAIN, "global_hub") in identifiers
            is_room_selector = (DOMAIN, "room_selector") in identifiers
            
            if is_token or is_room_selector:
                device_name = device.name or device.id
                current_area = device.area_id or "none"
                _LOGGER.debug(f"Found global device: {device_name}, current area: {current_area}, identifiers: {identifiers}")
                
                if device.area_id != ambient_area_id:
                    device_reg.async_update_device(device.id, area_id=ambient_area_id)
                    ogb_devices_updated += 1
                    _LOGGER.warning(f"🌐 Re-assigned global device '{device_name}' to 'ambient' (was: {current_area})")

        _LOGGER.debug(f"✅ Global device assignment complete: {ogb_devices_updated} device(s) assigned to ambient")
    except Exception as err:
        _LOGGER.error(f"CRITICAL: Could not ensure global devices in ambient: {err}")
    except Exception as err:
        _LOGGER.warning(f"Could not ensure global devices in ambient: {err}")


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload the OpenGrowBox config entry."""
    _LOGGER.debug(f"🛑 Unloading OpenGrowBox integration for entry {config_entry.entry_id}")
    
    # Get coordinator before unloading platforms
    coordinator = hass.data[DOMAIN].get(config_entry.entry_id)
    
    # CRITICAL: Shutdown coordinator and all its tasks FIRST
    # This prevents orphaned tasks that can crash HA
    if coordinator:
        try:
            _LOGGER.debug(f"🛑 Shutting down coordinator for {coordinator.room_name}")
            await coordinator.async_shutdown()
            _LOGGER.debug(f"✅ Coordinator shutdown complete for {coordinator.room_name}")
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
        
        _LOGGER.debug(f"✅ OpenGrowBox integration unloaded successfully")
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
