from pathlib import Path

from bthome_ble import BTHomeBluetoothDeviceData
from habluetooth import BluetoothServiceInfoBleak


ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"


def _service_info(payload: bytes) -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name="ATC_515D77",
        address="A4:C1:38:51:5D:77",
        rssi=-66,
        manufacturer_data={},
        service_data={"0000fcd2-0000-1000-8000-00805f9b34fb": payload},
        service_uuids=[],
        source="btproxy2",
        device=None,
        advertisement=None,
        connectable=False,
        time=100.0,
        tx_power=None,
    )


def test_home_assistant_bthome_library_parses_pvvx_battery_29():
    # BTHome v2 info=0x40, packet-id object 0x00=1, battery object 0x01=29%.
    parser = BTHomeBluetoothDeviceData()
    update = parser.update(_service_info(b"\x40\x00\x01\x01\x1d"))

    batteries = [
        value.native_value
        for key, value in update.entity_values.items()
        if key.key == "battery"
    ]
    assert batteries == [29]


def test_manager_uses_bthome_library_before_fallback():
    source = (ROOT / "manager.py").read_text(encoding="utf-8")
    assert "BTHomeBluetoothDeviceData" in source
    assert "_parse_bthome_library(address, info)" in source
    primary = source.index("_parse_bthome_library(address, info)")
    fallback = source.index("parse_bthome_v2(bthome)", primary)
    assert primary < fallback


def test_cached_battery_is_not_exposed_as_live_after_restart():
    sensor = (ROOT / "sensor.py").read_text(encoding="utf-8")
    binary = (ROOT / "binary_sensor.py").read_text(encoding="utf-8")

    assert "self.manager.battery_is_fresh(self.state_data)" in sensor
    assert "self.manager.battery_is_fresh(self.state_data)" in binary
