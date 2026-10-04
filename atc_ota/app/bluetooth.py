"""ESPHome Bluetooth Proxy bridge."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import habluetooth
from bleak_esphome import APIConnectionManager
from habluetooth import HaBleakClientWrapper, HaBleakScannerWrapper, set_manager

from .discovery import classify_atc, parse_bthome_battery

_LOGGER = logging.getLogger(__name__)


def _norm_mac(value: str | None) -> str:
    if not value:
        return ""
    chars = "".join(ch for ch in str(value).upper() if ch in "0123456789ABCDEF")
    if len(chars) == 12:
        return ":".join(chars[i : i + 2] for i in range(0, 12, 2))
    return str(value).upper()


@dataclass(slots=True)
class ProxyRuntimeState:
    address: str
    name: str = ""
    entry_id: str = ""
    bluetooth_mac: str = ""
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
                bluetooth_mac=_norm_mac(str(cfg.get("bluetooth_mac") or "")),
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

    def _proxy_name_for_source(self, source: str) -> str:
        """Map habluetooth scanner source (BLE MAC) to our ESPHome friendly name."""
        source = _norm_mac(source)
        if not source:
            return ""
        for state in self._states:
            if state.bluetooth_mac and _norm_mac(state.bluetooth_mac) == source:
                return state.name
        return source

    @staticmethod
    def _device_source(device: Any) -> str:
        """Extract the scanner source selected by habluetooth for a BLEDevice."""
        details = getattr(device, "details", None)
        if isinstance(details, dict):
            source = details.get("source")
            if source:
                return _norm_mac(str(source))
        # Be defensive against wrapper/backend changes that expose details as an object.
        source = getattr(details, "source", None)
        return _norm_mac(str(source)) if source else ""

    def _scanner_observations(self) -> dict[str, list[dict[str, Any]]]:
        """Return per-device RSSI observations from every registered remote scanner.

        The normal Bleak view is arbitrated by habluetooth and exposes one best
        advertisement per BLE address.  The registered scanner objects retain
        their own discovered-device maps, so we can show which ESPHome proxies
        actually heard the device and at what RSSI.
        """
        observations: dict[str, list[dict[str, Any]]] = {}
        if self._bt_manager is None:
            return observations
        try:
            scanners = self._bt_manager.async_current_scanners()
        except Exception:  # noqa: BLE001 - diagnostics are best effort
            _LOGGER.debug("Unable to enumerate habluetooth scanners", exc_info=True)
            return observations

        for scanner in scanners or []:
            source = _norm_mac(str(getattr(scanner, "source", "") or ""))
            scanner_name = self._proxy_name_for_source(source)
            try:
                discovered = getattr(scanner, "discovered_devices_and_advertisement_data", {})
                if callable(discovered):
                    discovered = discovered()
                values = discovered.values() if isinstance(discovered, dict) else discovered
                for pair in values or []:
                    try:
                        device, adv = pair
                    except (TypeError, ValueError):
                        continue
                    address = _norm_mac(str(getattr(device, "address", "") or ""))
                    if not address:
                        continue
                    rssi = getattr(adv, "rssi", None)
                    observations.setdefault(address, []).append(
                        {
                            "source": source,
                            "proxy": scanner_name or source,
                            "rssi": rssi,
                        }
                    )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Unable to inspect scanner %s", scanner_name or source, exc_info=True)

        for rows in observations.values():
            rows.sort(key=lambda row: row.get("rssi") if isinstance(row.get("rssi"), int) else -999, reverse=True)
        return observations

    async def scan(self, timeout: float) -> list[dict[str, Any]]:
        """Return BLE devices heard by connected proxies, including proxy provenance."""
        self._refresh_state()
        if not self.state.connected:
            raise RuntimeError(
                "No ESPHome Bluetooth Proxy is connected. Check automatic discovery/manual fallback and app logs."
            )

        import bleak

        # Keep every changed advertisement during the scan window. pvvx BTHome
        # v2 intentionally sends some measurements (including battery) in
        # separate packets, so looking only at the final advertisement can miss
        # battery even though it was received a moment earlier.
        advertised_battery: dict[str, dict[str, Any]] = {}

        def on_advertisement(device: Any, adv: Any) -> None:
            try:
                service_data = {
                    str(k).lower(): bytes(v)
                    for k, v in (getattr(adv, "service_data", None) or {}).items()
                }
                battery = parse_bthome_battery(service_data)
                if battery is None:
                    return
                address = _norm_mac(str(getattr(device, "address", "") or ""))
                if not address:
                    return
                advertised_battery[address] = {
                    "battery_percent": battery,
                    "battery_source": "BTHome v2 advertisement",
                    "battery_advertised_at": time.time(),
                }
            except Exception:  # noqa: BLE001 - malformed advertisements are ignored
                _LOGGER.debug("Unable to parse BTHome advertisement", exc_info=True)

        discovered = await bleak.BleakScanner.discover(
            timeout=timeout,
            return_adv=True,
            detection_callback=on_advertisement,
        )
        per_source = self._scanner_observations()
        result: list[dict[str, Any]] = []
        for device, adv in discovered.values():
            name = device.name or adv.local_name or ""
            address = device.address
            norm_address = _norm_mac(address)
            rssi = getattr(adv, "rssi", None)
            service_uuids = [str(v).lower() for v in (getattr(adv, "service_uuids", None) or [])]
            service_data = {
                str(k).lower(): bytes(v)
                for k, v in (getattr(adv, "service_data", None) or {}).items()
            }
            candidate, reason = classify_atc(
                name, service_uuids, service_data.keys(), address=address
            )
            # Defensive fallback for backends that only surface the final packet
            # to the detection callback.
            battery_fields = advertised_battery.get(norm_address)
            if battery_fields is None:
                battery = parse_bthome_battery(service_data)
                if battery is not None:
                    battery_fields = {
                        "battery_percent": battery,
                        "battery_source": "BTHome v2 advertisement",
                        "battery_advertised_at": time.time(),
                    }

            # The BLEDevice returned by habluetooth represents its currently
            # selected/arbitrated source.  That source is intentionally sticky
            # and is not guaranteed to be the scanner with the strongest *latest*
            # RSSI sample.  For the inventory table we want the strongest actual
            # observation from this scan, so derive RSSI/Best proxy from the
            # per-scanner observations instead.  Keep the arbitrated route as
            # separate diagnostics for connection troubleshooting.
            selected_source = self._device_source(device)
            seen_by = per_source.get(norm_address, [])
            strongest = seen_by[0] if seen_by else None

            if strongest is not None:
                strongest_source = str(strongest.get("source") or "")
                strongest_proxy = str(strongest.get("proxy") or "")
                strongest_rssi = strongest.get("rssi")
            else:
                strongest_source = selected_source
                strongest_proxy = self._proxy_name_for_source(selected_source)
                strongest_rssi = rssi

            # If scanner introspection did not provide a numeric RSSI, fall back
            # to the normal Bleak advertisement value.
            display_rssi = strongest_rssi if isinstance(strongest_rssi, int) else rssi
            best_proxy = strongest_proxy or self._proxy_name_for_source(strongest_source)
            route_proxy = self._proxy_name_for_source(selected_source) if selected_source else ""

            result.append(
                {
                    "address": address,
                    "name": name or "(unnamed)",
                    "rssi": display_rssi,
                    "candidate": candidate,
                    # This describes the advertisement format/signature, not the proxy.
                    "candidate_reason": reason,
                    "service_uuids": service_uuids,
                    "service_data_uuids": list(service_data.keys()),
                    # source/best_proxy now mean the strongest observation so
                    # persisted inventory and HA entities agree with the table.
                    "source": strongest_source,
                    "best_proxy": best_proxy,
                    "seen_by": seen_by,
                    # habluetooth's current arbitrated source can legitimately
                    # differ from the strongest instantaneous RSSI.
                    "route_source": selected_source,
                    "route_proxy": route_proxy,
                    "route_rssi": rssi,
                    **(battery_fields or {}),
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
