from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_actual_route_is_taken_from_connected_ha_backend():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert 'getattr(client, "_connected_scanner", None)' in source
    assert 'getattr(ha_backend, "_connected_scanner", None)' in source
    assert "get_discovered_device_advertisement_data" in source
    assert "_actual_connected_route(client, address)" in source


def test_last_ota_route_is_persisted_and_exposed():
    manager = (ROOT / "manager.py").read_text(encoding="utf-8")
    sensor = (ROOT / "sensor.py").read_text(encoding="utf-8")
    update = (ROOT / "update.py").read_text(encoding="utf-8")

    for field in (
        "last_ota_proxy",
        "last_ota_rssi",
        "last_ota_result",
        "last_ota_at",
        "last_ota_detail",
    ):
        assert f'"{field}"' in manager

    assert 'class AtcLastOtaRouteSensor' in sensor
    assert '"last_ota_proxy"' in update


def test_route_introspection_is_best_effort():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert "Unable to inspect actual BLE route" in source
    assert "return None, None" in source
