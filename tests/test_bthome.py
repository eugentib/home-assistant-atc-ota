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


def test_power_and_opening_are_not_misread_as_battery():
    # 0x10 0x01 = power on, 0x11 0x01 = opening open.
    # v0.2.6's raw fallback incorrectly matched the middle bytes 0x01 0x11
    # and reported 0x11 == 17% battery.
    values = mod.parse_bthome_v2(bytes.fromhex("4010011101"))
    assert values.battery is None


def test_packet_id_power_and_opening_are_not_misread_as_battery():
    values = mod.parse_bthome_v2(bytes.fromhex("40002A10011101"))
    assert values.battery is None


def test_real_battery_survives_following_binary_objects():
    values = mod.parse_bthome_v2(bytes.fromhex("40002A014F10011101"))
    assert values.battery == 79
