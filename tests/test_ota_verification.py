from pathlib import Path


ROOT = Path(__file__).parents[1]
MANAGER = ROOT / "custom_components" / "atc_ota" / "manager.py"


def test_ota_does_not_assume_target_version():
    source = MANAGER.read_text(encoding="utf-8")

    assert "state.current_version = target_version" not in source
    assert "await self._verify_firmware_after_ota(" in source


def test_explicit_bthome_firmware_version_can_refresh_current_version():
    source = MANAGER.read_text(encoding="utf-8")

    assert "if firmware:" in source
    assert "state.current_version = firmware" in source
    assert "if parsed.firmware_version and not state.current_version:" not in source


def test_ota_verification_checks_advertisement_and_gatt():
    source = MANAGER.read_text(encoding="utf-8")

    assert "Verifying firmware" in source
    assert "via advertisement" in source
    assert "over GATT" in source
    assert "firmware verification failed" in source
