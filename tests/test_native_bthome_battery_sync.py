from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_battery_freshness_uses_real_bluetooth_observation_time():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert "def _advertisement_age_seconds" in source
    assert "time.monotonic()" in source
    assert 'getattr(info, "time", None)' in source
    assert "state.battery_last_seen = advertisement_time" in source


def test_scanner_history_seed_does_not_retimestamp_old_battery():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert "for info in bluetooth.async_discovered_service_info" in source
    assert "advertisement_time = self._advertisement_wall_time(info)" in source
    assert "state.last_seen = advertisement_time" in source
    assert "state.battery_last_seen = time.time()" not in source.split(
        "def _async_bluetooth_event", 1
    )[1].split("def _schedule_metadata_probe", 1)[0]


def test_native_bthome_entity_timestamp_is_not_used_for_freshness():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert "_sync_native_bthome_battery" not in source
    assert '"last_reported"' not in source
    assert 'battery_source = "home_assistant_bthome"' not in source


def test_battery_entity_exposes_provenance_and_age():
    source = (ROOT / "sensor.py").read_text(encoding="utf-8")

    assert '"source": state.battery_source' in source
    assert '"age_seconds": self.manager.battery_age_seconds(state)' in source
    assert "self.manager.battery_is_fresh(self.state_data)" in source
