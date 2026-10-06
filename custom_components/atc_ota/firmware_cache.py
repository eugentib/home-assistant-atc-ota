"""Persistent firmware cache helpers with no Home Assistant dependencies."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Callable


def firmware_cache_filename(version: str, source_path: str) -> str:
    """Return a stable, filesystem-safe cache filename."""
    version_part = re.sub(r"[^A-Za-z0-9._-]+", "_", version).strip("._") or "unknown"
    path_part = source_path.strip("/\\").replace("\\", "__").replace("/", "__")
    path_part = re.sub(r"[^A-Za-z0-9._-]+", "_", path_part).strip("._") or "firmware.bin"
    return f"{version_part}__{path_part}"


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 digest for firmware bytes."""
    return hashlib.sha256(data).hexdigest()


def read_cached_firmware(
    path: Path,
    *,
    max_size: int,
    validator: Callable[[bytes], None],
) -> bytes | None:
    """Read and validate a cached image, deleting corrupt cache entries."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None

    try:
        if len(data) > max_size:
            raise ValueError("Cached firmware exceeds maximum size")
        validator(data)
    except (OSError, ValueError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return data


def write_cached_firmware(path: Path, data: bytes) -> None:
    """Atomically write firmware bytes to the persistent cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
