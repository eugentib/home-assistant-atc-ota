"""ATC OTA Home Assistant app entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from .bluetooth import BluetoothProxyBridge
from .device_info import read_device_info
from .firmware_source import (
    download_firmware,
    fetch_catalog,
    get_latest_choice,
    resolve_stable_firmware,
)
from .ha_publish import HomeAssistantPublisher
from .inventory import InventoryStore, normalize_address
from .ota import flash_telink
from .protocol import validate_firmware
from .proxy_discovery import discover_home_assistant_proxies
from .web import INDEX_HTML

OPTIONS_FILE = Path(os.environ.get("ATC_OTA_OPTIONS", "/data/options.json"))
INVENTORY_FILE = Path(os.environ.get("ATC_OTA_INVENTORY", "/data/devices.json"))
PROXY_CACHE_FILE = Path(os.environ.get("ATC_OTA_PROXY_CACHE", "/data/proxies.json"))
MAX_FIRMWARE_SIZE = 2 * 1024 * 1024


@dataclass(slots=True)
class Job:
    id: str
    address: str
    filename: str
    state: str = "queued"
    progress: int = 0
    message: str = "Queued"
    log: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def add(self, progress: int, message: str) -> None:
        self.progress = max(0, min(100, int(progress)))
        self.message = message
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp}  {message}")
        if len(self.log) > 500:
            del self.log[:-500]


@dataclass(slots=True)
class InventoryJob:
    state: str = "idle"
    progress: int = 0
    message: str = "Idle"
    log: list[str] = field(default_factory=list)
    started: float | None = None
    finished: float | None = None

    def add(self, progress: int, message: str) -> None:
        self.progress = max(0, min(100, int(progress)))
        self.message = message
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp}  {message}")
        if len(self.log) > 300:
            del self.log[:-300]


class Runtime:
    def __init__(self) -> None:
        self.options: dict[str, Any] = {}
        self.bridge: BluetoothProxyBridge | None = None
        self.jobs: dict[str, Job] = {}
        self.ota_lock = asyncio.Lock()
        self.ble_lock = asyncio.Lock()
        self.proxy_refresh_lock = asyncio.Lock()
        self.inventory = InventoryStore(INVENTORY_FILE)
        self.inventory_job = InventoryJob()
        self.inventory_task: asyncio.Task[None] | None = None
        self.maintenance_task: asyncio.Task[None] | None = None
        self.publisher = HomeAssistantPublisher(False)
        self.proxy_discovery: list[dict[str, Any]] = []
        self.proxy_discovery_error: str | None = None
        self.proxy_mode = "not-started"
        self.proxy_phase = "not-started"
        self.proxy_message = "Proxy discovery has not started"
        self.proxy_task: asyncio.Task[None] | None = None
        self.active_proxy_configs: list[dict[str, Any]] = []

    @property
    def proxy_label(self) -> str:
        if self.bridge is not None:
            return self.bridge.label
        return str(self.options.get("proxy_address", ""))

    async def start(self) -> None:
        """Start the web app immediately; initialize BLE proxies in the background."""
        self.options = load_options()
        configure_logging(bool(self.options.get("debug", False)))

        self.publisher = HomeAssistantPublisher(
            bool(self.options.get("publish_to_home_assistant", True))
        )
        self.proxy_phase = "discovering"
        self.proxy_message = "Discovering ESPHome Bluetooth Proxies in the background"
        self.proxy_mode = "discovering"
        self.proxy_task = asyncio.create_task(self._configure_proxies_background())
        self.maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def _configure_proxies_background(self) -> None:
        """Run slow Home Assistant/ESPHome probing without blocking Ingress startup."""
        try:
            await self.configure_proxies()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep web UI available for diagnostics
            logging.exception("ESPHome Bluetooth Proxy initialization failed")
            self.proxy_phase = "error"
            self.proxy_message = f"{type(exc).__name__}: {exc}"
            self.proxy_discovery_error = self.proxy_discovery_error or self.proxy_message
            if self.bridge is None:
                self.proxy_mode = "error"

    def start_proxy_refresh(self) -> bool:
        """Schedule proxy discovery/reconfiguration and return False if already running."""
        if self.proxy_task is not None and not self.proxy_task.done():
            return False
        self.proxy_phase = "discovering"
        self.proxy_message = "Rediscovering ESPHome Bluetooth Proxies in the background"
        self.proxy_task = asyncio.create_task(self._configure_proxies_background())
        return True

    def _load_proxy_cache(self) -> list[dict[str, Any]]:
        """Load last-known-good proxy configs so restarts do not wait for discovery."""
        try:
            raw = json.loads(PROXY_CACHE_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError):
            logging.exception("Unable to read proxy cache %s", PROXY_CACHE_FILE)
            return []
        configs = raw.get("proxies") if isinstance(raw, dict) else raw
        if not isinstance(configs, list):
            return []
        clean: list[dict[str, Any]] = []
        for item in configs:
            if not isinstance(item, dict):
                continue
            address = str(item.get("address") or "").strip()
            if not address:
                continue
            clean.append({
                "address": address,
                "noise_psk": item.get("noise_psk") or None,
                "name": str(item.get("name") or address),
                "entry_id": str(item.get("entry_id") or ""),
                "bluetooth_mac": str(item.get("bluetooth_mac") or ""),
            })
        return clean

    def _save_proxy_cache(self, configs: list[dict[str, Any]]) -> None:
        """Persist only the usable auto-discovered proxy configs, atomically."""
        if not configs:
            return
        payload = {"version": 1, "saved_at": int(time.time()), "proxies": configs}
        try:
            PROXY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = PROXY_CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(PROXY_CACHE_FILE)
        except OSError:
            logging.exception("Unable to write proxy cache %s", PROXY_CACHE_FILE)

    @staticmethod
    def _proxy_signature(configs: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
        return tuple(sorted(
            (
                str(item.get("entry_id") or ""),
                str(item.get("address") or "").strip().lower().rstrip("."),
                str(item.get("noise_psk") or ""),
            )
            for item in configs
            if str(item.get("address") or "").strip()
        ))

    async def _activate_proxy_configs(self, configs: list[dict[str, Any]], *, mode: str) -> None:
        """Replace the active bridge only when the effective proxy set changed."""
        if self.bridge is not None and self._proxy_signature(configs) == self._proxy_signature(self.active_proxy_configs):
            self.proxy_mode = mode
            self.active_proxy_configs = list(configs)
            return

        old_bridge = self.bridge
        self.bridge = None
        if old_bridge is not None:
            await old_bridge.stop()

        bridge = BluetoothProxyBridge(configs, mode=mode)
        await bridge.start()
        self.bridge = bridge
        self.active_proxy_configs = list(configs)
        self.proxy_mode = mode

    async def configure_proxies(self) -> None:
        """Discover compatible ESPHome proxies, using last-known-good cache immediately."""
        async with self.proxy_refresh_lock:
            self.proxy_phase = "discovering"
            self.proxy_message = "Querying Home Assistant and probing ESPHome nodes"
            auto = bool(self.options.get("auto_discover_proxies", True))
            configs: list[dict[str, Any]] = []
            mode = "manual"
            self.proxy_discovery = []
            self.proxy_discovery_error = None

            # On restart, bring the last-known-good proxies online first.  This
            # avoids making BLE unavailable while unrelated ESPHome entries time
            # out during reconciliation.  Discovery still runs below in the
            # background and replaces the cache if anything changed.
            if auto and self.bridge is None:
                cached = self._load_proxy_cache()
                if cached:
                    try:
                        await self._activate_proxy_configs(cached, mode="automatic-cache")
                        self.proxy_phase = "discovering"
                        self.proxy_message = (
                            f"Using {len(cached)} cached Bluetooth Proxy node(s); "
                            "validating Home Assistant configuration in the background"
                        )
                        logging.info(
                            "Started %d cached ESPHome Bluetooth Proxy node(s) before discovery: %s",
                            len(cached),
                            ", ".join(str(item.get("name") or item.get("address")) for item in cached),
                        )
                    except Exception:  # noqa: BLE001
                        logging.exception("Unable to start cached ESPHome Bluetooth Proxy configuration")

            if auto:
                discovered, discovery_error = await discover_home_assistant_proxies()
                self.proxy_discovery = [item.public_dict() for item in discovered]
                self.proxy_discovery_error = discovery_error
                configs = [item.manager_config() for item in discovered if item.usable]
                if configs:
                    mode = "automatic"
                    self._save_proxy_cache(configs)
                    logging.info(
                        "Automatically discovered %d usable ESPHome Bluetooth Proxy node(s): %s",
                        len(configs),
                        ", ".join(str(item.get("name") or item.get("address")) for item in configs),
                    )

            if configs:
                await self._activate_proxy_configs(configs, mode=mode)
            elif self.bridge is not None and self.active_proxy_configs:
                # A transient HA/DNS failure must not throw away working cached
                # proxies.  Keep them and expose the discovery warning in the UI.
                self.proxy_mode = "automatic-cache" if auto else self.proxy_mode
                if auto:
                    extra = self.proxy_discovery_error or "no usable proxies were confirmed"
                    self.proxy_discovery_error = f"Discovery did not confirm a replacement; retaining cached proxies: {extra}"
                    logging.warning(self.proxy_discovery_error)
            else:
                address = str(self.options.get("proxy_address", "")).strip()
                noise_psk = str(self.options.get("proxy_noise_psk", "")).strip() or None
                if not address:
                    detail = self.proxy_discovery_error or "no usable ESPHome Bluetooth Proxy was found"
                    raise RuntimeError(
                        "Automatic Bluetooth Proxy discovery did not produce a usable proxy and "
                        f"proxy_address is empty: {detail}"
                    )
                configs = [
                    {
                        "address": address,
                        "noise_psk": noise_psk,
                        "name": address,
                        "entry_id": "manual",
                    }
                ]
                mode = "manual-fallback" if auto else "manual"
                if auto:
                    logging.warning(
                        "No automatically discovered active Bluetooth Proxy is usable; "
                        "falling back to manual proxy %s%s",
                        address,
                        f" ({self.proxy_discovery_error})" if self.proxy_discovery_error else "",
                    )
                await self._activate_proxy_configs(configs, mode=mode)

            if self.bridge is None:
                self.proxy_phase = "error"
                self.proxy_message = "No Bluetooth Proxy bridge is available"
                return

            self.proxy_phase = "ready" if self.bridge.state.connected else "connecting"
            self.proxy_message = (
                f"{self.bridge.connected_count} Bluetooth Proxy connection(s) ready"
                if self.bridge.state.connected
                else "Proxy configuration completed; waiting for runtime connections"
            )

    async def stop(self) -> None:
        for task in (self.proxy_task, self.inventory_task, self.maintenance_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.bridge is not None:
            await self.bridge.stop()
        self.active_proxy_configs = []

    async def publish_inventory(self) -> None:
        if self.bridge is None:
            return
        await self.publisher.publish_inventory(
            self.inventory.all(),
            self.proxy_label,
            self.inventory.last_inventory_scan,
        )

    async def refresh_catalog_for_cache(self) -> None:
        try:
            catalog = await fetch_catalog()
            self.inventory.apply_catalog(catalog)
            self.inventory.save()
            await self.publish_inventory()
        except Exception:  # noqa: BLE001 - background maintenance should keep running
            logging.exception("Unable to refresh pvvx firmware catalog for cached inventory")

    async def _maintenance_loop(self) -> None:
        # Let Home Assistant Core and the proxies finish starting before the first publish.
        await asyncio.sleep(8)
        next_catalog_refresh = 0.0
        while True:
            try:
                now = time.time()
                if now >= next_catalog_refresh:
                    await self.refresh_catalog_for_cache()
                    hours = max(1, int(self.options.get("catalog_refresh_hours", 6)))
                    next_catalog_refresh = now + hours * 3600
                else:
                    # Re-publish cached states periodically so a Home Assistant Core
                    # restart does not require another BLE inventory pass.
                    await self.publish_inventory()
                await asyncio.sleep(300)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logging.exception("Inventory maintenance loop failed")
                await asyncio.sleep(300)


runtime = Runtime()


def load_options() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "auto_discover_proxies": True,
        "proxy_address": "btproxy1.local.",
        "proxy_noise_psk": "",
        "scan_seconds": 8,
        "publish_to_home_assistant": True,
        "catalog_refresh_hours": 6,
        "debug": False,
    }
    if not OPTIONS_FILE.exists():
        return defaults
    try:
        loaded = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.exception("Unable to read %s; using defaults", OPTIONS_FILE)
        return defaults
    defaults.update(loaded)
    return defaults


def configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # The websockets DEBUG logger prints complete frame payloads.  Our HA auth
    # message contains SUPERVISOR_TOKEN and ESPHome key replies contain the
    # Native API encryption key, so never allow this logger below INFO.
    logging.getLogger("websockets.client").setLevel(logging.INFO)
    logging.getLogger("websockets.protocol").setLevel(logging.INFO)
    if not debug:
        # This app intentionally has no local BlueZ adapter.  habluetooth may
        # probe for one before the remote ESPHome scanners register; suppress
        # those expected container-only warnings unless explicit debug is on.
        logging.getLogger("bluetooth_adapters").setLevel(logging.ERROR)
        logging.getLogger("habluetooth.channels.bluez").setLevel(logging.ERROR)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ATC OTA over ESPHome", version="0.1.8", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def status() -> dict[str, Any]:
    if runtime.bridge is not None and runtime.bridge.state.connected and runtime.proxy_phase == "connecting":
        runtime.proxy_phase = "ready"
        runtime.proxy_message = f"{runtime.bridge.connected_count} Bluetooth Proxy connection(s) ready"
    if runtime.bridge is None:
        proxy = {
            "address": runtime.options.get("proxy_address", ""),
            "connected": False,
            "status": runtime.proxy_phase,
            "error": runtime.proxy_message if runtime.proxy_phase == "error" else None,
            "proxies": [],
        }
    else:
        proxy = asdict(runtime.bridge.state)
    return {
        "version": app.version,
        "proxy": proxy,
        "proxy_mode": runtime.proxy_mode,
        "proxy_phase": runtime.proxy_phase,
        "proxy_message": runtime.proxy_message,
        "auto_discover_proxies": bool(runtime.options.get("auto_discover_proxies", True)),
        "proxy_discovery": runtime.proxy_discovery,
        "proxy_discovery_error": runtime.proxy_discovery_error,
        "scan_seconds": runtime.options.get("scan_seconds", 8),
        "home_assistant": {
            "publishing_enabled": runtime.publisher.enabled,
            "api_available": runtime.publisher.available,
            "last_error": runtime.publisher.last_error,
        },
    }


@app.post("/api/proxies/refresh")
async def refresh_proxies() -> dict[str, Any]:
    if runtime.ota_lock.locked():
        raise HTTPException(status_code=409, detail="Cannot rediscover proxies while OTA is running")
    if runtime.inventory_task and not runtime.inventory_task.done():
        raise HTTPException(status_code=409, detail="Cannot rediscover proxies while inventory refresh is running")
    if runtime.ble_lock.locked():
        raise HTTPException(status_code=409, detail="Cannot rediscover proxies while a BLE/GATT operation is active")
    if not runtime.start_proxy_refresh():
        return {"status": "already-running", "phase": runtime.proxy_phase}
    return {"status": "started", "phase": runtime.proxy_phase}


@app.post("/api/scan")
async def scan() -> dict[str, Any]:
    if runtime.bridge is None:
        raise HTTPException(status_code=503, detail="Bluetooth bridge is not initialized")
    try:
        seconds = float(runtime.options.get("scan_seconds", 8))
        devices = await runtime.bridge.scan(seconds)
        runtime.inventory.note_scan(devices, runtime.proxy_label)
        runtime.inventory.save()
        devices = runtime.inventory.enriched_scan(devices)
    except Exception as exc:  # noqa: BLE001 - convert BLE stack errors to API detail
        logging.exception("BLE scan failed")
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"devices": devices}


@app.get("/api/inventory")
async def inventory() -> dict[str, Any]:
    rows = runtime.inventory.all()
    return {
        "devices": rows,
        "last_inventory_scan": runtime.inventory.last_inventory_scan,
        "updates_available": sum(1 for item in rows if item.get("update_available") is True),
        "job": asdict(runtime.inventory_job),
    }


@app.post("/api/inventory/refresh")
async def refresh_inventory() -> dict[str, str]:
    if runtime.bridge is None or not runtime.bridge.state.connected:
        raise HTTPException(status_code=503, detail="ESPHome Bluetooth Proxy is not connected")
    if runtime.inventory_task and not runtime.inventory_task.done():
        raise HTTPException(status_code=409, detail="Inventory refresh is already running")
    runtime.inventory_task = asyncio.create_task(_run_inventory_refresh())
    return {"status": "started"}


async def _read_and_cache_device(
    address: str,
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    info = await read_device_info(address)
    latest: dict[str, Any] | None = None
    latest_error: str | None = None
    try:
        if catalog is None:
            choice = await get_latest_choice(
                model=info.get("model"),
                hardware_revision=info.get("hardware_revision"),
            )
        else:
            choice = resolve_stable_firmware(
                catalog,
                model=info.get("model"),
                hardware_revision=info.get("hardware_revision"),
            )
    except Exception as exc:  # noqa: BLE001 - unsupported model is not fatal
        latest_error = f"{type(exc).__name__}: {exc}"
    else:
        latest = {
            "version": choice.version,
            "path": choice.path,
            "filename": choice.filename,
        }
    entry = runtime.inventory.update_info(
        address,
        info,
        latest=latest,
        latest_error=latest_error,
    )
    runtime.inventory.save()
    if runtime.bridge is not None:
        try:
            await runtime.publisher.publish_device(entry, runtime.proxy_label)
        except Exception as exc:  # noqa: BLE001 - BLE success should not fail because HA publish failed
            runtime.publisher.last_error = f"{type(exc).__name__}: {exc}"
            logging.warning("Unable to publish device %s to Home Assistant: %s", address, exc)
    return entry


async def _run_inventory_refresh() -> None:
    job = runtime.inventory_job = InventoryJob(
        state="running",
        progress=0,
        message="Starting inventory scan",
        started=time.time(),
    )
    try:
        assert runtime.bridge is not None
        scan_started = time.time()
        seconds = float(runtime.options.get("scan_seconds", 8))
        job.add(2, f"Scanning BLE advertisements for {seconds:g}s")
        scanned = await runtime.bridge.scan(seconds)
        runtime.inventory.note_scan(scanned, runtime.proxy_label)
        runtime.inventory.save()
        candidates = runtime.inventory.candidates_seen_since(scan_started - 1)
        job.add(8, f"Found {len(candidates)} thermometer candidates; reading GATT information sequentially")

        try:
            catalog = await fetch_catalog()
        except Exception as exc:  # noqa: BLE001 - device reads can continue without catalog
            catalog = None
            job.add(10, f"Firmware catalog unavailable: {type(exc).__name__}: {exc}")

        if not candidates:
            runtime.inventory.mark_inventory_scan()
            runtime.inventory.save()
            await runtime.publish_inventory()
            job.state = "done"
            job.add(100, "Inventory completed; no candidate thermometers were seen")
            job.finished = time.time()
            return

        for index, item in enumerate(candidates, start=1):
            address = normalize_address(str(item.get("address", "")))
            label = item.get("device_name") or item.get("advertised_name") or address
            base = 10 + int(((index - 1) / len(candidates)) * 82)
            job.add(base, f"Reading {index}/{len(candidates)}: {label} ({address})")
            try:
                async with runtime.ble_lock:
                    await _read_and_cache_device(address, catalog=catalog)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep inventorying other devices
                message = f"{type(exc).__name__}: {exc}"
                runtime.inventory.update_error(address, message)
                runtime.inventory.save()
                job.add(base, f"Failed {address}: {message}")
            await asyncio.sleep(0.25)

        runtime.inventory.mark_inventory_scan()
        if catalog is not None:
            runtime.inventory.apply_catalog(catalog)
        runtime.inventory.save()
        await runtime.publish_inventory()
        updates = sum(
            1 for item in runtime.inventory.all() if item.get("update_available") is True
        )
        job.state = "done"
        job.add(100, f"Inventory completed; {updates} update(s) available")
        job.finished = time.time()
    except asyncio.CancelledError:
        job.state = "cancelled"
        job.add(job.progress, "Inventory cancelled")
        job.finished = time.time()
        raise
    except Exception as exc:  # noqa: BLE001
        logging.exception("Inventory refresh failed")
        job.state = "error"
        job.add(job.progress, f"{type(exc).__name__}: {exc}")
        job.finished = time.time()


@app.post("/api/device-info")
async def device_info(address: str = Form(...)) -> dict[str, Any]:
    address = normalize_address(address)
    if not address:
        raise HTTPException(status_code=400, detail="BLE address is required")
    if runtime.bridge is None or not runtime.bridge.state.connected:
        raise HTTPException(status_code=503, detail="ESPHome Bluetooth Proxy is not connected")
    try:
        async with runtime.ble_lock:
            info = await _read_and_cache_device(address)
        return info
    except Exception as exc:  # noqa: BLE001 - convert BLE errors to API detail
        logging.exception("Unable to read BLE device information for %s", address)
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/ota/latest")
async def start_latest_ota(address: str = Form(...)) -> dict[str, str]:
    address = normalize_address(address)
    if not address:
        raise HTTPException(status_code=400, detail="BLE address is required")
    if runtime.bridge is None or not runtime.bridge.state.connected:
        raise HTTPException(status_code=503, detail="ESPHome Bluetooth Proxy is not connected")

    try:
        async with runtime.ble_lock:
            info = await _read_and_cache_device(address)
        choice = await get_latest_choice(
            model=info.get("model"),
            hardware_revision=info.get("hardware_revision"),
        )
        data = await download_firmware(choice)
    except Exception as exc:  # noqa: BLE001 - report upstream/BLE errors to the UI
        logging.exception("Unable to prepare latest firmware OTA for %s", address)
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc

    job_id = uuid.uuid4().hex
    job = Job(
        id=job_id,
        address=address,
        filename=choice.filename,
    )
    current = info.get("current_version") or "unknown"
    job.add(0, f"Downloaded pvvx stable {choice.version} ({choice.filename}); current: {current}")
    runtime.jobs[job_id] = job
    asyncio.create_task(_run_ota_job(job, data))
    return {"job_id": job_id}


@app.post("/api/ota")
async def start_ota(
    address: str = Form(...),
    firmware: UploadFile = File(...),
) -> dict[str, str]:
    address = normalize_address(address)
    if not address:
        raise HTTPException(status_code=400, detail="BLE address is required")

    data = await firmware.read(MAX_FIRMWARE_SIZE + 1)
    if len(data) > MAX_FIRMWARE_SIZE:
        raise HTTPException(status_code=413, detail="Firmware file is larger than 2 MiB")

    try:
        validate_firmware(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex
    job = Job(
        id=job_id,
        address=address,
        filename=firmware.filename or "firmware.bin",
    )
    job.add(0, f"Queued {job.filename} for {address}")
    runtime.jobs[job_id] = job
    asyncio.create_task(_run_ota_job(job, data))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = runtime.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return {
        "id": job.id,
        "address": job.address,
        "filename": job.filename,
        "state": job.state,
        "progress": job.progress,
        "message": job.message,
        "log": job.log,
    }


async def _refresh_after_ota(address: str) -> None:
    for attempt in range(3):
        await asyncio.sleep(4 + attempt * 2)
        try:
            async with runtime.ble_lock:
                await _read_and_cache_device(address)
            await runtime.publish_inventory()
            return
        except Exception:  # noqa: BLE001 - target may still be rebooting
            logging.info("Post-OTA inventory refresh attempt %d failed for %s", attempt + 1, address)


async def _run_ota_job(job: Job, firmware: bytes) -> None:
    async with runtime.ota_lock:
        job.state = "running"
        job.add(0, "OTA job started")

        def update(progress: int, message: str) -> None:
            job.add(progress, message)
            logging.info("OTA %s: %s", job.address, message)

        try:
            if runtime.bridge is None or not runtime.bridge.state.connected:
                raise RuntimeError("ESPHome Bluetooth Proxy is not connected")
            async with runtime.ble_lock:
                await flash_telink(job.address, firmware, update)
        except Exception as exc:  # noqa: BLE001 - report complete OTA errors in UI
            logging.exception("OTA failed for %s", job.address)
            job.state = "error"
            job.add(job.progress, f"{type(exc).__name__}: {exc}")
        else:
            job.state = "done"
            job.add(100, "OTA completed successfully")
            asyncio.create_task(_refresh_after_ota(job.address))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8099, log_level="info")
