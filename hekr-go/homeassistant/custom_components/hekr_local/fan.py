"""新风机主实体:开关、风量百分比(新风)、模式预设。"""
from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import percentage_to_ranged_value, ranged_value_to_percentage

from . import HekrConfigEntry
from .const import MAX_SPEED, MODE_MANUAL, MODE_TO_PRESET, PRESET_TO_MODE
from .coordinator import HekrCoordinator
from .entity import HekrEntity, async_add_devices

SPEED_RANGE = (1, MAX_SPEED)


async def async_setup_entry(
    hass: HomeAssistant, entry: HekrConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entry.async_on_unload(
        async_add_devices(entry.runtime_data, async_add_entities, lambda c, tid: [HekrFan(c, tid)])
    )


class HekrFan(HekrEntity, FanEntity):
    _attr_name = None  # 实体名就是设备名
    _attr_translation_key = "ventilation"
    _attr_supported_features = (
        FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF | FanEntityFeature.SET_SPEED | FanEntityFeature.PRESET_MODE
    )
    _attr_speed_count = MAX_SPEED
    _attr_preset_modes = list(PRESET_TO_MODE)

    def __init__(self, coordinator: HekrCoordinator, dev_tid: str) -> None:
        super().__init__(coordinator, dev_tid, "fan")

    @property
    def is_on(self) -> bool:
        return self.is_running

    @property
    def percentage(self) -> int | None:
        if not self.is_running:
            return 0
        speed = self.status.get("airSpeed", 0)
        return ranged_value_to_percentage(SPEED_RANGE, speed) if speed > 0 else 0

    @property
    def preset_mode(self) -> str | None:
        return MODE_TO_PRESET.get(self.status.get("mode"))

    async def _send(self, body: dict[str, int]) -> None:
        await self.coordinator.async_command(self._tid, body)

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        body = {"sw": 1}
        if percentage is not None:
            body.update(self._speed_body(percentage))
        elif preset_mode is not None:
            body["mode"] = PRESET_TO_MODE[preset_mode]
        await self._send(body)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send({"sw": 0})

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self._send({"sw": 0})
        else:
            # 设定风量意味着手动控制;自动模式下设备会自己决定风量
            await self._send({"sw": 1, **self._speed_body(percentage)})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        # 设备在关机状态下会忽略模式切换,所以同时开机
        await self._send({"sw": 1, "mode": PRESET_TO_MODE[preset_mode]})

    @staticmethod
    def _speed_body(percentage: int) -> dict[str, int]:
        speed = round(percentage_to_ranged_value(SPEED_RANGE, percentage))
        return {"mode": MODE_MANUAL, "airSpeed": max(1, min(MAX_SPEED, speed))}
