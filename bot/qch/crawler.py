"""Read a reserve snapshot. This module does not open a socket.

A missing reader is ``reader_absent``. It is not an empty reserve and
not a zero score.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "snapshot.json"


def load_snapshot(path: Optional[Path] = None) -> dict:
    """The recorded snapshot. Unreadable stays unreadable."""
    target = path if path is not None else _FIXTURE
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"readable": False, "reason": "unreadable", "snapshot": None}
    if not isinstance(data, dict) or not isinstance(data.get("issuers"), list):
        return {"readable": False, "reason": "unreadable", "snapshot": None}
    return {"readable": True, "reason": None, "snapshot": data}


def crawl(reader: Optional[Callable[[], dict]] = None) -> dict:
    """Score only what ``reader`` returns. The default reader is the local fixture."""
    if reader is None:
        loaded = load_snapshot()
        if loaded["readable"] is not True:
            return {"readable": False, "reason": loaded["reason"], "snapshot": None}
        return {"readable": True, "reason": "fixture", "snapshot": loaded["snapshot"]}
    try:
        snapshot = reader()
    except Exception:
        return {"readable": False, "reason": "reader_failed", "snapshot": None}
    if not isinstance(snapshot, dict):
        return {"readable": False, "reason": "unreadable", "snapshot": None}
    return {"readable": True, "reason": "reader", "snapshot": snapshot}
