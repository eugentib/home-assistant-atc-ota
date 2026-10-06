from pathlib import Path


ROOT = Path(__file__).parents[1]
MANAGER = ROOT / "custom_components" / "atc_ota" / "manager.py"


def test_manager_background_tasks_do_not_block_startup():
    source = MANAGER.read_text(encoding="utf-8")

    assert "self.hass.async_create_task(" not in source
    assert source.count("self.entry.async_create_background_task(") >= 4


def test_ble_inventory_save_is_debounced():
    source = MANAGER.read_text(encoding="utf-8")

    assert "def _schedule_save" in source
    assert "def _async_delayed_save" in source
    assert "await asyncio.sleep(1.5)" in source
    assert 'self._schedule_save()' in source
