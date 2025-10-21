"""Workarounds."""

from homeassistant.core import HomeAssistant

DOMAIN_OVERRIDES = {
    "custom-components/sensor.custom_aftership": "custom_aftership"
}
try:
    from homeassistant.components.http import StaticPathConfig

    async def async_register_static_path(
        hass: HomeAssistant,
        url_path: str,
        path: str,
        cache_headers: bool = True,
    ) -> None:

        await hass.http.async_register_static_paths(
            [StaticPathConfig(url_path, path, cache_headers)]
        )
except ImportError:

    async def async_register_static_path(
        hass: HomeAssistant,
        url_path: str,
        path: str,
        cache_headers: bool = True,
    ) -> None:
        hass.http.register_static_path(url_path, path, cache_headers)
