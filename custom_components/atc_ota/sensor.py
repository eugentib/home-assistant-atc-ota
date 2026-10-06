"""Sensors for ATC OTA."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory

from .entity import AtcOtaEntity
from .manager import signal_device_added


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    manager = entry.runtime_data
    known: set[str] = set()

    def add(address: str) -> None:
        if address in known:
            return
        known.add(address)
        async_add_entities(
            [
                AtcBatterySensor(manager, address),
                AtcRssiSensor(manager, address),
                AtcGattRssiSensor(manager, address),
                AtcBroadcastSourceSensor(manager, address),
                AtcBleHealthSensor(manager, address),
                AtcOtaRouteSensor(manager, address),
                AtcLastOtaRouteSensor(manager, address),
                AtcOtaProgressSensor(manager, address),
                AtcOtaStatusSensor(manager, address),
                AtcOtaReadinessSensor(manager, address),
            ]
        )

    for address in manager.devices:
        add(address)
    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_device_added(entry.entry_id), add)
    )


class AtcBatterySensor(AtcOtaEntity, SensorEntity):
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_battery"

    @property
    def native_value(self):
        return self.state_data.battery

    @property
    def available(self) -> bool:
        return self.manager.battery_is_fresh(self.state_data)

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "source": state.battery_source,
            "age_seconds": self.manager.battery_age_seconds(state),
            "last_seen": state.battery_last_seen,
        }


class AtcRssiSensor(AtcOtaEntity, SensorEntity):
    """Strongest passive advertisement RSSI, regardless of GATT capability."""

    _attr_name = "Broadcast RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_icon = "mdi:signal"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        # Keep the old unique id so existing entity customizations/automations survive.
        self._attr_unique_id = f"{address}_rssi"

    @property
    def native_value(self):
        return self.state_data.rssi

    @property
    def extra_state_attributes(self):
        return {
            "broadcast_proxy": self.state_data.strongest_proxy,
            "last_seen": self.state_data.last_seen,
        }


class AtcGattRssiSensor(AtcOtaEntity, SensorEntity):
    """RSSI of Home Assistant's preferred connectable route."""

    _attr_name = "GATT RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_gatt_rssi"

    @property
    def native_value(self):
        return self.state_data.gatt_rssi

    @property
    def available(self) -> bool:
        return self.state_data.gatt_proxy is not None

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "gatt_proxy": state.gatt_proxy,
            "route_age_seconds": state.gatt_route_age_seconds,
            "connection_failures": state.gatt_failures,
            "free_slots": state.gatt_free_slots,
            "total_slots": state.gatt_slots,
        }


class AtcBroadcastSourceSensor(AtcOtaEntity, SensorEntity):
    _attr_name = "Broadcast source"
    _attr_icon = "mdi:access-point"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_broadcast_source"

    @property
    def native_value(self):
        return self.state_data.strongest_proxy


class AtcBleHealthSensor(AtcOtaEntity, SensorEntity):
    """Health of the ATC OTA BLE ingest path."""

    _attr_name = "BLE health"
    _attr_icon = "mdi:bluetooth-audio"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_ble_health"

    @property
    def native_value(self):
        return self.manager.ble_health(self.state_data)

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "callback_count": state.ble_callback_count,
            "callback_age_seconds": self.manager.ble_callback_age_seconds(state),
            "last_callback": state.ble_callback_last_seen,
            "last_advertisement": state.last_seen,
            "observation_time": state.ble_observation_time,
            "last_error": state.ble_callback_error,
        }


class AtcOtaRouteSensor(AtcOtaEntity, SensorEntity):
    _attr_name = "OTA route"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_ota_route"

    @property
    def native_value(self):
        return self.state_data.gatt_proxy

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "rssi": state.gatt_rssi,
            "route_age_seconds": state.gatt_route_age_seconds,
            "active_connections": state.gatt_active_connections,
            "feature_flags": state.gatt_feature_flags,
            "connection_failures": state.gatt_failures,
            "free_slots": state.gatt_free_slots,
            "total_slots": state.gatt_slots,
        }


class AtcLastOtaRouteSensor(AtcOtaEntity, SensorEntity):
    """Route actually used by the most recent OTA connection."""

    _attr_name = "Last OTA route"
    _attr_icon = "mdi:history"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_last_ota_route"

    @property
    def native_value(self):
        return self.state_data.last_ota_proxy

    @property
    def available(self) -> bool:
        return self.state_data.last_ota_result is not None

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "rssi": state.last_ota_rssi,
            "result": state.last_ota_result,
            "timestamp": state.last_ota_at,
            "detail": state.last_ota_detail,
        }


class AtcOtaProgressSensor(AtcOtaEntity, SensorEntity):
    """Visible OTA progress independent of the frontend update dialog."""

    _attr_name = "OTA progress"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:progress-upload"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_ota_progress"

    @property
    def native_value(self):
        return self.state_data.ota_progress

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "in_progress": state.ota_in_progress,
            "message": state.ota_message,
            "result": state.last_ota_result,
        }


class AtcOtaStatusSensor(AtcOtaEntity, SensorEntity):
    """Compact OTA lifecycle state with detailed message as an attribute."""

    _attr_name = "OTA status"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_ota_status"

    @property
    def native_value(self):
        state = self.state_data
        if state.ota_in_progress:
            return "running"
        return state.last_ota_result or "idle"

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "message": state.ota_message,
            "progress": state.ota_progress,
            "last_result": state.last_ota_result,
            "last_detail": state.last_ota_detail,
            "last_route": state.last_ota_proxy,
            "last_route_rssi": state.last_ota_rssi,
        }


class AtcOtaReadinessSensor(AtcOtaEntity, SensorEntity):
    """Compact preflight summary for the firmware update path."""

    _attr_name = "OTA readiness"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager, address: str) -> None:
        super().__init__(manager, address)
        self._attr_unique_id = f"{address}_ota_readiness"

    @property
    def native_value(self):
        return self.state_data.ota_readiness

    @property
    def extra_state_attributes(self):
        state = self.state_data
        return {
            "reason": state.ota_readiness_reason,
            "battery": state.battery,
            "battery_source": state.battery_source,
            "battery_age_seconds": self.manager.battery_age_seconds(state),
            "broadcast_proxy": state.strongest_proxy,
            "broadcast_rssi": state.rssi,
            "gatt_proxy": state.gatt_proxy,
            "gatt_rssi": state.gatt_rssi,
            "gatt_route_age_seconds": state.gatt_route_age_seconds,
            "gatt_failures": state.gatt_failures,
            "gatt_free_slots": state.gatt_free_slots,
            "gatt_slots": state.gatt_slots,
            "gatt_active_connections": state.gatt_active_connections,
            "gatt_feature_flags": state.gatt_feature_flags,
        }
