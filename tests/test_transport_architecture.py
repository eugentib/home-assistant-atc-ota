"""Architecture regressions for the first ATC OTA manager split.

These tests intentionally verify dependency boundaries and stable HA contracts
without requiring a running Home Assistant / BLE proxy in CI.
"""

from pathlib import Path

ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def test_transport_extracted_and_manager_owns_orchestration():
    manager = (ROOT / "manager.py").read_text(encoding="utf-8")
    transport = (ROOT / "ota_transport.py").read_text(encoding="utf-8")

    assert "class AtcManager(OtaTransportMixin):" in manager
    assert "from .ota_transport import OtaTransportMixin" in manager
    assert "async def async_install_latest(" in manager
    assert "async def _verify_firmware_after_ota(" in manager
    assert "async def _flash_locked(" not in manager

    assert "class OtaTransportMixin:" in transport
    for name in ("_flash", "_flash_locked", "_ota_write", "_ota_read_sync", "_is_link_lost"):
        assert f"def {name}(" in transport
    assert "async def async_install_latest(" not in transport


def test_ota_protocol_behavior_remains_in_transport():
    transport = (ROOT / "ota_transport.py").read_text(encoding="utf-8")
    assert 'b"\\x00\\xFF"' in transport
    assert 'b"\\x01\\xFF"' in transport
    assert "make_block(block_number" in transport
    assert "make_finish(block_count)" in transport
    assert "OTA_START_RECONNECT_ATTEMPTS" in transport
    assert "OTA_WRITE_RETRY_ATTEMPTS" in transport
    assert '"OTA start"' in transport


def test_last_success_rehydrates_terminal_progress_only():
    manager = (ROOT / "manager.py").read_text(encoding="utf-8")
    loaded = manager.split("for address, raw in stored.get", 1)[1].split(
        "self._refresh_home_assistant_names()", 1
    )[0]
    persisted = manager.split("def persistent(self)", 1)[1].split("@classmethod", 1)[0]
    assert 'if state.last_ota_result == "success":' in loaded
    assert "state.ota_progress = 100" in loaded
    assert '"ota_progress"' not in persisted
    assert '"last_ota_result"' in persisted
