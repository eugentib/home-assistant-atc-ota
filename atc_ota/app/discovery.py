"""BLE advertisement classification helpers for ATC/pvvx thermometers."""

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
) -> tuple[bool, str]:
    """Recognize pvvx/ATC thermometers by name OR advertisement format."""
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
    for uuid, label in ATC_SERVICE_UUIDS.items():
        if uuid in advertised:
            return True, label

    return False, ""
