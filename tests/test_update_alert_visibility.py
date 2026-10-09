from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_update_entities_are_hidden_but_not_disabled_by_default():
    source = (ROOT / "update.py").read_text(encoding="utf-8")
    update_entity = source.split("class AtcFirmwareUpdate", 1)[1]

    assert "_attr_entity_registry_visible_default = False" in update_entity
    assert "_attr_entity_registry_enabled_default = False" not in update_entity
    assert "UpdateEntityFeature.INSTALL" in update_entity
    assert "UpdateEntityFeature.PROGRESS" in update_entity


def test_existing_firmware_entities_are_hidden_only_once():
    source = (ROOT / "update.py").read_text(encoding="utf-8")
    migration = source.split("async def async_setup_entry", 1)[1].split(
        "class AtcFirmwareUpdate", 1
    )[0]

    assert "if not manager.firmware_updates_hidden_migrated:" in migration
    assert 'registry.async_get_entity_id(' in migration
    assert '"update", "atc_ota", f"{address}_firmware"' in migration
    assert "registered.hidden_by is None" in migration
    assert "hidden_by=er.RegistryEntryHider.INTEGRATION" in migration
    assert "manager.firmware_updates_hidden_migrated = True" in migration
    assert "manager._schedule_save()" in migration


def test_visibility_migration_state_persists_across_restarts():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert 'stored.get("firmware_updates_hidden_migrated", False)' in source
    assert (
        '"firmware_updates_hidden_migrated": self.firmware_updates_hidden_migrated'
        in source
    )


def test_native_update_progress_remains_enabled_and_live():
    source = (ROOT / "update.py").read_text(encoding="utf-8")

    assert "UpdateEntityFeature.PROGRESS" in source
    assert "return bool(self.state_data.ota_in_progress)" in source
    assert (
        "return self.state_data.ota_progress if self.state_data.ota_in_progress else None"
        in source
    )
    assert "self.async_write_ha_state()" in source
