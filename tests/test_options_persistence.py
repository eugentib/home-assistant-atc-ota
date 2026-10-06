from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def _manager_source() -> str:
    return (ROOT / "manager.py").read_text(encoding="utf-8")


def test_options_backup_is_stored_with_inventory():
    source = _manager_source()

    assert '"options_backup": dict(self._options_backup)' in source
    assert "self._options_backup = self._effective_options_snapshot()" in source


def test_missing_options_are_restored_from_backup_only():
    source = _manager_source()

    setup = source.split("async def async_setup", 1)[1].split(
        "def async_shutdown", 1
    )[0]
    assert "saved_options = self._known_options" in setup
    assert "if key not in current_options:" in setup
    assert "restored_options[key] = value" in setup
    assert "async_update_entry" in setup


def test_existing_config_entry_options_keep_priority():
    source = _manager_source()

    setup = source.split("async def async_setup", 1)[1].split(
        "def async_shutdown", 1
    )[0]
    assert "if key not in current_options:" in setup
    assert "restored_options[key] = value" in setup
    assert "restored_options.update(saved_options)" not in setup


def test_option_changes_are_backed_up_without_reload():
    source = _manager_source()
    config_flow = (ROOT / "config_flow.py").read_text(encoding="utf-8")

    assert "OptionsFlowWithReload" not in config_flow
    assert "self.entry.add_update_listener(self._async_options_updated)" in source
    assert "await self._async_save()" in source.split(
        "async def _async_options_updated", 1
    )[1].split("@property", 1)[0]


def test_option_persistence_diagnostics_are_exposed():
    update = (ROOT / "update.py").read_text(encoding="utf-8")
    diagnostics = (ROOT / "diagnostics.py").read_text(encoding="utf-8")

    assert '"entry_options": dict(self.manager.entry.options)' in update
    assert '"options_backup": dict(self.manager._options_backup)' in update
    assert '"options_recovered_keys": list(self.manager.options_recovered_keys)' in update
    assert '"options_backup": dict(manager._options_backup)' in diagnostics
