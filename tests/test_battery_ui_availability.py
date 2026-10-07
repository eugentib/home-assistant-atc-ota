from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_battery_sensor_visibility_is_not_tied_to_ota_freshness():
    sensor = (ROOT / "sensor.py").read_text(encoding="utf-8")

    block = sensor.split("class AtcBatterySensor", 1)[1].split(
        "class AtcRssiSensor", 1
    )[0]

    assert "return self.state_data.battery is not None" in block
    assert '"fresh_for_ota": self.manager.battery_is_fresh(state)' in block
    assert "return self.manager.battery_is_fresh(self.state_data)" not in block


def test_low_battery_binary_sensor_stays_freshness_gated():
    binary = (ROOT / "binary_sensor.py").read_text(encoding="utf-8")

    assert "return self.manager.battery_is_fresh(self.state_data)" in binary
