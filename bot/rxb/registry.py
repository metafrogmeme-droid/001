"""Issuer addresses this build will name.

Each address was taken from the Morpho assets search on 2026-10-23.
Nothing else is invented to fill the list. Silo is phase 2 and is not
a row here. Euler on Ethereum is read by the indexer, not by this file.
"""

from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).with_name("issuers.json")


def load_registry(path: Path | None = None) -> dict:
    raw = json.loads((path or _PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("issuers"), list):
        return {"readable": False, "issuers": None, "reason": "registry_unreadable"}
    return {"readable": True, "issuers": raw["issuers"], "reason": None}


def registry_tokens(path: Path | None = None) -> list[str]:
    loaded = load_registry(path)
    if not loaded["readable"]:
        return []
    out = []
    for row in loaded["issuers"]:
        token = row.get("token") if isinstance(row, dict) else None
        if isinstance(token, str) and token:
            out.append(token)
    return out
