from pathlib import Path


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def _manager_source() -> str:
    return (ROOT / "manager.py").read_text(encoding="utf-8")


def test_metadata_refresh_waits_for_gatt_lock_with_timeout():
    source = _manager_source()
    block = source.split("async def async_refresh_device", 1)[1].split(
        "@staticmethod\n    def _versions_match", 1
    )[0]

    assert "METADATA_GATT_LOCK_TIMEOUT_SECONDS" in block
    assert "await asyncio.wait_for(" in block
    assert "self._gatt_lock.acquire()" in block
    assert "Timed out waiting for the shared ATC OTA GATT lock" in block


def test_metadata_characteristic_reads_are_bounded():
    source = _manager_source()

    read_text = source.split("async def _read_text", 1)[1].split(
        "async def _read_battery", 1
    )[0]
    read_battery = source.split("async def _read_battery", 1)[1].split(
        "async def async_refresh_device", 1
    )[0]

    assert "METADATA_GATT_READ_TIMEOUT_SECONDS" in read_text
    assert "asyncio.wait_for(" in read_text
    assert "METADATA_GATT_READ_TIMEOUT_SECONDS" in read_battery
    assert "asyncio.wait_for(" in read_battery


def test_refresh_requires_a_real_firmware_revision():
    source = _manager_source()
    block = source.split("async def async_refresh_device", 1)[1].split(
        "@staticmethod\n    def _versions_match", 1
    )[0]

    assert "if current is None:" in block
    assert "cached version" in block
    assert '"was not "' in block
    assert '"accepted as refreshed metadata"' in block
    assert "state.current_version = current" in block


def test_manual_refresh_is_identifiable_in_diagnostics():
    source = _manager_source()
    update = (ROOT / "update.py").read_text(encoding="utf-8")
    button = (ROOT / "button.py").read_text(encoding="utf-8")

    assert 'origin="manual"' in source
    assert "metadata_refresh_request_count" in update
    assert "metadata_refresh_phase" in update
    assert "metadata_refresh_manager_instance" in update
    assert '"refresh_status": state.metadata_refresh_status' in button
    assert '"request_count": state.metadata_refresh_request_count' in button
