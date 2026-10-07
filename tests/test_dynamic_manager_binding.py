from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_entities_resolve_manager_from_current_runtime_data():
    source = (ROOT / "entity.py").read_text(encoding="utf-8")

    assert "self._entry = manager.entry" in source
    assert "self._created_manager_instance = manager.instance_id" in source
    assert "return self._entry.runtime_data" in source
    assert "self.manager = manager" not in source


def test_dispatcher_subscription_uses_stable_entry_id():
    source = (ROOT / "entity.py").read_text(encoding="utf-8")

    assert "signal_device_updated(self._entry.entry_id, self.address)" in source


def test_button_exposes_created_and_current_manager_instances():
    source = (ROOT / "button.py").read_text(encoding="utf-8")

    assert '"manager_instance": self.manager.instance_id' in source
    assert '"entity_created_manager_instance": self.created_manager_instance' in source


def test_firmware_exposes_created_and_current_manager_instances():
    source = (ROOT / "update.py").read_text(encoding="utf-8")

    assert '"manager_instance": self.manager.instance_id' in source
    assert '"entity_created_manager_instance": self.created_manager_instance' in source
