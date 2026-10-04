from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atc_ota"))

from app.ha_publish import entity_suffix  # noqa: E402
from app.inventory import InventoryStore, update_available, version_tuple  # noqa: E402


def test_version_parsing() -> None:
    assert version_tuple("V5.4") == (5, 4)
    assert version_tuple("5.9") == (5, 9)
    assert version_tuple(None) is None


def test_update_available() -> None:
    assert update_available("V5.4", "5.9") is True
    assert update_available("V5.9", "5.9") is False
    assert update_available("V6.0", "5.9") is False
    assert update_available(None, "5.9") is None


def test_entity_suffix() -> None:
    assert entity_suffix("A4:C1:38:51:5D:77") == "a4_c1_38_51_5d_77"


def test_inventory_persists_battery_percent(tmp_path):
    store = InventoryStore(tmp_path / "devices.json")
    entry = store.update_info(
        "A4:C1:38:00:00:01",
        {"model": "LYWSD03MMC", "current_version": "V5.9", "battery_percent": 27},
        latest={"version": "5.9", "path": "bin/ATC_v59.bin", "filename": "ATC_v59.bin"},
        latest_error=None,
    )
    assert entry["battery_percent"] == 27
    store.save()
    reloaded = InventoryStore(tmp_path / "devices.json")
    assert reloaded.get("A4:C1:38:00:00:01")["battery_percent"] == 27


def test_gatt_missing_battery_does_not_erase_advertised_battery(tmp_path):
    store = InventoryStore(tmp_path / "devices.json")
    store.note_scan(
        [{
            "address": "A4:C1:38:00:00:02",
            "name": "ATC_000002",
            "candidate": True,
            "candidate_reason": "BTHome v2 0xFCD2",
            "battery_percent": 33,
            "battery_source": "BTHome v2 advertisement",
            "battery_advertised_at": 1234.0,
        }],
        "BTproxy1",
    )
    entry = store.update_info(
        "A4:C1:38:00:00:02",
        {"model": "LYWSD03MMC", "current_version": "V5.9", "battery_percent": None},
        latest={"version": "5.9", "path": "bin/ATC_v59.bin", "filename": "ATC_v59.bin"},
        latest_error=None,
    )
    assert entry["battery_percent"] == 33
    assert entry["battery_source"] == "BTHome v2 advertisement"
