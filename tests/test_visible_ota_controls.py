from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_install_button_is_created_for_every_device():
    button = (ROOT / "button.py").read_text(encoding="utf-8")
    assert "AtcInstallFirmwareButton(manager, address)" in button
    assert 'self._attr_unique_id = f"{address}_install_firmware_update"' in button
    assert "self.manager.start_install_latest(self.address)" in button
    assert "await self.manager.async_install_latest(self.address)" not in button


def test_install_button_keeps_ota_nonblocking_and_explicit():
    manager = (ROOT / "manager.py").read_text(encoding="utf-8")
    start = manager.split("def start_install_latest(", 1)[1].split(
        "async def async_install_latest(", 1
    )[0]
    assert "self._pending_install_addresses.add(address)" in start
    assert "self._installing_addresses" in start
    assert "state.ota_in_progress" in start
    assert "if installed >= latest:" not in start
    assert "if installed > latest and not self._versions_match(" in start
    assert "self.entry.async_create_background_task(" in start
    assert "self.async_install_latest(address)" in start


def test_firmware_update_availability_is_a_visible_sensor():
    sensor = (ROOT / "sensor.py").read_text(encoding="utf-8")
    assert "AtcFirmwareAvailabilitySensor(manager, address)" in sensor
    block = sensor.split("class AtcFirmwareAvailabilitySensor", 1)[1].split(
        "class AtcRssiSensor", 1
    )[0]
    assert '"Update available" if installed < latest else "Up-to-date"' in block
    assert '"installed_version": state.current_version' in block
    assert '"latest_version": state.latest_version' in block
    assert "_attr_entity_category = EntityCategory.DIAGNOSTIC" not in block


def test_progress_and_status_are_promoted_from_diagnostics_without_id_changes():
    sensor = (ROOT / "sensor.py").read_text(encoding="utf-8")
    progress = sensor.split("class AtcOtaProgressSensor", 1)[1].split(
        "class AtcOtaStatusSensor", 1
    )[0]
    status = sensor.split("class AtcOtaStatusSensor", 1)[1].split(
        "class AtcOtaReadinessSensor", 1
    )[0]
    assert 'f"{address}_ota_progress"' in progress
    assert 'f"{address}_ota_status"' in status
    assert "_attr_entity_category = EntityCategory.DIAGNOSTIC" not in progress
    assert "_attr_entity_category = EntityCategory.DIAGNOSTIC" not in status
    assert "return self.state_data.ota_progress" in progress
    assert 'return "running"' in status


def test_settings_alerts_remain_hidden():
    update = (ROOT / "update.py").read_text(encoding="utf-8")
    assert "_attr_entity_registry_visible_default = False" in update
    assert "UpdateEntityFeature.INSTALL" in update
    assert "UpdateEntityFeature.PROGRESS" in update
