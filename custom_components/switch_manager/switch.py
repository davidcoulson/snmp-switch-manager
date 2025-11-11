# custom_components/switch_manager/switch.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .snmp import (
    IANA_IFTYPE_ETHERNET_CSMACD,
    IANA_IFTYPE_IEEE8023AD_LAG,
    IANA_IFTYPE_SOFTWARE_LOOPBACK,
    IANA_IFTYPE_VLAN_SUBINTERFACE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Switch Manager switches from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]  # DataUpdateCoordinator you already populate
    host: str = data["host"]           # stored at setup in __init__.py
    # Build entities once from latest coordinator snapshot
    ports: list[dict[str, Any]] = coordinator.data.get("ports", [])

    entities: list[SwitchManagerPort] = []
    for p in ports:
        # Skip CPU / virtual aggregates as you already do elsewhere
        if p.get("is_cpu"):
            continue
        # Friendly name is already computed upstream when possible, but keep a fallback
        friendly = _friendly_name(p)
        entities.append(
            SwitchManagerPort(
                coordinator=coordinator,
                entry=entry,
                host=host,
                port=p,
                friendly_name=friendly,
            )
        )

    if not entities:
        _LOGGER.debug("No ports discovered; nothing to add")
        return

    async_add_entities(entities, update_before_add=False)


def _friendly_name(p: Dict[str, Any]) -> str:
    """Derive a concise interface name (Gi/Te/Vl/Lo) based on type + U/S/P."""
    idx = p.get("index")
    name: str = f"Port {idx}" if idx is not None else "Port"

    iftype = p.get("ifType")
    unit = p.get("unit", 1)
    slot = p.get("slot", 0)
    port = p.get("port", idx)

    # VLAN subinterfaces
    if iftype == IANA_IFTYPE_VLAN_SUBINTERFACE:
        vlan_id = p.get("vlan_id") or _extract_vlan_from_name(p.get("name", ""))
        if vlan_id:
            return f"Vl{vlan_id}"
        return p.get("alias") or name

    # Loopback
    if iftype == IANA_IFTYPE_SOFTWARE_LOOPBACK:
        return "Lo0"

    # LAGs: keep only configured ones upstream; here just label if present
    if iftype == IANA_IFTYPE_IEEE8023AD_LAG:
        agg_id = p.get("aggregate_id") or port or idx
        return f"Po{agg_id}"

    # Ethernet (1G vs 10G) – rely on speed hint if present
    speed = int(p.get("speed", 0) or 0)
    if iftype == IANA_IFTYPE_ETHERNET_CSMACD:
        if speed >= 10_000_000_000 or "10G" in (p.get("name") or ""):
            return f"Te{unit}/{slot}/{port}"
        return f"Gi{unit}/{slot}/{port}"

    # Fallbacks
    if speed >= 10_000_000_000:
        return f"Te{unit}/{slot}/{port}"
    if "Gigabit" in (p.get("name") or ""):
        return f"Gi{unit}/{slot}/{port}"
    return p.get("alias") or name


def _extract_vlan_from_name(raw: str) -> Optional[int]:
    if not raw:
        return None
    raw = raw.strip()
    # Accept 'Vl11', 'VLAN 11', 'Vlan11', etc.
    for token in raw.replace("-", " ").replace("_", " ").split():
        if token.lower().startswith("vl"):
            try:
                return int(token[2:])
            except ValueError:
                continue
        if token.lower() == "vlan":
            # next token might be the id – handled by caller if needed
            continue
        try:
            # sometimes name is literally "11" for Vl11
            v = int(token)
            if 1 <= v <= 4094:
                return v
        except ValueError:
            pass
    return None


class SwitchManagerPort(CoordinatorEntity, SwitchEntity):
    """A single switch port entity."""

    _attr_icon = "mdi:ethernet"

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        host: str,
        port: Dict[str, Any],
        friendly_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._host = host
        self._port = port
        self._friendly_name = friendly_name

        # Unique & stable per host+ifIndex
        idx = port.get("index")
        self._attr_unique_id = f"{host}-if-{idx}"
        self._attr_name = friendly_name

    # ---- Device binding (fixes “no Device” column & broken click behavior)
    @property
    def device_info(self) -> DeviceInfo:
        sys = self.coordinator.data.get("system", {}) or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=sys.get("hostname") or self._host,
            manufacturer=sys.get("manufacturer"),
            model=sys.get("model"),
            sw_version=sys.get("firmware"),
        )

    # ---- Switch state
    @property
    def is_on(self) -> bool:
        # 'admin' 1=up / 2=down is what we were using
        return int(self._port.get("admin", 0) or 0) == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_admin(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_admin(2)

    async def _set_admin(self, state: int) -> None:
        client = self.coordinator.data.get("client")
        if not client:
            _LOGGER.debug("No SNMP client available to set admin on %s", self.entity_id)
            return
        idx = int(self._port.get("index"))
        await client.async_set_admin_status(idx, state)
        # Request a refresh
        await self.coordinator.async_request_refresh()

    # ---- Attributes
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        p = self._port
        attrs: Dict[str, Any] = {
            "Index": p.get("index"),
            "Name": p.get("name"),
            "Alias": p.get("alias"),
            "Admin": p.get("admin"),
            "Oper": p.get("oper"),
        }
        # If an IP (and mask bits) were already computed upstream, include it
        if p.get("ip_cidr"):
            attrs["IP address"] = p["ip_cidr"]
        elif p.get("ip"):
            attrs["IP address"] = p["ip"]
        return attrs

    # Keep entity enabled by default
    @property
    def available(self) -> bool:
        # Use coordinator success; specific ports can be unavailable if missing
        return self.coordinator.last_update_success
