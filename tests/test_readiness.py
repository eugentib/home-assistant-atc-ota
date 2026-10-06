from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"
spec = importlib.util.spec_from_file_location("atc_readiness_test", ROOT / "readiness.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def check(**kwargs):
    defaults = dict(
        battery=80,
        battery_fresh=True,
        low_battery_threshold=30,
        gatt_proxy="btproxy1",
        gatt_rssi=-70,
        gatt_failures=0,
        gatt_free_slots=3,
        gatt_route_age_seconds=5,
        gatt_route_max_age_seconds=60,
        gatt_active_connections=True,
    )
    defaults.update(kwargs)
    return mod.evaluate_ota_readiness(**defaults)


def test_ready():
    assert check().state == "ready"


def test_low_battery_blocks_before_route_quality():
    result = check(battery=20, gatt_rssi=-95)
    assert result.state == "low_battery"


def test_no_connectable_route():
    assert check(gatt_proxy=None, gatt_rssi=None).state == "no_gatt_route"


def test_busy_proxy():
    assert check(gatt_free_slots=0).state == "gatt_busy"


def test_weak_and_poor_signal_thresholds():
    assert check(gatt_rssi=-85).state == "weak_signal"
    assert check(gatt_rssi=-90).state == "poor_signal"


def test_recent_failures_are_reported():
    assert check(gatt_failures=2).state == "unstable_route"


def test_stale_battery_is_not_ready():
    assert check(battery_fresh=False).state == "waiting_for_battery"

def test_stale_gatt_route_is_not_ready():
    assert check(gatt_route_age_seconds=61).state == "stale_gatt_route"


def test_passive_only_esphome_proxy_is_not_ready():
    assert check(gatt_active_connections=False).state == "no_active_gatt"

def test_battery_threshold_is_inclusive_minimum():
    assert check(battery=30).state == "ready"
    assert check(battery=29).state == "low_battery"
