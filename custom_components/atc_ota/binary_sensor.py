"""Binary sensors for ATC OTA."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory

from .entity import AtcOtaEntity
from .manager import signal_device_added


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    manager = entry.runtime_data
    known: set[str] = set()

    def add(address: str) -> None:
        if address in known:
            return
        known.add(address)
        async_add_entities([AtcLowBatteryBinarySensor(manager, address)])

    for address in manager.devices:
        add(address)
    entry.async_on_unload(async_dispatcher_connect(hass, signal_device_added(entry.entry_id), add))


class AtcLowBatteryBinarySensor(AtcOtaEntity, BinarySensorEntity):
    _attr_name = "Low battery"
    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_low_battery"

    @property
    def is_on(self):
        battery = self.state_data.battery
        return battery is not None and battery <= self.manager.low_battery_threshold

    @property
    def available(self) -> bool:
        return self.state_data.battery_last_seen is not None
