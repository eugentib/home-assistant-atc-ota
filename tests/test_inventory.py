from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "atc_ota"))

from app.ha_publish import entity_suffix  # noqa: E402
from app.inventory import update_available, version_tuple  # noqa: E402


def test_version_parsing() -> None:
    assert version_tuple("V5.4") == (5, 4)
    assert version_tuple("5.9") == (5, 9)
    assert version_tuple(None) is None


def test_update_available() -> None:
    assert update_available("V5.4", "5.9") is True
    assert update_available("V5.9", "5.9") is False
    assert update_available("V6.0", "5.9") is False
    assert update_available(None, "5.9") is None


def test_entity_suffix() -> None:
    assert entity_suffix("A4:C1:38:51:5D:77") == "a4_c1_38_51_5d_77"
