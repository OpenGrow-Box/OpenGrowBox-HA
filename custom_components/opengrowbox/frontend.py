"""Starting setup task: Frontend."""

from __future__ import annotations

import filecmp
import logging
import os
import shutil
from typing import TYPE_CHECKING

from homeassistant.components.frontend import (
    add_extra_js_url,
    async_register_built_in_panel,
)

from .const import DOMAIN, FRONTEND_EXTRA_MODULE_URL, URL_BASE
from .OGBController.utils.workarounds import async_register_static_path

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .base import HacsBase

_LOGGER = logging.getLogger(__name__)
_LOVELACE_RESOURCE_URL = FRONTEND_EXTRA_MODULE_URL
_LOVELACE_RESOURCE_TYPE = "module"


async def async_register_frontend(hass: HomeAssistant) -> None:
    static_path = os.path.join(
        hass.config.path("custom_components"), "opengrowbox", "frontend", "static"
    )

    if not os.path.exists(static_path):
        _LOGGER.error("Static path not found: %s", static_path)
        return

    await async_register_static_path(
        hass, f"{URL_BASE}/static", static_path, cache_headers=False
    )

    icon_js_src = os.path.join(
        hass.config.path("custom_components"), "opengrowbox", "frontend", "ogb_icons.js"
    )
    www_opengrowbox_path = os.path.join(hass.config.path("www"), "opengrowbox")
    icon_js_dest = os.path.join(www_opengrowbox_path, "ogb_icons.js")

    if os.path.exists(icon_js_src):
        _copy_frontend_asset(icon_js_src, icon_js_dest, "ogb_icons.js")

    if os.path.exists(icon_js_dest):
        _register_global_frontend_module(hass, _LOVELACE_RESOURCE_URL)
        await _async_register_lovelace_resource(hass, _LOVELACE_RESOURCE_URL)

    png_src = os.path.join(
        hass.config.path("custom_components"), "opengrowbox", "frontend", "ogb_tree.png"
    )
    png_dest = os.path.join(www_opengrowbox_path, "ogb_tree.png")

    if os.path.exists(png_src):
        _copy_frontend_asset(png_src, png_dest, "ogb_tree.png")

    sidebar_icon = "ogb:tree"

    if "ogb-gui" not in hass.data.get("frontend_panels", {}):
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="OpenGrowBox",
            sidebar_icon=sidebar_icon,
            frontend_url_path="ogb-gui",
            config_panel_domain=DOMAIN,
            config={
                "_panel_custom": {
                    "name": "ogb-gui",
                    "mode": "shadow-dom",
                    "embed_iframe": False,
                    "trust_external": False,
                    "js_url": f"{URL_BASE}/static/static/js/main.js",
                }
            },
            require_admin=False,
        )
        _LOGGER.debug(f"Custom panel registered with icon: {sidebar_icon}")
    else:
        _LOGGER.debug("Custom panel already registered.")


def _copy_frontend_asset(source: str, destination: str, name: str) -> None:
    """Copy a frontend asset into /config/www when missing or outdated."""
    try:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if os.path.exists(destination) and filecmp.cmp(
            source,
            destination,
            shallow=False,
        ):
            _LOGGER.debug("%s already exists at %s", name, destination)
            return

        shutil.copy2(source, destination)
        _LOGGER.debug("Copied %s to %s", name, destination)
    except Exception as err:
        _LOGGER.warning("Could not copy %s to www: %s", name, err)


def _register_global_frontend_module(
    hass: HomeAssistant,
    module_url: str,
) -> None:
    """Register the icon module with Home Assistant's global frontend loader."""
    try:
        add_extra_js_url(hass, module_url)
        _LOGGER.debug("Registered OpenGrowBox global frontend module: %s", module_url)
    except Exception as err:
        _LOGGER.debug("Could not register OpenGrowBox global frontend module: %s", err)


async def _async_register_lovelace_resource(
    hass: HomeAssistant,
    resource_url: str,
) -> None:
    """Register OpenGrowBox frontend assets as Lovelace resources in storage mode."""
    try:
        resources = _get_lovelace_resources(hass)
        if resources is None:
            _LOGGER.debug("Lovelace resources are not available; skipping OGB resource setup")
            return

        if not getattr(resources, "loaded", False):
            await resources.async_load()
            if hasattr(resources, "loaded"):
                resources.loaded = True

        resource_base = _resource_base_url(resource_url)
        for item in resources.async_items():
            if _resource_base_url(str(item.get("url", ""))) != resource_base:
                continue

            item_type = item.get("res_type", item.get("type"))
            if item_type != _LOVELACE_RESOURCE_TYPE and item.get("id") and getattr(
                resources,
                "async_update_item",
                None,
            ):
                await resources.async_update_item(
                    item["id"],
                    {"res_type": _LOVELACE_RESOURCE_TYPE, "url": resource_url},
                )
                _LOGGER.debug("Updated OpenGrowBox Lovelace resource: %s", resource_url)
            else:
                _LOGGER.debug("OpenGrowBox Lovelace resource already exists: %s", item["url"])
            return

        if not getattr(resources, "async_create_item", None):
            _LOGGER.debug(
                "Lovelace resource storage is not writable; add %s manually if needed",
                resource_url,
            )
            return

        await resources.async_create_item(
            {"res_type": _LOVELACE_RESOURCE_TYPE, "url": resource_url}
        )
        _LOGGER.debug("Registered OpenGrowBox Lovelace resource: %s", resource_url)
    except Exception as err:
        _LOGGER.debug("Could not register OpenGrowBox Lovelace resource: %s", err)


def _resource_base_url(resource_url: str) -> str:
    """Return resource URL without cache-busting query string."""
    return resource_url.split("?", 1)[0]


def _get_lovelace_resources(hass: HomeAssistant):
    """Return the Lovelace resource collection across HA storage shapes."""
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        return None
    if isinstance(lovelace_data, dict):
        return lovelace_data.get("resources")
    return getattr(lovelace_data, "resources", None)
