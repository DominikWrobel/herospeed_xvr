"""Config flow for Herospeed XVR integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import (
    DOMAIN, 
    CONF_HOST, 
    CONF_PORT, 
    CONF_NUM_SENSORS, 
    CONF_MOTION_RESET_DELAY,
    CONF_SMTP_USERNAME,
    CONF_SMTP_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_MOTION_RESET_DELAY,
    DEFAULT_SMTP_USERNAME,
    DEFAULT_SMTP_PASSWORD
)

class HeropspeedXVRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Herospeed XVR."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            return self.async_create_entry(
                title=f"Herospeed XVR ({user_input[CONF_HOST]})",
                data=user_input
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_NUM_SENSORS): int,
                vol.Required(CONF_MOTION_RESET_DELAY, default=DEFAULT_MOTION_RESET_DELAY): int,
                vol.Required(CONF_SMTP_USERNAME, default=DEFAULT_SMTP_USERNAME): str,
                vol.Required(CONF_SMTP_PASSWORD, default=DEFAULT_SMTP_PASSWORD): str,
            }),
            errors=errors,
        )
