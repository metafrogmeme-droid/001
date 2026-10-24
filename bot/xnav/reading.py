"""The published ranking. A missing file is not an empty ranking."""
from __future__ import annotations

import json
from pathlib import Path

_FIXTURE = Path(__file__).with_name("fixtures") / "snapshot_sample.json"


class SnapshotUnreadable(Exception):
    """The ranking file could not be read."""

    # Survives docstring stripping, so this file stays parseable.
    pass


def load_snapshot(path: Path | None = None) -> dict:
    src = path or _FIXTURE
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotUnreadable(src.name) from exc
    if not isinstance(data, dict) or not isinstance(data.get("vaults"), list):
        raise SnapshotUnreadable(src.name)
    return data
