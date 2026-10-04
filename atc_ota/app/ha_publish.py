"""Publish cached ATC inventory into Home Assistant's state machine."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)
HA_API = "http://supervisor/core/api"


def entity_suffix(address: str) -> str:
    suffix = re.sub(r"[^a-z0-9]+", "_", address.lower()).strip("_")
    return suffix or "unknown"


class HomeAssistantPublisher:
    def __init__(self, enabled: bool, low_battery_threshold: int = 30) -> None:
        self.enabled = enabled
        self.low_battery_threshold = max(1, min(100, int(low_battery_threshold)))
        self.token = os.environ.get("SUPERVISOR_TOKEN", "") if enabled else ""
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.token)

    async def _set_state(self, entity_id: str, state: str, attributes: dict[str, Any]) -> None:
        if not self.available:
            return
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{HA_API}/states/{entity_id}",
                headers=headers,
                json={"state": state, "attributes": attributes},
            )
            response.raise_for_status()
        self.last_error = None

    async def publish_device(self, item: dict[str, Any], proxy_address: str) -> None:
        if not self.available:
            return
        address = str(item.get("address") or "")
        suffix = entity_suffix(address)
        name = str(
            item.get("device_name")
            or item.get("advertised_name")
            or f"ATC {address[-8:]}"
        )
        latest = item.get("latest") if isinstance(item.get("latest"), dict) else {}
        latest_version = latest.get("version") if latest else None
        battery = item.get("battery_percent")
        battery_numeric = battery if isinstance(battery, (int, float)) and not isinstance(battery, bool) else None
        low_battery = battery_numeric is not None and battery_numeric <= self.low_battery_threshold
        common = {
            "device_name": name,
            "address": address,
            "model": item.get("model"),
            "hardware_revision": item.get("hardware_revision"),
            "software_revision": item.get("software_revision"),
            "firmware_revision": item.get("firmware_revision"),
            "manufacturer": item.get("manufacturer"),
            "serial": item.get("serial"),
            "latest_version": latest_version,
            "latest_firmware": latest.get("filename") if latest else None,
            "update_available": item.get("update_available"),
            "rssi": item.get("rssi"),
            "battery_percent": battery_numeric,
            "battery_source": item.get("battery_source"),
            "battery_advertised_at": item.get("battery_advertised_at"),
            "low_battery": low_battery if battery_numeric is not None else None,
            "low_battery_threshold": self.low_battery_threshold,
            "proxy": proxy_address,
            "last_seen": item.get("last_seen"),
            "last_info_refresh": item.get("last_info_refresh"),
            "last_error": item.get("last_error"),
        }
        current = str(item.get("current_version") or "unknown")
        await self._set_state(
            f"sensor.atc_ota_{suffix}_firmware",
            current,
            {
                **common,
                "friendly_name": f"{name} firmware",
                "icon": "mdi:chip",
            },
        )
        if battery_numeric is not None:
            await self._set_state(
                f"sensor.atc_ota_{suffix}_battery",
                str(int(battery_numeric)),
                {
                    **common,
                    "friendly_name": f"{name} battery",
                    "icon": "mdi:battery",
                    "device_class": "battery",
                    "state_class": "measurement",
                    "unit_of_measurement": "%",
                },
            )
            await self._set_state(
                f"binary_sensor.atc_ota_{suffix}_low_battery",
                "on" if low_battery else "off",
                {
                    **common,
                    "friendly_name": f"{name} low battery",
                    "icon": "mdi:battery-alert",
                },
            )

        flag = item.get("update_available")
        update_state = "on" if flag is True else "off" if flag is False else "unknown"
        await self._set_state(
            f"binary_sensor.atc_ota_{suffix}_update_available",
            update_state,
            {
                **common,
                "friendly_name": f"{name} update available",
                "icon": "mdi:update",
                "current_version": item.get("current_version"),
            },
        )

    async def publish_inventory(
        self,
        devices: list[dict[str, Any]],
        proxy_address: str,
        last_inventory_scan: float | None,
    ) -> None:
        if not self.available:
            return
        errors: list[str] = []
        for item in devices:
            if not item.get("candidate") and not item.get("model"):
                continue
            try:
                await self.publish_device(item, proxy_address)
            except Exception as exc:  # noqa: BLE001 - one entity must not block the rest
                errors.append(f"{item.get('address')}: {type(exc).__name__}: {exc}")
                _LOGGER.warning("Unable to publish HA state for %s: %s", item.get("address"), exc)

        updatable = [
            item
            for item in devices
            if item.get("update_available") is True
        ]
        try:
            await self._set_state(
                "sensor.atc_ota_updates_available",
                str(len(updatable)),
                {
                    "friendly_name": "ATC OTA updates available",
                    "icon": "mdi:update",
                    "device_count": len(
                        [d for d in devices if d.get("candidate") or d.get("model")]
                    ),
                    "devices": [
                        {
                            "name": d.get("device_name") or d.get("advertised_name"),
                            "address": d.get("address"),
                            "current": d.get("current_version"),
                            "latest": (d.get("latest") or {}).get("version")
                            if isinstance(d.get("latest"), dict)
                            else None,
                            "battery_percent": d.get("battery_percent"),
                            "low_battery": (
                                isinstance(d.get("battery_percent"), (int, float))
                                and not isinstance(d.get("battery_percent"), bool)
                                and d.get("battery_percent") <= self.low_battery_threshold
                            ),
                        }
                        for d in updatable
                    ],
                    "proxy": proxy_address,
                    "last_inventory_scan": last_inventory_scan,
                },
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"summary: {type(exc).__name__}: {exc}")
            _LOGGER.warning("Unable to publish HA inventory summary: %s", exc)

        self.last_error = "; ".join(errors) if errors else None
