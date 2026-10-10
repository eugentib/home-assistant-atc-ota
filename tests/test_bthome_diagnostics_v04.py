"""Exercise the v0.4 diagnostics probe without a Home Assistant runtime."""

import ast
import asyncio
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).parents[1] / "custom_components" / "atc_ota" / "diagnostics.py"


def _diagnostics_function(probe):
    module_ast = ast.parse(SOURCE.read_text(encoding="utf-8"))
    func = next(
        node for node in module_ast.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "async_get_config_entry_diagnostics"
    )
    program = ast.Module(body=[func], type_ignores=[])
    namespace = {"configured_lywsd03mmc": probe, "asdict": asdict}
    exec(compile(ast.fix_missing_locations(program), str(SOURCE), "exec"), namespace)
    return namespace["async_get_config_entry_diagnostics"]


def _entry():
    manager = SimpleNamespace(
        devices={},
        instance_id="test-manager",
        low_battery_threshold=30,
        _options_backup={},
        options_recovered_keys=[],
        catalog=None,
    )
    return SimpleNamespace(runtime_data=manager, options={})


def test_bthome_diagnostics_shows_native_level_without_mutating_manager():
    bthome = SimpleNamespace(
        address="A4:C1:38:67:AA:41",
        name="Pool",
        battery=67,
        battery_entity_id="sensor.pool_battery",
    )
    entry = _entry()
    fn = _diagnostics_function(lambda _hass: {bthome.address: bthome})
    result = asyncio.run(fn(object(), entry))
    diag = result["bthome_inventory_v04_prototype"]
    assert diag["status"] == "ok"
    assert diag["device_count"] == 1
    assert diag["devices"][bthome.address]["battery"] == 67
    assert diag["devices"][bthome.address]["battery_entity_id"] == "sensor.pool_battery"
    assert diag["devices"][bthome.address]["legacy_atc_ota_known"] is False
    assert entry.runtime_data.devices == {}


def test_bthome_diagnostics_failure_does_not_break_existing_diagnostics():
    def fail(_hass):
        raise RuntimeError("BTHome not ready")
    result = asyncio.run(_diagnostics_function(fail)(object(), _entry()))
    assert result["devices"] == {}
    assert result["bthome_inventory_v04_prototype"] == {
        "status": "error",
        "error": "RuntimeError: BTHome not ready",
    }
