from __future__ import annotations

import importlib
import logging
from typing import Iterable, Tuple, Any, Optional

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

    We intentionally avoid the legacy CommandGenerator API; pysnmp 4.4.12 and
    pysnmp-lextudio 5.x both provide the HLAPI we use here.
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
    """
    Backward-compatible hook used by the config flow to force a fresh import.

    Earlier versions switched between multiple backends; we only use HLAPI,
    but we keep this symbol so old imports don't fail.
    """
    global _IMPORTS_CACHE
    _IMPORTS_CACHE = None


# -----------------------------------------------------------------------------
# Public helpers
# -----------------------------------------------------------------------------

def ensure_snmp_available() -> None:
    """Verify HLAPI is importable (raises SnmpDependencyError on failure)."""
    _imports()


def validate_environment_or_raise() -> None:
    """Back-compat alias used by older code paths."""
    ensure_snmp_available()


def snmp_get(host: str, community: str, port: int, oid: str) -> Any:
    """Perform a single GET and return the value for the OID."""
    (
        SnmpEngine,
        CommunityData,
        UdpTransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        getCmd,
        _nextCmd,
        _setCmd,
    ) = _imports()

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


def snmp_walk(
    host: str, community: str, port: int, base_oid: str
) -> Iterable[Tuple[str, Any]]:
    """Walk (nextCmd) starting at base_oid and yield (oid, value) pairs."""
    (
        SnmpEngine,
        CommunityData,
        UdpTransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        _getCmd,
        nextCmd,
        _setCmd,
    ) = _imports()

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


def snmp_set_octet_string(
    host: str, community: str, port: int, oid: str, value
) -> None:
    """Set an OCTET STRING (or compatible) value."""
    (
        SnmpEngine,
        CommunityData,
        UdpTransportTarget,
        ContextData,
        ObjectType,
        ObjectIdentity,
        _getCmd,
        _nextCmd,
        setCmd,
    ) = _imports()

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
# Backward-compatible client wrapper (sync + async)
# -----------------------------------------------------------------------------

class SwitchSnmpClient:
    """
    Thin wrapper preserved for compatibility with existing modules.

    Provides simple get/walk/set methods bound to a host/community/port and
    async wrappers that run in HA's executor.
    """

    def __init__(self, hass, host: str, community: str, port: int = 161) -> None:
        self._hass = hass
        self._host = host
        self._community = community
        self._port = int(port)

    # ---- factory used by __init__.py ----------------------------------------

    @classmethod
    async def async_create(cls, hass, host: str, community: str, port: int = 161):
        # Ensure dependency is present without blocking the event loop
        await hass.async_add_executor_job(ensure_snmp_available)
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
        return await self._hass.async_add_executor_job(
            snmp_get, self._host, self._community, self._port, oid
        )

    async def async_walk(self, base_oid: str) -> Iterable[Tuple[str, Any]]:
        return await self._hass.async_add_executor_job(
            lambda: list(snmp_walk(self._host, self._community, self._port, base_oid))
        )

    async def async_set_octet_string(self, oid: str, value) -> None:
        await self._hass.async_add_executor_job(
            snmp_set_octet_string, self._host, self._community, self._port, oid, value
        )
