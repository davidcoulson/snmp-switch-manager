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

_LOGGER = logging.getLogger(__name__)

# ---- Safe local ifType numbers (inline so we don’t import from snmp.py)
IFT_ETHERNET_CSMACD = 6           # ethernetCsmacd
IFT_SOFTWARE_LOOPBACK = 24        # softwareLoopback
IFT_PROP_VIRTUAL = 53             # propVirtual (some loopbacks/VLANs show as this)
IFT_L2VLAN = 135                  # l2vlan (common for VLAN SVI)
IFT_L3IPVLAN = 136                # l3ipvlan (also seen for VLAN SVI)
IFT_IEEE8023AD_LAG = 161          # ieee8023adLag

def _is_vlan_iftype(v: Optional[int]) -> bool:
    return v in {IFT_L2VLAN, IFT_L3IPVLAN, IFT_PROP_VIRTUAL}

def _is_loopback_iftype(v: Optional[int], name: str, alias: str) -> bool:
    if v == IFT_SOFTWARE_LOOPBACK:
        return True
    t = (name or "").lower() + " " + (alias or "").lower()
    return "loopback" in t

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Switch Manager switches from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    host: str = data["host"]

    ports: list[dict[str, Any]] = coordinator.data.get("ports", [])
    entities: list[SwitchManagerPort] = []

    for p in ports:
        if p.get("is_cpu"):
            continue
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

    if entities:
        async_add_entities(entities)

def _friendly_name(p: Dict[str, Any]) -> str:
    idx = p.get("index")
    name = str(p.get("name") or "")
    alias = str(p.get("alias") or "")
    iftype = p.get("ifType")
    unit = p.get("unit", 1)
    slot = p.get("slot", 0)
    port = p.get("port", idx)

    # VLAN SVI
    if _is_vlan_iftype(iftype) or name.lower().startswith(("vl", "vlan")):
        vlan_id = p.get("vlan_id") or _extract_vlan_from_name(name or alias)
        if vlan_id:
            return f"Vl{vlan_id}"

    # Loopback
    if _is_loopback_iftype(iftype, name, alias):
        return "Lo0"

    # LAG
    if iftype == IFT_IEEE8023AD_LAG or name.lower().startswith(("po", "port-channel", "lag")):
        agg_id = p.get("aggregate_id") or port or idx
        return f"Po{agg_id}"

    # Ethernet (Gi/Te) – use speed hint if available
    speed = int(p.get("speed", 0) or 0)
    if iftype == IFT_ETHERNET_CSMACD:
        if speed >= 10_000_000_000 or "10g" in name.lower():
            return f"Te{unit}/{slot}/{port}"
        return f"Gi{unit}/{slot}/{port}"

    if speed >= 10_000_000_000 or "10g" in name.lower():
        return f"Te{unit}/{slot}/{port}"
    if "gigabit" in name.lower():
        return f"Gi{unit}/{slot}/{port}"

    return alias or (f"Port {idx}" if idx is not None else "Port")

def _extract_vlan_from_name(raw: str) -> Optional[int]:
    if not raw:
        return None
    raw = raw.replace("-", " ").replace("_", " ")
    parts = raw.split()
    for i, tok in enumerate(parts):
        lo = tok.lower()
        if lo.startswith("vl"):
            try:
                return int(tok[2:])
            except ValueError:
                continue
        if lo == "vlan" and i + 1 < len(parts):
            try:
                return int(parts[i + 1])
            except ValueError:
                continue
        try:
            v = int(tok)
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
        self._attr_name = friendly_name
        idx = port.get("index")
        self._attr_unique_id = f"{host}-if-{idx}"

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

    @property
    def is_on(self) -> bool:
        return int(self._port.get("admin", 0) or 0) == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_admin(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_admin(2)

    async def _set_admin(self, state: int) -> None:
        client = self.coordinator.data.get("client")
        if not client:
            return
        idx = int(self._port.get("index"))
        await client.async_set_admin_status(idx, state)
        await self.coordinator.async_request_refresh()

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
        if p.get("ip_cidr"):
            attrs["IP address"] = p["ip_cidr"]
        elif p.get("ip"):
            attrs["IP address"] = p["ip"]
        return attrs

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
