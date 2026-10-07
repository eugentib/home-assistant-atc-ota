"""Shared entity base for ATC OTA."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .manager import signal_device_updated


class AtcOtaEntity(Entity):
    """Base entity bound to one discovered thermometer."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager, address: str) -> None:
        # Keep the config entry, not a permanently captured manager instance.
        # If Home Assistant reloads/replaces runtime_data while an entity object
        # survives longer than expected, every entity action must still use the
        # current manager.
        self._entry = manager.entry
        self._created_manager_instance = manager.instance_id
        self.address = address

    @property
    def manager(self):
        """Return the manager currently owned by this config entry."""
        return self._entry.runtime_data

    @property
    def created_manager_instance(self) -> str:
        """Manager instance this entity object was originally created with."""
        return self._created_manager_instance

    @property
    def state_data(self):
        return self.manager.devices[self.address]

    @property
    def device_info(self) -> DeviceInfo:
        state = self.state_data
        return DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            name=state.ha_name or state.name or f"ATC {self.address[-8:].replace(':', '')}",
            manufacturer=state.manufacturer or "Xiaomi / pvvx",
            model=state.model or "ATC/pvvx thermometer",
            hw_version=state.hardware_revision,
            sw_version=state.current_version,
            suggested_area=state.ha_area,
            configuration_url="https://github.com/pvvx/ATC_MiThermometer",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_device_updated(self._entry.entry_id, self.address),
                self._async_manager_updated,
            )
        )

    def _async_manager_updated(self) -> None:
        self.async_write_ha_state()
