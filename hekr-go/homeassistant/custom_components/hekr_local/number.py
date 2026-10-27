"""新风/排风风量设置(m³/h)。"""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HekrConfigEntry
from .const import DOMAIN, MAX_SPEED, MODE_AUTO, SPEED_UNIT_FACTOR
from .coordinator import HekrCoordinator
from .entity import HekrEntity, async_add_devices

KEYS = ("airSpeed", "exhaustSpeed")


async def async_setup_entry(
    hass: HomeAssistant, entry: HekrConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entry.async_on_unload(
        async_add_devices(
            entry.runtime_data, async_add_entities, lambda c, tid: [HekrFlowNumber(c, tid, k) for k in KEYS]
        )
    )


class HekrFlowNumber(HekrEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_SPEED * SPEED_UNIT_FACTOR
    _attr_native_step = SPEED_UNIT_FACTOR
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: HekrCoordinator, dev_tid: str, key: str) -> None:
        super().__init__(coordinator, dev_tid, key)
        self._key = key
        self._attr_translation_key = "supply_flow" if key == "airSpeed" else "exhaust_flow"

    @property
    def native_value(self) -> float | None:
        if not self.is_running:
            return 0
        return self.status.get(self._key, 0) * SPEED_UNIT_FACTOR

    async def async_set_native_value(self, value: float) -> None:
        if not self.is_running:
            raise ServiceValidationError("设备已关机,请先开机", translation_domain=DOMAIN, translation_key="device_off")
        if self.status.get("mode") == MODE_AUTO:
            raise ServiceValidationError(
                "自动模式下风量由设备控制,请先切换到手动或强风", translation_domain=DOMAIN, translation_key="auto_mode"
            )
        speed = max(0, min(MAX_SPEED, round(value / SPEED_UNIT_FACTOR)))
        await self.coordinator.async_command(self._tid, {self._key: speed})
