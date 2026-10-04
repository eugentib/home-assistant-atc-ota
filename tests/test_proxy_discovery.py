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


def test_candidate_hosts_rejects_generic_app_host() -> None:
    entry = {"title": "ESP32 Relay X2 EVSE"}
    device = {
        "configuration_url": "http://app/",
        "name": "ESP32 Relay X2 EVSE",
        "name_by_user": None,
    }
    values = _candidate_hosts(entry, device, None)
    assert all(host != "app" for host, _, _ in values)
    assert values == [("esp32-relay-x2-evse.local", 6053, "HA device name")]


def test_proxy_runtime_signature_changes_when_scanner_metadata_changes() -> None:
    # Importing main pulls in the BLE runtime dependencies, so keep this as a
    # structural regression assertion against the source: bluetooth_mac/name
    # must participate in the signature or an old cache can keep stale mapping.
    main_source = (ROOT / "atc_ota" / "app" / "main.py").read_text(encoding="utf-8")
    signature_block = main_source.split("def _proxy_signature", 1)[1].split("async def _activate_proxy_configs", 1)[0]
    assert 'item.get("bluetooth_mac")' in signature_block
    assert 'item.get("name")' in signature_block
