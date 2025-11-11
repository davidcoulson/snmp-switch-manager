from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Iterable, Tuple

from homeassistant.core import HomeAssistant

# Use synchronous HLAPI (stable across HA Python versions) and run in executor
from pysnmp.hlapi import (  # type: ignore[import]
    CommunityData,
    SnmpEngine,
    UdpTransportTarget,
    ContextData,
    ObjectType,
    ObjectIdentity,
    getCmd,
    nextCmd,
    setCmd,
)
from pysnmp.proto.rfc1902 import OctetString, Integer  # type: ignore[import]

from .const import (
    OID_sysDescr,
    OID_sysName,
    OID_sysUpTime,
    OID_ifIndex,
    OID_ifDescr,
    OID_ifAdminStatus,
    OID_ifOperStatus,
    OID_ifName,
    OID_ifAlias,
    OID_ipAdEntAddr,
    OID_ipAdEntIfIndex,
    OID_ipAdEntNetMask,
)

_LOGGER = logging.getLogger(__name__)

# --- Fallback OIDs kept local (no other files touched) ---
_OID_ipAddressAddr = "1.3.6.1.2.1.4.34.1.2"            # OCTET STRING (IPv4 only when 4 bytes)
_OID_ipAddressIfIndex = "1.3.6.1.2.1.4.34.1.3"         # Integer (ifIndex)
_OID_ipCidrRoutePrefixLength = "1.3.6.1.2.1.4.24.4.1.3"  # Integer (bits)
_OID_ipCidrRouteIfIndex      = "1.3.6.1.2.1.4.24.4.1.7"  # Integer (ifIndex)


# ------------------ low-level sync helpers ------------------

def _do_get_one(
    engine: SnmpEngine,
    community: CommunityData,
    target: UdpTransportTarget,
    context: ContextData,
    oid: str,
) -> Optional[str]:
    it = getCmd(engine, community, target, context, ObjectType(ObjectIdentity(oid)))
    err_ind, err_stat, err_idx, vbs = next(it)
    if err_ind or err_stat:
        return None
    return str(vbs[0][1])


def _do_next_walk(
    engine: SnmpEngine,
    community: CommunityData,
    target: UdpTransportTarget,
    context: ContextData,
    base_oid: str,
) -> Iterable[Tuple[str, Any]]:
    it = nextCmd(
        engine,
        community,
        target,
        context,
        ObjectType(ObjectIdentity(base_oid)),
        lexicographicMode=False,
    )
    for err_ind, err_stat, err_idx, vbs in it:
        if err_ind or err_stat:
            break
        for vb in vbs:
            oid_obj, val = vb
            yield str(oid_obj), val


def _do_set_alias(
    engine: SnmpEngine,
    community: CommunityData,
    target: UdpTransportTarget,
    context: ContextData,
    if_index: int,
    alias: str,
) -> bool:
    it = setCmd(
        engine,
        community,
        target,
        context,
        ObjectType(ObjectIdentity(f"{OID_ifAlias}.{if_index}"), OctetString(alias)),
    )
    err_ind, err_stat, err_idx, _ = next(it)
    return (not err_ind) and (not err_stat)


def _do_set_admin_status(
    engine: SnmpEngine,
    community: CommunityData,
    target: UdpTransportTarget,
    context: ContextData,
    if_index: int,
    value: int,
) -> bool:
    it = setCmd(
        engine,
        community,
        target,
        context,
        ObjectType(ObjectIdentity(f"1.3.6.1.2.1.2.2.1.7.{if_index}"), Integer(value)),
    )
    err_ind, err_stat, err_idx, _ = next(it)
    return (not err_ind) and (not err_stat)


# ------------------ small utils ------------------

def _octets_to_ipv4(val: Any) -> Optional[str]:
    """Try to coerce an OCTET STRING to dotted IPv4 (expects exactly 4 bytes)."""
    try:
        bs = bytes(val)
    except Exception:
        try:
            bs = val.asOctets()  # type: ignore[attr-defined]
        except Exception:
            s = str(val)
            return s if s.count(".") == 3 else None
    if len(bs) == 4:
        return ".".join(str(b) for b in bs)
    return None


def _bits_to_mask(bits: int) -> str:
    """Convert CIDR bits (0..32) to dotted mask."""
    if bits <= 0:
        return "0.0.0.0"
    if bits >= 32:
        return "255.255.255.255"
    mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
    return ".".join(str((mask >> s) & 0xFF) for s in (24, 16, 8, 0))


# ------------------ client ------------------

class SwitchSnmpClient:
    """SNMP client that exposes async methods by offloading sync pysnmp calls to a thread."""

    def __init__(self, hass: HomeAssistant, host: str, community: str, port: int) -> None:
        self.hass = hass
        self.host = host
        self.community = community
        self.port = port

        self.engine = SnmpEngine()
        self.target = UdpTransportTarget((host, port), timeout=1.5, retries=1)
        self.community_data = CommunityData(community, mpModel=1)  # v2c
        self.context = ContextData()

        self.cache: Dict[str, Any] = {
            "sysDescr": None,
            "sysName": None,
            "sysUpTime": None,
            "ifTable": {},     # index -> dict
            "ipIndex": {},     # ip -> ifIndex
            "ipMask": {},      # ip -> dotted mask
        }

    async def async_initialize(self) -> None:
        self.cache["sysDescr"] = await self._async_get_one(OID_sysDescr)
        self.cache["sysName"] = await self._async_get_one(OID_sysName)
        self.cache["sysUpTime"] = await self._async_get_one(OID_sysUpTime)
        await self._walk_interfaces()
        await self._walk_ipv4()

    async def async_poll(self) -> Dict[str, Any]:
        await self._walk_interfaces(dynamic_only=True)
        await self._walk_ipv4()
        return self.cache

    # ---------- async wrappers over sync calls ----------

    async def _async_get_one(self, oid: str) -> Optional[str]:
        return await self.hass.async_add_executor_job(
            _do_get_one, self.engine, self.community_data, self.target, self.context, oid
        )

    async def _async_walk(self, base_oid: str) -> list[tuple[str, Any]]:
        def _collect():
            return list(_do_next_walk(self.engine, self.community_data, self.target, self.context, base_oid))
        return await self.hass.async_add_executor_job(_collect)

    # ---------- walkers ----------

    async def _walk_interfaces(self, dynamic_only: bool = False) -> None:
        if not dynamic_only:
            self.cache
