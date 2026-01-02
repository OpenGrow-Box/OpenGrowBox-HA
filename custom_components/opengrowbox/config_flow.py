import re
import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN


class IntegrationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the integration."""

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            room_name = user_input.get("room_name", "")
            
            # Validate room name format
            if not re.match(r"^[a-zA-Z0-9_-]+$", room_name):
                errors["room_name"] = "invalid_room_name"
            elif len(room_name) < 1 or len(room_name) > 50:
                errors["room_name"] = "invalid_length"
            else:
                return self.async_create_entry(
                    title=room_name, data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("room_name"): str,
                }
            ),
            errors=errors,
        )
