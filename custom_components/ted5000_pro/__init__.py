"""The TED5000 Pro integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TedConnectionError, Ted5000Client
from .const import DEFAULT_PORT
from .coordinator import TedCoordinator

PLATFORMS = [Platform.SENSOR]

type TedConfigEntry = ConfigEntry[TedCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: TedConfigEntry) -> bool:
    client = Ted5000Client(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data.get(CONF_USERNAME),
        entry.data.get(CONF_PASSWORD),
    )
    try:
        await client.async_load_config()
    except TedConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = TedCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TedConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: TedConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
