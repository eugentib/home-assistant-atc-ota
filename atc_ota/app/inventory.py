"""Persistent inventory for discovered pvvx/ATC devices."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from .firmware_source import resolve_stable_firmware

_LOGGER = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?")


def normalize_address(address: str) -> str:
    return address.strip().upper()


def version_tuple(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    match = _VERSION_RE.search(str(value).strip().lstrip("Vv"))
    if not match:
        return None
    parts = [int(match.group(1))]
    if match.group(2) is not None:
        parts.append(int(match.group(2)))
    return tuple(parts)


def update_available(current: str | None, latest: str | None) -> bool | None:
    cur = version_tuple(current)
    lat = version_tuple(latest)
    if cur is None or lat is None:
        return None
    size = max(len(cur), len(lat))
    cur += (0,) * (size - len(cur))
    lat += (0,) * (size - len(lat))
    return cur < lat


class InventoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.devices: dict[str, dict[str, Any]] = {}
        self.last_inventory_scan: float | None = None
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            _LOGGER.exception("Unable to read inventory %s", self.path)
            return
        if isinstance(raw, dict):
            stored = raw.get("devices", {})
            if isinstance(stored, dict):
                self.devices = {
                    normalize_address(k): v
                    for k, v in stored.items()
                    if isinstance(k, str) and isinstance(v, dict)
                }
            stamp = raw.get("last_inventory_scan")
            if isinstance(stamp, (int, float)):
                self.last_inventory_scan = float(stamp)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_inventory_scan": self.last_inventory_scan,
            "devices": self.devices,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def note_scan(self, scanned: list[dict[str, Any]], proxy_address: str) -> None:
        now = time.time()
        for item in scanned:
            address = normalize_address(str(item.get("address", "")))
            if not address:
                continue
            entry = self.devices.setdefault(address, {"address": address})
            entry.update(
                {
                    "address": address,
                    "advertised_name": item.get("name"),
                    "rssi": item.get("rssi"),
                    "candidate": bool(item.get("candidate")),
                    "candidate_reason": item.get("candidate_reason"),
                    "service_uuids": item.get("service_uuids", []),
                    "service_data_uuids": item.get("service_data_uuids", []),
                    "proxy_address": proxy_address,
                    "source": item.get("source"),
                    "best_proxy": item.get("best_proxy"),
                    "seen_by": item.get("seen_by", []),
                    "route_source": item.get("route_source"),
                    "route_proxy": item.get("route_proxy"),
                    "route_rssi": item.get("route_rssi"),
                    "last_seen": now,
                }
            )

    def update_info(
        self,
        address: str,
        info: dict[str, Any],
        *,
        latest: dict[str, Any] | None,
        latest_error: str | None,
    ) -> dict[str, Any]:
        key = normalize_address(address)
        now = time.time()
        entry = self.devices.setdefault(key, {"address": key})
        entry.update(info)
        entry["address"] = key
        entry["last_info_refresh"] = now
        entry["last_error"] = None
        entry["latest"] = latest
        entry["latest_error"] = latest_error
        latest_version = latest.get("version") if latest else None
        entry["update_available"] = update_available(
            entry.get("current_version"), latest_version
        )
        return entry

    def update_error(self, address: str, message: str) -> dict[str, Any]:
        key = normalize_address(address)
        entry = self.devices.setdefault(key, {"address": key})
        entry["last_info_refresh"] = time.time()
        entry["last_error"] = message
        return entry

    def apply_catalog(self, catalog: dict[str, Any]) -> None:
        for entry in self.devices.values():
            try:
                choice = resolve_stable_firmware(
                    catalog,
                    model=entry.get("model"),
                    hardware_revision=entry.get("hardware_revision"),
                )
            except Exception as exc:  # noqa: BLE001 - unsupported devices are expected
                entry["latest_error"] = f"{type(exc).__name__}: {exc}"
                if not entry.get("latest"):
                    entry["latest"] = None
                    entry["update_available"] = None
                continue
            entry["latest"] = {
                "version": choice.version,
                "path": choice.path,
                "filename": choice.filename,
            }
            entry["latest_error"] = None
            entry["update_available"] = update_available(
                entry.get("current_version"), choice.version
            )

    def enriched_scan(self, scanned: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in scanned:
            key = normalize_address(str(item.get("address", "")))
            cached = self.devices.get(key, {})
            merged = dict(cached)
            merged.update(item)
            # Keep GATT-resolved name over an absent advertisement name.
            if cached.get("device_name"):
                merged["device_name"] = cached["device_name"]
            result.append(merged)
        return result

    def get(self, address: str) -> dict[str, Any] | None:
        return self.devices.get(normalize_address(address))

    def all(self) -> list[dict[str, Any]]:
        rows = [dict(item) for item in self.devices.values()]
        rows.sort(
            key=lambda item: (
                item.get("update_available") is not True,
                -(item.get("rssi") if isinstance(item.get("rssi"), int) else -999),
                str(item.get("device_name") or item.get("advertised_name") or item.get("address")),
            )
        )
        return rows

    def candidates_seen_since(self, since: float) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.devices.values()
            if item.get("candidate") and float(item.get("last_seen") or 0) >= since
        ]

    def mark_inventory_scan(self) -> None:
        self.last_inventory_scan = time.time()
