"""Self-update entity for OpenGrowBox.

Reports and installs new OpenGrowBox releases directly from GitHub, so the
integration keeps working for users after it can no longer be distributed
through HACS (OGBCL license conflict). Installation always requires an
explicit user action (or automation calling `update.install`) - the entity
never installs on its own.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from datetime import timedelta

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ENABLE_AUTO_UPDATE,
    DEFAULT_ENABLE_AUTO_UPDATE,
    DOMAIN,
    GITHUB_RELEASES_LATEST_API,
    GITHUB_RELEASES_TAG_API,
    RELEASE_ASSET_NAME,
    VERSION,
)
from .naming import global_device_info

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=6)

_NOTIFICATION_ID = "opengrowbox_update_installed"


class OGBUpdateEntity(UpdateEntity):
    """Update entity that checks and installs OpenGrowBox GitHub releases."""

    _attr_has_entity_name = False
    _attr_name = "OpenGrowBox Update"
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        self.hass = hass
        self._config_entry = config_entry
        self._attr_unique_id = f"{DOMAIN}_update"
        self.entity_id = "update.opengrowbox_update"
        self._attr_installed_version = VERSION
        self._attr_latest_version = VERSION
        self._release_notes: str | None = None

    @property
    def device_info(self):
        """Link this entity to the OpenGrowBox hub device."""
        return global_device_info("OpenGrowBox Integration")

    def _auto_update_enabled(self) -> bool:
        return bool(
            self._config_entry.options.get(
                CONF_ENABLE_AUTO_UPDATE,
                self._config_entry.data.get(
                    CONF_ENABLE_AUTO_UPDATE, DEFAULT_ENABLE_AUTO_UPDATE
                ),
            )
        )

    async def async_update(self) -> None:
        """Poll GitHub for the latest published release."""
        if not self._auto_update_enabled():
            return

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(GITHUB_RELEASES_LATEST_API, timeout=15) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "OpenGrowBox release check failed: HTTP %s", response.status
                    )
                    return
                data = await response.json()
        except Exception as err:  # noqa: BLE001 - network hiccups shouldn't break polling
            _LOGGER.debug("OpenGrowBox release check failed: %s", err)
            return

        tag = str(data.get("tag_name") or "").lstrip("v")
        if not tag:
            return

        self._attr_latest_version = tag
        self._attr_release_url = data.get("html_url")
        summary = (data.get("body") or "").strip()
        self._release_notes = summary or None
        self._attr_release_summary = summary[:255] if summary else None

    async def async_release_notes(self) -> str | None:
        """Return the full release notes body for the HA update dialog."""
        return self._release_notes

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        """Download the release asset and install it over the running integration."""
        target_version = version or self._attr_latest_version
        session = async_get_clientsession(self.hass)

        asset_url = await self._resolve_asset_url(session, target_version)
        if not asset_url:
            raise HomeAssistantError(
                f"Release asset '{RELEASE_ASSET_NAME}' for version {target_version} "
                "was not found."
            )

        async with session.get(asset_url, timeout=60) as response:
            if response.status != 200:
                raise HomeAssistantError(
                    f"Download of {RELEASE_ASSET_NAME} failed "
                    f"(HTTP {response.status})."
                )
            payload = await response.read()

        await self.hass.async_add_executor_job(self._install_payload, payload)

        self._attr_installed_version = target_version
        self.async_write_ha_state()

        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "notification_id": _NOTIFICATION_ID,
                "title": "OpenGrowBox Update installed",
                "message": (
                    f"OpenGrowBox was updated to version {target_version}. "
                    "Please restart Home Assistant to complete the update."
                ),
            },
            blocking=False,
        )

    async def _resolve_asset_url(self, session, version: str) -> str | None:
        """Look up the download URL of the release asset for the given version."""
        url = GITHUB_RELEASES_TAG_API.format(version=version)
        try:
            async with session.get(url, timeout=15) as response:
                if response.status != 200:
                    return None
                data = await response.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Release lookup for version %s failed: %s", version, err
            )
            return None

        for asset in data.get("assets", []):
            if asset.get("name") == RELEASE_ASSET_NAME:
                return asset.get("browser_download_url")
        return None

    def _install_payload(self, payload: bytes) -> None:
        """Extract the release zip and atomically swap it into custom_components (blocking)."""
        integration_dir = os.path.dirname(os.path.abspath(__file__))
        components_dir = os.path.dirname(integration_dir)
        staging_dir = tempfile.mkdtemp(prefix="opengrowbox_update_")
        old_dir = os.path.join(components_dir, "opengrowbox.old")
        tmp_zip_path = None

        try:
            if os.path.exists(old_dir):
                shutil.rmtree(old_dir)

            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
                tmp_zip.write(payload)
                tmp_zip_path = tmp_zip.name

            with zipfile.ZipFile(tmp_zip_path) as archive:
                archive.extractall(staging_dir)

            extracted_dir = os.path.join(staging_dir, "opengrowbox")
            if not os.path.isdir(extracted_dir):
                raise HomeAssistantError(
                    "Release archive does not contain an 'opengrowbox' folder."
                )

            os.rename(integration_dir, old_dir)
            try:
                shutil.move(extracted_dir, integration_dir)
            except Exception:
                # Roll back so Home Assistant keeps running the previous version.
                os.rename(old_dir, integration_dir)
                raise

            shutil.rmtree(old_dir, ignore_errors=True)
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
            if tmp_zip_path and os.path.exists(tmp_zip_path):
                os.unlink(tmp_zip_path)


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities) -> None:
    """Set up the single, global OpenGrowBox update entity."""
    room_name = config_entry.data.get("room_name", "").lower()
    if room_name != "ambient" or "update_entity" in hass.data[DOMAIN]:
        return

    entity = OGBUpdateEntity(hass, config_entry)
    async_add_entities([entity])
    hass.data[DOMAIN]["update_entity"] = entity
