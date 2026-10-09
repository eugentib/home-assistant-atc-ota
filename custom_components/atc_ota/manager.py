"""Bluetooth inventory and OTA manager using Home Assistant's shared Bluetooth stack."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
import re
import time
import uuid
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from bthome_ble import BTHomeBluetoothDeviceData

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .bthome import parse_bthome_v2
from .const import (
    BTHOME_UUID,
    CATALOG_REFRESH_SECONDS,
    CONF_AUTO_PROBE_METADATA,
    CONF_AUTO_PROBE_MIN_RSSI,
    CONF_LOW_BATTERY_THRESHOLD,
    DEFAULT_AUTO_PROBE_METADATA,
    DEFAULT_AUTO_PROBE_MIN_RSSI,
    DEFAULT_LOW_BATTERY_THRESHOLD,
    DOMAIN,
    ENV_SENSING_UUID,
    GATT_ROUTE_MAX_AGE_SECONDS,
    OTA_BATTERY_MAX_AGE_SECONDS,
    OTA_BATTERY_SCAN_SECONDS,
    METADATA_RETRY_SECONDS,
    METADATA_GATT_LOCK_TIMEOUT_SECONDS,
    METADATA_GATT_READ_TIMEOUT_SECONDS,
    STORAGE_KEY,
    STORAGE_VERSION,
    UUID_BATTERY_LEVEL,
    UUID_DEVICE_NAME,
    UUID_FIRMWARE_REV,
    UUID_HARDWARE_REV,
    UUID_MANUFACTURER,
    UUID_MODEL_NUMBER,
    UUID_SERIAL_NUMBER,
    UUID_SOFTWARE_REV,
)
from .firmware import fetch_catalog, get_cached_firmware, resolve_stable_firmware
from .readiness import evaluate_ota_readiness
from .ota_transport import OtaTransportMixin

_LOGGER = logging.getLogger(__name__)
_VERSION_RE = re.compile(r"^V?(\d+)(?:\.(\d+)){1,2}(?:[-+._a-zA-Z0-9]*)?$")


def signal_device_added(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_device_added"


def signal_device_updated(entry_id: str, address: str) -> str:
    return f"{DOMAIN}_{entry_id}_{address}_updated"


def normalize_version(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.lower().startswith("github.com/"):
        return None
    return text[1:] if text[:1].upper() == "V" and text[1:2].isdigit() else text


def version_tuple(value: str | None) -> tuple[int, ...] | None:
    value = normalize_version(value)
    if not value:
        return None
    match = re.search(r"(\d+(?:\.\d+)+)", value)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


@dataclass(slots=True)
class DeviceState:
    address: str
    name: str = "ATC thermometer"
    model: str | None = None
    hardware_revision: str | None = None
    software_revision: str | None = None
    firmware_revision: str | None = None
    serial: str | None = None
    manufacturer: str | None = None
    current_version: str | None = None
    latest_version: str | None = None
    latest_filename: str | None = None
    battery: int | None = None
    battery_last_seen: float | None = None
    battery_source: str | None = None
    rssi: int | None = None
    strongest_proxy: str | None = None
    last_seen: float | None = None
    ble_callback_last_seen: float | None = None
    ble_observation_time: float | None = None
    ble_callback_count: int = 0
    ble_callback_error: str | None = None
    gatt_rssi: int | None = None
    gatt_proxy: str | None = None
    gatt_route_age_seconds: int | None = None
    gatt_failures: int | None = None
    gatt_free_slots: int | None = None
    gatt_slots: int | None = None
    gatt_active_connections: bool | None = None
    gatt_feature_flags: int | None = None
    ota_readiness: str = "unknown"
    last_ota_proxy: str | None = None
    last_ota_rssi: int | None = None
    last_ota_result: str | None = None
    last_ota_at: float | None = None
    last_ota_detail: str | None = None
    ota_readiness_reason: str | None = None
    last_metadata_success: float | None = None
    last_metadata_error: str | None = None
    metadata_refresh_status: str = "idle"
    metadata_refresh_phase: str | None = None
    metadata_refresh_request_count: int = 0
    metadata_refresh_started_at: float | None = None
    metadata_refresh_finished_at: float | None = None
    metadata_refresh_manager_instance: str | None = None
    metadata_refresh_origin: str | None = None
    ota_in_progress: bool = False
    ota_progress: int | None = None
    ota_message: str | None = None
    firmware_source: str | None = None
    firmware_cache_file: str | None = None
    firmware_sha256: str | None = None
    ha_name: str | None = None
    ha_area: str | None = None
    ha_area_id: str | None = None

    def persistent(self) -> dict[str, Any]:
        """Return only durable inventory fields.

        RSSI, scanner routes, last-seen timestamps and OTA runtime state are
        intentionally excluded: they are live observations and persisting them
        caused unnecessary .storage writes while also restoring stale BLE data.
        """
        durable = (
            "address",
            "name",
            "model",
            "hardware_revision",
            "software_revision",
            "firmware_revision",
            "serial",
            "manufacturer",
            "current_version",
            "latest_version",
            "latest_filename",
            "battery",
            "last_metadata_success",
            "last_metadata_error",
            "firmware_source",
            "firmware_cache_file",
            "firmware_sha256",
            "last_ota_proxy",
            "last_ota_rssi",
            "last_ota_result",
            "last_ota_at",
            "last_ota_detail",
            "ha_name",
            "ha_area",
            "ha_area_id",
        )
        return {key: getattr(self, key) for key in durable}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeviceState":
        allowed = {field.name for field in cls.__dataclass_fields__.values() if field.init}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


class AtcManager(OtaTransportMixin):
    """Own the integration state while HA owns all Bluetooth proxy/scanner connections."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.devices: dict[str, DeviceState] = {}
        self.catalog: dict[str, Any] | None = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._gatt_lock = asyncio.Lock()
        self._ota_lock = asyncio.Lock()
        self._installing_addresses: set[str] = set()
        self._pending_install_addresses: set[str] = set()
        self._install_tasks: dict[str, asyncio.Task] = {}
        self._shutting_down = False
        self.instance_id = uuid.uuid4().hex[:8]
        self._metadata_tasks: dict[str, asyncio.Task] = {}
        self._route_refresh_tasks: dict[str, asyncio.Task] = {}
        self._bthome_parsers: dict[str, BTHomeBluetoothDeviceData] = {}
        self._save_task: asyncio.Task | None = None
        self._last_metadata_attempt: dict[str, float] = {}
        self._options_backup: dict[str, Any] = {}
        self.options_recovered_keys: list[str] = []
        self.firmware_updates_hidden_migrated = False
        self._unsubs: list[Any] = []

    @staticmethod
    def _known_options(raw: dict[str, Any]) -> dict[str, Any]:
        """Return only valid ATC OTA option values suitable for backup/restore."""
        result: dict[str, Any] = {}

        if CONF_LOW_BATTERY_THRESHOLD in raw:
            try:
                value = int(raw[CONF_LOW_BATTERY_THRESHOLD])
            except (TypeError, ValueError):
                pass
            else:
                if 5 <= value <= 80:
                    result[CONF_LOW_BATTERY_THRESHOLD] = value

        if CONF_AUTO_PROBE_METADATA in raw:
            value = raw[CONF_AUTO_PROBE_METADATA]
            if isinstance(value, bool):
                result[CONF_AUTO_PROBE_METADATA] = value

        if CONF_AUTO_PROBE_MIN_RSSI in raw:
            try:
                value = int(raw[CONF_AUTO_PROBE_MIN_RSSI])
            except (TypeError, ValueError):
                pass
            else:
                if -110 <= value <= -30:
                    result[CONF_AUTO_PROBE_MIN_RSSI] = value

        return result

    def _effective_options_snapshot(self) -> dict[str, Any]:
        """Return all effective settings, including defaults, for durable backup."""
        return {
            CONF_LOW_BATTERY_THRESHOLD: self.low_battery_threshold,
            CONF_AUTO_PROBE_METADATA: self.auto_probe,
            CONF_AUTO_PROBE_MIN_RSSI: self.auto_probe_min_rssi,
        }

    async def _async_options_updated(self, _hass, _entry) -> None:
        """Persist changed options immediately without reloading the manager."""
        self._options_backup = self._effective_options_snapshot()
        for state in self.devices.values():
            self._update_ota_readiness(state)
            self._notify(state.address)
        await self._async_save()

    @property
    def low_battery_threshold(self) -> int:
        return int(self.entry.options.get(CONF_LOW_BATTERY_THRESHOLD, DEFAULT_LOW_BATTERY_THRESHOLD))

    @property
    def auto_probe(self) -> bool:
        return bool(self.entry.options.get(CONF_AUTO_PROBE_METADATA, DEFAULT_AUTO_PROBE_METADATA))

    @property
    def auto_probe_min_rssi(self) -> int:
        return int(self.entry.options.get(CONF_AUTO_PROBE_MIN_RSSI, DEFAULT_AUTO_PROBE_MIN_RSSI))

    async def async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self.firmware_updates_hidden_migrated = bool(
            stored.get("firmware_updates_hidden_migrated", False)
        )

        saved_options = self._known_options(stored.get("options_backup", {}))
        current_options = dict(self.entry.options)
        restored_options = dict(current_options)
        self.options_recovered_keys = []
        for key, value in saved_options.items():
            if key not in current_options:
                restored_options[key] = value
                self.options_recovered_keys.append(key)

        if restored_options != current_options:
            _LOGGER.warning(
                "Restoring missing ATC OTA options from integration backup: %s",
                ", ".join(self.options_recovered_keys),
            )
            self.hass.config_entries.async_update_entry(
                self.entry, options=restored_options
            )

        self._options_backup = self._effective_options_snapshot()

        for address, raw in stored.get("devices", {}).items():
            try:
                state = DeviceState.from_dict(raw)
                # Cached battery remains useful for the UI, but it must not satisfy
                # the OTA safety gate after a Home Assistant restart. Reconfirm it
                # from a fresh BTHome advertisement or GATT read first. This also
                # prevents a bogus value persisted by the v0.2.6 parser from being
                # trusted immediately after upgrading to v0.2.7.
                state.battery_last_seen = None
                state.battery_source = "cache" if state.battery is not None else None
                # The completed OTA outcome is durable, but live transfer
                # percentages are intentionally not persisted. Restore the
                # *terminal* 100% for a verified successful OTA, so the device
                # page does not show "success" next to "Unknown" on restart.
                if state.last_ota_result == "success":
                    state.ota_progress = 100
                    state.ota_message = "Previous OTA completed successfully"
                self.devices[address.upper()] = state
            except (TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid stored ATC OTA device %s", address)

        self._refresh_home_assistant_names()

        self._unsubs.extend(
            [
                bluetooth.async_register_callback(
                    self.hass,
                    self._async_bluetooth_event,
                    {"service_data_uuid": BTHOME_UUID, "connectable": False},
                    bluetooth.BluetoothScanningMode.PASSIVE,
                ),
                bluetooth.async_register_callback(
                    self.hass,
                    self._async_bluetooth_event,
                    {"service_data_uuid": ENV_SENSING_UUID, "connectable": False},
                    bluetooth.BluetoothScanningMode.PASSIVE,
                ),
            ]
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_catalog_timer,
                timedelta(seconds=CATALOG_REFRESH_SECONDS),
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_ble_health_timer,
                timedelta(seconds=30),
            )
        )

        self._unsubs.append(
            self.entry.add_update_listener(self._async_options_updated)
        )
        self.entry.async_on_unload(self.async_shutdown)
        self.entry.async_create_background_task(
            self.hass,
            self.async_refresh_catalog(),
            "ATC OTA catalog refresh",
        )

    @callback
    def async_shutdown(self) -> None:
        self._shutting_down = True
        for task in tuple(self._install_tasks.values()):
            if not task.done():
                task.cancel()
        self._install_tasks.clear()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for task in self._metadata_tasks.values():
            task.cancel()
        self._metadata_tasks.clear()
        for task in self._route_refresh_tasks.values():
            task.cancel()
        self._route_refresh_tasks.clear()
        if self._save_task is not None:
            self._save_task.cancel()
            self._save_task = None

    @callback
    def _async_catalog_timer(self, _now) -> None:
        self.entry.async_create_background_task(
            self.hass,
            self.async_refresh_catalog(),
            "ATC OTA catalog refresh",
        )

    @callback
    def _async_ble_health_timer(self, _now) -> None:
        """Refresh BLE diagnostics and recover a missed callback from HA history."""
        last_info_fn = getattr(bluetooth, "async_last_service_info", None)
        for address, state in tuple(self.devices.items()):
            if last_info_fn is not None:
                try:
                    info = last_info_fn(self.hass, address, connectable=False)
                except Exception as exc:  # noqa: BLE001
                    state.ble_callback_error = (
                        f"history lookup: {type(exc).__name__}: {exc}"
                    )
                    info = None
                if info is not None:
                    observed = getattr(info, "time", None)
                    if (
                        isinstance(observed, (int, float))
                        and (
                            state.ble_observation_time is None
                            or float(observed) > state.ble_observation_time
                        )
                    ):
                        # HA's Bluetooth history advanced but our subscribed
                        # callback did not. Re-ingest the newest observation.
                        self._async_bluetooth_event(
                            info, bluetooth.BluetoothChange.ADVERTISEMENT
                        )
                        continue

            try:
                self._refresh_ble_routes(state)
            except Exception as exc:  # noqa: BLE001
                state.ble_callback_error = (
                    f"health refresh: {type(exc).__name__}: {exc}"
                )
                _LOGGER.exception("BLE health refresh failed for %s", address)
            self._notify(address)

    def ble_callback_age_seconds(self, state: DeviceState) -> int | None:
        if state.ble_callback_last_seen is None:
            return None
        return max(0, int(time.time() - state.ble_callback_last_seen))

    def ble_health(self, state: DeviceState) -> str:
        age = self.ble_callback_age_seconds(state)
        if state.ble_callback_error and (age is None or age > 60):
            return "error"
        if age is None:
            return "unknown"
        if age > 60:
            return "stale"
        return "receiving"

    @staticmethod
    def _normalize_address(value: str | None) -> str:
        return (value or "").replace("-", ":").upper()

    def _source_device_for_address(self, address: str):
        """Find the existing HA device/config entry that already owns this BLE sensor.

        BTHome config entries use the Bluetooth address as unique_id. Prefer that
        exact mapping, then fall back to any non-ATC-OTA device carrying the same
        Bluetooth connection. This lets us mirror the user-assigned HA device name
        without opening any Bluetooth connection.
        """
        address = self._normalize_address(address)
        registry = dr.async_get(self.hass)

        matching_entries = [
            entry
            for entry in self.hass.config_entries.async_entries("bthome")
            if self._normalize_address(entry.unique_id) == address
        ]
        matching_entry_ids = {entry.entry_id for entry in matching_entries}

        # Prefer the BTHome-owned device, especially one explicitly renamed by user.
        candidates = [
            device
            for device in registry.devices
            if device.config_entry_id in matching_entry_ids
        ]
        if candidates:
            candidates.sort(key=lambda device: (device.name_by_user is None, device.name is None))
            return candidates[0], matching_entries[0] if matching_entries else None

        # Fallback for another Bluetooth integration exposing the same physical MAC.
        for device in registry.devices:
            if device.config_entry_id == self.entry.entry_id:
                continue
            for conn_type, conn_value in getattr(device, "connections", set()):
                if (
                    conn_type == dr.CONNECTION_BLUETOOTH
                    and self._normalize_address(conn_value) == address
                ):
                    return device, None

        return None, matching_entries[0] if matching_entries else None

    def _resolve_home_assistant_identity(
        self, address: str
    ) -> tuple[str | None, str | None, str | None]:
        """Return HA user-facing name, area name and area id for a BLE address."""
        device, config_entry = self._source_device_for_address(address)
        name: str | None = None
        area_name: str | None = None
        area_id: str | None = None

        if device is not None:
            name = device.name_by_user or device.name
            area_id = device.area_id
            if area_id:
                try:
                    area = ar.async_get(self.hass).async_get_area(area_id)
                    if area is not None:
                        area_name = area.name
                except Exception:  # Registry metadata must never break BLE handling.
                    pass

        if not name and config_entry is not None:
            name = config_entry.title

        return name, area_name, area_id

    def _sync_own_device_registry_identity(self, state: DeviceState) -> None:
        """Mirror the source HA device name and area onto the ATC OTA device.

        An explicit user rename made directly on the ATC OTA device is preserved.
        Area is intentionally mirrored from the source Bluetooth/BTHome device so
        both representations of the same physical thermometer stay in the same HA
        area.
        """
        registry = dr.async_get(self.hass)
        own = registry.async_get_device_by_identifier(
            (DOMAIN, state.address), self.entry.entry_id
        )
        if own is None:
            return

        changes: dict[str, Any] = {}
        if state.ha_name and own.name_by_user is None and own.name != state.ha_name:
            changes["name"] = state.ha_name
        if own.area_id != state.ha_area_id:
            changes["area_id"] = state.ha_area_id
        if not changes:
            return

        try:
            registry.async_update_device(own.id, **changes)
        except Exception as exc:  # Never fail the integration for cosmetic metadata.
            _LOGGER.debug("Unable to sync HA identity for %s: %s", state.address, exc)

    def _refresh_home_assistant_name(self, state: DeviceState) -> bool:
        name, area_name, area_id = self._resolve_home_assistant_identity(state.address)
        changed = (
            state.ha_name != name
            or state.ha_area != area_name
            or state.ha_area_id != area_id
        )
        state.ha_name = name
        state.ha_area = area_name
        state.ha_area_id = area_id
        if changed:
            self._sync_own_device_registry_identity(state)
        return changed

    def _refresh_home_assistant_names(self) -> None:
        for state in self.devices.values():
            self._refresh_home_assistant_name(state)

    def _looks_like_atc(self, info) -> bool:
        address = info.address.upper()
        if address in self.devices:
            return True
        name = (info.name or "").upper()
        if address.startswith("A4:C1:38:"):
            return True
        return name.startswith(("ATC_", "LYWSD", "MHO-", "MJWSD", "CGG1", "CGDK"))

    def _parse_bthome_library(
        self,
        address: str,
        info,
    ) -> tuple[int | None, str | None]:
        """Parse BTHome with the same library used by Home Assistant.

        Returns an explicit battery percentage from the current advertisement,
        plus an actual firmware version when object 0xF1/0xF2 is present.
        """
        parser = self._bthome_parsers.setdefault(
            address, BTHomeBluetoothDeviceData()
        )
        try:
            update = parser.update(info)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("bthome-ble parse failed for %s: %s", address, exc)
            return None, None

        battery: int | None = None
        for device_key, value in update.entity_values.items():
            if getattr(device_key, "key", None) != "battery":
                continue
            native = getattr(value, "native_value", None)
            if native is None:
                continue
            try:
                parsed = int(native)
            except (TypeError, ValueError):
                continue
            if 0 <= parsed <= 100:
                battery = parsed
                break

        firmware: str | None = None
        device_info = update.devices.get(None)
        sw_version = getattr(device_info, "sw_version", None)
        if (
            isinstance(sw_version, str)
            and _VERSION_RE.match(sw_version.strip())
        ):
            firmware = normalize_version(sw_version)

        return battery, firmware

    def _service_data(self, info, uuid: str) -> bytes | None:
        target = uuid.lower()
        for key, value in info.service_data.items():
            if key.lower() == target:
                return bytes(value)
        return None

    def _scanner_name_for_source(self, address: str, source: str, *, connectable: bool) -> tuple[str, Any | None]:
        """Resolve a habluetooth source id to its HA scanner/proxy name."""
        try:
            observations = bluetooth.async_scanner_devices_by_address(
                self.hass, address, connectable=connectable
            )
        except Exception:
            observations = []
        for item in observations:
            scanner = getattr(item, "scanner", None)
            scanner_source = str(getattr(scanner, "source", "") or "")
            if scanner_source == str(source):
                name = str(
                    getattr(scanner, "name", None)
                    or scanner_source
                    or source
                    or "unknown"
                )
                return name, scanner
        return str(source or "unknown"), None

    def _esphome_gatt_capability(self, source: str) -> tuple[bool | None, int | None]:
        """Return ESPHome ACTIVE_CONNECTIONS capability for a scanner source.

        Home Assistant registers ESPHome scanners as connectable based on the same
        feature flag, but surfacing it explicitly makes passive-only/stale routes
        understandable to the user and restores the useful v0.1.x capability view.
        """
        source_norm = self._normalize_address(source)
        for entry in self.hass.config_entries.async_entries("esphome"):
            runtime = getattr(entry, "runtime_data", None)
            info = getattr(runtime, "device_info", None)
            if info is None:
                continue
            bt_mac = self._normalize_address(
                getattr(info, "bluetooth_mac_address", None)
            )
            if bt_mac != source_norm:
                continue

            flags: int | None = None
            compat = getattr(info, "bluetooth_proxy_feature_flags_compat", None)
            if callable(compat):
                try:
                    flags = int(compat(getattr(runtime, "api_version", None)))
                except Exception:
                    flags = None
            if flags is None:
                try:
                    flags = int(
                        getattr(info, "bluetooth_proxy_feature_flags", 0) or 0
                    )
                except Exception:
                    flags = None
            if flags is None:
                return None, None
            # aioesphomeapi BluetoothProxyFeature.ACTIVE_CONNECTIONS == 1 << 1.
            return bool(flags & (1 << 1)), flags
        return None, None

    @staticmethod
    def _advertisement_age_seconds(info) -> float | None:
        """Return the real age of a HA Bluetooth observation."""
        observed = getattr(info, "time", None)
        if not isinstance(observed, (int, float)):
            return None
        return max(0.0, time.monotonic() - float(observed))

    @classmethod
    def _advertisement_wall_time(cls, info) -> float:
        """Convert HA's monotonic advertisement timestamp to epoch time."""
        age = cls._advertisement_age_seconds(info)
        return time.time() if age is None else time.time() - age

    def battery_age_seconds(self, state: DeviceState) -> int | None:
        if state.battery_last_seen is None:
            return None
        return max(0, int(time.time() - state.battery_last_seen))

    def battery_is_fresh(self, state: DeviceState) -> bool:
        age = self.battery_age_seconds(state)
        return age is not None and age <= OTA_BATTERY_MAX_AGE_SECONDS

    def _update_ota_readiness(self, state: DeviceState) -> None:
        """Derive a user-facing OTA readiness state from live preflight data."""
        battery_fresh = self.battery_is_fresh(state)
        result = evaluate_ota_readiness(
            battery=state.battery,
            battery_fresh=battery_fresh,
            low_battery_threshold=self.low_battery_threshold,
            gatt_proxy=state.gatt_proxy,
            gatt_rssi=state.gatt_rssi,
            gatt_failures=state.gatt_failures,
            gatt_free_slots=state.gatt_free_slots,
            gatt_route_age_seconds=state.gatt_route_age_seconds,
            gatt_route_max_age_seconds=GATT_ROUTE_MAX_AGE_SECONDS,
            gatt_active_connections=state.gatt_active_connections,
        )
        state.ota_readiness = result.state
        state.ota_readiness_reason = result.reason

    def _refresh_ble_routes(self, state: DeviceState) -> None:
        """Refresh broadcast and preferred connectable-route diagnostics from HA."""
        address = state.address

        try:
            observations = bluetooth.async_scanner_devices_by_address(
                self.hass, address, connectable=False
            )
        except Exception:
            observations = []
        if observations:
            usable = []
            for item in observations:
                advertisement = getattr(item, "advertisement", None)
                rssi = getattr(advertisement, "rssi", None)
                if isinstance(rssi, (int, float)):
                    usable.append((float(rssi), item))
            if usable:
                _rssi, best = max(usable, key=lambda pair: pair[0])
                state.rssi = int(_rssi)
                state.strongest_proxy = self._scanner_source(best)

        try:
            last_info_fn = getattr(bluetooth, "async_last_service_info", None)
            route = (
                last_info_fn(self.hass, address, connectable=True)
                if last_info_fn is not None
                else None
            )
        except Exception:
            route = None

        if route is None:
            state.gatt_rssi = None
            state.gatt_proxy = None
            state.gatt_route_age_seconds = None
            state.gatt_failures = None
            state.gatt_free_slots = None
            state.gatt_slots = None
            state.gatt_active_connections = None
            state.gatt_feature_flags = None
            self._update_ota_readiness(state)
            return

        state.gatt_rssi = int(route.rssi) if route.rssi is not None else None
        route_time = getattr(route, "time", None)
        state.gatt_route_age_seconds = (
            max(0, int(time.monotonic() - route_time))
            if isinstance(route_time, (int, float))
            else None
        )

        route_source = str(getattr(route, "source", "") or "")
        state.gatt_proxy, scanner = self._scanner_name_for_source(
            address, route_source, connectable=True
        )
        (
            state.gatt_active_connections,
            state.gatt_feature_flags,
        ) = self._esphome_gatt_capability(route_source)
        state.gatt_failures = None
        state.gatt_free_slots = None
        state.gatt_slots = None
        if scanner is not None:
            failures_fn = getattr(scanner, "connection_failures", None)
            if callable(failures_fn):
                try:
                    state.gatt_failures = int(failures_fn(address))
                except Exception:
                    pass
            allocations_fn = getattr(scanner, "get_allocations", None)
            if callable(allocations_fn):
                try:
                    allocations = allocations_fn()
                except Exception:
                    allocations = None
                if allocations is not None:
                    state.gatt_free_slots = int(allocations.free)
                    state.gatt_slots = int(allocations.slots)

        self._update_ota_readiness(state)

    def _schedule_route_refresh(self, address: str) -> None:
        """Coalesce expensive HA route diagnostics outside the BLE hot path."""
        address = address.upper()
        existing = self._route_refresh_tasks.get(address)
        if existing is not None and not existing.done():
            return
        task = self.entry.async_create_background_task(
            self.hass,
            self._async_deferred_route_refresh(address),
            f"ATC OTA route refresh {address}",
        )
        self._route_refresh_tasks[address] = task
        task.add_done_callback(
            lambda done, addr=address: self._route_refresh_tasks.pop(addr, None)
        )

    async def _async_deferred_route_refresh(self, address: str) -> None:
        """Refresh route diagnostics after the advertisement callback returns."""
        await asyncio.sleep(0.15)
        state = self.devices.get(address)
        if state is None:
            return
        try:
            self._refresh_ble_routes(state)
        except Exception as exc:  # noqa: BLE001
            state.ble_callback_error = (
                f"route refresh: {type(exc).__name__}: {exc}"
            )
            _LOGGER.exception("Deferred BLE route refresh failed for %s", address)
        self._notify(address)

    @callback
    def _async_bluetooth_event(self, info, _change) -> None:
        """Ingest one BLE advertisement with a deliberately small hot path."""
        if not self._looks_like_atc(info):
            return

        address = self._normalize_address(getattr(info, "address", None))
        if not address:
            return

        is_new = address not in self.devices
        state = self.devices.setdefault(address, DeviceState(address=address))
        persistent_before = None if is_new else state.persistent()

        # Record receipt first. Even if optional parsing/diagnostics fail, the UI
        # can show that ATC OTA is still receiving Bluetooth advertisements.
        state.ble_callback_count += 1
        state.ble_callback_last_seen = time.time()
        observed = getattr(info, "time", None)
        if isinstance(observed, (int, float)):
            state.ble_observation_time = float(observed)
        state.ble_callback_error = None
        advertisement_time = self._advertisement_wall_time(info)
        state.last_seen = advertisement_time

        if info.name and info.name not in (address, "Unknown"):
            state.name = info.name

        # Do not walk HA registries for every 2.5 s advertisement.
        if is_new or state.ha_name is None:
            try:
                self._refresh_home_assistant_name(state)
            except Exception as exc:  # noqa: BLE001
                state.ble_callback_error = (
                    f"identity: {type(exc).__name__}: {exc}"
                )
                _LOGGER.debug(
                    "Unable to refresh HA identity for %s: %s", address, exc
                )

        # Publish the current advertisement immediately. The deferred route
        # refresh will replace this with the strongest scanner observation.
        try:
            if info.rssi is not None:
                state.rssi = int(info.rssi)
            source = str(getattr(info, "source", "") or "")
            if source:
                state.strongest_proxy = source
        except Exception as exc:  # noqa: BLE001
            state.ble_callback_error = f"rssi: {type(exc).__name__}: {exc}"

        bthome = self._service_data(info, BTHOME_UUID)
        if bthome is not None:
            try:
                # Primary parser: exactly the bthome-ble library HA uses.
                battery, firmware = self._parse_bthome_library(address, info)

                # Small local parser remains a fallback only.
                parsed = parse_bthome_v2(bthome)
                if battery is None:
                    battery = parsed.battery
                if firmware is None and parsed.firmware_version:
                    firmware = normalize_version(parsed.firmware_version)

                if battery is not None:
                    state.battery = battery
                    state.battery_last_seen = advertisement_time
                    state.battery_source = "bthome_advertisement"
                if firmware:
                    state.current_version = firmware
            except Exception as exc:  # noqa: BLE001
                state.ble_callback_error = (
                    f"BTHome parse: {type(exc).__name__}: {exc}"
                )
                _LOGGER.exception(
                    "Unable to process BTHome advertisement for %s", address
                )

        # Battery readiness can be updated cheaply from the current route snapshot.
        try:
            self._update_ota_readiness(state)
        except Exception as exc:  # noqa: BLE001
            state.ble_callback_error = (
                f"readiness: {type(exc).__name__}: {exc}"
            )
            _LOGGER.exception("Unable to update OTA readiness for %s", address)

        if is_new:
            async_dispatcher_send(
                self.hass,
                signal_device_added(self.entry.entry_id),
                address,
            )

        # Update entities immediately, then refresh expensive route diagnostics
        # out-of-band. This prevents scanner/registry work from delaying BLE ingest.
        self._notify(address)
        self._schedule_route_refresh(address)

        if is_new or state.persistent() != persistent_before:
            self._schedule_save()

        if (
            self.auto_probe
            and not state.current_version
            and (state.rssi is None or state.rssi >= self.auto_probe_min_rssi)
        ):
            self._schedule_metadata_probe(address)

    def _schedule_metadata_probe(self, address: str, *, force: bool = False) -> None:
        if address in self._metadata_tasks and not self._metadata_tasks[address].done():
            return
        now = time.monotonic()
        if not force and now - self._last_metadata_attempt.get(address, 0.0) < METADATA_RETRY_SECONDS:
            return
        self._last_metadata_attempt[address] = now
        task = self.entry.async_create_background_task(
            self.hass,
            self._async_auto_probe(address),
            f"ATC OTA metadata {address}",
        )
        self._metadata_tasks[address] = task
        task.add_done_callback(lambda _task, addr=address: self._metadata_tasks.pop(addr, None))

    async def _async_auto_probe(self, address: str) -> None:
        """Probe metadata in the background without leaking a task exception."""
        try:
            await self.async_refresh_device(address)
        except Exception:
            # async_refresh_device already stores and logs the concrete failure.
            return

    def _schedule_save(self) -> None:
        """Coalesce high-rate BLE inventory updates into one background save."""
        if self._save_task is not None and not self._save_task.done():
            return
        task = self.entry.async_create_background_task(
            self.hass,
            self._async_delayed_save(),
            "Save ATC OTA inventory",
        )
        self._save_task = task
        task.add_done_callback(self._clear_save_task)

    @callback
    def _clear_save_task(self, task: asyncio.Task) -> None:
        if self._save_task is task:
            self._save_task = None

    async def _async_delayed_save(self) -> None:
        # Advertisements can arrive in bursts during startup and scanner replay.
        # One short debounce prevents dozens of writes to .storage for the same
        # effective inventory state.
        await asyncio.sleep(1.5)
        await self._async_save()

    async def _async_save(self) -> None:
        self._options_backup = self._effective_options_snapshot()
        await self._store.async_save(
            {
                "devices": {
                    addr: state.persistent() for addr, state in self.devices.items()
                },
                "options_backup": dict(self._options_backup),
                "firmware_updates_hidden_migrated": self.firmware_updates_hidden_migrated,
            }
        )

    def _notify(self, address: str) -> None:
        async_dispatcher_send(self.hass, signal_device_updated(self.entry.entry_id, address))

    async def async_refresh_catalog(self) -> None:
        try:
            self.catalog = await fetch_catalog(self.hass)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Unable to refresh pvvx firmware catalog: %s", exc)
            return
        for state in self.devices.values():
            self._apply_latest(state)
            self._notify(state.address)
        await self._async_save()

    def _apply_latest(self, state: DeviceState) -> None:
        if self.catalog is None:
            return
        try:
            choice = resolve_stable_firmware(
                self.catalog,
                model=state.model,
                hardware_revision=state.hardware_revision,
            )
        except Exception:
            return
        state.latest_version = choice.version
        state.latest_filename = choice.filename

    async def async_request_scan(self, duration: float = 4.0) -> None:
        """Ask HA's Bluetooth integration for one active sweep; do not own a scanner."""
        await bluetooth.async_request_active_scan(self.hass, duration=duration)

    @staticmethod
    def _scanner_source(scanner_device) -> str:
        scanner = getattr(scanner_device, "scanner", None)
        return str(
            getattr(scanner, "name", None)
            or getattr(scanner, "source", None)
            or getattr(scanner_device, "source", None)
            or "unknown"
        )

    def _reachability_diagnostics(self, address: str) -> str | None:
        """Return HA's human-readable Bluetooth connection diagnostics when available."""
        fn = getattr(bluetooth, "async_address_reachability_diagnostics", None)
        intent_cls = getattr(bluetooth, "BluetoothReachabilityIntent", None)
        if fn is None or intent_cls is None:
            return None
        try:
            return str(fn(self.hass, address, intent_cls.CONNECTION))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Unable to get reachability diagnostics for %s: %s", address, exc)
            return None

    def _actual_connected_route(
        self, client, address: str
    ) -> tuple[str | None, int | None]:
        """Best-effort route actually selected by Home Assistant for this link.

        Home Assistant's wrapped Bleak backend records the scanner only after a
        successful connection. These are intentionally treated as optional
        implementation details: failure to inspect them must never affect OTA.
        """
        try:
            # Current habluetooth keeps the selected scanner on the wrapped
            # client itself. Keep the backend fallback for older HA versions.
            scanner = getattr(client, "_connected_scanner", None)
            if scanner is None:
                ha_backend = getattr(client, "_backend", None)
                scanner = getattr(ha_backend, "_connected_scanner", None)
            if scanner is None:
                return None, None
            proxy = str(
                getattr(scanner, "name", None)
                or getattr(scanner, "source", None)
                or "unknown"
            )
            rssi: int | None = None
            get_adv = getattr(scanner, "get_discovered_device_advertisement_data", None)
            if callable(get_adv):
                device_adv = get_adv(address)
                if device_adv is not None:
                    advertisement = device_adv[1]
                    value = getattr(advertisement, "rssi", None)
                    if value is not None:
                        rssi = int(value)
            return proxy, rssi
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Unable to inspect actual BLE route for %s: %s", address, exc)
            return None, None

    async def _establish_client(self, address: str, name: str):
        """Connect through Home Assistant's native Bluetooth route selection.

        Home Assistant already scores all connectable scanners for this address
        using RSSI, recent failures, connections in progress and available slots.
        Passing the HA-managed BLEDevice lets the wrapped Bleak backend choose the
        best currently usable GATT route. Advertisement-only scanners are excluded
        from connectable routing by Home Assistant.
        """
        state = self.devices.get(address)
        if state is not None:
            self._refresh_ble_routes(state)
            self._notify(address)

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        )
        if ble_device is None:
            # A previous failed connection or a recent scanner restart may leave
            # HA without a current connectable-history entry even while passive
            # advertisements are still arriving. Request a longer active sweep
            # and give the connectable route time to repopulate.
            await self.async_request_scan(8.0)
            deadline = time.monotonic() + 4.0
            while ble_device is None and time.monotonic() < deadline:
                ble_device = bluetooth.async_ble_device_from_address(
                    self.hass, address, connectable=True
                )
                if ble_device is None:
                    await asyncio.sleep(0.25)
        if ble_device is None:
            reason = self._reachability_diagnostics(address)
            extra = f"; {reason}" if reason else ""
            raise HomeAssistantError(
                f"No connectable Home Assistant Bluetooth route currently sees {address}{extra}"
            )

        _LOGGER.info(
            "Connecting to %s using Home Assistant Bluetooth route selection",
            address,
        )
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                name or address,
                max_attempts=2,
                timeout=12.0,
                use_services_cache=True,
            )
            if state is not None:
                self._refresh_ble_routes(state)
                self._notify(address)
            return client
        except Exception as exc:  # noqa: BLE001
            if state is not None:
                self._refresh_ble_routes(state)
                self._notify(address)
            reason = self._reachability_diagnostics(address)
            suffix = f" HA diagnostics: {reason}" if reason else ""
            raise HomeAssistantError(
                f"Unable to connect to {address} through Home Assistant Bluetooth routing: "
                f"{type(exc).__name__}: {exc}{suffix}"
            ) from exc

    async def _read_text(self, client, uuid: str) -> str | None:
        characteristic = client.services.get_characteristic(uuid)
        if characteristic is None:
            return None
        try:
            value = bytes(
                await asyncio.wait_for(
                    client.read_gatt_char(characteristic),
                    timeout=METADATA_GATT_READ_TIMEOUT_SECONDS,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Unable to read %s: %s", uuid, exc)
            return None
        return value.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip() or None

    async def _read_battery(self, client) -> int | None:
        characteristic = client.services.get_characteristic(UUID_BATTERY_LEVEL)
        if characteristic is None:
            return None
        try:
            value = bytes(
                await asyncio.wait_for(
                    client.read_gatt_char(characteristic),
                    timeout=METADATA_GATT_READ_TIMEOUT_SECONDS,
                )
            )
        except Exception:  # noqa: BLE001
            return None
        return max(0, min(100, int(value[0]))) if value else None

    async def async_refresh_device(
        self, address: str, *, origin: str = "background"
    ) -> None:
        """Read metadata through HA's shared Bluetooth route with bounded waits."""
        address = address.upper()
        state = self.devices.get(address)
        if state is None:
            return

        state.metadata_refresh_request_count += 1
        state.metadata_refresh_status = "waiting"
        state.metadata_refresh_phase = "waiting_for_gatt_lock"
        state.metadata_refresh_started_at = time.time()
        state.metadata_refresh_finished_at = None
        state.metadata_refresh_manager_instance = self.instance_id
        state.metadata_refresh_origin = origin
        state.last_metadata_error = None
        self._notify(address)

        acquired = False
        client = None
        try:
            try:
                await asyncio.wait_for(
                    self._gatt_lock.acquire(),
                    timeout=METADATA_GATT_LOCK_TIMEOUT_SECONDS,
                )
                acquired = True
            except TimeoutError as exc:
                raise HomeAssistantError(
                    "Timed out waiting for the shared ATC OTA GATT lock "
                    f"after {METADATA_GATT_LOCK_TIMEOUT_SECONDS}s"
                ) from exc

            state.metadata_refresh_status = "running"
            state.metadata_refresh_phase = "connecting"
            self._notify(address)

            client = await self._establish_client(address, state.name or address)

            fields = [
                ("device_name", UUID_DEVICE_NAME),
                ("model", UUID_MODEL_NUMBER),
                ("serial", UUID_SERIAL_NUMBER),
                ("firmware_revision", UUID_FIRMWARE_REV),
                ("hardware_revision", UUID_HARDWARE_REV),
                ("software_revision", UUID_SOFTWARE_REV),
                ("manufacturer", UUID_MANUFACTURER),
            ]
            values: dict[str, str | None] = {}
            for field_name, uuid_value in fields:
                state.metadata_refresh_phase = f"reading_{field_name}"
                self._notify(address)
                values[field_name] = await self._read_text(client, uuid_value)

            state.metadata_refresh_phase = "reading_battery"
            self._notify(address)
            battery = await self._read_battery(client)

            device_name = values["device_name"]
            model = values["model"]
            serial = values["serial"]
            fw = values["firmware_revision"]
            hw = values["hardware_revision"]
            sw = values["software_revision"]
            manufacturer = values["manufacturer"]

            if device_name:
                state.name = device_name
            self._refresh_home_assistant_name(state)
            state.model = model or state.model
            state.serial = serial or state.serial
            state.firmware_revision = fw or state.firmware_revision
            state.hardware_revision = hw or state.hardware_revision
            state.software_revision = sw or state.software_revision
            state.manufacturer = manufacturer or state.manufacturer
            if battery is not None:
                state.battery = battery
                state.battery_last_seen = time.time()
                state.battery_source = "gatt"

            revisions = [sw, fw]
            current = next(
                (
                    normalize_version(value)
                    for value in revisions
                    if value and _VERSION_RE.match(value.strip())
                ),
                None,
            )
            if current is None:
                raise HomeAssistantError(
                    "Connected over GATT, but neither Software Revision nor "
                    "Firmware Revision returned a valid firmware version; "
                    f"cached version {state.current_version or 'unknown'} was not "
                    "accepted as refreshed metadata"
                )

            state.current_version = current
            state.last_metadata_success = time.time()
            state.last_metadata_error = None
            state.metadata_refresh_status = "success"
            state.metadata_refresh_phase = "complete"
            self._apply_latest(state)
            self._update_ota_readiness(state)
        except Exception as exc:  # noqa: BLE001
            state.last_metadata_error = f"{type(exc).__name__}: {exc}"
            state.metadata_refresh_status = "failed"
            state.metadata_refresh_phase = "failed"
            _LOGGER.warning(
                "Metadata read failed for %s (%s): %s",
                address,
                origin,
                state.last_metadata_error,
            )
            raise
        finally:
            if client is not None and client.is_connected:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=8.0)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "Disconnect failed after metadata read for %s: %s",
                        address,
                        exc,
                    )
            if acquired:
                self._gatt_lock.release()
            state.metadata_refresh_finished_at = time.time()
            self._notify(address)
            await self._async_save()

    async def async_user_refresh_device(self, address: str) -> None:
        """Run an explicit user-requested metadata refresh immediately."""
        address = address.upper()
        self._last_metadata_attempt[address] = 0.0
        await self.async_refresh_device(address, origin="manual")


    @staticmethod
    def _versions_match(actual: str | None, expected: str | None) -> bool:
        """Compare normalized dotted versions while tolerating trailing zeroes."""
        actual_tuple = version_tuple(actual)
        expected_tuple = version_tuple(expected)
        if actual_tuple is None or expected_tuple is None:
            return normalize_version(actual) == normalize_version(expected)
        width = max(len(actual_tuple), len(expected_tuple))
        return (
            actual_tuple + (0,) * (width - len(actual_tuple))
            == expected_tuple + (0,) * (width - len(expected_tuple))
        )

    async def _verify_firmware_after_ota(
        self,
        address: str,
        target_version: str,
        previous_version: str | None,
    ) -> None:
        """Confirm the device actually booted the requested firmware."""
        state = self.devices[address]
        state.ota_progress = 99
        state.ota_message = f"Verifying firmware {target_version} after reboot"
        self._notify(address)

        # Give the thermometer time to reboot and resume advertisements.
        await asyncio.sleep(6.0)

        # First allow BTHome advertisements to confirm a *changed* version
        # without consuming a GATT connection. On a same-version reinstall,
        # current_version was already equal to the target before any OTA data
        # was transmitted, so that cached value is not proof of success.
        same_version_reinstall = self._versions_match(previous_version, target_version)
        try:
            await self.async_request_scan(4.0)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Post-OTA verification scan failed for %s: %s", address, exc)

        if (
            not same_version_reinstall
            and self._versions_match(state.current_version, target_version)
        ):
            state.ota_message = f"Verified firmware {target_version} via advertisement"
            self._notify(address)
            return

        last_error: Exception | None = None
        for attempt in range(1, 4):
            state.ota_message = (
                f"Verifying firmware {target_version} over GATT "
                f"(attempt {attempt}/3)"
            )
            self._notify(address)
            try:
                await self.async_refresh_device(address)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                _LOGGER.debug(
                    "Post-OTA verification attempt %s failed for %s: %s",
                    attempt,
                    address,
                    exc,
                )
            else:
                if self._versions_match(state.current_version, target_version):
                    state.ota_message = f"Verified firmware {target_version} over GATT"
                    self._notify(address)
                    return
                last_error = HomeAssistantError(
                    f"Device reports firmware {state.current_version or 'unknown'}"
                )

            if attempt < 3:
                await asyncio.sleep(4.0)

        reported = state.current_version or previous_version or "unknown"
        detail = f": {type(last_error).__name__}: {last_error}" if last_error else ""
        raise HomeAssistantError(
            f"OTA transfer finished but firmware verification failed. "
            f"Device reports {reported}; expected {target_version}{detail}"
        )

    @callback
    def start_install_latest(self, address: str) -> None:
        """Queue a user-requested OTA without blocking the Button service call.

        The existing install coroutine remains the single owner of OTA safety
        checks, task tracking and progress. Explicitly pressing the button may
        reinstall the same firmware version to test OTA progress or recover a
        device; it never bypasses battery and connection safety checks.
        """
        address = address.upper()
        state = self.devices.get(address)
        if state is None:
            raise HomeAssistantError(f"ATC OTA device {address} is not known")
        if self._shutting_down:
            raise HomeAssistantError("ATC OTA is shutting down")
        if (
            address in self._pending_install_addresses
            or address in self._installing_addresses
            or state.ota_in_progress
        ):
            raise HomeAssistantError("A firmware update is already running for this device")

        installed = version_tuple(state.current_version)
        latest = version_tuple(state.latest_version)
        if installed is None or latest is None:
            raise HomeAssistantError(
                "Firmware version is unknown; use Refresh firmware info first"
            )
        # Reinstalling the *same* stable release is intentional and permitted.
        # Do not silently downgrade devices that already run a newer version.
        if installed > latest and not self._versions_match(
            state.current_version, state.latest_version
        ):
            raise HomeAssistantError(
                f"Installed firmware {state.current_version} is newer than the "
                f"available stable release {state.latest_version}; refusing downgrade"
            )

        # Reserve the slot synchronously so repeated button presses cannot queue
        # duplicate OTA jobs before the first task is scheduled to run.
        self._pending_install_addresses.add(address)
        try:
            task = self.entry.async_create_background_task(
                self.hass,
                self.async_install_latest(address),
                f"ATC OTA firmware install {address}",
            )
        except Exception:
            self._pending_install_addresses.discard(address)
            raise
        task.add_done_callback(
            lambda _done, addr=address: self._pending_install_addresses.discard(addr)
        )

    async def async_install_latest(self, address: str) -> None:
        """Install latest stable pvvx firmware using HA-managed Bluetooth routing."""
        address = address.upper()
        if self._shutting_down:
            raise HomeAssistantError("ATC OTA is shutting down; refusing a new update")
        state = self.devices[address]
        previous_version = state.current_version

        # Guard the complete install operation, including battery checks, catalog
        # refresh and firmware preparation. Do not replace the tracked task if a
        # second service call arrives while the first update is still active.
        if address in self._installing_addresses:
            raise HomeAssistantError("An OTA update is already in progress for this device")

        install_task = asyncio.current_task()
        if install_task is not None:
            self._install_tasks[address] = install_task
        self._installing_addresses.add(address)
        state.ota_in_progress = True
        state.ota_progress = 0
        state.ota_message = "Waiting for OTA slot"
        state.firmware_source = None
        state.firmware_cache_file = None
        state.firmware_sha256 = None
        state.last_ota_proxy = None
        state.last_ota_rssi = None
        state.last_ota_result = "running"
        state.last_ota_at = None
        state.last_ota_detail = "OTA started"
        self._notify(address)

        succeeded = False
        try:
            async with self._ota_lock:
                state.ota_progress = 1
                state.ota_message = "Checking battery"
                self._notify(address)

                battery_fresh = (
                    state.battery is not None and self.battery_is_fresh(state)
                )
                if not battery_fresh:
                    state.ota_message = "Scanning for a fresh battery reading"
                    self._notify(address)
                    try:
                        await self.async_request_scan(float(OTA_BATTERY_SCAN_SECONDS))
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Pre-OTA battery scan failed: %s", exc)

                battery_fresh = (
                    state.battery is not None and self.battery_is_fresh(state)
                )

                if not battery_fresh:
                    state.ota_message = "Reading battery over GATT"
                    self._notify(address)
                    try:
                        await self.async_refresh_device(address)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Pre-OTA GATT battery refresh failed: %s", exc)

                    battery_fresh = (
                        state.battery is not None and self.battery_is_fresh(state)
                    )

                if not battery_fresh:
                    raise HomeAssistantError(
                        "OTA refused because a recent battery level could not be obtained "
                        "from BTHome or the Battery Level GATT characteristic."
                    )

                if state.battery < self.low_battery_threshold:
                    raise HomeAssistantError(
                        f"OTA refused: battery is {state.battery}% and the configured minimum is "
                        f"{self.low_battery_threshold}%. Change the ATC OTA option only if you "
                        "intentionally want to accept the risk."
                    )

                if not state.model or not state.hardware_revision or not state.current_version:
                    state.ota_message = "Refreshing device metadata"
                    self._notify(address)
                    await self.async_refresh_device(address)

                if self.catalog is None:
                    state.ota_message = "Refreshing firmware catalog"
                    self._notify(address)
                    await self.async_refresh_catalog()
                if self.catalog is None:
                    raise HomeAssistantError("The pvvx firmware catalog is not available")

                choice = resolve_stable_firmware(
                    self.catalog,
                    model=state.model,
                    hardware_revision=state.hardware_revision,
                )

                state.ota_progress = 2
                state.ota_message = f"Preparing firmware {choice.version}"
                self._notify(address)

                payload = await get_cached_firmware(self.hass, choice)
                state.firmware_source = payload.source
                state.firmware_cache_file = payload.cache_file
                state.firmware_sha256 = payload.sha256
                state.ota_progress = 3
                if payload.source == "cache":
                    state.ota_message = f"Using cached firmware {choice.filename}"
                else:
                    state.ota_message = f"Downloaded and cached firmware {choice.filename}"
                self._notify(address)

                # Battery advertisements may update while firmware is prepared.
                # Revalidate immediately before opening the OTA session.
                self._assert_battery_safe_for_ota(
                    state, context="final preflight"
                )
                await self._flash(address, payload.data, choice.version)
                await self._verify_firmware_after_ota(
                    address,
                    choice.version,
                    previous_version,
                )
                succeeded = True

        except Exception as exc:  # noqa: BLE001
            if not (state.ota_message or "").startswith("OTA failed:"):
                state.ota_message = f"Update failed: {type(exc).__name__}: {exc}"
            state.last_ota_result = "failed"
            state.last_ota_at = time.time()
            state.last_ota_detail = state.ota_message
            _LOGGER.warning("Firmware update failed for %s: %s", address, state.ota_message)
            if isinstance(exc, HomeAssistantError):
                raise
            raise HomeAssistantError(state.ota_message) from exc
        finally:
            self._installing_addresses.discard(address)
            if self._install_tasks.get(address) is install_task:
                self._install_tasks.pop(address, None)
            state.ota_in_progress = False
            if succeeded:
                state.ota_progress = 100
                state.ota_message = "OTA completed"
                state.last_ota_result = "success"
                state.last_ota_at = time.time()
                state.last_ota_detail = (
                    f"Firmware {state.current_version or 'unknown'} verified after OTA"
                )
            self._notify(address)
            await self._async_save()
