"""托马仕新风(Hekr)本地控制:通过 hekr-local 服务器接入。"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HekrLocalClient
from .const import CONF_TOKEN
from .coordinator import HekrCoordinator

PLATFORMS = [Platform.FAN, Platform.NUMBER, Platform.SENSOR, Platform.BINARY_SENSOR]

type HekrConfigEntry = ConfigEntry[HekrCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HekrConfigEntry) -> bool:
    client = HekrLocalClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data.get(CONF_TOKEN, ""),
    )
    coordinator = HekrCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HekrConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
