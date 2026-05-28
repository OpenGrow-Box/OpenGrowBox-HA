"""Starting setup task: Frontend."""

from __future__ import annotations

import logging
import os
import shutil
from typing import TYPE_CHECKING

from homeassistant.components.frontend import async_register_built_in_panel

from .const import DOMAIN, URL_BASE
from .OGBController.utils.workarounds import async_register_static_path

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .base import HacsBase

_LOGGER = logging.getLogger(__name__)
_LOVELACE_RESOURCE_URL = "/local/opengrowbox/ogb_icons.js"
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
    www_path = hass.config.path("www")
    icon_js_dest_dir = os.path.join(www_path, "opengrowbox")
    icon_js_dest = os.path.join(icon_js_dest_dir, "ogb_icons.js")

    if os.path.exists(icon_js_src):
        try:
            os.makedirs(icon_js_dest_dir, exist_ok=True)
            if not os.path.exists(icon_js_dest):
                shutil.copy(icon_js_src, icon_js_dest)
                _LOGGER.debug(f"Copied ogb_icons.js to {icon_js_dest}")
            else:
                _LOGGER.debug(f"ogb_icons.js already exists in {icon_js_dest_dir}")
        except Exception as e:
            _LOGGER.warning(f"Could not copy ogb_icons.js to www: {e}")

    if os.path.exists(icon_js_dest):
        await _async_register_lovelace_resource(hass, _LOVELACE_RESOURCE_URL)

    png_src = os.path.join(
        hass.config.path("custom_components"), "opengrowbox", "frontend", "ogb_tree.png"
    )
    png_dest_dir = os.path.join(www_path, "opengrowbox")
    png_dest = os.path.join(png_dest_dir, "ogb_tree.png")

    if os.path.exists(png_src):
        try:
            os.makedirs(png_dest_dir, exist_ok=True)
            if not os.path.exists(png_dest):
                shutil.copy(png_src, png_dest)
                _LOGGER.debug(f"Copied ogb_tree.png to {png_dest}")
            else:
                _LOGGER.debug(f"ogb_tree.png already exists in {png_dest_dir}")
        except Exception as e:
            _LOGGER.warning(f"Could not copy ogb_tree.png to www: {e}")

    sidebar_icon = "custom:ogb_tree"

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
