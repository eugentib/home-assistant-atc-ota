"""ESPHome Bluetooth Proxy bridge."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import habluetooth
from bleak_esphome import APIConnectionManager

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ProxyState:
    address: str
    connected: bool = False
    status: str = "starting"
    error: str | None = None


class BluetoothProxyBridge:
    """Own the bleak-esphome connection manager used by the app."""

    def __init__(self, address: str, noise_psk: str | None) -> None:
        self.state = ProxyState(address=address)
        self._manager = APIConnectionManager(
            {
                "address": address,
                "noise_psk": noise_psk or None,
            }
        )
        self._start_task: asyncio.Task[None] | None = None
        self._bt_manager: Any | None = None

    async def start(self) -> None:
        """Initialize habluetooth and start the ESPHome connection in background."""
        self._bt_manager = habluetooth.BluetoothManager()
        await self._bt_manager.async_setup()
        self.state.status = "connecting"
        self._start_task = asyncio.create_task(self._run_connection())

    async def _run_connection(self) -> None:
        try:
            await self._manager.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface upstream connection failures
            self.state.connected = False
            self.state.status = "error"
            self.state.error = f"{type(exc).__name__}: {exc}"
            _LOGGER.exception("ESPHome proxy connection failed")
        else:
            self.state.connected = True
            self.state.status = "connected"
            self.state.error = None
            _LOGGER.info("Connected to ESPHome Bluetooth Proxy %s", self.state.address)

    async def stop(self) -> None:
        """Stop the ESPHome connection manager."""
        try:
            await self._manager.stop()
        finally:
            if self._start_task and not self._start_task.done():
                self._start_task.cancel()
                try:
                    await self._start_task
                except asyncio.CancelledError:
                    pass

    async def scan(self, timeout: float) -> list[dict[str, Any]]:
        """Return BLE devices heard by the configured proxy."""
        if not self.state.connected:
            raise RuntimeError(
                "ESPHome proxy is not connected yet. Check proxy_address, API encryption key, and app logs."
            )

        import bleak

        discovered = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
        result: list[dict[str, Any]] = []
        for device, adv in discovered.values():
            name = device.name or adv.local_name or ""
            address = device.address
            rssi = getattr(adv, "rssi", None)
            candidate = _looks_like_atc(name)
            result.append(
                {
                    "address": address,
                    "name": name or "(unnamed)",
                    "rssi": rssi,
                    "candidate": candidate,
                }
            )

        result.sort(
            key=lambda item: (
                not item["candidate"],
                -(item["rssi"] if isinstance(item["rssi"], int) else -999),
                item["name"],
            )
        )
        return result


def _looks_like_atc(name: str) -> bool:
    value = name.upper()
    prefixes = (
        "ATC_",
        "LYWSD",
        "MJWSD",
        "MHO",
        "CGG",
        "CGDK",
        "QINGPING",
    )
    return value.startswith(prefixes)
