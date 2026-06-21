import logging
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_AUTO_CONFIGURE_HA, DEFAULT_AUTO_CONFIGURE_HA, DOMAIN
from .ha_config_status import (
    HAConfigStatus,
    apply_runtime_ha_config_status,
    format_ha_config_status_message,
    get_ha_config_status,
    history_component_loaded,
)

_LOGGER = logging.getLogger(__name__)


async def _async_get_configuration_status_message(hass) -> str:
    """Return current configuration.yaml status for config-flow descriptions."""
    status = await _async_get_configuration_status(hass)
    return format_ha_config_status_message(status)


async def _async_get_configuration_status(hass):
    """Return current configuration.yaml status."""
    if hass is None:
        return HAConfigStatus(
            path="configuration.yaml",
            missing=(
                "logger:",
                "logger.default: info",
                "logger.logs.*: debug",
                "history:",
            ),
            error="OpenGrowBox could not check configuration.yaml from this setup flow",
        )

    config_path = hass.config.path("configuration.yaml")
    status = await hass.async_add_executor_job(get_ha_config_status, config_path)
    return apply_runtime_ha_config_status(
        status,
        history_loaded=history_component_loaded(hass),
    )


class IntegrationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the integration."""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return IntegrationOptionsFlow()

    @staticmethod
    def _normalize_room_name(room_name: str) -> str:
        return str(room_name or "").strip()

    @staticmethod
    def _is_valid_room_name(room_name: str) -> bool:
        if not re.match(r"^[a-zA-Z0-9_-]+$", room_name):
            return False
        return 1 <= len(room_name) <= 50

    def _has_existing_entries(self) -> bool:
        """Check if any OpenGrowBox entries already exist."""
        # Get the hass instance from the flow context
        if hasattr(self, "hass") and self.hass:
            entries = self.hass.config_entries.async_entries(DOMAIN)
            return len(entries) > 0
        return False

    @staticmethod
    def _as_bool(value) -> bool:
        """Return a bool for config-flow data with conservative string handling."""
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    async def _async_create_room_entry(
        self,
        room_name: str,
        auto_configure_ha: bool = DEFAULT_AUTO_CONFIGURE_HA,
    ):
        normalized = self._normalize_room_name(room_name)
        room_lower = normalized.lower()

        await self.async_set_unique_id(f"{DOMAIN}_{room_lower}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=normalized,
            data={"room_name": normalized},
            options={CONF_AUTO_CONFIGURE_HA: bool(auto_configure_ha)},
        )

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        auto_configure_default = DEFAULT_AUTO_CONFIGURE_HA

        if user_input is not None:
            room_name = self._normalize_room_name(user_input.get("room_name", ""))
            auto_configure_ha = self._as_bool(
                user_input.get(CONF_AUTO_CONFIGURE_HA, DEFAULT_AUTO_CONFIGURE_HA)
            )
            auto_configure_default = auto_configure_ha
            config_status = await _async_get_configuration_status(
                getattr(self, "hass", None)
            )

            # Validate room name format
            if not re.match(r"^[a-zA-Z0-9_-]+$", room_name):
                errors["room_name"] = "invalid_room_name"
            elif len(room_name) < 1 or len(room_name) > 50:
                errors["room_name"] = "invalid_length"
            elif not config_status.is_complete and not auto_configure_ha:
                errors["base"] = "required_ha_config_missing"
            else:
                # CRITICAL: Ensure ambient exists FIRST on fresh install
                # Check if this is the first room being created
                existing_entries = []
                if hasattr(self, "hass") and self.hass:
                    existing_entries = self.hass.config_entries.async_entries(DOMAIN)

                has_ambient = any(
                    str(entry.data.get("room_name", "")).strip().lower() == "ambient"
                    for entry in existing_entries
                )

                # Only create ambient automatically on first setup (no existing entries)
                # Skip if user explicitly names their room "ambient"
                if not existing_entries and room_name.lower() != "ambient" and not has_ambient:
                    _LOGGER.debug("First-time setup: Creating 'ambient' room first")
                    await self._async_create_room_entry("ambient")
                    _LOGGER.debug("Ambient room created, now creating user room: %s", room_name)
                
                return await self._async_create_room_entry(room_name, auto_configure_ha)

        configuration_status = await _async_get_configuration_status_message(
            getattr(self, "hass", None)
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("room_name"): str,
                    vol.Optional(
                        CONF_AUTO_CONFIGURE_HA,
                        default=auto_configure_default,
                    ): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "configuration_status": configuration_status,
            },
        )

    async def async_step_import(self, import_data=None):
        """Allow programmatic room creation via import source."""
        import_data = import_data or {}
        room_name = self._normalize_room_name(import_data.get("room_name", ""))
        auto_configure_ha = self._as_bool(
            import_data.get(CONF_AUTO_CONFIGURE_HA, DEFAULT_AUTO_CONFIGURE_HA)
        )

        if not self._is_valid_room_name(room_name):
            return self.async_abort(reason="invalid_room_name")

        config_status = await _async_get_configuration_status(getattr(self, "hass", None))
        if not config_status.is_complete and not auto_configure_ha:
            return self.async_abort(reason="required_ha_config_missing")

        # Same logic for import: ensure ambient first if this is first room
        existing_entries = []
        if hasattr(self, "hass") and self.hass:
            existing_entries = self.hass.config_entries.async_entries(DOMAIN)

        has_ambient = any(
            str(entry.data.get("room_name", "")).strip().lower() == "ambient"
            for entry in existing_entries
        )

        if not existing_entries and room_name.lower() != "ambient" and not has_ambient:
            _LOGGER.debug("Import setup (first room): Creating 'ambient' room first")
            await self._async_create_room_entry("ambient")

        return await self._async_create_room_entry(room_name, auto_configure_ha)


class IntegrationOptionsFlow(config_entries.OptionsFlow):
    """Handle OpenGrowBox options."""

    def _current_auto_configure_ha(self) -> bool:
        """Return the stored automatic HA configuration preference."""
        return bool(
            self.config_entry.options.get(
                CONF_AUTO_CONFIGURE_HA,
                self.config_entry.data.get(
                    CONF_AUTO_CONFIGURE_HA,
                    DEFAULT_AUTO_CONFIGURE_HA,
                ),
            )
        )

    async def async_step_init(self, user_input=None):
        """Manage OpenGrowBox options."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_AUTO_CONFIGURE_HA: bool(
                        user_input.get(
                            CONF_AUTO_CONFIGURE_HA,
                            DEFAULT_AUTO_CONFIGURE_HA,
                        )
                    ),
                },
            )

        configuration_status = await _async_get_configuration_status_message(
            getattr(self, "hass", None)
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_AUTO_CONFIGURE_HA,
                        default=self._current_auto_configure_ha(),
                    ): bool,
                }
            ),
            description_placeholders={
                "configuration_status": configuration_status,
            },
        )
