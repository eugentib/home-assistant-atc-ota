from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atc_ota"))

from app.proxy_discovery import (  # noqa: E402
    _candidate_hosts,
    _host_from_configuration_url,
    _slug_hostname,
)


def test_configuration_url_host() -> None:
    assert _host_from_configuration_url("http://192.168.1.44:80/") == "192.168.1.44"
    assert _host_from_configuration_url("http://btproxy2.local/") == "btproxy2.local"


def test_slug_hostname() -> None:
    assert _slug_hostname("BTproxy2") == "btproxy2.local"
    assert _slug_hostname("ESP32 Relay X2 EVSE") == "esp32-relay-x2-evse.local"


def test_candidate_hosts_prefers_registry_url() -> None:
    entry = {"title": "BTproxy2"}
    device = {
        "configuration_url": "http://192.168.1.77/",
        "name": "BTproxy2",
        "name_by_user": None,
    }
    values = _candidate_hosts(entry, device, None)
    assert values[0] == ("192.168.1.77", 6053, "HA device configuration URL")
    assert ("btproxy2.local", 6053, "HA device name") in values
