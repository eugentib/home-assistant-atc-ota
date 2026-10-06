from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_options_flow_does_not_reload_config_entry():
    source = (ROOT / "config_flow.py").read_text(encoding="utf-8")

    assert "OptionsFlowWithReload" not in source
    assert "class AtcOtaOptionsFlow(OptionsFlow):" in source


def test_manager_tracks_and_cancels_ota_service_tasks_on_unload():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert "self._install_tasks: dict[str, asyncio.Task] = {}" in source
    shutdown = source.split("def async_shutdown", 1)[1].split(
        "def _async_catalog_timer", 1
    )[0]
    assert "for task in tuple(self._install_tasks.values())" in shutdown
    assert "task.cancel()" in shutdown
    assert "self._shutting_down = True" in shutdown


def test_duplicate_install_guard_precedes_task_replacement():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    install = source.split("async def async_install_latest", 1)[1].split(
        "def _is_gatt_congested", 1
    )[0]
    guard = install.index("if address in self._installing_addresses")
    track = install.index("self._install_tasks[address] = install_task")
    assert guard < track


def test_install_task_is_removed_in_finally():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert "if self._install_tasks.get(address) is install_task:" in source
    assert "self._install_tasks.pop(address, None)" in source


def test_firmware_diagnostics_expose_manager_instance():
    source = (ROOT / "update.py").read_text(encoding="utf-8")

    assert '"manager_instance": self.manager.instance_id' in source
