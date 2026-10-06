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
        data["battery_live"] = {
            "value": state.battery,
            "minimum": manager.low_battery_threshold,
            "source": state.battery_source,
            "last_seen": state.battery_last_seen,
            "age_seconds": manager.battery_age_seconds(state),
            "fresh": manager.battery_is_fresh(state),
        }
        data["ble_ingest"] = {
            "health": manager.ble_health(state),
            "callback_count": state.ble_callback_count,
            "callback_age_seconds": manager.ble_callback_age_seconds(state),
            "last_callback": state.ble_callback_last_seen,
            "observation_time": state.ble_observation_time,
            "last_error": state.ble_callback_error,
        }
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
            "active_connections": state.gatt_active_connections,
            "feature_flags": state.gatt_feature_flags,
        }
        data["ota_readiness"] = {
            "state": state.ota_readiness,
            "reason": state.ota_readiness_reason,
        }
        data["last_ota"] = {
            "proxy": state.last_ota_proxy,
            "rssi": state.last_ota_rssi,
            "result": state.last_ota_result,
            "timestamp": state.last_ota_at,
            "detail": state.last_ota_detail,
        }
        data["ha_reachability"] = manager._reachability_diagnostics(address)
        devices[address] = data
    return {
        "manager_instance": manager.instance_id,
        "options": dict(entry.options),
        "options_backup": dict(manager._options_backup),
        "options_recovered_keys": list(manager.options_recovered_keys),
        "catalog_loaded": manager.catalog is not None,
        "devices": devices,
    }
