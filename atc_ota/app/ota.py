"""Telink OTA implementation over Bleak."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .protocol import BLOCK_SIZE, make_block, make_finish, pad_firmware, validate_firmware

_LOGGER = logging.getLogger(__name__)

OTA_SERVICE_UUID = "00010203-0405-0607-0809-0a0b0c0d1912"
OTA_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d2b12"

ProgressCallback = Callable[[int, str], None]


async def flash_telink(
    address: str,
    firmware: bytes,
    progress: ProgressCallback,
) -> None:
    """Flash a Telink firmware image to *address* through registered BLE proxies."""
    import bleak

    validate_firmware(firmware)
    padded = pad_firmware(firmware)
    block_count = len(padded) // BLOCK_SIZE

    if block_count > 0x10000:
        raise ValueError("Firmware image has too many 16-byte blocks for classic Telink OTA")

    progress(1, f"Firmware validated: {len(firmware)} bytes, {block_count} blocks")

    device = await bleak.BleakScanner.find_device_by_address(address, timeout=12.0)
    if device is None:
        raise RuntimeError(
            f"BLE device {address} was not found. Make sure it is advertising and is in range of the proxy."
        )

    progress(2, f"Connecting to {device.name or address} ({address})")

    # Retry transient proxy/GATT connection failures before OTA mode starts.
    # Once flashing begins we intentionally do not retry/restart the transfer.
    from bleak_retry_connector import establish_connection

    client = await establish_connection(
        bleak.BleakClient,
        device,
        name=device.name or address,
        max_attempts=2,
        timeout=12.0,
    )
    try:
        progress(3, "GATT connected; checking Telink OTA service")

        characteristic = client.services.get_characteristic(OTA_CHAR_UUID)
        if characteristic is None:
            raise RuntimeError(
                "Telink OTA characteristic was not found. The device may not be running compatible ATC/pvvx firmware."
            )

        # pvvx TelinkOTA.html waits briefly before starting, then sends these two commands.
        await asyncio.sleep(0.50)
        await client.write_gatt_char(characteristic, b"\x00\xFF", response=False)
        await asyncio.sleep(0.05)
        await client.write_gatt_char(characteristic, b"\x01\xFF", response=False)
        await asyncio.sleep(0.30)

        progress(4, "OTA mode started")

        for block_number in range(block_count):
            start = block_number * BLOCK_SIZE
            payload = padded[start : start + BLOCK_SIZE]
            packet = make_block(block_number, payload)
            await client.write_gatt_char(characteristic, packet, response=False)

            # Match pvvx behavior: force a read after each group of 8 blocks.
            if (block_number + 1) % 8 == 0:
                await client.read_gatt_char(characteristic)

            percent = 5 + int(((block_number + 1) / block_count) * 93)
            if block_number == 0 or (block_number + 1) % 32 == 0 or block_number + 1 == block_count:
                progress(
                    min(percent, 98),
                    f"Sending block {block_number + 1}/{block_count}",
                )

            # Yield to the event loop without adding a fixed transfer delay.
            await asyncio.sleep(0)

        await client.write_gatt_char(
            characteristic,
            make_finish(block_count),
            response=False,
        )
        progress(99, "Final OTA command sent; target should reboot")
        await asyncio.sleep(0.5)
    finally:
        if client.is_connected:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - best effort during teardown
                _LOGGER.exception("BLE disconnect failed")

    progress(100, "OTA completed")
