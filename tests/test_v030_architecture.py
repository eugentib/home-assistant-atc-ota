from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_entities_are_push_based():
    source = (ROOT / "entity.py").read_text(encoding="utf-8")
    assert "_attr_should_poll = False" in source


def test_volatile_ble_state_is_not_persisted():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")
    persistent_block = source.split("def persistent(self)", 1)[1].split("@classmethod", 1)[0]

    for field in (
        "rssi",
        "strongest_proxy",
        "last_seen",
        "battery_last_seen",
        "gatt_rssi",
        "gatt_proxy",
        "gatt_route_age_seconds",
        "gatt_failures",
        "gatt_free_slots",
        "gatt_slots",
        "ota_readiness",
    ):
        assert f'"{field}"' not in persistent_block


def test_manager_uses_separate_connectable_history():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")
    assert '"async_last_service_info"' in source
    assert "connectable=True" in source
    assert "gatt_proxy" in source
