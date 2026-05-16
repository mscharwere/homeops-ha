"""Config flow for the HomeOps integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HomeOpsApiError, HomeOpsAuthError, HomeOpsClient
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SERVER_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SERVER_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERVER_URL, default=DEFAULT_SERVER_URL): str,
        vol.Required(CONF_API_KEY): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            int, vol.Range(min=1, max=60)
        ),
    }
)


class HomeOpsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the HomeOps config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            server_url = user_input[CONF_SERVER_URL].rstrip("/")
            api_key = user_input[CONF_API_KEY]

            await self.async_set_unique_id(server_url)
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            client = HomeOpsClient(session, server_url, api_key)

            try:
                await client.get_health()
            except HomeOpsAuthError:
                errors[CONF_API_KEY] = "invalid_auth"
            except HomeOpsApiError:
                errors["base"] = "cannot_connect"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during HomeOps setup")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"HomeOps ({server_url})",
                    data={
                        CONF_SERVER_URL: server_url,
                        CONF_API_KEY: api_key,
                        CONF_SCAN_INTERVAL: user_input.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HomeOpsOptionsFlow:
        """Return the options flow."""
        return HomeOpsOptionsFlow(config_entry)


class HomeOpsOptionsFlow(OptionsFlow):
    """Handle HomeOps options (scan interval)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage HomeOps options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self._config_entry.data.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): vol.All(int, vol.Range(min=1, max=60)),
                }
            ),
        )
