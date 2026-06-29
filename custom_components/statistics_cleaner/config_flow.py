"""Config flow for Statistics Cleaner."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_DB_PATH,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_THRESHOLD,
    CONF_WINDOW_HOURS,
    DEFAULT_DB_PATH,
    DEFAULT_NAME,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_HOURS,
    DOMAIN,
)


class StatisticsCleanerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Statistics Cleaner."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_ENTITY_ID]}::{user_input[CONF_NAME]}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_ENTITY_ID: user_input[CONF_ENTITY_ID],
                    CONF_DB_PATH: user_input[CONF_DB_PATH],
                    CONF_THRESHOLD: user_input[CONF_THRESHOLD],
                    CONF_WINDOW_HOURS: user_input[CONF_WINDOW_HOURS],
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                    vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(CONF_DB_PATH, default=DEFAULT_DB_PATH): str,
                    vol.Required(CONF_THRESHOLD, default=DEFAULT_THRESHOLD): vol.Coerce(float),
                    vol.Required(
                        CONF_WINDOW_HOURS, default=DEFAULT_WINDOW_HOURS
                    ): vol.Coerce(int),
                }
            ),
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return StatisticsCleanerOptionsFlow(config_entry)


class StatisticsCleanerOptionsFlow(config_entries.OptionsFlow):
    """Handle Statistics Cleaner options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DB_PATH, default=data.get(CONF_DB_PATH, DEFAULT_DB_PATH)): str,
                    vol.Required(
                        CONF_THRESHOLD,
                        default=data.get(CONF_THRESHOLD, DEFAULT_THRESHOLD),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_WINDOW_HOURS,
                        default=data.get(CONF_WINDOW_HOURS, DEFAULT_WINDOW_HOURS),
                    ): vol.Coerce(int),
                }
            ),
        )
