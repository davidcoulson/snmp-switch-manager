# custom_components/switch_manager/config_flow.py
from __future__ import annotations

import logging
from typing import Any, Dict

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN  # make sure DOMAIN = "switch_manager"
from .snmp import ensure_snmp_available, SnmpDependencyError

_LOGGER = logging.getLogger(__name__)

# Form fields shown in the screenshot
DATA_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
        vol.Required("community"): str,
        vol.Required("port", default=161): int,
        vol.Optional("name", default="Switch"): str,
    }
)


class SwitchManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Switch Manager."""

    VERSION = 1

    async def async_step_user(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}

        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

        # 1) Ensure HLAPI is importable (dependency installed) — single check.
        try:
            await self.hass.async_add_executor_job(ensure_snmp_available)
        except SnmpDependencyError as err:
            _LOGGER.error("pysnmp dependency issue: %s", err)
            errors["base"] = "missing_dependency"
            return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

        # 2) Avoid duplicate entries per host:port
        unique_id = f"{user_input['host']}:{int(user_input.get('port', 161))}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        # 3) Store the data; device probing occurs in async_setup_entry
        data = {
            "host": user_input["host"],
            "community": user_input["community"],
            "port": int(user_input.get("port", 161)),
            "name": user_input.get("name") or "Switch",
        }

        return self.async_create_entry(title=data["name"], data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SwitchManagerOptionsFlow(config_entry)


class SwitchManagerOptionsFlow(config_entries.OptionsFlow):
    """Options flow; mirrors the initial schema for simple edits."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: Dict[str, Any] | None = None):
        errors: Dict[str, str] = {}

        if user_input is not None:
            # Just save; validation handled by runtime and logs
            return self.async_create_entry(title="", data=user_input)

        defaults = {
            "host": self._entry.data.get("host", ""),
            "community": self._entry.data.get("community", ""),
            "port": self._entry.data.get("port", 161),
            "name": self._entry.data.get("name", "Switch"),
        }

        schema = vol.Schema(
            {
                vol.Required("host", default=defaults["host"]): str,
                vol.Required("community", default=defaults["community"]): str,
                vol.Required("port", default=defaults["port"]): int,
                vol.Optional("name", default=defaults["name"]): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
