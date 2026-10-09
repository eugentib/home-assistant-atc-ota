from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_same_version_reinstall_is_not_rejected_before_ota():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")
    start = source.split("def start_install_latest(", 1)[1].split(
        "async def async_install_latest(", 1
    )[0]
    assert "if installed >= latest:" not in start
    assert "if installed > latest and not self._versions_match(" in start
    assert "self.async_install_latest(address)" in start
    assert "self._pending_install_addresses.add(address)" in start


def test_same_version_reinstall_requires_fresh_gatt_verification():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")
    verify = source.split("async def _verify_firmware_after_ota(", 1)[1].split(
        "def start_install_latest(", 1
    )[0]
    assert (
        "same_version_reinstall = self._versions_match(previous_version, target_version)"
        in verify
    )
    assert "not same_version_reinstall" in verify
    assert "await self.async_refresh_device(address)" in verify
    assert "Verified firmware {target_version} over GATT" in verify
    assert "state.current_version = target_version" not in verify


def test_preflight_battery_guards_remain_in_effect():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")
    install = source.split("async def async_install_latest(", 1)[1]
    assert "self.battery_is_fresh(state)" in install
    assert "state.battery < self.low_battery_threshold" in install
    assert "self._assert_battery_safe_for_ota(" in install
    assert "await self._flash(address, payload.data, choice.version)" in install
    assert "await self._verify_firmware_after_ota(" in install
