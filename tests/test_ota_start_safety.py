from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_battery_is_revalidated_before_flash_and_before_start_commands():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert 'context="final preflight"' in source
    assert 'context="OTA start"' in source
    assert "def _assert_battery_safe_for_ota" in source
    assert "state.battery < self.low_battery_threshold" in source


def test_ota_start_reconnect_is_bounded_and_before_firmware_blocks():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    flash = source.split("async def _flash_locked", 1)[1]
    assert "OTA_START_RECONNECT_ATTEMPTS" in flash
    assert "OTA start link lost; reconnecting" in flash
    assert "for block_number in range(block_count)" in flash
    assert flash.index("OTA start link lost; reconnecting") < flash.index(
        "for block_number in range(block_count)"
    )


def test_link_loss_detection_covers_observed_failure():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")

    assert '"not connected"' in source
    assert '"timeout waiting for connect"' in source
