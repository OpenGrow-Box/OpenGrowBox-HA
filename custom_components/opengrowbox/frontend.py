"""Starting setup task: Frontend."""

from __future__ import annotations

import os
import logging
from typing import TYPE_CHECKING

from homeassistant.components.frontend import (
    add_extra_js_url,
    async_register_built_in_panel,
)

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

    if "ogb-gui" not in hass.data.get("frontend_panels", {}):
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="OpenGrowBox",
            sidebar_icon="mdi:cannabis",
            frontend_url_path="ogb-gui",
            config_panel_domain=DOMAIN,
            config={
                "_panel_custom": {
                    "name": "ogb-gui",
                    "mode":"shadow-dom",
                    "embed_iframe": False,
                    "trust_external": False,
                    "js_url": f"{URL_BASE}/static/static/js/main.js",
                }
            },
            require_admin=False,
        )
        _LOGGER.info("Custom panel registered successfully.")
    else:
        _LOGGER.debug("Custom panel already registered.")