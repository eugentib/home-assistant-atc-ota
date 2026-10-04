"""Sensors for ATC OTA."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE
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
        async_add_entities([AtcBatterySensor(manager, address), AtcRssiSensor(manager, address)])

    for address in manager.devices:
        add(address)
    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_device_added(entry.entry_id), add)
    )


class AtcBatterySensor(AtcOtaEntity, SensorEntity):
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_battery"

    @property
    def native_value(self):
        return self.state_data.battery


class AtcRssiSensor(AtcOtaEntity, SensorEntity):
    _attr_name = "Bluetooth RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_icon = "mdi:signal"

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_rssi"

    @property
    def native_value(self):
        return self.state_data.rssi

    @property
    def extra_state_attributes(self):
        return {"strongest_proxy": self.state_data.strongest_proxy}
