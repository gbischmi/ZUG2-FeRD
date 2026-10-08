"""ZUG2-FeRD – Produkt- und Versionsangaben.

Einzige Quelle: zug2ferd/conf/sysctl.json (Schlüssel "version"). Hier NICHTS von Hand pflegen.
"""
from __future__ import annotations

import json
from pathlib import Path

__all__ = ["__version__", "SYSCTL_PATH", "load_sysctl"]

SYSCTL_PATH = Path(__file__).resolve().parent / "conf" / "sysctl.json"


def load_sysctl() -> dict:
    try:
        data = json.loads(SYSCTL_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


__version__ = str(load_sysctl().get("version") or "0.0.0")
