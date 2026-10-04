"""Bluetooth inventory and OTA manager using Home Assistant's shared Bluetooth stack."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import timedelta
import logging
import re
import time
from typing import Any

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
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
    LOW_BATTERY_CONFIRM_SECONDS,
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
from .firmware import download_firmware, fetch_catalog, resolve_stable_firmware
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
    rssi: int | None = None
    strongest_proxy: str | None = None
    last_seen: float | None = None
    last_metadata_success: float | None = None
    last_metadata_error: str | None = None
    ota_in_progress: bool = False
    ota_progress: int | None = None
    ota_message: str | None = None
    _confirm_until: float = field(default=0.0, repr=False)

    def persistent(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("ota_in_progress", "ota_progress", "ota_message", "_confirm_until"):
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
        self._metadata_tasks: dict[str, asyncio.Task] = {}
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
                self.devices[address.upper()] = DeviceState.from_dict(raw)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid stored ATC OTA device %s", address)

        self._unsubs.extend(
            [
                bluetooth.async_register_callback(
                    self.hass,
                    self._async_bluetooth_event,
                    BluetoothCallbackMatcher(service_uuid=BTHOME_UUID, connectable=False),
                    bluetooth.BluetoothScanningMode.PASSIVE,
                ),
                bluetooth.async_register_callback(
                    self.hass,
                    self._async_bluetooth_event,
                    BluetoothCallbackMatcher(service_uuid=ENV_SENSING_UUID, connectable=False),
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
        self.hass.async_create_task(self.async_refresh_catalog(), "ATC OTA catalog refresh")

    @callback
    def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for task in self._metadata_tasks.values():
            task.cancel()
        self._metadata_tasks.clear()

    @callback
    def _async_catalog_timer(self, _now) -> None:
        self.hass.async_create_task(self.async_refresh_catalog(), "ATC OTA catalog refresh")

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
            if parsed.firmware_version and not state.current_version:
                state.current_version = normalize_version(parsed.firmware_version)

        if is_new:
            async_dispatcher_send(self.hass, signal_device_added(self.entry.entry_id), address)
        self._notify(address)
        self.hass.async_create_task(self._async_save(), "Save ATC OTA inventory")

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
        task = self.hass.async_create_task(
            self._async_auto_probe(address), f"ATC OTA metadata {address}"
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

    async def _get_connectable_device(self, address: str):
        device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if device is not None:
            return device
        await self.async_request_scan(3.0)
        return bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)

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
                ble_device = await self._get_connectable_device(address)
                if ble_device is None:
                    raise HomeAssistantError(
                        f"{state.name}: no connectable Home Assistant Bluetooth route currently sees {address}"
                    )
                client = await establish_connection(
                    BleakClient,
                    ble_device,
                    state.name or address,
                    max_attempts=2,
                    timeout=12.0,
                )
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
                state.model = model or state.model
                state.serial = serial or state.serial
                state.firmware_revision = fw or state.firmware_revision
                state.hardware_revision = hw or state.hardware_revision
                state.software_revision = sw or state.software_revision
                state.manufacturer = manufacturer or state.manufacturer
                if battery is not None:
                    state.battery = battery

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

    async def async_install_latest(self, address: str) -> None:
        """Install latest stable pvvx firmware using HA-managed Bluetooth routing."""
        address = address.upper()
        state = self.devices[address]
        async with self._ota_lock:
            if state.ota_in_progress:
                raise HomeAssistantError("An OTA update is already in progress for this device")

            # Refresh advertisements so BTHome battery/RSSI are recent without a GATT connection.
            try:
                await self.async_request_scan(3.0)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Pre-OTA active scan failed: %s", exc)

            if state.battery is not None and state.battery <= self.low_battery_threshold:
                now = time.monotonic()
                if now > state._confirm_until:
                    state._confirm_until = now + LOW_BATTERY_CONFIRM_SECONDS
                    self._notify(address)
                    raise HomeAssistantError(
                        f"Battery is only {state.battery}% (threshold {self.low_battery_threshold}%). "
                        "Press Install again within 60 seconds to confirm the OTA anyway."
                    )
                state._confirm_until = 0.0

            if not state.model or not state.hardware_revision or not state.current_version:
                await self.async_refresh_device(address)

            if self.catalog is None:
                await self.async_refresh_catalog()
            if self.catalog is None:
                raise HomeAssistantError("The pvvx firmware catalog is not available")

            choice = resolve_stable_firmware(
                self.catalog,
                model=state.model,
                hardware_revision=state.hardware_revision,
            )
            firmware = await download_firmware(self.hass, choice)
            await self._flash(address, firmware, choice.version)

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
        state.ota_in_progress = True
        state.ota_progress = 1
        state.ota_message = "Preparing OTA"
        self._notify(address)

        client = None
        try:
            ble_device = await self._get_connectable_device(address)
            if ble_device is None:
                raise HomeAssistantError(
                    f"No connectable Home Assistant Bluetooth route currently sees {address}"
                )
            client = await establish_connection(
                BleakClient,
                ble_device,
                state.name or address,
                max_attempts=2,
                timeout=15.0,
            )
            characteristic = client.services.get_characteristic(OTA_CHAR_UUID)
            if characteristic is None:
                raise HomeAssistantError("Compatible Telink OTA characteristic was not found")

            state.ota_progress = 3
            state.ota_message = "Starting Telink OTA"
            self._notify(address)

            await asyncio.sleep(0.50)
            await client.write_gatt_char(characteristic, b"\x00\xFF", response=False)
            await asyncio.sleep(0.05)
            await client.write_gatt_char(characteristic, b"\x01\xFF", response=False)
            await asyncio.sleep(0.30)

            for block_number in range(block_count):
                start = block_number * BLOCK_SIZE
                packet = make_block(block_number, padded[start : start + BLOCK_SIZE])
                await client.write_gatt_char(characteristic, packet, response=False)
                if (block_number + 1) % 8 == 0:
                    await client.read_gatt_char(characteristic)
                if block_number == 0 or (block_number + 1) % 32 == 0 or block_number + 1 == block_count:
                    state.ota_progress = min(98, 5 + int(((block_number + 1) / block_count) * 93))
                    state.ota_message = f"Sending block {block_number + 1}/{block_count}"
                    self._notify(address)
                await asyncio.sleep(0)

            await client.write_gatt_char(characteristic, make_finish(block_count), response=False)
            state.ota_progress = 99
            state.ota_message = "Final command sent; waiting for reboot"
            self._notify(address)
            await asyncio.sleep(0.5)
            state.current_version = target_version
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
            state.ota_in_progress = False
            if state.ota_progress == 99:
                state.ota_progress = 100
                state.ota_message = "OTA completed"
            self._notify(address)
            await self._async_save()

        # Let the thermometer reboot, then refresh metadata through HA later.
        async def _refresh_after_reboot() -> None:
            await asyncio.sleep(10)
            try:
                await self.async_refresh_device(address)
            except Exception:
                pass

        self.hass.async_create_task(_refresh_after_reboot(), f"ATC OTA post-update {address}")
