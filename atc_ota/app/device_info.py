"""Read standard pvvx/ATC device information over BLE."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

UUID_DEVICE_NAME = "00002a00-0000-1000-8000-00805f9b34fb"
UUID_MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"
UUID_FIRMWARE_REV = "00002a26-0000-1000-8000-00805f9b34fb"
UUID_HARDWARE_REV = "00002a27-0000-1000-8000-00805f9b34fb"
UUID_SOFTWARE_REV = "00002a28-0000-1000-8000-00805f9b34fb"
UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"


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

        # pvvx uses the Software Revision String for the actual release version
        # (for example V5.9), while Firmware Revision commonly contains github.com/pvvx.
        current_version = software_revision or firmware_revision

        return {
            "address": address,
            "device_name": device_name or device.name or "(unnamed)",
            "model": model,
            "serial": serial,
            "firmware_revision": firmware_revision,
            "hardware_revision": hardware_revision,
            "software_revision": software_revision,
            "manufacturer": manufacturer,
            "current_version": current_version,
        }
    finally:
        if client.is_connected:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - best effort on teardown
                _LOGGER.exception("BLE disconnect failed after reading device info")
