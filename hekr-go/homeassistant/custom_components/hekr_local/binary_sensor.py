"""二值传感器:滤网更换报警、设备在线状态。"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HekrConfigEntry
from .coordinator import HekrCoordinator
from .entity import HekrEntity, async_add_devices

ALARM_KEYS = {  # 设备字段 -> 翻译键
    "alarm": "alarm_primary",
    "highAlarm": "alarm_high",
    "ltcAlarm": "alarm_catalyst",
    "eleAlarm": "alarm_electrostatic",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: HekrConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    def make(c: HekrCoordinator, tid: str):
        return [HekrAlarm(c, tid, k) for k in ALARM_KEYS] + [HekrOnline(c, tid)]

    entry.async_on_unload(async_add_devices(entry.runtime_data, async_add_entities, make))


class HekrAlarm(HekrEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: HekrCoordinator, dev_tid: str, key: str) -> None:
        super().__init__(coordinator, dev_tid, key)
        self._key = key
        self._attr_translation_key = ALARM_KEYS[key]

    @property
    def is_on(self) -> bool:
        return bool(self.status.get(self._key))


class HekrOnline(HekrEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "online"
    _available_when_offline = True

    def __init__(self, coordinator: HekrCoordinator, dev_tid: str) -> None:
        super().__init__(coordinator, dev_tid, "online")

    @property
    def is_on(self) -> bool:
        return bool((self.device or {}).get("online"))
