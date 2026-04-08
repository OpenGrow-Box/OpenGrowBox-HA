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
                _LOGGER.info(f"Copied ogb_icons.js to {icon_js_dest}")
            else:
                _LOGGER.debug(f"ogb_icons.js already exists in {icon_js_dest_dir}")
        except Exception as e:
            _LOGGER.warning(f"Could not copy ogb_icons.js to www: {e}")

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
                _LOGGER.info(f"Copied ogb_tree.png to {png_dest}")
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
        _LOGGER.info(f"Custom panel registered with icon: {sidebar_icon}")
    else:
        _LOGGER.debug("Custom panel already registered.")
