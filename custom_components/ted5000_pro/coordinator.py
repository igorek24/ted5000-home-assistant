"""Polling coordinator for the TED5000."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TedAuthError, TedConnectionError, Ted5000Client, TedData
from .const import CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class TedCoordinator(DataUpdateCoordinator[TedData]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: Ted5000Client
    ) -> None:
        seconds = entry.options.get(CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=seconds),
            config_entry=entry,
        )
        self.client = client

    async def _async_update_data(self) -> TedData:
        try:
            return await self.client.async_get_data()
        except TedAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TedConnectionError as err:
            raise UpdateFailed(str(err)) from err
