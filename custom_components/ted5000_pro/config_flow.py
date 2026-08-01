"""Config flow for TED5000 Pro."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TedAuthError, TedConnectionError, Ted5000Client
from .const import (
    CONF_CREATE_CIRCUIT_ENERGY,
    CONF_PHANTOM_DAYS,
    CONF_PHANTOM_END,
    CONF_PHANTOM_START,
    CONF_SCAN_INTERVAL_SECONDS,
    DEFAULT_PHANTOM_DAYS,
    DEFAULT_PHANTOM_END,
    DEFAULT_PHANTOM_START,
    MAX_PHANTOM_DAYS,
    MIN_PHANTOM_DAYS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

STEP_USER = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD): str,
    }
)


class TedConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = Ted5000Client(
                async_get_clientsession(self.hass),
                user_input[CONF_HOST],
                user_input.get(CONF_PORT, DEFAULT_PORT),
                user_input.get(CONF_USERNAME),
                user_input.get(CONF_PASSWORD),
            )
            try:
                gateway_id = await client.async_validate()
            except TedAuthError:
                errors["base"] = "invalid_auth"
            except TedConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(gateway_id)
                self._abort_if_unique_id_configured()
                circuits = len(client.circuits)
                title = f"TED5000 ({gateway_id})"
                return self.async_create_entry(
                    title=title,
                    data=user_input,
                    description_placeholders={"circuits": str(circuits)},
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TedOptionsFlow:
        return TedOptionsFlow()


class TedOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL_SECONDS,
                        default=options.get(
                            CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                    vol.Required(
                        CONF_CREATE_CIRCUIT_ENERGY,
                        default=options.get(CONF_CREATE_CIRCUIT_ENERGY, True),
                    ): bool,
                    vol.Required(
                        CONF_PHANTOM_DAYS,
                        default=options.get(CONF_PHANTOM_DAYS, DEFAULT_PHANTOM_DAYS),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_PHANTOM_DAYS, max=MAX_PHANTOM_DAYS),
                    ),
                    vol.Required(
                        CONF_PHANTOM_START,
                        default=options.get(CONF_PHANTOM_START, DEFAULT_PHANTOM_START),
                    ): str,
                    vol.Required(
                        CONF_PHANTOM_END,
                        default=options.get(CONF_PHANTOM_END, DEFAULT_PHANTOM_END),
                    ): str,
                }
            ),
        )
