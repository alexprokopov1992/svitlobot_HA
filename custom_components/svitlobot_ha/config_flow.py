from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_VOLTAGE_ENTITY_ID,
    CONF_SVITLOBOT_CHANNEL_KEY,
    DEFAULT_SVITLOBOT_CHANNEL_KEY,
    CONF_DEBOUNCE_SECONDS,
    DEFAULT_DEBOUNCE_SECONDS,
    CONF_STALE_TIMEOUT_SECONDS,
    DEFAULT_STALE_TIMEOUT_SECONDS,
    CONF_REFRESH_SECONDS,
    DEFAULT_REFRESH_SECONDS,
)


class PowerWatchdogConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id(
                f"svitlobot_ha::{user_input[CONF_VOLTAGE_ENTITY_ID]}"
            )
            self._abort_if_unique_id_configured()
            title = f"Svitlobot: {user_input[CONF_VOLTAGE_ENTITY_ID]}"
            return self.async_create_entry(title=title, data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_VOLTAGE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig()
                ),

                vol.Optional(
                    CONF_SVITLOBOT_CHANNEL_KEY,
                    default=DEFAULT_SVITLOBOT_CHANNEL_KEY
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),

                vol.Optional(
                    CONF_DEBOUNCE_SECONDS,
                    default=DEFAULT_DEBOUNCE_SECONDS
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=120, step=1,
                        mode=selector.NumberSelectorMode.BOX
                    )
                ),

                vol.Optional(
                    CONF_STALE_TIMEOUT_SECONDS,
                    default=DEFAULT_STALE_TIMEOUT_SECONDS
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=3600, step=10,
                        mode=selector.NumberSelectorMode.BOX
                    )
                ),

                vol.Optional(
                    CONF_REFRESH_SECONDS,
                    default=DEFAULT_REFRESH_SECONDS
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=600, step=5,
                        mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return PowerWatchdogOptionsFlow(config_entry)


class PowerWatchdogOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    @property
    def config_entry(self) -> config_entries.ConfigEntry:
        return self._config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        def _get(key, default):
            return self.config_entry.options.get(key, self.config_entry.data.get(key, default))

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SVITLOBOT_CHANNEL_KEY,
                    default=str(_get(CONF_SVITLOBOT_CHANNEL_KEY, DEFAULT_SVITLOBOT_CHANNEL_KEY))
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),

                vol.Optional(
                    CONF_DEBOUNCE_SECONDS,
                    default=int(_get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=120, step=1,
                        mode=selector.NumberSelectorMode.BOX
                    )
                ),

                vol.Optional(
                    CONF_STALE_TIMEOUT_SECONDS,
                    default=int(_get(CONF_STALE_TIMEOUT_SECONDS, DEFAULT_STALE_TIMEOUT_SECONDS))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=3600, step=10,
                        mode=selector.NumberSelectorMode.BOX
                    )
                ),

                vol.Optional(
                    CONF_REFRESH_SECONDS,
                    default=int(_get(CONF_REFRESH_SECONDS, DEFAULT_REFRESH_SECONDS))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=600, step=5,
                        mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
