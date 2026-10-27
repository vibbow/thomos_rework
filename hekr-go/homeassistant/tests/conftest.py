"""测试夹具:一个与 hekr-local Go 服务器接口一致的假服务器(真 HTTP)。"""
from __future__ import annotations

import copy
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

pytest_plugins = "pytest_homeassistant_custom_component"

TOKEN = "secret-token"


def make_device(tid: str, name: str, *, online: bool = True, **status: int) -> dict[str, Any]:
    base = {
        "flag": 0, "sw": 0, "mode": 0, "airSpeed": 0, "exhaustSpeed": 0, "PM25": 0, "temp": 20, "hum": 0,
        "co2": 0, "filter": 45, "ltcFilter": 73, "eleFilter": 73, "highFilter": 45,
        "alarm": 0, "ltcAlarm": 0, "eleAlarm": 0, "highAlarm": 0,
    }
    base.update(status)
    return {
        "devTid": tid, "name": name, "online": online, "ip": "172.17.2.109", "lastSeen": 1.0,
        "status": base, "info": {"MAC": "AABBCCDDEEFF", "binVer": "4.2.1.1", "rssi": -64, "SSID": "Things"},
    }


# 与真机测试里见到的三种状态一致
DEVICES = [
    make_device("ESP_RUN", "负一楼 新风控制器", sw=1, mode=0, airSpeed=20, exhaustSpeed=25, PM25=21, temp=38, hum=56),
    make_device("ESP_OFF", "一楼 新风控制器", filter=0, highFilter=0, alarm=1, highAlarm=1),
    make_device("ESP_GONE", "二楼 新风控制器", online=False, mode=1),
]


class FakeServer:
    def __init__(self) -> None:
        self.devices = {d["devTid"]: copy.deepcopy(d) for d in DEVICES}
        self.token = TOKEN
        self.commands: list[tuple[str, dict[str, int]]] = []
        self.fail_commands: str | None = None
        self.server: TestServer | None = None

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        return self.server.port

    def _authorized(self, request: web.Request) -> bool:
        return not self.token or request.headers.get("Authorization") == f"Bearer {self.token}"

    async def list_devices(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"error": "未授权"}, status=401)
        return web.json_response(list(self.devices.values()))

    async def command(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"error": "未授权"}, status=401)
        tid = request.match_info["tid"]
        body = await request.json()
        self.commands.append((tid, body))
        dev = self.devices.get(tid)
        if dev is None:
            return web.json_response({"error": "没有这台设备"}, status=404)
        if self.fail_commands or not dev["online"]:
            return web.json_response({"error": self.fail_commands or "设备不在线"}, status=502)
        for key in ("sw", "mode", "airSpeed", "exhaustSpeed"):  # 与服务器的下发顺序一致
            if key in body:
                dev["status"][key] = body[key]
        return web.json_response({"ok": True, "sent": [], "device": dev})


@pytest.fixture
async def server(socket_enabled: None) -> AsyncGenerator[FakeServer]:
    fake = FakeServer()
    app = web.Application()
    app.router.add_get("/api/devices", fake.list_devices)
    app.router.add_post("/api/devices/{tid}/command", fake.command)
    fake.server = TestServer(app, host="127.0.0.1")
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """让测试能加载仓库里的 custom_components。"""
