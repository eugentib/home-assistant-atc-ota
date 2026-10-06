from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_native_bthome_battery_is_used_as_authoritative_source():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert "async_entries_for_config_entry" in source
    assert 'entity.platform != "bthome"' in source
    assert '!= "battery"' in source
    assert '"last_reported"' in source
    assert 'state.battery_source = "home_assistant_bthome"' in source


def test_readiness_reconciles_native_bthome_before_evaluation():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    block = source.split("def _update_ota_readiness", 1)[1].split(
        "def _refresh_ble_routes", 1
    )[0]
    assert "self._sync_native_bthome_battery(state)" in block
    assert "self.battery_is_fresh(state)" in block


def test_battery_entity_exposes_provenance_and_age():
    source = (ROOT / "sensor.py").read_text(encoding="utf-8")

    assert '"source": state.battery_source' in source
    assert '"age_seconds": self.manager.battery_age_seconds(state)' in source
    assert "self.manager.battery_is_fresh(self.state_data)" in source
