# custom_components/switch_manager/switch.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, DEFAULT_PORT
from .snmp import SwitchSnmpClient  # we only rely on the public client

_LOGGER = logging.getLogger(__name__)


# ---------------------------
# Helpers
# ---------------------------

def _merge_entry_data(entry: ConfigEntry) -> Dict[str, Any]:
    """Return a merged (data ∪ options) dict without raising KeyError."""
    merged: Dict[str, Any] = {}
    if hasattr(entry, "data") and isinstance(entry.data, dict):
        merged.update(entry.data)
    if hasattr(entry, "options") and isinstance(entry.options, dict):
        merged.update(entry.options)
    return merged


# ---------------------------
# Platform setup
# ---------------------------

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Switch entities for a config entry."""

    _LOGGER.debug("Switch platform setup start for entry_id=%s", entry.entry_id)

    # 1) Prefer the client that __init__.py stored for us
    store = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    client: Optional[SwitchSnmpClient] = store.get("client")  # type: ignore[assignment]

    # 2) If missing (e.g. reload), reconstruct from entry data/options
    if client is None:
        cfg = _merge_entry_data(entry)
        host = cfg.get("host") or cfg.get("ip") or cfg.get("address")
        community = cfg.get("community") or cfg.get("snmp_community")
        port = int(cfg.get("port", DEFAULT_PORT))

        if not host or not community:
            _LOGGER.error(
                "Missing SNMP connection details (host/community) for entry_id=%s; "
                "data keys=%s options keys=%s",
                entry.entry_id,
                list(getattr(entry, "data", {}).keys()),
                list(getattr(entry, "options", {}).keys()),
            )
            return

        client = await SwitchSnmpClient.async_create(hass, host, port, community)
        hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})["client"] = client

    # 3) Query port/interface table from the client.
    #    We tolerate either 'async_get_port_data' (older) or 'async_get_interfaces' (fallback).
    interfaces: List[Dict[str, Any]]
    if hasattr(client, "async_get_port_data"):
        interfaces = await client.async_get_port_data()  # type: ignore[attr-defined]
    elif hasattr(client, "async_get_interfaces"):
        interfaces = await client.async_get_interfaces()  # type: ignore[attr-defined]
    else:
        _LOGGER.error(
            "SNMP client does not provide an interface listing method. "
            "Expected 'async_get_port_data' or 'async_get_interfaces'."
        )
        return

    entities: List[SwitchPortEntity] = []
    for row in interfaces:
        # Expected row keys (we tolerate variations):
        # ifIndex, ifDescr, ifAlias, ifAdminStatus, ifOperStatus
        idx = row.get("ifIndex") or row.get("index")
        descr = row.get("ifDescr") or row.get("name") or f"Port {idx}"
        alias = row.get("ifAlias") or row.get("alias") or ""
        admin = int(row.get("ifAdminStatus", row.get("admin", 1)))
        oper = int(row.get("ifOperStatus", row.get("oper", 1)))

        # Skip rows without an index
        if idx is None:
            continue

        entities.append(
            SwitchPortEntity(
                entry_id=entry.entry_id,
                index=int(idx),
                name=str(descr),
                alias=str(alias),
                admin=admin,
                oper=oper,
                client=client,
            )
        )

    if not entities:
        _LOGGER.warning("No switch entities discovered for entry_id=%s", entry.entry_id)

    async_add_entities(entities)
    _LOGGER.debug("Switch platform setup complete: added %d entities", len(entities))


# ---------------------------
# Entity
# ---------------------------

class SwitchPortEntity(SwitchEntity):
    """A network switch port represented as a Toggleable entity."""

    _attr_should_poll = False

    def __init__(
        self,
        *,
        entry_id: str,
        index: int,
        name: str,
        alias: str,
        admin: int,
        oper: int,
        client: SwitchSnmpClient,
    ) -> None:
        self._entry_id = entry_id
        self._index = index
        self._name = name
        self._alias = alias
        self._admin = admin
        self._oper = oper
        self._client = client

        self._attr_unique_id = f"{entry_id}_if_{index}"
        self._attr_name = name

    # ---- Home Assistant required properties ----

    @property
    def is_on(self) -> bool:
        # Reflect Admin state (1=up, 2=down) — default to True if unknown
        return int(self._admin) == 1

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._client.host if hasattr(self._client, "host") else "Switch",
            manufacturer=None,
            model=None,
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {
            "Index": self._index,
            "Name": self._name,
            "Alias": self._alias,
            "Admin": int(self._admin),
            "Oper": int(self._oper),
        }

    # ---- Commands ----

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the port (admin up) if supported by the client."""
        if hasattr(self._client, "async_set_admin_status"):
            ok = await self._client.async_set_admin_status(self._index, True)  # type: ignore[attr-defined]
            if ok:
                self._admin = 1
                self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the port (admin down) if supported by the client."""
        if hasattr(self._client, "async_set_admin_status"):
            ok = await self._client.async_set_admin_status(self._index, False)  # type: ignore[attr-defined]
            if ok:
                self._admin = 2
                self.async_write_ha_state()

    # ---- Updates ----

    async def async_update(self) -> None:
        """Refresh a single port row if the client supports it."""
        if hasattr(self._client, "async_get_port_row"):
            row = await self._client.async_get_port_row(self._index)  # type: ignore[attr-defined]
            if row:
                self._admin = int(row.get("ifAdminStatus", row.get("admin", self._admin)))
                self._oper = int(row.get("ifOperStatus", row.get("oper", self._oper)))
                alias = row.get("ifAlias") or row.get("alias")
                if alias is not None:
                    self._alias = str(alias)
