"""实体基类与"新设备自动出现"的辅助函数。"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HekrCoordinator


class HekrEntity(CoordinatorEntity[HekrCoordinator]):
    _attr_has_entity_name = True
    # 设备离线时,设备相关实体不可用;个别实体(在线状态)可以覆盖
    _available_when_offline = False

    def __init__(self, coordinator: HekrCoordinator, dev_tid: str, key: str) -> None:
        super().__init__(coordinator)
        self._tid = dev_tid
        self._attr_unique_id = f"{dev_tid}_{key}"
        device = coordinator.data[dev_tid]
        info = device.get("info") or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, dev_tid)},
            name=device.get("name") or dev_tid,
            manufacturer="托马仕 Thomos",
            model="THD-4B",
            sw_version=info.get("binVer"),
            configuration_url=coordinator.client.base_url,
        )

    @property
    def device(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._tid)

    @property
    def status(self) -> dict[str, int]:
        return (self.device or {}).get("status") or {}

    @property
    def is_running(self) -> bool:
        return bool(self.status.get("sw"))

    @property
    def available(self) -> bool:
        if not super().available or self.device is None:
            return False
        return self._available_when_offline or bool(self.device.get("online"))


@callback
def async_add_devices(
    coordinator: HekrCoordinator,
    async_add_entities: AddEntitiesCallback,
    make_entities: Callable[[HekrCoordinator, str], Iterable[Entity]],
) -> Callable[[], None]:
    """为每台设备创建实体;之后服务器上出现新设备时自动补建。返回取消监听的函数。"""
    known: set[str] = set()

    @callback
    def _check() -> None:
        new = [tid for tid in coordinator.data if tid not in known]
        if new:
            known.update(new)
            async_add_entities([e for tid in new for e in make_entities(coordinator, tid)])

    _check()
    return coordinator.async_add_listener(_check)
