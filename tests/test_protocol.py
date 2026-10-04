from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atc_ota"))

from app.protocol import (  # noqa: E402
    BLOCK_SIZE,
    crc16_modbus,
    make_block,
    make_finish,
    pad_firmware,
    validate_firmware,
)


def test_crc16_modbus_known_vector() -> None:
    assert crc16_modbus(b"123456789") == 0x4B37


def test_validate_magic() -> None:
    fw = b"\x00" * 8 + b"KNLT" + b"\x00" * 32
    validate_firmware(fw)


def test_padding() -> None:
    fw = b"x" * 17
    padded = pad_firmware(fw)
    assert len(padded) == 32
    assert padded[:17] == fw
    assert padded[17:] == b"\xff" * 15


def test_block_shape_and_crc() -> None:
    payload = bytes(range(BLOCK_SIZE))
    packet = make_block(0x1234, payload)
    assert len(packet) == 20
    assert packet[:2] == b"\x34\x12"
    expected_crc = crc16_modbus(packet[:18]).to_bytes(2, "little")
    assert packet[18:] == expected_crc


def test_finish_packet() -> None:
    assert make_finish(1) == b"\x02\xff\x00\x00\xff\xff"
    assert make_finish(2) == b"\x02\xff\x01\x00\xfe\xff"


def test_ble_candidate_detection_by_service_data_uuid():
    from atc_ota.app.discovery import classify_atc

    assert classify_atc("", service_data_uuids=["0000181a-0000-1000-8000-00805f9b34fb"])[0]
    assert classify_atc("", service_data_uuids=["0000fe95-0000-1000-8000-00805f9b34fb"])[0]
    assert classify_atc("", service_data_uuids=["0000fcd2-0000-1000-8000-00805f9b34fb"])[0]


def test_ble_candidate_detection_accepts_short_uuid():
    from atc_ota.app.discovery import classify_atc

    candidate, reason = classify_atc("", service_uuids=["181A"])
    assert candidate
    assert "181A" in reason
