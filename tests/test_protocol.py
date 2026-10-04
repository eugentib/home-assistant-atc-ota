from pathlib import Path
import importlib.util

ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

protocol = load("protocol")


def test_crc_known():
    assert protocol.crc16_modbus(b"123456789") == 0x4B37


def test_block_shape():
    packet = protocol.make_block(1, bytes(range(16)))
    assert len(packet) == 20
    assert packet[:2] == b"\x01\x00"


def test_finish():
    assert protocol.make_finish(2) == b"\x02\xff\x01\x00\xfe\xff"


def test_validate_knlt():
    protocol.validate_firmware(b"\x00" * 8 + b"KNLT" + b"\x00" * 20)
