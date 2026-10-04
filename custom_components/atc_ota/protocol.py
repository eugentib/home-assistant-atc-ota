"""Pure Telink OTA framing helpers."""

from __future__ import annotations

TELINK_MAGIC = b"KNLT"
TELINK_MAGIC_OFFSET = 8
BLOCK_SIZE = 16


def crc16_modbus(data: bytes) -> int:
    """Return CRC16/MODBUS for data."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def validate_firmware(data: bytes) -> None:
    """Validate that data looks like a classic Telink firmware image."""
    if len(data) < TELINK_MAGIC_OFFSET + len(TELINK_MAGIC):
        raise ValueError("Firmware file is too small")
    if data[TELINK_MAGIC_OFFSET : TELINK_MAGIC_OFFSET + 4] != TELINK_MAGIC:
        raise ValueError("Not a Telink firmware image: KNLT marker missing at offset 8")


def pad_firmware(data: bytes) -> bytes:
    """Pad firmware to a 16-byte boundary."""
    remainder = len(data) % BLOCK_SIZE
    return data if not remainder else data + b"\xFF" * (BLOCK_SIZE - remainder)


def make_block(block_number: int, payload: bytes) -> bytes:
    """Create one classic Telink OTA block."""
    if not 0 <= block_number <= 0xFFFF:
        raise ValueError("Block number out of range")
    if len(payload) != BLOCK_SIZE:
        raise ValueError(f"Payload must be exactly {BLOCK_SIZE} bytes")
    body = block_number.to_bytes(2, "little") + payload
    return body + crc16_modbus(body).to_bytes(2, "little")


def make_finish(block_count: int) -> bytes:
    """Create the final classic Telink OTA command."""
    if not 1 <= block_count <= 0x10000:
        raise ValueError("Invalid block count")
    last = block_count - 1
    return b"\x02\xFF" + last.to_bytes(2, "little") + ((~last) & 0xFFFF).to_bytes(2, "little")
