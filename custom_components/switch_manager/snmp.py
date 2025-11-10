from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Iterable, Tuple, Any, Optional, Dict

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "SnmpError",
    "SnmpDependencyError",
    "SwitchSnmpClient",
    "ensure_snmp_available",
    "validate_environment_or_raise",
    "reset_backend_cache",
    "snmp_get",
    "snmp_walk",
    "snmp_set_octet_string",
]

# -----------------------------------------------------------------------------
# Exceptions (backward compatible)
# -----------------------------------------------------------------------------

class SnmpError(RuntimeError):
    """Base SNMP error for this integration."""


class SnmpDependencyError(SnmpError):
    """Raised when pysnmp cannot be imported/used."""


# -----------------------------------------------------------------------------
# Lazy HLAPI import with a tiny in-module cache
# -----------------------------------------------------------------------------

_IMPORTS_CACHE: Optional[tuple] = None


def _imports():
    """
    Lazy-import pysnmp HLAPI so HA can install requirements first.

    Works with pysnmp 4.4.12 (classic) and pysnmp-lextudio 5.x; we only use HLAPI.
    """
    global _IMPORTS_CACHE
    if _IMPORTS_CACHE is not None:
        return _IMPORTS_CACHE

    try:
        pysnmp_mod = importlib.import_module("pysnmp")
        _LOGGER.debug(
            "Switch Manager using pysnmp from %s (version=%s)",
            getattr(pysnmp_mod, "__file__", "?"),
            getattr(pysnmp_mod, "__version__", "?"),
        )

        from pysnmp.hlapi import (  # type: ignore
            SnmpEngine,
            CommunityData,
            UdpTransportTarget,
            ContextData,
            ObjectType,
            ObjectIdentity,
            getCmd,
            nextCmd,
            setCmd,
        )
    except Exception as e:  # pragma: no cover
        raise SnmpDependencyError(f"pysnmp.hlapi import failed: {e}")

    _IMPORTS_CACHE = (
        SnmpEngine,
        CommunityData,
        UdpTransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        getCmd,
        nextCmd,
        setCmd,
    )
    return _IMPORTS_CACHE


def reset_backend_cache() -> None:
    """Back-compat hook to clear our small import cache."""
    global _IMPORTS_CACHE
    _IMPORTS_CACHE = None


# -----------------------------------------------------------------------------
# Public low-level helpers
# -----------------------------------------------------------------------------

def ensure_snmp_available() -> None:
    """Verify HLAPI is importable (raises SnmpDependencyError on failure)."""
    _imports()


def validate_environment_or_raise() -> None:
    """Back-compat alias used by older code paths."""
    ensure_snmp_available()


def snmp_get(host: str, community: str, port: int, oid: str) -> Any:
    (SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
     ObjectType, ObjectIdentity, getCmd, _nextCmd, _setCmd) = _imports()

    iterator = getCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=1),  # SNMP v2c
        UdpTransportTarget((host, int(port))),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
    )

    error_indication, error_status, error_index, var_binds = next(iterator)
    if error_indication:
        raise SnmpError(error_indication)
    if error_status:
        where = var_binds[int(error_index) - 1][0] if error_index else "?"
        raise SnmpError(f"{error_status.prettyPrint()} at {where}")
    return var_binds[0][1]


def snmp_walk(host: str, community: str, port: int, base_oid: str) -> Iterable[Tuple[str, Any]]:
    (SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
     ObjectType, ObjectIdentity, _getCmd, nextCmd, _setCmd) = _imports()

    for (err_ind, err_stat, err_idx, var_binds) in nextCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=1),
        UdpTransportTarget((host, int(port))),
        ContextData(),
        ObjectType(ObjectIdentity(base_oid)),
        lexicographicMode=False,
    ):
        if err_ind:
            raise SnmpError(err_ind)
        if err_stat:
            where = var_binds[int(err_idx) - 1][0] if err_idx else "?"
            raise SnmpError(f"{err_stat.prettyPrint()} at {where}")
        for name, val in var_binds:
            yield (str(name), val)


def snmp_set_octet_string(host: str, community: str, port: int, oid: str, value) -> None:
    (SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
     ObjectType, ObjectIdentity, _getCmd, _nextCmd, setCmd) = _imports()

    err_ind, err_stat, err_idx, var_binds = next(
        setCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1),
            UdpTransportTarget((host, int(port))),
            ContextData(),
            ObjectType(ObjectIdentity(oid), value),
        )
    )
    if err_ind:
        raise SnmpError(err_ind)
    if err_stat:
        where = var_binds[int(err_idx) - 1][0] if err_idx else "?"
        raise SnmpError(f"{err_stat.prettyPrint()} at {where}")


# -----------------------------------------------------------------------------
# Backward-compatible client wrapper (sync + async; tolerant factory)
# -----------------------------------------------------------------------------

class SwitchSnmpClient:
    """
    Thin wrapper preserved for compatibility with existing modules.

    Provides simple get/walk/set methods bound to a host/community/port and
    async wrappers that run in an executor when hass is unavailable.
    """

    # Standard IF-MIB OIDs we’ll use
    OID_ifDescr = "1.3.6.1.2.1.2.2.1.2"
    OID_ifAdminStatus = "1.3.6.1.2.1.2.2.1.7"
    OID_ifOperStatus = "1.3.6.1.2.1.2.2.1.8"
    OID_ifAlias = "1.3.6.1.2.1.31.1.1.1.18"  # IF-MIB::ifAlias (human description)

    def __init__(self, hass, host: str, community: str, port: int = 161) -> None:
        self._hass = hass  # may be None
        self._host = host
        self._community = community
        self._port = int(port)

    # ---- tolerant factory: supports (hass, host, community, port) OR (host, community, port, hass)

    @classmethod
    async def async_create(cls, *args):
        """
        Create a client.

        Accepted orders:
          1) (hass, host, community, port)
          2) (host, community, port, hass)
        """
        hass = None
        host = ""
        community = ""
        port = 161

        if not args:
            raise SnmpError("async_create requires arguments")

        # Case 1: first arg looks like hass (has async_add_executor_job)
        first = args[0]
        if hasattr(first, "async_add_executor_job"):
            # (hass, host, community, port?)
            hass = first
            try:
                host = args[1]
                community = args[2]
                port = int(args[3]) if len(args) > 3 else 161
            except Exception as e:
                raise SnmpError(f"async_create(hass, host, community, port) invalid: {e}")
        else:
            # (host, community, port?, hass?)
            try:
                host = args[0]
                community = args[1]
                port = int(args[2]) if len(args) > 2 else 161
                hass = args[3] if len(args) > 3 and hasattr(args[3], "async_add_executor_job") else None
            except Exception as e:
                raise SnmpError(f"async_create(host, community, port[, hass]) invalid: {e}")

        # Ensure dependency without blocking the loop
        if hass is not None:
            await hass.async_add_executor_job(ensure_snmp_available)
        else:
            ensure_snmp_available()

        return cls(hass, host, community, port)

    # ---- sync helpers (legacy callers) --------------------------------------

    def get(self, oid: str) -> Any:
        return snmp_get(self._host, self._community, self._port, oid)

    def walk(self, base_oid: str) -> Iterable[Tuple[str, Any]]:
        return snmp_walk(self._host, self._community, self._port, base_oid)

    def set_octet_string(self, oid: str, value) -> None:
        snmp_set_octet_string(self._host, self._community, self._port, oid, value)

    # ---- async helpers (preferred for HA) -----------------------------------

    async def async_get(self, oid: str) -> Any:
        if self._hass is not None:
            return await self._hass.async_add_executor_job(
                snmp_get, self._host, self._community, self._port, oid
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, snmp_get, self._host, self._community, self._port, oid)

    async def async_walk(self, base_oid: str) -> Iterable[Tuple[str, Any]]:
        if self._hass is not None:
            return await self._hass.async_add_executor_job(
                lambda: list(snmp_walk(self._host, self._community, self._port, base_oid))
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: list(snmp_walk(self._host, self._community, self._port, base_oid)))

    async def async_set_octet_string(self, oid: str, value) -> None:
        if self._hass is not None:
            await self._hass.async_add_executor_job(
                snmp_set_octet_string, self._host, self._community, self._port, oid, value
            )
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, snmp_set_octet_string, self._host, self._community, self._port, oid, value)

    # ---- High-level helpers expected by coordinator --------------------------

    def get_port_data(self) -> Dict[int, Dict[str, Any]]:
        """
        Gather port data from IF-MIB.

        Returns:
          {
            <ifIndex>: {
              "index": int,
              "name": str,        # ifDescr
              "admin": int,       # ifAdminStatus (1=up,2=down,3=testing)
              "oper": int,        # ifOperStatus
              "alias": str        # ifAlias (port description)
            },
            ...
          }
        """
        # Walk individual tables then merge by ifIndex
        descr = {int(oid.split(".")[-1]): str(val) for oid, val in self.walk(self.OID_ifDescr)}
        admin = {int(oid.split(".")[-1]): int(val) for oid, val in self.walk(self.OID_ifAdminStatus)}
        oper  = {int(oid.split(".")[-1]): int(val) for oid, val in self.walk(self.OID_ifOperStatus)}
        alias = {int(oid.split(".")[-1]): str(val) for oid, val in self.walk(self.OID_ifAlias)}

        indices = set(descr) | set(admin) | set(oper) | set(alias)
        out: Dict[int, Dict[str, Any]] = {}
        for idx in sorted(indices):
            out[idx] = {
                "index": idx,
                "name": descr.get(idx, ""),
                "admin": admin.get(idx, 0),
                "oper": oper.get(idx, 0),
                "alias": alias.get(idx, ""),
            }
        return out

    async def async_get_port_data(self) -> Dict[int, Dict[str, Any]]:
        if self._hass is not None:
            return await self._hass.async_add_executor_job(self.get_port_data)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_port_data)
