from __future__ import annotations

import logging
from typing import Any, Dict

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .snmp import ensure_snmp_available, SwitchSnmpClient, SnmpDependencyError, SnmpError

_LOGGER = logging.getLogger(__name__)

# Form keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_COMMUNITY = "community"

DEFAULT_PORT = 161


class SwitchManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Switch Manager."""

    VERSION = 1

    @staticmethod
    def _schema() -> vol.Schema:
        """Build the user form schema (no Name field)."""
        return vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_COMMUNITY): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
            }
        )

    async def async_step_user(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is None:
            # show the form
            return self.async_show_form(step_id="user", data_schema=self._schema(), errors=errors)

        # Validate pysnmp availability early
        try:
            await self.hass.async_add_executor_job(ensure_snmp_available)
        except SnmpDependencyError as exc:
            _LOGGER.exception("pysnmp dependency issue: %s", exc)
            errors["base"] = "pysnmp_missing"
            return self.async_show_form(step_id="user", data_schema=self._schema(), errors=errors)

        host: str = user_input[CONF_HOST]
        community: str = user_input[CONF_COMMUNITY]
        port: int = int(user_input[CONF_PORT])

        # Try a lightweight probe to avoid creating dead entries
        try:
            client = await SwitchSnmpClient.async_create(self.hass, host, port, community)
            await client.async_get_system_info()
        except (SnmpError, Exception) as exc:  # keep same behavior as before
            _LOGGER.warning("SNMP validation failed for %s:%s: %s", host, port, exc)
            errors["base"] = "cannot_connect"
            return self.async_show_form(step_id="user", data_schema=self._schema(), errors=errors)

        # Unique ID = host:port
        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=host,  # Displayed until device registry picks up hostname/sensors
            data={
                CONF_HOST: host,
                CONF_COMMUNITY: community,
                CONF_PORT: port,
            },
        )
