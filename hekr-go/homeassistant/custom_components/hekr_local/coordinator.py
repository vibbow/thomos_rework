"""定时从 hekr-local 拉取全部设备状态。"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HekrLocalAuthError, HekrLocalClient, HekrLocalError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class HekrCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """data: devTid -> 服务器返回的设备字典(name/online/ip/status/info...)。"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: HekrLocalClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.client = client

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            devices = await self.client.devices()
        except HekrLocalAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HekrLocalError as err:
            raise UpdateFailed(str(err)) from err
        return {d["devTid"]: d for d in devices}

    async def async_command(self, dev_tid: str, body: dict[str, int]) -> None:
        """下发指令;成功后用服务器返回的最新状态立即刷新界面。失败抛 HomeAssistantError。"""
        try:
            device = await self.client.command(dev_tid, body)
        except HekrLocalError as err:
            raise HomeAssistantError(f"指令失败: {err}") from err
        self.async_set_updated_data({**(self.data or {}), dev_tid: device})
