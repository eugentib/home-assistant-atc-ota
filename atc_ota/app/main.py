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
from .ota import flash_telink
from .protocol import validate_firmware
from .web import INDEX_HTML

OPTIONS_FILE = Path(os.environ.get("ATC_OTA_OPTIONS", "/data/options.json"))
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


class Runtime:
    def __init__(self) -> None:
        self.options: dict[str, Any] = {}
        self.bridge: BluetoothProxyBridge | None = None
        self.jobs: dict[str, Job] = {}
        self.ota_lock = asyncio.Lock()

    async def start(self) -> None:
        self.options = load_options()
        configure_logging(bool(self.options.get("debug", False)))

        address = str(self.options.get("proxy_address", "")).strip()
        if not address:
            raise RuntimeError("proxy_address is empty")
        noise_psk = str(self.options.get("proxy_noise_psk", "")).strip() or None

        self.bridge = BluetoothProxyBridge(address, noise_psk)
        await self.bridge.start()

    async def stop(self) -> None:
        if self.bridge is not None:
            await self.bridge.stop()


runtime = Runtime()


def load_options() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "proxy_address": "btproxy1.local.",
        "proxy_noise_psk": "",
        "scan_seconds": 8,
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ATC OTA over ESPHome", version="0.1.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def status() -> dict[str, Any]:
    if runtime.bridge is None:
        proxy = {
            "address": runtime.options.get("proxy_address", ""),
            "connected": False,
            "status": "not-started",
            "error": None,
        }
    else:
        proxy = asdict(runtime.bridge.state)
    return {
        "version": app.version,
        "proxy": proxy,
        "scan_seconds": runtime.options.get("scan_seconds", 8),
    }


@app.post("/api/scan")
async def scan() -> dict[str, Any]:
    if runtime.bridge is None:
        raise HTTPException(status_code=503, detail="Bluetooth bridge is not initialized")
    try:
        seconds = float(runtime.options.get("scan_seconds", 8))
        devices = await runtime.bridge.scan(seconds)
    except Exception as exc:  # noqa: BLE001 - convert BLE stack errors to API detail
        logging.exception("BLE scan failed")
        raise HTTPException(status_code=503, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"devices": devices}


@app.post("/api/ota")
async def start_ota(
    address: str = Form(...),
    firmware: UploadFile = File(...),
) -> dict[str, str]:
    address = address.strip()
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
            await flash_telink(job.address, firmware, update)
        except Exception as exc:  # noqa: BLE001 - report complete OTA errors in UI
            logging.exception("OTA failed for %s", job.address)
            job.state = "error"
            job.add(job.progress, f"{type(exc).__name__}: {exc}")
        else:
            job.state = "done"
            job.add(100, "OTA completed successfully")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8099, log_level="info")
