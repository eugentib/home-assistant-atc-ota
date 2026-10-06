from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def _manager_source() -> str:
    return (ROOT / "manager.py").read_text(encoding="utf-8")


def test_ble_callback_hot_path_does_not_refresh_routes_inline():
    source = _manager_source()
    callback_block = source.split("def _async_bluetooth_event", 1)[1].split(
        "def _schedule_metadata_probe", 1
    )[0]

    assert "_schedule_route_refresh(address)" in callback_block
    assert "_refresh_ble_routes(state)" not in callback_block
    assert "self._notify(address)" in callback_block


def test_ble_callback_records_health_before_optional_processing():
    source = _manager_source()
    callback_block = source.split("def _async_bluetooth_event", 1)[1].split(
        "def _schedule_metadata_probe", 1
    )[0]

    count_index = callback_block.index("state.ble_callback_count += 1")
    parser_index = callback_block.index("bthome = self._service_data")
    assert count_index < parser_index
    assert "state.ble_callback_last_seen = time.time()" in callback_block
    assert "state.ble_callback_error = None" in callback_block


def test_ble_health_timer_recovers_missed_callback_from_ha_history():
    source = _manager_source()

    assert "def _async_ble_health_timer" in source
    assert 'last_info_fn(self.hass, address, connectable=False)' in source
    assert "float(observed) > state.ble_observation_time" in source
    assert "self._async_bluetooth_event(" in source


def test_route_refresh_ignores_observations_without_numeric_rssi():
    source = _manager_source()

    assert 'isinstance(rssi, (int, float))' in source
    assert "max(usable, key=lambda pair: pair[0])" in source


def test_ble_health_sensor_is_exposed():
    sensor = (ROOT / "sensor.py").read_text(encoding="utf-8")

    assert "class AtcBleHealthSensor" in sensor
    assert '"callback_count": state.ble_callback_count' in sensor
    assert '"last_error": state.ble_callback_error' in sensor
