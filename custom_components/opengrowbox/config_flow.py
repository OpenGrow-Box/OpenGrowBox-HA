import re
import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN


class IntegrationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the integration."""

    @staticmethod
    def _normalize_room_name(room_name: str) -> str:
        return str(room_name or "").strip()

    @staticmethod
    def _is_valid_room_name(room_name: str) -> bool:
        if not re.match(r"^[a-zA-Z0-9_-]+$", room_name):
            return False
        return 1 <= len(room_name) <= 50

    async def _async_create_room_entry(self, room_name: str):
        normalized = self._normalize_room_name(room_name)
        room_lower = normalized.lower()

        await self.async_set_unique_id(f"{DOMAIN}_{room_lower}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=normalized,
            data={"room_name": normalized},
        )

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            room_name = self._normalize_room_name(user_input.get("room_name", ""))
            
            # Validate room name format
            if not re.match(r"^[a-zA-Z0-9_-]+$", room_name):
                errors["room_name"] = "invalid_room_name"
            elif len(room_name) < 1 or len(room_name) > 50:
                errors["room_name"] = "invalid_length"
            else:
                return await self._async_create_room_entry(room_name)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("room_name"): str,
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data=None):
        """Allow programmatic room creation via import source."""
        room_name = self._normalize_room_name((import_data or {}).get("room_name", ""))

        if not self._is_valid_room_name(room_name):
            return self.async_abort(reason="invalid_room_name")

        return await self._async_create_room_entry(room_name)
