from __future__ import annotations

import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Switch Manager port switches."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    ports = coordinator.data.get("ports", {})

    entities: list[SwitchManagerPort] = []

    # Accept both dict[int, dict] and list[dict] or list[int]
    if isinstance(ports, dict):
        iterable = ports.values()
    else:
        iterable = ports

    for port in iterable:
        if isinstance(port, dict):
            idx = port.get("index") or port.get("ifIndex") or 0
        else:
            idx = int(port)
        entities.append(SwitchManagerPort(coordinator, entry, idx))

    async_add_entities(entities)


class SwitchManagerPort(CoordinatorEntity, SwitchEntity):
    """Representation of a network switch port."""

    _attr_should_poll = False

    def __init__(self, coordinator, entry, port_index: int):
        """Initialize the switch port entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._port_index = port_index
        self._attr_unique_id = f"{entry.entry_id}_{port_index}"
        self._attr_name = f"Port {port_index}"

    @property
    def is_on(self) -> bool:
        """Return True if port is administratively up."""
        ports = self.coordinator.data.get("ports", {})
        port = ports.get(self._port_index)
        if isinstance(port, dict):
            admin = port.get("admin")
            oper = port.get("oper")
            return admin == 1 and oper == 1
        return False

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the switch port."""
        client = self.coordinator.client
        try:
            await client.async_set_octet_string(
                f"1.3.6.1.2.1.2.2.1.7.{self._port_index}", 1
            )  # ifAdminStatus.1 = up(1)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to enable port %s: %s", self._port_index, err)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the switch port."""
        client = self.coordinator.client
        try:
            await client.async_set_octet_string(
                f"1.3.6.1.2.1.2.2.1.7.{self._port_index}", 2
            )  # ifAdminStatus.1 = down(2)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to disable port %s: %s", self._port_index, err)

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        """Expose additional port attributes."""
        ports = self.coordinator.data.get("ports", {})
        port = ports.get(self._port_index)
        if not isinstance(port, dict):
            return {}
        return {
            "name": port.get("name"),
            "alias": port.get("alias"),
            "admin": port.get("admin"),
            "oper": port.get("oper"),
            "index": port.get("index"),
        }
