from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN

class IntegrationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the integration."""

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input["room_name"], data=user_input
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("room_name"): vol.All(
                        str,
                        vol.Length(min=1, max=50),
                        vol.Match(r'^[a-zA-Z0-9_-]+$', msg="Room name can only contain letters, numbers, hyphens, and underscores")
                    ),
                }
            ),
        )
