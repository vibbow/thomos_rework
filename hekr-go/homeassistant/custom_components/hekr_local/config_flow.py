"""添加/重新认证 hekr-local 服务器。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import HekrLocalAuthError, HekrLocalClient, HekrLocalError
from .const import CONF_TOKEN, DEFAULT_PORT, DOMAIN

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class HekrLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, host: str, port: int, token: str) -> str | None:
        """返回错误键;None 表示通过。"""
        client = HekrLocalClient(async_get_clientsession(self.hass), host, port, token)
        try:
            await client.devices()
        except HekrLocalAuthError:
            return "invalid_auth"
        except HekrLocalError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            token = user_input.get(CONF_TOKEN, "").strip()
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            if (error := await self._validate(host, port, token)) is None:
                return self.async_create_entry(
                    title=f"hekr-local ({host})",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token},
                )
            errors["base"] = error

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Optional(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): TOKEN_SELECTOR,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            if (error := await self._validate(entry.data[CONF_HOST], entry.data[CONF_PORT], token)) is None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            errors=errors,
        )
