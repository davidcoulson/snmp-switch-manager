
from __future__ import annotations

import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_NAME
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, CONF_COMMUNITY, DEFAULT_PORT, CONF_CUSTOM_OIDS, CONF_ENABLE_CUSTOM_OIDS, CONF_RESET_CUSTOM_OIDS
from .snmp import test_connection, get_sysname

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_COMMUNITY): str,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    vol.Optional(CONF_NAME): str,
})

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            community = user_input[CONF_COMMUNITY]

            ok = await test_connection(self.hass, host, community, port)
            if not ok:
                errors["base"] = "cannot_connect"
            else:
                # Use sysName for device naming if available
                sysname = await get_sysname(self.hass, host, community, port)
                title = user_input.get(CONF_NAME) or sysname or host

                await self.async_set_unique_id(f"{host}:{port}:{community}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(title=title, data={
                    "host": host, "port": port, "community": community, "name": title
                })
        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)


OID_FIELDS = [
    ("manufacturer", "Manufacturer OID"),
    ("model", "Model OID"),
    ("firmware", "Firmware OID"),
    ("hostname", "Hostname OID"),
    ("uptime", "Uptime OID"),
]


def _normalize_oid(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    # Allow leading dot, store numeric dotted OID without it
    if v.startswith("."):
        v = v[1:]
    return v


def _is_valid_numeric_oid(value: str) -> bool:
    v = _normalize_oid(value)
    if not v:
        return True
    return bool(re.fullmatch(r"(\d+\.)*\d+", v))


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Reset clears all per-device overrides
            if user_input.get(CONF_RESET_CUSTOM_OIDS):
                new_custom = {}
            else:
                # Per-field delete: blank values remove keys
                new_custom = {}
                for k in ("manufacturer", "model", "firmware", "hostname", "uptime"):
                    v = (user_input.get(k) or "").strip()
                    if v:
                        # normalize leading dot
                        if v.startswith("."):
                            v = v[1:]
                        new_custom[k] = v

            enable = bool(user_input.get(CONF_ENABLE_CUSTOM_OIDS))

            return self.async_create_entry(
                title="",
                data={
                    CONF_ENABLE_CUSTOM_OIDS: enable,
                    CONF_CUSTOM_OIDS: new_custom,
                },
            )

        # Defaults (must never throw)
        opts = dict(self._entry.options or {})
        enable_default = bool(opts.get(CONF_ENABLE_CUSTOM_OIDS, False))
        custom = dict(opts.get(CONF_CUSTOM_OIDS, {}) or {})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_ENABLE_CUSTOM_OIDS, default=enable_default): bool,
                    vol.Optional("manufacturer", default=custom.get("manufacturer", "")): str,
                    vol.Optional("model", default=custom.get("model", "")): str,
                    vol.Optional("firmware", default=custom.get("firmware", "")): str,
                    vol.Optional("hostname", default=custom.get("hostname", "")): str,
                    vol.Optional("uptime", default=custom.get("uptime", "")): str,
                    vol.Optional(CONF_RESET_CUSTOM_OIDS, default=False): bool,
                }
            ),
        )
