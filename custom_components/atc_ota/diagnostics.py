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
        data["broadcast"] = {
            "rssi": state.rssi,
            "proxy": state.strongest_proxy,
            "last_seen": state.last_seen,
        }
        data["gatt_route"] = {
            "rssi": state.gatt_rssi,
            "proxy": state.gatt_proxy,
            "age_seconds": state.gatt_route_age_seconds,
            "failures": state.gatt_failures,
            "free_slots": state.gatt_free_slots,
            "slots": state.gatt_slots,
        }
        data["ota_readiness"] = {
            "state": state.ota_readiness,
            "reason": state.ota_readiness_reason,
        }
        data["ha_reachability"] = manager._reachability_diagnostics(address)
        devices[address] = data
    return {
        "options": dict(entry.options),
        "catalog_loaded": manager.catalog is not None,
        "devices": devices,
    }
