"""Behavioral tests for the future BTHome-first inventory adapter.

Tests execute the actual module; there is no Home Assistant installation in CI.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import sys


PATH = Path(__file__).parents[1] / "custom_components" / "atc_ota" / "bthome_inventory.py"
SPEC = spec_from_file_location("atc_ota_bthome_inventory_under_test", PATH)
MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _entry(address, entry_id, title):
    return SimpleNamespace(unique_id=address, entry_id=entry_id, title=title)


def _entity(entity_id, domain="sensor"):
    return SimpleNamespace(entity_id=entity_id, domain=domain)


def _state(value, device_class="battery"):
    return SimpleNamespace(state=value, attributes={"device_class": device_class})


def _collect(entries, registered, states):
    return MODULE.snapshots_from_bthome(
        entries,
        lambda entry_id: registered.get(entry_id, []),
        states.get,
    )


def test_uses_native_bthome_battery_entity_not_a_second_parser():
    address = "a4:c1:38:67:aa:41"
    entries = [_entry(address, "bthome-1", "Pool")]
    devices = _collect(
        entries,
        {"bthome-1": [_entity("sensor.pool_battery")]},
        {"sensor.pool_battery": _state("79")},
    )
    assert list(devices) == ["A4:C1:38:67:AA:41"]
    assert devices["A4:C1:38:67:AA:41"] == MODULE.BTHomeThermometer(
        address="A4:C1:38:67:AA:41",
        name="Pool",
        battery=79,
        battery_entity_id="sensor.pool_battery",
    )


def test_only_configured_lywsd03mmc_entries_are_returned():
    devices = _collect(
        [
            _entry("A4:C1:38:67:AA:41", "b1", "Pool"),
            _entry("AA:BB:CC:DD:EE:FF", "b2", "Other BTHome sensor"),
            _entry(None, "b3", "Invalid"),
        ],
        {},
        {},
    )
    assert list(devices) == ["A4:C1:38:67:AA:41"]
    assert devices["A4:C1:38:67:AA:41"].battery is None


def test_does_not_guess_battery_sensor_by_name_or_cross_device():
    devices = _collect(
        [_entry("A4:C1:38:67:AA:41", "b1", "Pool")],
        {
            "b1": [
                _entity("sensor.pool_temperature"),
                _entity("sensor.pool_battery"),
                _entity("switch.pool_fake", domain="switch"),
            ],
            "b2": [_entity("sensor.unrelated_battery")],
        },
        {
            "sensor.pool_temperature": _state("20.3", device_class="temperature"),
            "sensor.pool_battery": _state("unknown"),
            "sensor.unrelated_battery": _state("90"),
            "switch.pool_fake": _state("80"),
        },
    )
    assert devices["A4:C1:38:67:AA:41"].battery is None
    assert devices["A4:C1:38:67:AA:41"].battery_entity_id is None


def test_invalid_battery_values_are_rejected_and_zero_is_preserved():
    for invalid in ("unknown", "unavailable", None, "-1", "101", "nan", "inf", "50.5"):
        devices = _collect(
            [_entry("A4:C1:38:67:AA:41", "b1", "Pool")],
            {"b1": [_entity("sensor.pool_battery")]},
            {"sensor.pool_battery": _state(invalid)},
        )
        assert devices["A4:C1:38:67:AA:41"].battery is None

    devices = _collect(
        [_entry("A4:C1:38:67:AA:41", "b1", "Pool")],
        {"b1": [_entity("sensor.pool_battery")]},
        {"sensor.pool_battery": _state("0")},
    )
    assert devices["A4:C1:38:67:AA:41"].battery == 0
