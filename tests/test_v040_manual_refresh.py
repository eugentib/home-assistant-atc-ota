"""Behavioral v0.4 tests for bounded manual refresh during OTA."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).parents[1] / "custom_components/atc_ota/manager.py"


def _user_refresh_method():
    tree = ast.parse(PATH.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AtcManager")
    method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == "async_user_refresh_device")
    ns = {"HomeAssistantError": RuntimeError}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(PATH), "exec"), ns)
    return ns["async_user_refresh_device"]


class FakeManager:
    def __init__(self):
        self._ota_lock = asyncio.Lock()
        self._installing_addresses = set()
        self._pending_install_addresses = set()
        self._last_metadata_attempt = {}
        self.called = []

    async def async_refresh_device(self, address, *, origin):
        self.called.append((address, origin))


def test_manual_refresh_fails_fast_while_an_ota_has_the_slot():
    async def run():
        mgr = FakeManager()
        await mgr._ota_lock.acquire()
        with pytest.raises(RuntimeError, match="OTA is running"):
            await _user_refresh_method()(mgr, "a4:c1:38:67:aa:41")
        assert mgr.called == []
        mgr._ota_lock.release()

        mgr._pending_install_addresses.add("A4:C1:38:67:AA:41")
        with pytest.raises(RuntimeError, match="OTA is running"):
            await _user_refresh_method()(mgr, "a4:c1:38:67:aa:41")
        assert mgr.called == []

        mgr._pending_install_addresses.clear()
        await _user_refresh_method()(mgr, "a4:c1:38:67:aa:41")
        assert mgr.called == [("A4:C1:38:67:AA:41", "manual")]

    asyncio.run(run())
