from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _resolve_runtime(hass: HomeAssistant, entry_id: str) -> Optional[Dict[str, Any]]:
    """
    Resolve runtime node that holds {"coordinator": ..., "client": ...}.
    Be permissive but do NOT change the structure.
    """
    node = hass.data.get(DOMAIN)
    if not node:
        return None
    # Primary (working) layout
    if entry_id in node:
        rt = node.get(entry_id)
        if isinstance(rt, dict) and "coordinator" in rt:
            return rt
    # Some setups stash under "entries"
    entries = node.get("entries")
    if isinstance(entries, dict) and entry_id in entries:
        rt = entries[entry_id]
        if isinstance(rt, dict) and "coordinator" in rt:
            return rt
    return None


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    """Set up Switch Manager port switches from a config entry."""
    runtime = _resolve_runtime(hass, entry.entry_id)
    if not runtime:
        _LOGGER.error(
            "Could not resolve coordinator for entry_id=%s; hass.data keys: %s; node=%s",
            entry.entry_id,
            list(hass.data.get(DOMAIN, {}).keys()),
            type(hass.data.get(DOMAIN)).__name__,
        )
        return

    coordinator = runtime["coordinator"]
    ports: List[Dict[str, Any]] = coordinator.data.get("ports", [])
    entities: List[SwitchManagerPort] = []

    for port in ports:
        idx = port.get("index")
        if idx is None:
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
        # Preserve the previously working naming
        self._attr_name = (
            port.get("friendly_name") or port.get("descr") or f"Port {port.get('index')}"
        )
        self._attr_unique_id = f"{entry.entry_id}-if-{port.get('index')}"

    # --- SwitchEntity API -----------------------------------------------------

    @property
    def is_on(self) -> bool:
        """Admin up == True."""
        return bool(self._port.get("admin") == 1)

    async def async_turn_on(self, **kwargs) -> None:
        client = self.coordinator.data.get("client")
        if not client:
            return
        await client.async_set_admin_state(self._port.get("index"), True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        client = self.coordinator.data.get("client")
        if not client:
            return
        await client.async_set_admin_state(self._port.get("index"), False)
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Expose the same attributes as before; only add ip if backend provided it."""
        attrs: Dict[str, Any] = {
            "Index": self._port.get("index"),
            "Name": self._port.get("descr"),
            "Alias": self._port.get("alias"),
            "Admin": self._port.get("admin"),
            "Oper": self._port.get("oper"),
        }
        # NEW (safe): use preformatted ip from backend if present
        ip_display = self._port.get("ip_display")
        if ip_display:
            attrs["IP address"] = ip_display
        return attrs

    # --- Coordinator hook -----------------------------------------------------

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh local snapshot when coordinator updates."""
        idx = self._port.get("index")
        for p in self.coordinator.data.get("ports", []):
            if p.get("index") == idx:
                self._port = p
                break
        self.async_write_ha_state()
