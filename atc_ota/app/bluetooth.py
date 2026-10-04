"""ESPHome Bluetooth Proxy bridge."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import habluetooth
from bleak_esphome import APIConnectionManager
from habluetooth import HaBleakClientWrapper, HaBleakScannerWrapper, set_manager

from .discovery import classify_atc

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ProxyRuntimeState:
    address: str
    name: str = ""
    entry_id: str = ""
    connected: bool = False
    status: str = "starting"
    error: str | None = None


@dataclass(slots=True)
class ProxyState:
    address: str = ""
    connected: bool = False
    status: str = "starting"
    error: str | None = None
    mode: str = "manual"
    proxies: list[dict[str, Any]] = field(default_factory=list)


class BluetoothProxyBridge:
    """Own one or more bleak-esphome connection managers used by the app."""

    def __init__(self, proxies: list[dict[str, Any]], *, mode: str = "manual") -> None:
        if not proxies:
            raise ValueError("At least one ESPHome Bluetooth Proxy is required")
        self._configs = list(proxies)
        self.state = ProxyState(mode=mode)
        self._bt_manager: Any | None = None
        self._managers: list[APIConnectionManager] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._states: list[ProxyRuntimeState] = []

    async def start(self) -> None:
        """Initialize habluetooth and start every configured ESPHome proxy."""
        # Route normal Bleak calls (scanner + client) through habluetooth.
        # APIConnectionManager registers each ESPHome scanner in this global manager.
        import bleak

        bleak.BleakClient = HaBleakClientWrapper  # type: ignore[assignment]
        bleak.BleakScanner = HaBleakScannerWrapper  # type: ignore[assignment]
        self._bt_manager = habluetooth.BluetoothManager()
        set_manager(self._bt_manager)
        await self._bt_manager.async_setup()
        self._states = []
        self._managers = []
        self._tasks = []

        for cfg in self._configs:
            address = str(cfg.get("address") or "").strip()
            if not address:
                continue
            state = ProxyRuntimeState(
                address=address,
                name=str(cfg.get("name") or address),
                entry_id=str(cfg.get("entry_id") or ""),
            )
            manager = APIConnectionManager(
                {
                    "address": address,
                    "noise_psk": cfg.get("noise_psk") or None,
                }
            )
            self._states.append(state)
            self._managers.append(manager)
            self._tasks.append(asyncio.create_task(self._run_connection(manager, state)))

        if not self._tasks:
            raise RuntimeError("No valid ESPHome Bluetooth Proxy addresses were configured")

        # Give fast local proxies a moment to become available without blocking startup for long.
        await asyncio.sleep(0.25)
        self._refresh_state()

    async def _run_connection(self, manager: APIConnectionManager, state: ProxyRuntimeState) -> None:
        state.status = "connecting"
        self._refresh_state()
        try:
            await manager.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state.connected = False
            state.status = "error"
            state.error = f"{type(exc).__name__}: {exc}"
            _LOGGER.exception("ESPHome proxy connection failed for %s", state.address)
        else:
            state.connected = True
            state.status = "connected"
            state.error = None
            _LOGGER.info("Connected to ESPHome Bluetooth Proxy %s", state.address)
        finally:
            self._refresh_state()

    def _refresh_state(self) -> None:
        connected = [item for item in self._states if item.connected]
        errors = [item for item in self._states if item.status == "error"]
        self.state.connected = bool(connected)
        if connected:
            self.state.status = "connected"
        elif errors and len(errors) == len(self._states):
            self.state.status = "error"
        else:
            self.state.status = "connecting"
        self.state.address = ", ".join(item.address for item in connected) or ", ".join(
            item.address for item in self._states
        )
        self.state.error = "; ".join(
            f"{item.name}: {item.error}" for item in errors if item.error
        ) or None
        self.state.proxies = [asdict(item) for item in self._states]

    async def stop(self) -> None:
        """Stop every ESPHome connection manager."""
        for manager in self._managers:
            try:
                await manager.stop()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Error stopping ESPHome proxy manager", exc_info=True)
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._managers = []
        self._states = []
        if self._bt_manager is not None:
            try:
                await self._bt_manager.async_stop()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Error stopping habluetooth manager", exc_info=True)
            self._bt_manager = None
        self._refresh_state()

    @property
    def connected_count(self) -> int:
        return sum(1 for item in self._states if item.connected)

    @property
    def label(self) -> str:
        names = [item.name for item in self._states if item.connected]
        return ", ".join(names) if names else self.state.address

    async def scan(self, timeout: float) -> list[dict[str, Any]]:
        """Return BLE devices heard by any connected configured proxy."""
        self._refresh_state()
        if not self.state.connected:
            raise RuntimeError(
                "No ESPHome Bluetooth Proxy is connected. Check automatic discovery/manual fallback and app logs."
            )

        import bleak

        discovered = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
        result: list[dict[str, Any]] = []
        for device, adv in discovered.values():
            name = device.name or adv.local_name or ""
            address = device.address
            rssi = getattr(adv, "rssi", None)
            service_uuids = [str(v).lower() for v in (getattr(adv, "service_uuids", None) or [])]
            service_data = {
                str(k).lower(): bytes(v)
                for k, v in (getattr(adv, "service_data", None) or {}).items()
            }
            candidate, reason = classify_atc(name, service_uuids, service_data.keys())
            result.append(
                {
                    "address": address,
                    "name": name or "(unnamed)",
                    "rssi": rssi,
                    "candidate": candidate,
                    "candidate_reason": reason,
                    "service_uuids": service_uuids,
                    "service_data_uuids": list(service_data.keys()),
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
