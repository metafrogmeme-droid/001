"""The reading line a chat tool puts in front of the card it already built.

The skill's own card stays the prose. This line says whether that prose was
read, missing, or never measured, and it carries a scalar only when the tool
itself measured one. Parsing the card into a second number would be a second
portfolio reading.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

READ_STATES = frozenset({"read", "unread", "absent"})


def render_reading(source: str, read_state: str, prose: str = "",
                   value: Any = None, unit: Optional[str] = None,
                   as_of: Optional[str] = None) -> str:
    """One JSON object, then the card the skill already returned.

    ``value`` stays null when the tool measured a card rather than a scalar.
    A null here is not a zero: nothing numeric was claimed.
    """
    if read_state not in READ_STATES:
        raise ValueError(f"read_state {read_state!r}")
    row = {
        "as_of": as_of or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "read_state": read_state,
        "source": str(source),
        "unit": unit,
        "value": value,
    }
    line = json.dumps(row, separators=(",", ":"), sort_keys=True)
    body = (prose or "").strip()
    return f"{line}\n{body}" if body else line


def parse_reading(text: str) -> Optional[dict]:
    """The reading object, when ``text`` starts with one. None otherwise."""
    raw = (text or "").lstrip()
    if not raw.startswith("{"):
        return None
    line, _, _rest = raw.partition("\n")
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict) or row.get("read_state") not in READ_STATES:
        return None
    return row


def prose_of(text: str) -> str:
    """The card under the reading line, or the whole text when there is none."""
    if parse_reading(text) is None:
        return text or ""
    _line, sep, rest = (text or "").lstrip().partition("\n")
    return rest if sep else ""
