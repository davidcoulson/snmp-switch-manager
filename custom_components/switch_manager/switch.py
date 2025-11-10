from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _mask_to_prefix(mask: Optional[str]) -> Optional[int]:
    if not mask:
        return None
    try:
        parts = [int(p) for p in mask.split(".")]
        # convert dotted mask to prefix length
        bits = "".join(f"{p:08b}" for p in parts)
        return bits.count("1")
    except Exception:  # pragma: no cover
        return None


def _format_ip_with_prefix(ips: List[Tuple[str, str, Optional[int]]]) -> Optional[str]:
    """
    `ips` is a list like: [(ip, mask, prefix), ...]
    Prefer an explicit prefix if present; otherwise derive from mask.
    Return the first address formatted as `a.b.c.d/yy`.
    """
    if not ips:
        return None
    ip, mask, pref = ips[0]
    prefix = pref if pref is not None else _mask_to_prefix(mask)
    return f"{ip}/{prefix}" if prefix is not None else ip


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    """Set up Switch Manager port switches from a config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    runtime = domain_data.get(entry.entry_id)
    if not runtime:
        _LOGGER.error(
            "Could not resolve coordinator for entry_id=%s; hass.data keys: %s",
            entry.entry_id,
            list(domain_data.keys()),
        )
        return

    coordinator = runtime["coordinator"]
    ports: List[Dict[str, Any]] = coordinator.data.get("ports", [])
    entities: List[SwitchManagerPort] = []

    for port in ports:
        # Each port dict comes from snmp.py async_get_interfaces()
        # Fields: index, descr, alias, admin, oper, type, ips=[(ip, mask, prefix),...]
        index = port.get("index")
        if index is None:
            continue
        entities.append(SwitchManagerPort(coordinator, entry, port))

    if entities:
        async_add_entities(entities)


class SwitchManagerPort(CoordinatorEntity, SwitchEntity):
    """HA switch representing the admin state of a switch interface."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, port: Dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._port = port

        # Human-friendly name already built upstream (e.g., Gi1/0/46, Te1/1/2, Vl11, Lo0)
        # If upstream didn’t set one, fall back to descr.
        self._attr_name = port.get("friendly_name") or port.get("descr") or f"Port {port.get('index')}"
        self._attr_unique_id = f"{entry.entry_id}-if-{port.get('index')}"

    @property
    def is_on(self) -> bool:
        """Admin up == True."""
        admin = self._port.get("admin")
        return bool(admin == 1)

    async def async_turn_on(self, **kwargs) -> None:
        client = self.coordinator.data.get("client")
        if not client:
            return
        idx = self._port.get("index")
        await client.async_set_admin_state(idx, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        client = self.coordinator.data.get("client")
        if not client:
            return
        idx = self._port.get("index")
        await client.async_set_admin_state(idx, False)
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Expose interface attributes, including IP/prefix when available."""
        attrs: Dict[str, Any] = {
            "Index": self._port.get("index"),
            "Name": self._port.get("descr"),
            "Alias": self._port.get("alias"),
            "Admin": self._port.get("admin"),
            "Oper": self._port.get("oper"),
        }

        # NEW: add `IP address` with prefix (or ip if prefix unknown)
        ip_str = _format_ip_with_prefix(self._port.get("ips", []))
        if ip_str:
            attrs["IP address"] = ip_str

        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update local port snapshot when coordinator refreshes."""
        # Find this port again by index and replace our cached dict
        idx = self._port.get("index")
        ports: List[Dict[str, Any]] = self.coordinator.data.get("ports", [])
        for p in ports:
            if p.get("index") == idx:
                self._port = p
                break
        self.async_write_ha_state()
