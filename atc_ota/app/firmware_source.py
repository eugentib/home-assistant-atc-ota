"""Resolve and download stable pvvx firmware from the upstream repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .protocol import validate_firmware

CATALOG_URL = "https://raw.githubusercontent.com/pvvx/ATC_MiThermometer/master/firmware.json"
RAW_BASE_URL = "https://raw.githubusercontent.com/pvvx/ATC_MiThermometer/master/"
MAX_FIRMWARE_SIZE = 2 * 1024 * 1024

# Indices used by pvvx's hw_version_str / firmware.json for LYWSD03MMC variants.
LYWSD03MMC_INDICES = (0, 3, 4, 5, 10, 14)


@dataclass(slots=True)
class FirmwareChoice:
    version: str
    path: str
    filename: str
    url: str


def bcd_version(value: int) -> str:
    """Convert pvvx's one-byte BCD version integer (0x59) to '5.9'."""
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError("Invalid firmware version in upstream catalog")
    return f"{(value >> 4) & 0x0F}.{value & 0x0F}"


async def fetch_catalog() -> dict[str, Any]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        response = await client.get(CATALOG_URL)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("custom"), list):
        raise RuntimeError("Unexpected pvvx firmware.json format")
    return data


def resolve_stable_firmware(
    catalog: dict[str, Any],
    *,
    model: str | None,
    hardware_revision: str | None,
) -> FirmwareChoice:
    """Resolve the stable pvvx image for a supported device.

    v0.1.2 intentionally auto-resolves only LYWSD03MMC. Manual upload remains
    available for other device families until their HW-id mapping is verified.
    """
    model_norm = (model or "").strip().upper().replace("-", "")
    hw = (hardware_revision or "").strip().upper()
    custom = catalog.get("custom")
    if not isinstance(custom, list):
        raise RuntimeError("Upstream firmware catalog has no custom image list")

    is_lywsd03 = "LYWSD03MMC" in model_norm
    if not is_lywsd03 and hw.startswith("B1."):
        # pvvx LYWSD03MMC builds expose B1.x/B2.0 HW strings. This fallback is
        # useful if an older build omits Model Number String.
        is_lywsd03 = True
    if not is_lywsd03:
        raise RuntimeError(
            "Automatic upstream firmware selection currently supports LYWSD03MMC only. "
            "Use manual .bin upload for this device."
        )

    paths: list[str] = []
    for idx in LYWSD03MMC_INDICES:
        if idx < len(custom):
            value = custom[idx]
            if isinstance(value, str) and value and value != "?":
                paths.append(value)
    unique = sorted(set(paths))
    if not unique:
        raise RuntimeError("No LYWSD03MMC stable firmware is listed upstream")

    # If upstream ever starts shipping different images for the LYWSD03MMC HW
    # variants, do not guess for ambiguous B1.6 hardware.
    if len(unique) != 1:
        index_by_hw = {
            "B1.4": 0,
            "B1.9": 3,
            "B1.7": 5,
            "B2.0": 5,
            "B1.5": 10,
            "B1.1": 14,
        }
        idx = index_by_hw.get(hw)
        if idx is None or idx >= len(custom):
            raise RuntimeError(
                "Upstream now has multiple LYWSD03MMC images and this hardware revision is ambiguous. "
                "Use manual .bin upload."
            )
        path = custom[idx]
        if not isinstance(path, str) or not path or path == "?":
            raise RuntimeError("No stable firmware is mapped to this hardware revision")
    else:
        path = unique[0]

    version = bcd_version(int(catalog["version"]))
    filename = path.rsplit("/", 1)[-1]
    return FirmwareChoice(
        version=version,
        path=path,
        filename=filename,
        url=RAW_BASE_URL + path,
    )


async def get_latest_choice(
    *, model: str | None, hardware_revision: str | None
) -> FirmwareChoice:
    catalog = await fetch_catalog()
    return resolve_stable_firmware(
        catalog,
        model=model,
        hardware_revision=hardware_revision,
    )


async def download_firmware(choice: FirmwareChoice) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(choice.url)
        response.raise_for_status()
        data = response.content
    if len(data) > MAX_FIRMWARE_SIZE:
        raise RuntimeError("Downloaded firmware is larger than 2 MiB")
    validate_firmware(data)
    return data
