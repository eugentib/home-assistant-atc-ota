"""Read standard pvvx/ATC device information over BLE."""

from __future__ import annotations

import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)
_VERSION_TEXT_RE = re.compile(r"^V?\d+(?:\.\d+){1,2}(?:[-+._a-zA-Z0-9]*)?$")

UUID_DEVICE_NAME = "00002a00-0000-1000-8000-00805f9b34fb"
UUID_MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"
UUID_FIRMWARE_REV = "00002a26-0000-1000-8000-00805f9b34fb"
UUID_HARDWARE_REV = "00002a27-0000-1000-8000-00805f9b34fb"
UUID_SOFTWARE_REV = "00002a28-0000-1000-8000-00805f9b34fb"
UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
UUID_BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"


def _decode_text(data: bytes | bytearray) -> str:
    return bytes(data).split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


async def _read_text(client: Any, uuid: str) -> str | None:
    characteristic = client.services.get_characteristic(uuid)
    if characteristic is None:
        return None
    try:
        value = await client.read_gatt_char(characteristic)
    except Exception as exc:  # noqa: BLE001 - characteristics differ by firmware
        _LOGGER.debug("Unable to read %s: %s", uuid, exc)
        return None
    text = _decode_text(value)
    return text or None


async def _read_battery_percent(client: Any) -> int | None:
    """Read the standard Battery Level characteristic (0x2A19)."""
    characteristic = client.services.get_characteristic(UUID_BATTERY_LEVEL)
    if characteristic is None:
        return None
    try:
        value = bytes(await client.read_gatt_char(characteristic))
    except Exception as exc:  # noqa: BLE001 - service may be absent on other devices
        _LOGGER.debug("Unable to read battery level: %s", exc)
        return None
    if not value:
        return None
    # Bluetooth Battery Level is an unsigned percentage. Be defensive about
    # non-conforming firmware values while keeping the raw GATT read harmless.
    return max(0, min(100, int(value[0])))


async def read_device_info(address: str) -> dict[str, Any]:
    """Connect to one BLE device and read standard GAP/DIS characteristics."""
    import bleak

    device = await bleak.BleakScanner.find_device_by_address(address, timeout=12.0)
    if device is None:
        raise RuntimeError(
            f"BLE device {address} was not found. Make sure it is advertising and in range of the proxy."
        )

    client = bleak.BleakClient(device, timeout=20.0)
    try:
        await client.connect()
        if not client.is_connected:
            raise RuntimeError("GATT connection did not become active")

        device_name = await _read_text(client, UUID_DEVICE_NAME)
        model = await _read_text(client, UUID_MODEL_NUMBER)
        serial = await _read_text(client, UUID_SERIAL_NUMBER)
        firmware_revision = await _read_text(client, UUID_FIRMWARE_REV)
        hardware_revision = await _read_text(client, UUID_HARDWARE_REV)
        software_revision = await _read_text(client, UUID_SOFTWARE_REV)
        manufacturer = await _read_text(client, UUID_MANUFACTURER)
        battery_percent = await _read_battery_percent(client)

        # Most pvvx releases put the release in Software Revision and the
        # project identifier in Firmware Revision.  Some builds expose those
        # strings in the opposite characteristics, so prefer whichever value
        # actually looks like a version instead of blindly trusting one UUID.
        revisions = [software_revision, firmware_revision]
        current_version = next(
            (value for value in revisions if value and _VERSION_TEXT_RE.match(value.strip())),
            software_revision or firmware_revision,
        )

        return {
            "address": address,
            "device_name": device_name or device.name or "(unnamed)",
            "model": model,
            "serial": serial,
            "firmware_revision": firmware_revision,
            "hardware_revision": hardware_revision,
            "software_revision": software_revision,
            "manufacturer": manufacturer,
            "battery_percent": battery_percent,
            "current_version": current_version,
        }
    finally:
        if client.is_connected:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - best effort on teardown
                _LOGGER.exception("BLE disconnect failed after reading device info")
