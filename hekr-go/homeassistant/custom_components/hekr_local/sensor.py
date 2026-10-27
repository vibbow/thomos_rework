"""传感器:PM2.5、温湿度、CO₂、滤网寿命、WiFi 信号。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfDensity,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HekrConfigEntry
from .const import TEMP_OFFSET
from .coordinator import HekrCoordinator
from .entity import HekrEntity, async_add_devices


@dataclass(frozen=True, kw_only=True)
class HekrSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any], dict[str, Any]], float | int | None]  # (status, info) -> 值
    needs_running: bool = False  # 关机时设备上报的传感器值全是 0,没有意义


FILTER_KEYS = {  # 设备字段 -> 翻译键
    "filter": "filter_primary",
    "highFilter": "filter_high",
    "ltcFilter": "filter_catalyst",
    "eleFilter": "filter_electrostatic",
}


def _filter(key: str) -> HekrSensorDescription:
    return HekrSensorDescription(
        key=key,
        translation_key=FILTER_KEYS[key],
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:air-filter",
        value_fn=lambda st, info, k=key: st.get(k),
    )


SENSORS: tuple[HekrSensorDescription, ...] = (
    HekrSensorDescription(
        key="PM25",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        needs_running=True,
        value_fn=lambda st, info: st.get("PM25"),
    ),
    HekrSensorDescription(
        key="temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        needs_running=True,
        value_fn=lambda st, info: st["temp"] - TEMP_OFFSET if "temp" in st else None,
    ),
    HekrSensorDescription(
        key="hum",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        needs_running=True,
        value_fn=lambda st, info: st.get("hum"),
    ),
    HekrSensorDescription(
        key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        needs_running=True,
        # 0 ppm 不可能出现,说明这台机器没有 CO₂ 传感器
        value_fn=lambda st, info: st.get("co2") or None,
    ),
    _filter("filter"),
    _filter("highFilter"),
    _filter("ltcFilter"),
    _filter("eleFilter"),
    HekrSensorDescription(
        key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda st, info: info.get("rssi"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HekrConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entry.async_on_unload(
        async_add_devices(
            entry.runtime_data, async_add_entities, lambda c, tid: [HekrSensor(c, tid, d) for d in SENSORS]
        )
    )


class HekrSensor(HekrEntity, SensorEntity):
    entity_description: HekrSensorDescription

    def __init__(self, coordinator: HekrCoordinator, dev_tid: str, description: HekrSensorDescription) -> None:
        super().__init__(coordinator, dev_tid, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        if self.entity_description.needs_running and not self.is_running:
            return None
        info = (self.device or {}).get("info") or {}
        return self.entity_description.value_fn(self.status, info)
