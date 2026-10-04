"""Pure Telink OTA framing helpers.

The framing mirrors the classic Telink OTA procedure used by pvvx TelinkOTA:
start commands 00ff / 01ff, 16-byte payload blocks prefixed with a little-endian
block number and suffixed with CRC16/MODBUS, then 02ff plus the final block
index and its 16-bit inverse.
"""

from __future__ import annotations

TELINK_MAGIC = b"KNLT"
TELINK_MAGIC_OFFSET = 8
BLOCK_SIZE = 16


def crc16_modbus(data: bytes) -> int:
    """Return CRC16/MODBUS for *data*."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def validate_firmware(data: bytes) -> None:
    """Raise ValueError if *data* does not look like a Telink firmware image."""
    if len(data) < TELINK_MAGIC_OFFSET + len(TELINK_MAGIC):
        raise ValueError("Firmware file is too small")
    if data[TELINK_MAGIC_OFFSET : TELINK_MAGIC_OFFSET + 4] != TELINK_MAGIC:
        raise ValueError(
            "Not a Telink firmware image: expected KNLT marker at byte offset 8"
        )


def pad_firmware(data: bytes) -> bytes:
    """Pad firmware to a 16-byte boundary with 0xFF."""
    remainder = len(data) % BLOCK_SIZE
    if not remainder:
        return data
    return data + (b"\xFF" * (BLOCK_SIZE - remainder))


def make_block(block_number: int, payload: bytes) -> bytes:
    """Create one 20-byte Telink OTA data packet."""
    if not 0 <= block_number <= 0xFFFF:
        raise ValueError("Block number is out of range")
    if len(payload) != BLOCK_SIZE:
        raise ValueError(f"Payload must be exactly {BLOCK_SIZE} bytes")

    body = block_number.to_bytes(2, "little") + payload
    crc = crc16_modbus(body)
    return body + crc.to_bytes(2, "little")


def make_finish(block_count: int) -> bytes:
    """Create the final Telink OTA command."""
    if not 1 <= block_count <= 0x10000:
        raise ValueError("Invalid block count")
    last = block_count - 1
    inverted = (~last) & 0xFFFF
    return b"\x02\xFF" + last.to_bytes(2, "little") + inverted.to_bytes(2, "little")
