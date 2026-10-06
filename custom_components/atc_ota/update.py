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
        self._sync_progress_attrs()

    def _sync_progress_attrs(self) -> None:
        """Keep Home Assistant's native update progress attributes in sync."""
        state = self.state_data
        self._attr_in_progress = bool(state.ota_in_progress)
        self._attr_update_percentage = (
            state.ota_progress if state.ota_in_progress else None
        )

    def _async_manager_updated(self) -> None:
        """Sync native update progress before publishing the new entity state."""
        self._sync_progress_attrs()
        self.async_write_ha_state()

    @property
    def installed_version(self):
        return self.state_data.current_version

    @property
    def latest_version(self):
        return self.state_data.latest_version

    @property
    def release_summary(self):
        state = self.state_data
        battery = "unknown" if state.battery is None else f"{state.battery}%"
        if state.battery is not None and state.battery <= self.manager.low_battery_threshold:
            return (
                f"BLOCKED: battery {battery} is at/below the configured "
                f"{self.manager.low_battery_threshold}% OTA minimum."
            )
        gatt = (
            f"{state.gatt_proxy} / {state.gatt_rssi} dBm"
            if state.gatt_proxy and state.gatt_rssi is not None
            else "no connectable route"
        )
        return (
            f"Stable pvvx firmware. OTA readiness: {state.ota_readiness}. "
            f"Battery: {battery}. GATT: {gatt}."
        )

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
            "Firmware is obtained from the official pvvx/ATC_MiThermometer repository, validated and "
            "cached persistently in Home Assistant after the first download. Later updates using the same "
            "image reuse the local cache. Transfer still goes through Home Assistant's shared Bluetooth "
            "stack; the integration does not connect directly to ESPHome Bluetooth Proxies."
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
            "home_assistant_area_id": state.ha_area_id,
            "ble_name": state.name,
            "model": state.model,
            "hardware_revision": state.hardware_revision,
            "battery": state.battery,
            "battery_last_seen": state.battery_last_seen,
            "broadcast_rssi": state.rssi,
            "broadcast_proxy": state.strongest_proxy,
            "gatt_rssi": state.gatt_rssi,
            "gatt_proxy": state.gatt_proxy,
            "gatt_route_age_seconds": state.gatt_route_age_seconds,
            "gatt_failures": state.gatt_failures,
            "gatt_free_slots": state.gatt_free_slots,
            "gatt_slots": state.gatt_slots,
            "ota_readiness": state.ota_readiness,
            "ota_readiness_reason": state.ota_readiness_reason,
            "ota_message": state.ota_message,
            "ota_progress": state.ota_progress,
            "firmware_source": state.firmware_source,
            "firmware_cache_file": state.firmware_cache_file,
            "firmware_sha256": state.firmware_sha256,
            "last_metadata_error": state.last_metadata_error,
        }
