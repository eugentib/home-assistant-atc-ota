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

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "manager_instance": self.manager.instance_id,
            "refresh_status": state.metadata_refresh_status,
            "refresh_phase": state.metadata_refresh_phase,
            "request_count": state.metadata_refresh_request_count,
            "started_at": state.metadata_refresh_started_at,
            "finished_at": state.metadata_refresh_finished_at,
            "refresh_manager_instance": state.metadata_refresh_manager_instance,
            "origin": state.metadata_refresh_origin,
            "last_success": state.last_metadata_success,
            "last_error": state.last_metadata_error,
        }
