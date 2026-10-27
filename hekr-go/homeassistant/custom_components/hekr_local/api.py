"""hekr-local 服务器的 HTTP 客户端。"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import COMMAND_TIMEOUT, REQUEST_TIMEOUT


class HekrLocalError(Exception):
    """与 hekr-local 服务器通信失败(含服务器返回的错误)。"""


class HekrLocalAuthError(HekrLocalError):
    """访问令牌错误。"""


class HekrLocalClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, token: str) -> None:
        self._session = session
        self.base_url = f"http://{host}:{port}"
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def _request(self, method: str, path: str, timeout: int, **kwargs: Any) -> Any:
        try:
            async with self._session.request(
                method,
                self.base_url + path,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
                **kwargs,
            ) as resp:
                if resp.status == 401:
                    raise HekrLocalAuthError("访问令牌无效")
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = None
                if resp.status >= 400:
                    msg = data.get("error") if isinstance(data, dict) else None
                    raise HekrLocalError(msg or f"HTTP {resp.status}")
                return data
        except HekrLocalError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HekrLocalError(f"无法连接服务器: {err}") from err

    async def devices(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/devices", REQUEST_TIMEOUT)
        if not isinstance(data, list):
            raise HekrLocalError("服务器返回了意外的数据")
        return data

    async def command(self, dev_tid: str, body: dict[str, int]) -> dict[str, Any]:
        """下发指令,返回设备最新状态。body 如 {"sw": 1, "mode": 0, "airSpeed": 30}。"""
        data = await self._request("POST", f"/api/devices/{dev_tid}/command", COMMAND_TIMEOUT, json=body)
        if not isinstance(data, dict) or "device" not in data:
            raise HekrLocalError("服务器返回了意外的数据")
        return data["device"]
