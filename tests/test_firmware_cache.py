from pathlib import Path
import importlib.util


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"
spec = importlib.util.spec_from_file_location("atc_firmware_cache_test", ROOT / "firmware_cache.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _validator(data: bytes) -> None:
    if not data.startswith(b"VALID"):
        raise ValueError("invalid firmware")


def test_cache_filename_is_stable_and_safe():
    name = mod.firmware_cache_filename("5.9", "bin/ATC_v59.bin")
    assert name == "5.9__bin__ATC_v59.bin"
    assert "/" not in name
    assert "\\" not in name


def test_cache_round_trip(tmp_path):
    path = tmp_path / "firmware.bin"
    data = b"VALID" + bytes(range(32))
    mod.write_cached_firmware(path, data)

    loaded = mod.read_cached_firmware(path, max_size=1024, validator=_validator)
    assert loaded == data
    assert mod.sha256_hex(loaded) == mod.sha256_hex(data)
    assert not (tmp_path / ".firmware.bin.tmp").exists()


def test_invalid_cache_is_removed(tmp_path):
    path = tmp_path / "firmware.bin"
    path.write_bytes(b"BROKEN")

    loaded = mod.read_cached_firmware(path, max_size=1024, validator=_validator)
    assert loaded is None
    assert not path.exists()


def test_oversized_cache_is_removed(tmp_path):
    path = tmp_path / "firmware.bin"
    path.write_bytes(b"VALID" + b"x" * 100)

    loaded = mod.read_cached_firmware(path, max_size=10, validator=_validator)
    assert loaded is None
    assert not path.exists()
