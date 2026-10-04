"""Automatic discovery of ESPHome Bluetooth Proxies configured in Home Assistant."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from aioesphomeapi import APIClient
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

_LOGGER = logging.getLogger(__name__)

HA_WS_URL = os.environ.get("ATC_OTA_HA_WS", "ws://supervisor/core/websocket")
ESPHOME_SERVICE = "_esphomelib._tcp.local."
ACTIVE_CONNECTIONS_FLAG = 1 << 1


def normalize_mac(value: str | None) -> str:
    if not value:
        return ""
    chars = "".join(ch for ch in value.lower() if ch in "0123456789abcdef")
    if len(chars) != 12:
        return value.lower()
    return ":".join(chars[i : i + 2] for i in range(0, 12, 2))


@dataclass(slots=True)
class MDNSNode:
    name: str
    host: str
    port: int
    mac: str
    friendly_name: str = ""
    api_encryption: bool = False


@dataclass(slots=True)
class DiscoveredProxy:
    entry_id: str
    name: str
    host: str
    port: int
    mac: str
    noise_psk: str | None
    esphome_version: str = ""
    model: str = ""
    bluetooth_mac: str = ""
    feature_flags: int = 0
    active_connections: bool = False
    usable: bool = False
    status: str = "discovered"
    error: str | None = None
    resolved_via: str = ""

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("noise_psk", None)
        return data

    def manager_config(self) -> dict[str, Any]:
        return {
            "address": self.host,
            "noise_psk": self.noise_psk or None,
            "name": self.name,
            "entry_id": self.entry_id,
        }


class HAWebSocket:
    """Tiny Home Assistant WebSocket client for one-shot discovery commands."""

    def __init__(self, token: str, url: str = HA_WS_URL) -> None:
        self.token = token
        self.url = url
        self._ws: Any = None
        self._next_id = 1

    async def __aenter__(self) -> "HAWebSocket":
        import websockets

        self._ws = await websockets.connect(self.url, open_timeout=10, close_timeout=5)
        hello = json.loads(await asyncio.wait_for(self._ws.recv(), 10))
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected Home Assistant WebSocket greeting: {hello.get('type')}")
        await self._ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        auth = json.loads(await asyncio.wait_for(self._ws.recv(), 10))
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant WebSocket authentication failed: {auth}")
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def command(self, command_type: str, **payload: Any) -> Any:
        if self._ws is None:
            raise RuntimeError("Home Assistant WebSocket is not connected")
        request_id = self._next_id
        self._next_id += 1
        await self._ws.send(json.dumps({"id": request_id, "type": command_type, **payload}))
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), 15)
            msg = json.loads(raw)
            if msg.get("id") != request_id:
                continue
            if msg.get("type") != "result":
                continue
            if not msg.get("success"):
                error = msg.get("error") or {}
                raise RuntimeError(
                    f"Home Assistant command {command_type} failed: "
                    f"{error.get('code', 'unknown')}: {error.get('message', error)}"
                )
            return msg.get("result")


async def discover_esphome_mdns(timeout: float = 3.0) -> list[MDNSNode]:
    """Discover ESPHome Native API nodes by mDNS."""
    azc = AsyncZeroconf()
    tasks: set[asyncio.Task[None]] = set()
    found: dict[str, MDNSNode] = {}

    async def resolve(name: str) -> None:
        info = AsyncServiceInfo(ESPHOME_SERVICE, name)
        if not await info.async_request(azc.zeroconf, int(max(1.0, timeout) * 1000)):
            return
        props: dict[str, str] = {}
        for raw_key, raw_value in (info.properties or {}).items():
            try:
                key = raw_key.decode("utf-8", "replace") if isinstance(raw_key, bytes) else str(raw_key)
                value = raw_value.decode("utf-8", "replace") if isinstance(raw_value, bytes) else str(raw_value or "")
            except Exception:  # noqa: BLE001 - malformed TXT record should not break discovery
                continue
            props[key] = value

        addresses: list[str] = []
        try:
            addresses = list(info.parsed_scoped_addresses())
        except Exception:  # noqa: BLE001
            try:
                addresses = list(info.parsed_addresses())
            except Exception:  # noqa: BLE001
                addresses = []

        # Prefer IPv4 where available; the addon can still resolve the .local server name.
        ipv4 = [addr for addr in addresses if _is_ipv4(addr)]
        host = (ipv4 or addresses or [str(info.server or "").rstrip(".")])[0]
        if not host:
            return
        mac = normalize_mac(props.get("mac"))
        friendly = props.get("friendly_name", "")
        found[name] = MDNSNode(
            name=name.removesuffix(f".{ESPHOME_SERVICE}").removesuffix("."),
            host=host,
            port=int(info.port or 6053),
            mac=mac,
            friendly_name=friendly,
            api_encryption=bool(props.get("api_encryption")),
        )

    def handler(_: Any, service_type: str, name: str, state: ServiceStateChange) -> None:
        if service_type != ESPHOME_SERVICE or state not in (
            ServiceStateChange.Added,
            ServiceStateChange.Updated,
        ):
            return
        task = asyncio.create_task(resolve(name))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    browser = AsyncServiceBrowser(azc.zeroconf, ESPHOME_SERVICE, handlers=[handler])
    try:
        await asyncio.sleep(timeout)
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
    finally:
        await browser.async_cancel()
        await azc.async_close()
    return list(found.values())


def _is_ipv4(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value.split("%", 1)[0]), ipaddress.IPv4Address)
    except ValueError:
        return False


def _device_for_entry(devices: list[dict[str, Any]], entry_id: str) -> dict[str, Any] | None:
    """Return the main HA device-registry row belonging to a config entry."""
    for device in devices:
        config_ids = set(device.get("config_entries") or [])
        if device.get("config_entry_id"):
            config_ids.add(device["config_entry_id"])
        if entry_id in config_ids and not device.get("parent_device_id"):
            return device
    return None


def _host_from_configuration_url(value: str | None) -> str:
    """Extract only the host from a Home Assistant device configuration URL."""
    if not value:
        return ""
    try:
        parsed = urlparse(str(value))
    except ValueError:
        return ""
    return parsed.hostname or ""


def _slug_hostname(value: str | None) -> str:
    """Build the common ESPHome <node-name>.local hostname from a display name."""
    if not value:
        return ""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return f"{slug}.local" if slug else ""


def _candidate_hosts(
    entry: dict[str, Any],
    device: dict[str, Any] | None,
    mdns_node: MDNSNode | None,
) -> list[tuple[str, int, str]]:
    """Return host candidates ordered from strongest to weakest evidence.

    Containers used by Home Assistant Apps do not always receive LAN multicast,
    so browsing _esphomelib._tcp can fail even though direct .local resolution
    works.  Home Assistant's ESPHome device registry often exposes the current
    host through configuration_url; display-name derived .local names are a
    final best-effort fallback.
    """
    candidates: list[tuple[str, int, str]] = []

    def add(host: str, port: int, source: str) -> None:
        host = str(host or "").strip().rstrip(".")
        if not host:
            return
        key = (host.lower(), int(port))
        if any((h.lower(), p) == key for h, p, _ in candidates):
            return
        candidates.append((host, int(port), source))

    if mdns_node is not None:
        add(mdns_node.host, mdns_node.port, "mDNS browse")

    if device is not None:
        config_host = _host_from_configuration_url(device.get("configuration_url"))
        if config_host:
            add(config_host, 6053, "HA device configuration URL")
        # Depending on ESPHome/friendly_name settings, either registry name can
        # be closer to the node name than the config-entry title.
        add(_slug_hostname(device.get("name")), 6053, "HA device name")
        add(_slug_hostname(device.get("name_by_user")), 6053, "HA device user name")

    add(_slug_hostname(entry.get("title")), 6053, "HA config-entry title")
    return candidates


def _network_mac_for_entry(devices: list[dict[str, Any]], entry_id: str) -> str:
    for device in devices:
        config_ids = set(device.get("config_entries") or [])
        if device.get("config_entry_id"):
            config_ids.add(device["config_entry_id"])
        if entry_id not in config_ids:
            continue
        for conn in device.get("connections") or []:
            if not isinstance(conn, (list, tuple)) or len(conn) != 2:
                continue
            if str(conn[0]) == "mac":
                return normalize_mac(str(conn[1]))
    return ""


async def _probe_proxy(proxy: DiscoveredProxy) -> DiscoveredProxy:
    if proxy.port != 6053:
        proxy.status = "unsupported-port"
        proxy.error = "bleak-esphome currently requires ESPHome API port 6053"
        return proxy

    client = APIClient(proxy.host, proxy.port, noise_psk=proxy.noise_psk or None)
    try:
        async with asyncio.timeout(12):
            await client.connect(login=True)
            info = await client.device_info()
        device_mac = normalize_mac(str(getattr(info, "mac_address", "") or ""))
        if proxy.mac and device_mac and normalize_mac(proxy.mac) != device_mac:
            proxy.status = "wrong-device"
            proxy.error = (
                f"Resolved host answered as MAC {device_mac}, expected {normalize_mac(proxy.mac)}"
            )
            return proxy
        proxy.esphome_version = str(getattr(info, "esphome_version", "") or "")
        proxy.model = str(getattr(info, "model", "") or "")
        proxy.bluetooth_mac = str(getattr(info, "bluetooth_mac_address", "") or "")
        proxy.feature_flags = int(getattr(info, "bluetooth_proxy_feature_flags", 0) or 0)
        proxy.active_connections = bool(proxy.feature_flags & ACTIVE_CONNECTIONS_FLAG)
        if proxy.feature_flags == 0:
            proxy.status = "not-bluetooth-proxy"
        elif not proxy.active_connections:
            proxy.status = "passive-only"
            proxy.error = "Bluetooth Proxy exists but active GATT connections are disabled"
        else:
            proxy.status = "usable"
            proxy.usable = True
    except Exception as exc:  # noqa: BLE001 - discovery report should keep other nodes
        proxy.status = "probe-failed"
        proxy.error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await client.disconnect(force=True)
        except Exception:  # noqa: BLE001
            pass
    return proxy


async def discover_home_assistant_proxies(timeout: float = 3.0) -> tuple[list[DiscoveredProxy], str | None]:
    """Find ESPHome entries in HA, match them to mDNS, retrieve API keys, and probe BT capability."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return [], "SUPERVISOR_TOKEN is unavailable; automatic proxy discovery cannot query Home Assistant"

    try:
        async with HAWebSocket(token) as ws:
            entries = await ws.command("config_entries/get", domain="esphome")
            devices = await ws.command("config/device_registry/list")
            key_by_entry: dict[str, str | None] = {}
            for entry in entries or []:
                if entry.get("state") != "loaded" or entry.get("disabled_by") is not None:
                    continue
                entry_id = str(entry.get("entry_id") or "")
                if not entry_id:
                    continue
                try:
                    key_result = await ws.command("esphome/get_encryption_key", entry_id=entry_id)
                    key_by_entry[entry_id] = (key_result or {}).get("encryption_key") or None
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Unable to read ESPHome encryption key for %s: %s", entry.get("title"), exc)
                    key_by_entry[entry_id] = None
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Unable to query Home Assistant ESPHome configuration")
        return [], f"{type(exc).__name__}: {exc}"

    mdns_error: str | None = None
    try:
        mdns_nodes = await discover_esphome_mdns(timeout)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("ESPHome mDNS discovery failed; continuing with HA registry/name resolution")
        mdns_nodes = []
        mdns_error = f"mDNS browse unavailable: {type(exc).__name__}: {exc}"

    by_mac = {node.mac: node for node in mdns_nodes if node.mac}
    by_name = {node.name.lower(): node for node in mdns_nodes}
    discovered: list[DiscoveredProxy] = []

    for entry in entries or []:
        if entry.get("state") != "loaded" or entry.get("disabled_by") is not None:
            continue
        entry_id = str(entry.get("entry_id") or "")
        title = str(entry.get("title") or entry_id)
        mac = _network_mac_for_entry(devices or [], entry_id)
        device = _device_for_entry(devices or [], entry_id)
        node = by_mac.get(mac)
        if node is None:
            normalized_title = title.lower().replace(" ", "-")
            node = by_name.get(normalized_title) or by_name.get(title.lower())

        candidates = _candidate_hosts(entry, device, node)
        if not candidates:
            discovered.append(
                DiscoveredProxy(
                    entry_id=entry_id,
                    name=title,
                    host="",
                    port=6053,
                    mac=mac,
                    noise_psk=key_by_entry.get(entry_id),
                    status="not-resolved",
                    error="No mDNS, configuration URL, or usable .local hostname candidate was available",
                )
            )
            continue

        attempts: list[str] = []
        selected: DiscoveredProxy | None = None
        for host, port, source in candidates:
            proxy = DiscoveredProxy(
                entry_id=entry_id,
                name=(node.friendly_name if node and node.friendly_name else title),
                host=host,
                port=port,
                mac=mac or (node.mac if node else ""),
                noise_psk=key_by_entry.get(entry_id),
                resolved_via=source,
            )
            await _probe_proxy(proxy)
            if proxy.status in {"usable", "passive-only", "not-bluetooth-proxy"}:
                selected = proxy
                break
            attempts.append(f"{host} via {source}: {proxy.status}")

        if selected is None:
            # Keep the strongest candidate in the UI, but include all attempted
            # resolution paths in the error for useful diagnostics.
            host, port, source = candidates[0]
            selected = DiscoveredProxy(
                entry_id=entry_id,
                name=title,
                host=host,
                port=port,
                mac=mac,
                noise_psk=key_by_entry.get(entry_id),
                status="probe-failed",
                error="; ".join(attempts) or "All host candidates failed",
                resolved_via=source,
            )
        discovered.append(selected)

    discovered.sort(key=lambda p: (not p.usable, p.name.lower(), p.host))
    # A failed multicast browse is informational if another HA-derived route worked.
    return discovered, mdns_error
