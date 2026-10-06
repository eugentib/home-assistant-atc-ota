"""Bluetooth inventory and OTA manager using Home Assistant's shared Bluetooth stack."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import timedelta
import logging
import re
import time
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

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
    OTA_BATTERY_MAX_AGE_SECONDS,
    OTA_BATTERY_SCAN_SECONDS,
    OTA_BLOCK_PACING_SECONDS,
    OTA_START_COMMAND_GAP_SECONDS,
    OTA_WRITE_RETRY_ATTEMPTS,
    OTA_WRITE_RETRY_BASE_SECONDS,
    METADATA_RETRY_SECONDS,
    OTA_CHAR_UUID,
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
from .protocol import BLOCK_SIZE, make_block, make_finish, pad_firmware, validate_firmware

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
    rssi: int | None = None
    strongest_proxy: str | None = None
    last_seen: float | None = None
    last_metadata_success: float | None = None
    last_metadata_error: str | None = None
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
        data = asdict(self)
        for key in ("ota_in_progress", "ota_progress", "ota_message"):
            data.pop(key, None)
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeviceState":
        allowed = {field.name for field in cls.__dataclass_fields__.values() if field.init}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


class AtcManager:
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
        self._metadata_tasks: dict[str, asyncio.Task] = {}
        self._save_task: asyncio.Task | None = None
        self._last_metadata_attempt: dict[str, float] = {}
        self._unsubs: list[Any] = []

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
        for address, raw in stored.get("devices", {}).items():
            try:
                state = DeviceState.from_dict(raw)
                # Cached battery remains useful for the UI, but it must not satisfy
                # the OTA safety gate after a Home Assistant restart. Reconfirm it
                # from a fresh BTHome advertisement or GATT read first. This also
                # prevents a bogus value persisted by the v0.2.6 parser from being
                # trusted immediately after upgrading to v0.2.7.
                state.battery_last_seen = None
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

        # Seed from HA's existing scanner history without opening a second scanner.
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=False):
            self._async_bluetooth_event(info, bluetooth.BluetoothChange.ADVERTISEMENT)

        self.entry.async_on_unload(self.async_shutdown)
        self.entry.async_create_background_task(
            self.hass,
            self.async_refresh_catalog(),
            "ATC OTA catalog refresh",
        )

    @callback
    def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for task in self._metadata_tasks.values():
            task.cancel()
        self._metadata_tasks.clear()
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

    def _service_data(self, info, uuid: str) -> bytes | None:
        target = uuid.lower()
        for key, value in info.service_data.items():
            if key.lower() == target:
                return bytes(value)
        return None

    @callback
    def _async_bluetooth_event(self, info, _change) -> None:
        if not self._looks_like_atc(info):
            return

        address = info.address.upper()
        is_new = address not in self.devices
        state = self.devices.setdefault(address, DeviceState(address=address))
        if info.name and info.name not in (address, "Unknown"):
            state.name = info.name
        self._refresh_home_assistant_name(state)
        state.last_seen = time.time()

        # Compute strongest current observation across HA's shared scanners/proxies.
        try:
            observations = bluetooth.async_scanner_devices_by_address(
                self.hass, address, connectable=False
            )
        except Exception:  # API is diagnostic-only; never break data handling.
            observations = []
        if observations:
            best = max(observations, key=lambda item: item.advertisement.rssi)
            state.rssi = int(best.advertisement.rssi)
            scanner = best.scanner
            state.strongest_proxy = str(
                getattr(scanner, "name", None)
                or getattr(scanner, "source", None)
                or "unknown"
            )
        else:
            state.rssi = int(info.rssi)
            state.strongest_proxy = str(getattr(info, "source", "unknown"))

        bthome = self._service_data(info, BTHOME_UUID)
        if bthome is not None:
            parsed = parse_bthome_v2(bthome)
            if parsed.battery is not None:
                state.battery = parsed.battery
                state.battery_last_seen = time.time()
            if parsed.firmware_version:
                state.current_version = normalize_version(parsed.firmware_version)

        if is_new:
            async_dispatcher_send(self.hass, signal_device_added(self.entry.entry_id), address)
        self._notify(address)
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
        await self._store.async_save(
            {"devices": {addr: state.persistent() for addr, state in self.devices.items()}}
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

    async def _establish_client(self, address: str, name: str):
        """Connect through Home Assistant's native Bluetooth route selection.

        Home Assistant already scores all connectable scanners for this address
        using RSSI, recent failures, connections in progress and available slots.
        Passing the HA-managed BLEDevice lets the wrapped Bleak backend choose the
        best currently usable GATT route. Advertisement-only scanners are excluded
        from connectable routing by Home Assistant.
        """
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
            return await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                name or address,
                max_attempts=2,
                timeout=12.0,
                use_services_cache=True,
            )
        except Exception as exc:  # noqa: BLE001
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
            value = bytes(await client.read_gatt_char(characteristic))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Unable to read %s: %s", uuid, exc)
            return None
        return value.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip() or None

    async def _read_battery(self, client) -> int | None:
        characteristic = client.services.get_characteristic(UUID_BATTERY_LEVEL)
        if characteristic is None:
            return None
        try:
            value = bytes(await client.read_gatt_char(characteristic))
        except Exception:  # noqa: BLE001
            return None
        return max(0, min(100, int(value[0]))) if value else None

    async def async_refresh_device(self, address: str) -> None:
        """Read metadata through HA's shared Bluetooth route and release GATT promptly."""
        address = address.upper()
        state = self.devices.get(address)
        if state is None:
            return
        async with self._gatt_lock:
            client = None
            try:
                client = await self._establish_client(address, state.name or address)
                device_name = await self._read_text(client, UUID_DEVICE_NAME)
                model = await self._read_text(client, UUID_MODEL_NUMBER)
                serial = await self._read_text(client, UUID_SERIAL_NUMBER)
                fw = await self._read_text(client, UUID_FIRMWARE_REV)
                hw = await self._read_text(client, UUID_HARDWARE_REV)
                sw = await self._read_text(client, UUID_SOFTWARE_REV)
                manufacturer = await self._read_text(client, UUID_MANUFACTURER)
                battery = await self._read_battery(client)

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

                revisions = [sw, fw]
                current = next(
                    (
                        normalize_version(value)
                        for value in revisions
                        if value and _VERSION_RE.match(value.strip())
                    ),
                    None,
                )
                if current:
                    state.current_version = current
                state.last_metadata_success = time.time()
                state.last_metadata_error = None
                self._apply_latest(state)
            except Exception as exc:  # noqa: BLE001
                state.last_metadata_error = f"{type(exc).__name__}: {exc}"
                _LOGGER.warning("Metadata read failed for %s: %s", address, state.last_metadata_error)
                raise
            finally:
                if client is not None and client.is_connected:
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=8.0)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.warning("Disconnect failed after metadata read for %s: %s", address, exc)
                self._notify(address)
                await self._async_save()

    async def async_user_refresh_device(self, address: str) -> None:
        self._last_metadata_attempt[address.upper()] = 0.0
        await self.async_refresh_device(address)

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

        # First allow BTHome advertisements to confirm the new version without
        # consuming a GATT connection. Explicit BTHome firmware objects update
        # state.current_version in _async_bluetooth_event().
        try:
            await self.async_request_scan(4.0)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Post-OTA verification scan failed for %s: %s", address, exc)

        if self._versions_match(state.current_version, target_version):
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

    async def async_install_latest(self, address: str) -> None:
        """Install latest stable pvvx firmware using HA-managed Bluetooth routing."""
        address = address.upper()
        state = self.devices[address]
        previous_version = state.current_version

        # Guard the complete install operation, including battery checks, catalog
        # refresh and firmware preparation. Home Assistant's update entity can then
        # report a consistent in-progress state from the first await until cleanup.
        if address in self._installing_addresses:
            raise HomeAssistantError("An OTA update is already in progress for this device")

        self._installing_addresses.add(address)
        state.ota_in_progress = True
        state.ota_progress = 0
        state.ota_message = "Waiting for OTA slot"
        state.firmware_source = None
        state.firmware_cache_file = None
        state.firmware_sha256 = None
        self._notify(address)

        succeeded = False
        try:
            async with self._ota_lock:
                state.ota_progress = 1
                state.ota_message = "Checking battery"
                self._notify(address)

                now = time.time()
                battery_fresh = (
                    state.battery is not None
                    and state.battery_last_seen is not None
                    and now - state.battery_last_seen <= OTA_BATTERY_MAX_AGE_SECONDS
                )
                if not battery_fresh:
                    state.ota_message = "Scanning for a fresh battery reading"
                    self._notify(address)
                    try:
                        await self.async_request_scan(float(OTA_BATTERY_SCAN_SECONDS))
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Pre-OTA battery scan failed: %s", exc)

                now = time.time()
                battery_fresh = (
                    state.battery is not None
                    and state.battery_last_seen is not None
                    and now - state.battery_last_seen <= OTA_BATTERY_MAX_AGE_SECONDS
                )

                if not battery_fresh:
                    state.ota_message = "Reading battery over GATT"
                    self._notify(address)
                    try:
                        await self.async_refresh_device(address)
                    except Exception as exc:  # noqa: BLE001
                        _LOGGER.debug("Pre-OTA GATT battery refresh failed: %s", exc)

                    now = time.time()
                    battery_fresh = (
                        state.battery is not None
                        and state.battery_last_seen is not None
                        and now - state.battery_last_seen <= OTA_BATTERY_MAX_AGE_SECONDS
                    )

                if not battery_fresh:
                    raise HomeAssistantError(
                        "OTA refused because a recent battery level could not be obtained "
                        "from BTHome or the Battery Level GATT characteristic."
                    )

                if state.battery <= self.low_battery_threshold:
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
            _LOGGER.warning("Firmware update failed for %s: %s", address, state.ota_message)
            if isinstance(exc, HomeAssistantError):
                raise
            raise HomeAssistantError(state.ota_message) from exc
        finally:
            self._installing_addresses.discard(address)
            state.ota_in_progress = False
            if succeeded:
                state.ota_progress = 100
                state.ota_message = "OTA completed"
            self._notify(address)
            await self._async_save()

    @staticmethod
    def _is_gatt_congested(exc: Exception) -> bool:
        """Return True only for the ESP-IDF GATT congestion status."""
        text = f"{type(exc).__name__}: {exc}".lower()
        return (
            "congested" in text
            or "error=143" in text
            or "error 143" in text
            or "0x8f" in text
        )

    async def _ota_write(
        self,
        state: DeviceState,
        client,
        characteristic,
        payload: bytes,
        *,
        label: str,
        pacing: float = 0.0,
    ) -> None:
        """Write one OTA packet with bounded retry for ESP_GATT_CONGESTED."""
        delay = OTA_WRITE_RETRY_BASE_SECONDS
        for attempt in range(1, OTA_WRITE_RETRY_ATTEMPTS + 1):
            try:
                await client.write_gatt_char(characteristic, payload, response=False)
                if pacing > 0:
                    await asyncio.sleep(pacing)
                return
            except Exception as exc:  # noqa: BLE001
                if not self._is_gatt_congested(exc) or attempt >= OTA_WRITE_RETRY_ATTEMPTS:
                    raise
                state.ota_message = (
                    f"{label}: BLE congested, retry {attempt}/{OTA_WRITE_RETRY_ATTEMPTS}"
                )
                self._notify(state.address)
                _LOGGER.warning(
                    "BLE congestion while %s for %s; retry %s/%s after %.2fs",
                    label,
                    state.address,
                    attempt,
                    OTA_WRITE_RETRY_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 1.0)

    async def _ota_read_sync(self, state: DeviceState, client, characteristic) -> None:
        """Read the OTA characteristic, retrying only explicit BLE congestion."""
        delay = OTA_WRITE_RETRY_BASE_SECONDS
        for attempt in range(1, OTA_WRITE_RETRY_ATTEMPTS + 1):
            try:
                await client.read_gatt_char(characteristic)
                return
            except Exception as exc:  # noqa: BLE001
                if not self._is_gatt_congested(exc) or attempt >= OTA_WRITE_RETRY_ATTEMPTS:
                    raise
                state.ota_message = (
                    f"OTA sync read: BLE congested, retry {attempt}/{OTA_WRITE_RETRY_ATTEMPTS}"
                )
                self._notify(state.address)
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 1.0)

    async def _flash(self, address: str, firmware: bytes, target_version: str) -> None:
        """Serialize OTA against metadata GATT reads."""
        async with self._gatt_lock:
            await self._flash_locked(address, firmware, target_version)

    async def _flash_locked(self, address: str, firmware: bytes, target_version: str) -> None:
        validate_firmware(firmware)
        padded = pad_firmware(firmware)
        block_count = len(padded) // BLOCK_SIZE
        if block_count > 0x10000:
            raise HomeAssistantError("Firmware image has too many Telink OTA blocks")

        state = self.devices[address]
        state.ota_progress = 4
        state.ota_message = "Preparing OTA"
        self._notify(address)

        client = None
        try:
            client = await self._establish_client(address, state.name or address)
            characteristic = client.services.get_characteristic(OTA_CHAR_UUID)
            if characteristic is None:
                raise HomeAssistantError("Compatible Telink OTA characteristic was not found")

            state.ota_progress = 5
            state.ota_message = "Starting Telink OTA"
            self._notify(address)

            await asyncio.sleep(0.50)
            state.ota_message = "Starting Telink OTA (phase 1/2)"
            self._notify(address)
            await self._ota_write(
                state,
                client,
                characteristic,
                b"\x00\xFF",
                label="OTA start phase 1",
            )
            await asyncio.sleep(OTA_START_COMMAND_GAP_SECONDS)

            state.ota_message = "Starting Telink OTA (phase 2/2)"
            self._notify(address)
            await self._ota_write(
                state,
                client,
                characteristic,
                b"\x01\xFF",
                label="OTA start phase 2",
            )
            await asyncio.sleep(0.30)

            for block_number in range(block_count):
                start = block_number * BLOCK_SIZE
                packet = make_block(block_number, padded[start : start + BLOCK_SIZE])
                await self._ota_write(
                    state,
                    client,
                    characteristic,
                    packet,
                    label=f"OTA block {block_number + 1}/{block_count}",
                    pacing=OTA_BLOCK_PACING_SECONDS,
                )
                if (block_number + 1) % 8 == 0:
                    await self._ota_read_sync(state, client, characteristic)
                if block_number == 0 or (block_number + 1) % 32 == 0 or block_number + 1 == block_count:
                    state.ota_progress = min(98, 5 + int(((block_number + 1) / block_count) * 93))
                    state.ota_message = f"Sending block {block_number + 1}/{block_count}"
                    self._notify(address)
                await asyncio.sleep(0)

            await self._ota_write(
                state,
                client,
                characteristic,
                make_finish(block_count),
                label="OTA final command",
            )
            state.ota_progress = 99
            state.ota_message = "Final command sent; waiting for reboot"
            self._notify(address)
            await asyncio.sleep(0.5)
            state.last_metadata_error = None
        except Exception as exc:  # noqa: BLE001
            state.ota_message = f"OTA failed: {type(exc).__name__}: {exc}"
            _LOGGER.exception("OTA failed for %s", address)
            raise HomeAssistantError(state.ota_message) from exc
        finally:
            if client is not None and client.is_connected:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=8.0)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Disconnect failed after OTA for %s: %s", address, exc)
            self._notify(address)

