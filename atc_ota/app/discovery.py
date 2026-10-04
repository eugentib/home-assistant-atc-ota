"""BLE advertisement classification/parsing helpers for ATC/pvvx thermometers."""

from __future__ import annotations

from typing import Iterable

# BLE service UUIDs used by the advertising formats supported by pvvx/ATC.
# 0x181A = Environmental Sensing (ATC1441/custom formats)
# 0xFE95 = Xiaomi MiBeacon
# 0xFCD2 = BTHome v2
ATC_SERVICE_UUIDS = {
    "0000181a-0000-1000-8000-00805f9b34fb": "ATC/custom 0x181A",
    "0000fe95-0000-1000-8000-00805f9b34fb": "Xiaomi MiBeacon 0xFE95",
    "0000fcd2-0000-1000-8000-00805f9b34fb": "BTHome v2 0xFCD2",
}
BTHOME_V2_UUID = "0000fcd2-0000-1000-8000-00805f9b34fb"

# The LYWSD03MMC units seen by pvvx use Xiaomi's A4:C1:38 prefix.  BTHome
# itself is generic, so 0xFCD2 alone must not make every BTHome device an ATC
# thermometer candidate (e.g. unrelated NBHome devices).
LYWSD03MMC_MAC_PREFIX = "A4:C1:38:"


def normalize_uuid(value: str) -> str:
    """Normalize 16-bit or full UUID strings into canonical lowercase UUIDs."""
    value = str(value).strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    if len(value) == 4 and all(c in "0123456789abcdef" for c in value):
        return f"0000{value}-0000-1000-8000-00805f9b34fb"
    return value


def classify_atc(
    name: str,
    service_uuids: Iterable[str] = (),
    service_data_uuids: Iterable[str] = (),
    *,
    address: str = "",
) -> tuple[bool, str]:
    """Recognize likely pvvx/ATC thermometers and describe the advert format.

    0x181A and Xiaomi 0xFE95 are sufficiently specific for this project.  BTHome
    v2 (0xFCD2) is a generic format used by many unrelated devices, so unnamed
    BTHome devices are only treated as LYWSD03MMC candidates when their address
    uses Xiaomi's known A4:C1:38 prefix.  Named ATC/LYWSD/etc devices remain
    candidates regardless of address.
    """
    value = (name or "").upper()
    prefixes = (
        "ATC_",
        "LYWSD",
        "MJWSD",
        "MHO",
        "CGG",
        "CGDK",
        "QINGPING",
    )
    if value.startswith(prefixes):
        return True, "device name"

    advertised = {
        normalize_uuid(v) for v in (*tuple(service_uuids), *tuple(service_data_uuids))
    }

    if "0000181a-0000-1000-8000-00805f9b34fb" in advertised:
        return True, "ATC/custom 0x181A"
    if "0000fe95-0000-1000-8000-00805f9b34fb" in advertised:
        return True, "Xiaomi MiBeacon 0xFE95"
    if BTHOME_V2_UUID in advertised:
        if str(address or "").upper().startswith(LYWSD03MMC_MAC_PREFIX):
            return True, "BTHome v2 0xFCD2"
        return False, "BTHome v2 0xFCD2"

    return False, ""


def parse_bthome_v2_service_data(payload: bytes | bytearray | memoryview) -> dict[str, int | bool]:
    """Parse the small subset of unencrypted BTHome v2 data useful to this app.

    Bleak's ``AdvertisementData.service_data`` normally strips the 16-bit UUID,
    so *payload* starts with the BTHome Device Information byte.  A defensive
    prefix check also accepts raw data that still begins with D2 FC / FC D2.

    BTHome requires object IDs to be in ascending order. Battery is object 0x01
    and is one uint8 percentage, so we can read it safely without implementing
    every later object type.  Packet ID 0x00, when present, is skipped first.
    """
    data = bytes(payload)
    if len(data) >= 2 and data[:2] in (b"\xd2\xfc", b"\xfc\xd2"):
        data = data[2:]
    if not data:
        return {}

    info = data[0]
    version = (info >> 5) & 0x07
    encrypted = bool(info & 0x01)
    result: dict[str, int | bool] = {
        "bthome_version": version,
        "bthome_encrypted": encrypted,
    }
    if version != 2 or encrypted:
        return result

    index = 1
    while index < len(data):
        object_id = data[index]
        index += 1

        if object_id == 0x00:  # packet id: uint8
            if index >= len(data):
                break
            result["packet_id"] = data[index]
            index += 1
            continue

        if object_id == 0x01:  # battery: uint8, 0..100 %
            if index >= len(data):
                break
            value = int(data[index])
            if 0 <= value <= 100:
                result["battery_percent"] = value
            break

        # IDs must be sorted. Once we are beyond 0x01 there cannot be a battery
        # object later in this advertisement, so no generic object-length table
        # is necessary merely to extract battery percentage.
        if object_id > 0x01:
            break

    return result


def parse_bthome_battery(service_data: dict[str, bytes]) -> int | None:
    """Return an unencrypted BTHome v2 battery percentage, if present."""
    for key, value in service_data.items():
        if normalize_uuid(key) != BTHOME_V2_UUID:
            continue
        parsed = parse_bthome_v2_service_data(value)
        battery = parsed.get("battery_percent")
        if isinstance(battery, int):
            return battery
    return None
