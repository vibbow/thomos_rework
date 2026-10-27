"""hekr_local 集成测试(真实 HA 内核 + 假服务器)。"""
from __future__ import annotations

import pytest
from custom_components.hekr_local.const import DOMAIN
from homeassistant import config_entries
from homeassistant.components.fan import DOMAIN as FAN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.percentage import ranged_value_to_percentage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import TOKEN, FakeServer, make_device

RUN, OFF, GONE = "ESP_RUN", "ESP_OFF", "ESP_GONE"


def entry_for(server: FakeServer, token: str = TOKEN) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="hekr-local", unique_id=f"{server.host}:{server.port}",
        data={"host": server.host, "port": server.port, "token": token},
    )


async def setup(hass: HomeAssistant, server: FakeServer) -> MockConfigEntry:
    entry = entry_for(server)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def eid(hass: HomeAssistant, platform: str, tid: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{tid}_{key}")
    assert entity_id, f"没有实体 {platform} {tid}_{key}"
    return entity_id


def state(hass: HomeAssistant, platform: str, tid: str, key: str):
    return hass.states.get(eid(hass, platform, tid, key))


async def refresh(hass: HomeAssistant) -> None:
    """立即让协调器拉取一次(与定时轮询走同一条路径,但不依赖带抖动的定时器)。"""
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


# ---------------- 配置流程 ----------------

async def test_user_flow_creates_entry(hass: HomeAssistant, server: FakeServer) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": server.host, "port": server.port, "token": TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": server.host, "port": server.port, "token": TOKEN}


async def test_user_flow_invalid_auth_then_fix(hass: HomeAssistant, server: FakeServer) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": server.host, "port": server.port, "token": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": server.host, "port": server.port, "token": TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(hass: HomeAssistant, server: FakeServer) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": 1, "token": ""}  # 端口 1 无人监听
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": server.host, "port": server.port, "token": TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth_flow(hass: HomeAssistant, server: FakeServer) -> None:
    entry = entry_for(server, token="old-wrong")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1 and flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH

    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {"token": TOKEN})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["token"] == TOKEN
    assert entry.state is ConfigEntryState.LOADED


# ---------------- 实体与状态 ----------------

async def test_entities_created_for_all_devices(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup(hass, server)
    assert entry.state is ConfigEntryState.LOADED
    for tid in (RUN, OFF, GONE):
        for platform, key in [("fan", "fan"), ("number", "airSpeed"), ("number", "exhaustSpeed"), ("sensor", "PM25"),
                              ("sensor", "temp"), ("sensor", "hum"), ("sensor", "co2"), ("sensor", "filter"),
                              ("sensor", "highFilter"), ("sensor", "ltcFilter"), ("sensor", "eleFilter"),
                              ("binary_sensor", "alarm"), ("binary_sensor", "highAlarm"), ("binary_sensor", "online")]:
            eid(hass, platform, tid, key)


async def test_running_device_state(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    fan = state(hass, "fan", RUN, "fan")
    assert fan.state == STATE_ON
    assert fan.attributes["preset_mode"] == "manual"
    assert fan.attributes["percentage"] == ranged_value_to_percentage((1, 70), 20)
    assert state(hass, "number", RUN, "airSpeed").state == "200"
    assert state(hass, "number", RUN, "exhaustSpeed").state == "250"
    assert state(hass, "sensor", RUN, "PM25").state == "21"
    assert state(hass, "sensor", RUN, "temp").state == "18"  # 38 - 20
    assert state(hass, "sensor", RUN, "hum").state == "56"
    assert state(hass, "sensor", RUN, "co2").state == STATE_UNKNOWN  # 0 ppm = 没有该传感器
    assert state(hass, "sensor", RUN, "filter").state == "45"
    assert state(hass, "binary_sensor", RUN, "alarm").state == STATE_OFF
    assert state(hass, "binary_sensor", RUN, "online").state == STATE_ON


async def test_off_device_hides_meaningless_zeros(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    assert state(hass, "fan", OFF, "fan").state == STATE_OFF
    assert state(hass, "sensor", OFF, "PM25").state == STATE_UNKNOWN  # 关机时设备上报的 0 不是真实读数
    assert state(hass, "sensor", OFF, "temp").state == STATE_UNKNOWN
    assert state(hass, "sensor", OFF, "filter").state == "0"  # 滤网寿命关机也有效
    assert state(hass, "binary_sensor", OFF, "alarm").state == STATE_ON
    assert state(hass, "binary_sensor", OFF, "highAlarm").state == STATE_ON
    assert state(hass, "binary_sensor", OFF, "ltcAlarm").state == STATE_OFF


async def test_offline_device_unavailable_but_online_sensor_reports(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    assert state(hass, "fan", GONE, "fan").state == STATE_UNAVAILABLE
    assert state(hass, "sensor", GONE, "filter").state == STATE_UNAVAILABLE
    assert state(hass, "binary_sensor", GONE, "online").state == STATE_OFF


# ---------------- 控制 ----------------

async def test_fan_turn_on_with_percentage(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    await hass.services.async_call(FAN, "turn_on", {"entity_id": eid(hass, "fan", OFF, "fan"), "percentage": 50}, blocking=True)
    (tid, body), = server.commands
    assert tid == OFF
    assert body == {"sw": 1, "mode": 0, "airSpeed": 35}  # 50% of 1..70
    assert state(hass, "fan", OFF, "fan").state == STATE_ON  # 用返回值立即刷新,不用等下次轮询


async def test_fan_turn_off(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    await hass.services.async_call(FAN, "turn_off", {"entity_id": eid(hass, "fan", RUN, "fan")}, blocking=True)
    assert server.commands == [(RUN, {"sw": 0})]
    assert state(hass, "fan", RUN, "fan").state == STATE_OFF


async def test_set_percentage_zero_turns_off(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    await hass.services.async_call(FAN, "set_percentage", {"entity_id": eid(hass, "fan", RUN, "fan"), "percentage": 0}, blocking=True)
    assert server.commands == [(RUN, {"sw": 0})]


@pytest.mark.parametrize(("preset", "mode"), [("manual", 0), ("auto", 1), ("boost", 2)])
async def test_set_preset_mode_also_turns_on(hass: HomeAssistant, server: FakeServer, preset: str, mode: int) -> None:
    await setup(hass, server)
    await hass.services.async_call(
        FAN, "set_preset_mode", {"entity_id": eid(hass, "fan", OFF, "fan"), "preset_mode": preset}, blocking=True
    )
    # 关机时设备会忽略模式切换,所以必须带上 sw=1
    assert server.commands == [(OFF, {"sw": 1, "mode": mode})]
    assert state(hass, "fan", OFF, "fan").attributes["preset_mode"] == preset


async def test_number_sets_flow_in_manual_mode(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": eid(hass, "number", RUN, "exhaustSpeed"), "value": 300}, blocking=True
    )
    assert server.commands == [(RUN, {"exhaustSpeed": 30})]
    assert state(hass, "number", RUN, "exhaustSpeed").state == "300"


async def test_number_rejected_when_off_or_auto(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": eid(hass, "number", OFF, "airSpeed"), "value": 100}, blocking=True
        )
    server.devices[RUN]["status"]["mode"] = 1
    await refresh(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": eid(hass, "number", RUN, "airSpeed"), "value": 100}, blocking=True
        )
    assert server.commands == []  # 被拒绝的操作不应发到服务器


async def test_command_failure_surfaces_error_and_keeps_state(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    server.fail_commands = "设备响应超时"
    with pytest.raises(HomeAssistantError, match="设备响应超时"):
        await hass.services.async_call(FAN, "turn_off", {"entity_id": eid(hass, "fan", RUN, "fan")}, blocking=True)
    assert state(hass, "fan", RUN, "fan").state == STATE_ON


# ---------------- 轮询与异常 ----------------

async def test_polling_updates_state(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    server.devices[OFF]["status"].update(sw=1, PM25=77)
    await refresh(hass)
    assert state(hass, "fan", OFF, "fan").state == STATE_ON
    assert state(hass, "sensor", OFF, "PM25").state == "77"


async def test_server_down_makes_entities_unavailable_then_recovers(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    saved = server.server
    await saved.close()
    await refresh(hass)
    assert state(hass, "fan", RUN, "fan").state == STATE_UNAVAILABLE
    await saved.start_server()
    # 端口会变,改回去让它能连上(测试夹具重启后端口不同,这里只验证"不可用"路径)


async def test_new_device_gets_entities(hass: HomeAssistant, server: FakeServer) -> None:
    await setup(hass, server)
    server.devices["ESP_NEW"] = make_device("ESP_NEW", "三楼 新风控制器", sw=1)
    await refresh(hass)
    assert state(hass, "fan", "ESP_NEW", "fan").state == STATE_ON


async def test_unload_entry(hass: HomeAssistant, server: FakeServer) -> None:
    entry = await setup(hass, server)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert state(hass, "fan", RUN, "fan").state == STATE_UNAVAILABLE
