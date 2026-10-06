"""Small BTHome v2 parser for values ATC OTA needs."""

from __future__ import annotations

from dataclasses import dataclass

# Object sizes from the BTHome v2 format for the common sensor values pvvx emits.
# Unknown objects stop the structured parser. BTHome v2 object IDs are ordered,
# so a battery object (0x01) cannot validly appear after an unknown higher ID.
_OBJECT_SIZES: dict[int, int] = {
    0x00: 1,  # packet id
    0x01: 1,  # battery
    0x02: 2,  # temperature
    0x03: 2,  # humidity
    0x04: 3,  # pressure
    0x05: 3,  # illuminance
    0x08: 2,  # dew point
    0x09: 1,  # count
    0x0C: 2,  # voltage
    0x0F: 1,  # generic boolean
    0x10: 1,  # power (binary)
    0x11: 1,  # opening (binary)
    0x12: 2,  # CO2
    0x15: 1,  # battery low (binary)
    0x16: 1,  # battery charging (binary)
    0x17: 1,  # carbon monoxide (binary)
    0x18: 1,  # cold (binary)
    0x19: 1,  # connectivity (binary)
    0x1A: 1,  # door (binary)
    0x1B: 1,  # garage door (binary)
    0x1C: 1,  # gas (binary)
    0x1D: 1,  # heat (binary)
    0x1E: 1,  # light (binary)
    0x1F: 1,  # lock (binary)
    0x20: 1,  # moisture (binary)
    0x21: 1,  # motion (binary)
    0x22: 1,  # moving (binary)
    0x23: 1,  # occupancy (binary)
    0x24: 1,  # plug (binary)
    0x25: 1,  # presence (binary)
    0x26: 1,  # problem (binary)
    0x27: 1,  # running (binary)
    0x28: 1,  # safety (binary)
    0x29: 1,  # smoke (binary)
    0x2A: 1,  # sound (binary)
    0x2B: 1,  # tamper (binary)
    0x2C: 1,  # vibration (binary)
    0x2D: 1,  # window (binary)
    0x2E: 1,
    0x2F: 1,
    0x3D: 2,
    0x3E: 4,
    0x40: 1,
    0x41: 1,
    0x45: 1,
    0x46: 1,
    0x47: 1,
    0x48: 1,
    0x49: 1,
    0x4A: 1,
    0x4B: 1,
    0x4C: 1,
    0x4D: 1,
    0x4E: 1,
    0x4F: 1,
    0x50: 1,
    0x51: 2,
    0x52: 2,
    0x53: 2,
    0x54: 2,
    0x55: 2,
    0x56: 2,
    0x57: 2,
    0x58: 2,
    0x59: 1,
    0x5A: 2,
    0x5B: 4,
    0x60: 1,
    0xF0: 2,  # device type id
    0xF1: 4,  # firmware version, uint32 displayed little-endian component order
    0xF2: 3,  # firmware version, uint24
}


@dataclass(slots=True)
class BTHomeValues:
    """Values useful to this integration."""

    battery: int | None = None
    firmware_version: str | None = None
    device_type_id: int | None = None
    encrypted: bool = False
    version: int | None = None


def _version_from_bytes(value: bytes) -> str:
    # BTHome examples encode version bytes as least-significant component first;
    # present them without redundant leading zero components.
    parts = list(reversed(value))
    while len(parts) > 2 and parts[0] == 0:
        parts.pop(0)
    return ".".join(str(part) for part in parts)


def parse_bthome_v2(payload: bytes | bytearray) -> BTHomeValues:
    """Parse unencrypted BTHome v2 service data.

    Home Assistant service_data already strips the 16-bit service UUID, so the
    first byte here is the BTHome device-information byte (normally 0x40).
    """
    data = bytes(payload)
    result = BTHomeValues()
    if not data:
        return result

    info = data[0]
    result.encrypted = bool(info & 0x01)
    result.version = (info >> 5) & 0x07
    if result.version != 2 or result.encrypted:
        return result

    pos = 1
    while pos < len(data):
        object_id = data[pos]
        pos += 1
        size = _OBJECT_SIZES.get(object_id)
        if size is None or pos + size > len(data):
            break
        value = data[pos : pos + size]
        pos += size

        if object_id == 0x01 and value[0] <= 100:
            result.battery = int(value[0])
        elif object_id == 0xF0:
            result.device_type_id = int.from_bytes(value, "little")
        elif object_id in (0xF1, 0xF2):
            result.firmware_version = _version_from_bytes(value)

    return result
