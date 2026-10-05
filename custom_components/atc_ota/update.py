"""Firmware update entity for ATC/pvvx thermometers."""

from __future__ import annotations

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import PVVX_REPO_URL
from .entity import AtcOtaEntity
from .manager import signal_device_added, version_tuple


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    manager = entry.runtime_data
    known: set[str] = set()

    def add(address: str) -> None:
        if address in known:
            return
        known.add(address)
        async_add_entities([AtcFirmwareUpdate(manager, address)])

    for address in manager.devices:
        add(address)
    entry.async_on_unload(async_dispatcher_connect(hass, signal_device_added(entry.entry_id), add))


class AtcFirmwareUpdate(AtcOtaEntity, UpdateEntity):
    _attr_name = "Firmware"
    _attr_title = "pvvx ATC firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS | UpdateEntityFeature.RELEASE_NOTES
    )
    _attr_release_url = PVVX_REPO_URL
    _attr_auto_update = False

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_firmware"

    @property
    def installed_version(self):
        return self.state_data.current_version

    @property
    def latest_version(self):
        return self.state_data.latest_version

    @property
    def in_progress(self):
        return self.state_data.ota_in_progress

    @property
    def update_percentage(self):
        return self.state_data.ota_progress if self.state_data.ota_in_progress else None

    @property
    def release_summary(self):
        state = self.state_data
        battery = "unknown" if state.battery is None else f"{state.battery}%"
        if state.battery is not None and state.battery <= self.manager.low_battery_threshold:
            return (
                f"BLOCKED: battery {battery} is at/below the configured "
                f"{self.manager.low_battery_threshold}% OTA minimum."
            )
        return f"Stable pvvx firmware. Battery: {battery}. RSSI: {state.rssi if state.rssi is not None else 'unknown'} dBm."

    async def async_release_notes(self):
        state = self.state_data
        warning = ""
        if state.battery is not None and state.battery <= self.manager.low_battery_threshold:
            warning = (
                f"\n\n⚠️ OTA is **blocked** while battery is **{state.battery}%**, at/below the configured "
                f"minimum of **{self.manager.low_battery_threshold}%**. Lower the integration option only if "
                "you intentionally want to accept the risk."
            )
        return (
            "Firmware is downloaded from the official pvvx/ATC_MiThermometer repository and transferred "
            "through Home Assistant's shared Bluetooth stack. The integration does not connect directly to "
            "ESPHome Bluetooth Proxies."
            + warning
        )

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        latest = version_tuple(latest_version)
        installed = version_tuple(installed_version)
        if latest is None or installed is None:
            return latest_version != installed_version
        width = max(len(latest), len(installed))
        return latest + (0,) * (width - len(latest)) > installed + (0,) * (width - len(installed))

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        await self.manager.async_install_latest(self.address)

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "address": state.address,
            "home_assistant_name": state.ha_name,
            "home_assistant_area": state.ha_area,
            "ble_name": state.name,
            "model": state.model,
            "hardware_revision": state.hardware_revision,
            "battery": state.battery,
            "battery_last_seen": state.battery_last_seen,
            "strongest_rssi": state.rssi,
            "strongest_proxy": state.strongest_proxy,
            "ota_message": state.ota_message,
            "last_metadata_error": state.last_metadata_error,
        }
