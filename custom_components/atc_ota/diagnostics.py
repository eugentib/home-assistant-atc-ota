"""Diagnostics for ATC OTA."""

from __future__ import annotations

from dataclasses import asdict


async def async_get_config_entry_diagnostics(hass, entry):
    """Return non-secret integration diagnostics."""
    manager = entry.runtime_data
    devices = {}
    for address, state in manager.devices.items():
        data = state.persistent()
        data["ota_in_progress"] = state.ota_in_progress
        data["ota_progress"] = state.ota_progress
        data["ota_message"] = state.ota_message
        devices[address] = data
    return {
        "options": dict(entry.options),
        "catalog_loaded": manager.catalog is not None,
        "devices": devices,
    }
