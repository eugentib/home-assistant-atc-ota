"""Behavioral tests for v0.4 runtime BTHome inventory sync.

The production manager imports Home Assistant; load only the actual method
from its AST and provide fake HA dependencies. No source-string assertions.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


PATH = Path(__file__).parents[1] / "custom_components/atc_ota/manager.py"


@dataclass
class FakeState:
    address: str
    name: str = ""
    model: str | None = None
    battery: int | None = None
    battery_source: str | None = None
    battery_last_seen: float | None = None


def _sync_method(snapshots):
    parsed = ast.parse(PATH.read_text(encoding="utf-8"))
    manager = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == "AtcManager")
    method = next(n for n in manager.body if isinstance(n, ast.FunctionDef) and n.name == "_sync_bthome_inventory")
    ns = {
        "DeviceState": FakeState,
        "configured_lywsd03mmc": lambda _hass: snapshots,
        "async_dispatcher_send": lambda *_args: None,
        "signal_device_added": lambda *_args: "added",
        "_LOGGER": SimpleNamespace(warning=lambda *_args: None),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(PATH), "exec"), ns)
    return ns["_sync_bthome_inventory"]


class Manager:
    def __init__(self, states=None):
        self.devices = states or {}
        self.hass = object()
        self.entry = SimpleNamespace(entry_id="test")
        self.notifications = []
        self.added = 0

    def _refresh_home_assistant_name(self, state):
        pass

    def _schedule_save(self):
        self.added += 1

    def _update_ota_readiness(self, state):
        pass

    def _notify(self, address):
        self.notifications.append(address)


def test_only_configured_bthome_devices_are_loaded_and_reported():
    mac = "A4:C1:38:67:AA:41"
    snapshots = {mac: SimpleNamespace(name="Pool", battery=72)}
    mgr = Manager({"A4:C1:38:00:00:01": FakeState("A4:C1:38:00:00:01")})
    _sync_method(snapshots)(mgr, prune=True)
    assert list(mgr.devices) == [mac]
    state = mgr.devices[mac]
    assert state.model == "LYWSD03MMC"
    assert state.battery == 72
    assert state.battery_source == "bthome_entity"
    assert state.battery_last_seen is None
    assert mgr.notifications == [mac]
    assert mgr.added == 1


def test_native_bthome_battery_change_invalidates_gatt_safety_proof():
    mac = "A4:C1:38:67:AA:41"
    state = FakeState(mac, "Pool", battery=81, battery_source="gatt", battery_last_seen=12345.0)
    mgr = Manager({mac: state})
    _sync_method({mac: SimpleNamespace(name="Pool", battery=80)})(mgr)
    assert state.battery == 80
    assert state.battery_source == "bthome_entity"
    assert state.battery_last_seen is None


def test_unchanged_bthome_battery_does_not_erase_recent_gatt_proof():
    mac = "A4:C1:38:67:AA:41"
    state = FakeState(mac, "Pool", battery=80, battery_source="gatt", battery_last_seen=12345.0)
    mgr = Manager({mac: state})
    _sync_method({mac: SimpleNamespace(name="Pool", battery=80)})(mgr)
    assert state.battery_source == "gatt"
    assert state.battery_last_seen == 12345.0


def test_absent_battery_does_not_mark_safety_fresh():
    mac = "A4:C1:38:67:AA:41"
    mgr = Manager()
    _sync_method({mac: SimpleNamespace(name="Pool", battery=None)})(mgr)
    assert mgr.devices[mac].battery is None
    assert mgr.devices[mac].battery_last_seen is None
