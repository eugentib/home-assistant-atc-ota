from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"
spec = importlib.util.spec_from_file_location("atc_bthome_test", ROOT / "bthome.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_battery_packet():
    values = mod.parse_bthome_v2(bytes.fromhex("400001012A"))
    assert values.version == 2
    assert values.battery == 42


def test_encrypted_not_parsed():
    values = mod.parse_bthome_v2(bytes.fromhex("410161"))
    assert values.encrypted
    assert values.battery is None


def test_firmware_v2_object():
    values = mod.parse_bthome_v2(bytes.fromhex("40F2000106"))
    assert values.firmware_version == "6.1.0"


def test_device_type_and_battery():
    values = mod.parse_bthome_v2(bytes.fromhex("40F001000161"))
    assert values.device_type_id == 1
    assert values.battery == 97
