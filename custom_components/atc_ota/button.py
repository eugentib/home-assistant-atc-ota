"""Buttons for ATC OTA."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .entity import AtcOtaEntity
from .manager import signal_device_added


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    manager = entry.runtime_data
    known: set[str] = set()

    def add(address: str) -> None:
        if address in known:
            return
        known.add(address)
        async_add_entities([AtcRefreshInfoButton(manager, address)])

    for address in manager.devices:
        add(address)
    entry.async_on_unload(async_dispatcher_connect(hass, signal_device_added(entry.entry_id), add))


class AtcRefreshInfoButton(AtcOtaEntity, ButtonEntity):
    _attr_name = "Refresh firmware info"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_refresh_firmware_info"

    async def async_press(self) -> None:
        await self.manager.async_user_refresh_device(self.address)
