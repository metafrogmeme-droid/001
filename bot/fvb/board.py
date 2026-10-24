"""The published board. One JSON file the website reads.

The website does not recompute the blend. A missing file is "not
published". A file that cannot be parsed is "unreadable". Neither is an
empty price.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from bot.fvb.chains import public_chains
from bot.fvb.packet import LABEL, SCHEMA, PayloadRefused, public_signal


def empty_board(reason: str) -> dict[str, object]:
    return {"available": False, "reason": reason, "label": LABEL, "v": SCHEMA, "rows": []}


def load_board(path: Path) -> dict[str, object]:
    if not path.is_file():
        return empty_board("not_published")
    try:
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return empty_board("unreadable")
    if not isinstance(doc, dict):
        return empty_board("unreadable")
    try:
        public_signal({"label": doc.get("label", LABEL)})
    except PayloadRefused:
        return empty_board("unreadable")
    return doc


def save_board(path: Path, rows: list[Mapping[str, object]]) -> dict[str, object]:
    """Write an allowlisted board. Refuses a banned string before the write."""
    clean = [public_signal(row) for row in rows]
    doc: dict[str, object] = {
        "available": True,
        "label": LABEL,
        "v": SCHEMA,
        "chains": public_chains(),
        "rows": clean,
    }
    public_signal({"basis": LABEL})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc
