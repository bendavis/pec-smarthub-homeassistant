import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ElectricUsageAPI, InvalidAuth, CannotConnect
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

class PECSmartHubConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PEC SmartHub."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = ElectricUsageAPI(
                session,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD]
            )

            try:
                # Attempt to authenticate
                await api.login()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during config validation: %s", err)
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors
        )
