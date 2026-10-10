"""Read-only inventory adapter for already configured Home Assistant BTHome devices.

This is the v0.4 inventory source; ATC OTA never owns a BLE advertising scanner.
Home Assistant BTHome owns decoding and entity state; we never decode its
advertisements or create a second battery sensor here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Callable, Iterable

_LYWSD03MMC_ADDRESS = re.compile(r"^A4:C1:38:(?:[0-9A-F]{2}:){2}[0-9A-F]{2}$")


@dataclass(frozen=True, slots=True)
class BTHomeThermometer:
    """Small, read-only view of one configured LYWSD03MMC."""

    address: str
    name: str
    battery: int | None
    battery_entity_id: str | None


def _battery_percentage(entity_state: object) -> int | None:
    """Only accept a valid, explicitly typed HA battery sensor state."""
    if entity_state is None:
        return None
    attrs = getattr(entity_state, "attributes", {}) or {}
    if attrs.get("device_class") != "battery":
        return None
    raw = getattr(entity_state, "state", None)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0 <= value <= 100 or not value.is_integer():
        return None
    return int(value)


def snapshots_from_bthome(
    config_entries: Iterable[object],
    entities_for_entry: Callable[[str], Iterable[object]],
    get_state: Callable[[str], object],
) -> dict[str, BTHomeThermometer]:
    """Project configured BTHome entries to a deterministic OTA device list.

    The caller must provide *BTHome-only* config entries. Identification is by
    exact unique_id/MAC, never by a guessed entity_id, friendly name or RSSI.
    No radio operations, persistent inventory or state mutation are performed.
    """
    devices: dict[str, BTHomeThermometer] = {}
    for config_entry in config_entries:
        unique_id = getattr(config_entry, "unique_id", None)
        if not isinstance(unique_id, str):
            continue
        address = unique_id.upper()
        if not _LYWSD03MMC_ADDRESS.fullmatch(address):
            continue

        battery = None
        battery_entity_id = None
        candidates = sorted(
            (
                item
                for item in entities_for_entry(config_entry.entry_id)
                if getattr(item, "domain", None) == "sensor"
            ),
            key=lambda item: item.entity_id,
        )
        for item in candidates:
            value = _battery_percentage(get_state(item.entity_id))
            if value is not None:
                battery = value
                battery_entity_id = item.entity_id
                break

        devices[address] = BTHomeThermometer(
            address=address,
            name=getattr(config_entry, "title", None) or address,
            battery=battery,
            battery_entity_id=battery_entity_id,
        )
    return devices


def configured_lywsd03mmc(hass) -> dict[str, BTHomeThermometer]:
    """Use supported public HA config/entity registries (no BTHome internals)."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    return snapshots_from_bthome(
        hass.config_entries.async_entries("bthome"),
        lambda entry_id: er.async_entries_for_config_entry(registry, entry_id),
        hass.states.get,
    )
