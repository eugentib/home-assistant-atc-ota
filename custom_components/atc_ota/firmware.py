"""pvvx firmware catalog, download and persistent cache helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    LYWSD03MMC_INDICES,
    MAX_FIRMWARE_SIZE,
    PVVX_CATALOG_URL,
    PVVX_RAW_BASE_URL,
)
from .firmware_cache import (
    firmware_cache_filename,
    read_cached_firmware,
    sha256_hex,
    write_cached_firmware,
)
from .protocol import validate_firmware


@dataclass(slots=True)
class FirmwareChoice:
    version: str
    path: str
    filename: str
    url: str


@dataclass(slots=True)
class FirmwarePayload:
    """Firmware bytes plus diagnostics about where they came from."""

    data: bytes
    source: str
    cache_file: str
    sha256: str


def bcd_version(value: int) -> str:
    """Convert pvvx BCD version integer, e.g. 0x59 -> 5.9."""
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError("Invalid pvvx version")
    return f"{(value >> 4) & 0x0F}.{value & 0x0F}"


def resolve_stable_firmware(
    catalog: dict[str, Any], *, model: str | None, hardware_revision: str | None
) -> FirmwareChoice:
    """Resolve the stable pvvx image for supported LYWSD03MMC hardware."""
    model_norm = (model or "").strip().upper().replace("-", "")
    hw = (hardware_revision or "").strip().upper()
    custom = catalog.get("custom")
    if not isinstance(custom, list):
        raise RuntimeError("Unexpected pvvx firmware catalog")

    is_lywsd03 = "LYWSD03MMC" in model_norm or hw.startswith("B1.") or hw == "B2.0"
    if not is_lywsd03:
        raise RuntimeError("Automatic firmware selection currently supports LYWSD03MMC only")

    paths = [
        custom[idx]
        for idx in LYWSD03MMC_INDICES
        if idx < len(custom) and isinstance(custom[idx], str) and custom[idx] not in ("", "?")
    ]
    unique = sorted(set(paths))
    if not unique:
        raise RuntimeError("No LYWSD03MMC stable firmware is listed upstream")

    if len(unique) == 1:
        path = unique[0]
    else:
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
            raise RuntimeError("Multiple pvvx images exist and this hardware revision is ambiguous")
        path = custom[idx]
        if not isinstance(path, str) or path in ("", "?"):
            raise RuntimeError("No stable firmware is mapped to this hardware revision")

    version = bcd_version(int(catalog["version"]))
    return FirmwareChoice(version, path, path.rsplit("/", 1)[-1], PVVX_RAW_BASE_URL + path)


async def fetch_catalog(hass) -> dict[str, Any]:
    """Download pvvx firmware.json using Home Assistant's shared HTTP session."""
    session = async_get_clientsession(hass)
    async with session.get(PVVX_CATALOG_URL, timeout=20) as response:
        response.raise_for_status()
        data = await response.json(content_type=None)
    if not isinstance(data, dict) or not isinstance(data.get("custom"), list):
        raise RuntimeError("Unexpected pvvx firmware.json format")
    return data


async def download_firmware(hass, choice: FirmwareChoice) -> bytes:
    """Download and validate a pvvx firmware image."""
    session = async_get_clientsession(hass)
    async with session.get(choice.url, timeout=30) as response:
        response.raise_for_status()
        data = await response.read()
    if len(data) > MAX_FIRMWARE_SIZE:
        raise RuntimeError("Downloaded firmware is larger than 2 MiB")
    validate_firmware(data)
    return data


async def get_cached_firmware(hass, choice: FirmwareChoice) -> FirmwarePayload:
    """Return a validated persistent cached image, downloading only on cache miss."""
    cache_dir = Path(hass.config.path(".storage", "atc_ota_firmware"))
    cache_name = firmware_cache_filename(choice.version, choice.path)
    cache_path = cache_dir / cache_name

    cached = await hass.async_add_executor_job(
        read_cached_firmware,
        cache_path,
        max_size=MAX_FIRMWARE_SIZE,
        validator=validate_firmware,
    )
    if cached is not None:
        return FirmwarePayload(
            data=cached,
            source="cache",
            cache_file=cache_name,
            sha256=sha256_hex(cached),
        )

    data = await download_firmware(hass, choice)
    await hass.async_add_executor_job(write_cached_firmware, cache_path, data)
    return FirmwarePayload(
        data=data,
        source="download",
        cache_file=cache_name,
        sha256=sha256_hex(data),
    )
