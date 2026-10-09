"""Exercise extracted OTA transport against an in-memory fake BLE connection.

The GitHub validation runner does not install Home Assistant. Load the Python
class AST with explicit fake dependencies to test real coroutine behavior
without radio hardware or the HA runtime.
"""

import ast
import asyncio
import logging
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "custom_components" / "atc_ota"
OTA_MODULE = ROOT / "ota_transport.py"


def _load_transport_mixin():
    text = OTA_MODULE.read_text(encoding="utf-8")
    nodes = ast.parse(text).body
    cls = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == "OtaTransportMixin")
    namespace = {
        "asyncio": asyncio,
        "_LOGGER": logging.getLogger(__name__),
        "HomeAssistantError": RuntimeError,
        "DeviceState": object,
        "OTA_WRITE_RETRY_ATTEMPTS": 3,
        "OTA_WRITE_RETRY_BASE_SECONDS": 0.001,
        "OTA_START_RECONNECT_ATTEMPTS": 2,
        "OTA_START_COMMAND_GAP_SECONDS": 0.001,
        "OTA_BLOCK_PACING_SECONDS": 0.0,
        "OTA_CHAR_UUID": "ota-characteristic",
        "BLOCK_SIZE": 16,
        "validate_firmware": lambda data: None,
        "pad_firmware": lambda data: data,
        "make_block": lambda n, data: b"B" + n.to_bytes(2, "little") + data,
        "make_finish": lambda count: b"FINISH" + count.to_bytes(2, "little"),
    }
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls],
        type_ignores=[],
    )
    exec(compile(module, str(OTA_MODULE), "exec"), namespace)
    return namespace["OtaTransportMixin"]


def test_gatt_congestion_is_retried_but_other_errors_are_not():
    mixin = _load_transport_mixin()

    class Client:
        def __init__(self, error: str):
            self.attempts = 0
            self.error = error

        async def write_gatt_char(self, characteristic, payload, response):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError(self.error)

    class Transport(mixin):
        def _notify(self, address):
            pass

    class State:
        address = "A4:C1:38:00:00:01"
        ota_message = None

    async def exercise():
        transient = Client("ESP_GATT_CONGESTED error 143")
        await Transport()._ota_write(State(), transient, "char", b"test", label="test")
        assert transient.attempts == 2

        permanent = Client("device went offline")
        with pytest.raises(RuntimeError, match="device went offline"):
            await Transport()._ota_write(State(), permanent, "char", b"test", label="test")
        assert permanent.attempts == 1

    asyncio.run(exercise())


def test_flash_uses_start_commands_blocks_finish_then_disconnects():
    mixin = _load_transport_mixin()

    class Services:
        def get_characteristic(self, uuid):
            return uuid

    class Client:
        services = Services()

        def __init__(self):
            self.writes = []
            self.is_connected = True
            self.disconnects = 0

        async def write_gatt_char(self, characteristic, payload, response):
            assert response is False
            self.writes.append(payload)

        async def disconnect(self):
            self.disconnects += 1
            self.is_connected = False

    class State:
        name = "Test"
        address = "A4:C1:38:00:00:02"
        ota_progress = None
        ota_message = None
        battery = 100
        last_ota_proxy = None
        last_ota_rssi = None
        last_ota_detail = None
        last_metadata_error = None

    class Transport(mixin):
        def __init__(self, client):
            self.devices = {State.address: State()}
            self._gatt_lock = asyncio.Lock()
            self.client = client
            self.observed_progress = []

        def _notify(self, address):
            self.observed_progress.append(self.devices[address].ota_progress)

        async def _establish_client(self, address, name):
            return self.client

        def _actual_connected_route(self, client, address):
            return ("test-proxy", -60)

        def _assert_battery_safe_for_ota(self, state, context):
            assert state.battery >= 30

        def _update_ota_readiness(self, state):
            return None

    async def exercise():
        client = Client()
        transport = Transport(client)
        payload = bytes(range(32))
        await transport._flash(State.address, payload, "5.9")
        assert client.writes == [
            b"\\x00\\xff",
            b"\\x01\\xff",
            b"B" + b"\\x00\\x00" + payload[:16],
            b"B" + b"\\x01\\x00" + payload[16:],
            b"FINISH" + b"\\x02\\x00",
        ]
        assert client.disconnects == 1
        assert transport.devices[State.address].ota_progress == 99
        assert any(5 <= value <= 98 for value in transport.observed_progress if isinstance(value, int))

    asyncio.run(exercise())
