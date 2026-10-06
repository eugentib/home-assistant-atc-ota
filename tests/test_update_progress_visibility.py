from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_update_entity_reads_progress_directly_from_manager_state():
    source = (ROOT / "update.py").read_text(encoding="utf-8")

    assert "def in_progress(self)" in source
    assert "return bool(self.state_data.ota_in_progress)" in source
    assert "def update_percentage(self)" in source
    assert "self.state_data.ota_progress if self.state_data.ota_in_progress else None" in source
    assert "_sync_progress_attrs" not in source


def test_visible_ota_progress_and_status_entities_exist():
    source = (ROOT / "sensor.py").read_text(encoding="utf-8")

    assert "class AtcOtaProgressSensor" in source
    assert 'self._attr_unique_id = f"{address}_ota_progress"' in source
    assert "class AtcOtaStatusSensor" in source
    assert 'self._attr_unique_id = f"{address}_ota_status"' in source


def test_gatt_battery_refresh_recalculates_readiness():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    refresh = source.split("async def async_refresh_device", 1)[1].split(
        "async def async_user_refresh_device", 1
    )[0]
    assert "self._update_ota_readiness(state)" in refresh
