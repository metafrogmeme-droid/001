"""A bounded, persisted record of what a control would have done.

Extracted from `bot/risk/ladder_shadow.py` when the balance-relative bounds
needed the same thing one flag over: a second copy of a ledger is two answers
about what "unreadable" means, which is the shape this repo keeps finding in
maps and gates. The class knows nothing about its rows -- each control keeps
its own row builder, summary and card beside its ledger.

THREE LOAD STATES. ``fresh`` (no file yet), ``read`` (the file parsed and holds
a list of rows), ``unreadable`` (a file is there and is not a ledger). A file
that will not parse is NEVER overwritten -- `exchange_credentials._load`'s
rule, because a record of evidence destroyed by the reader that could not open
it is the `secrets_vault` defect one store over. Rows recorded after that live
in memory, ``recorded_since_load`` counts them, and the next boot reads the
same file again.

``record`` never raises: the money path that writes a row must not lose a
trade to a ledger fault, so a write that fails is logged and answers False.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from bot.utils.atomic_write import atomic_write_json

logger = logging.getLogger("runeclaw.shadow_ledger")

MAX_ROWS = 500
"""The newest rows kept, by default. Stated on a card only when the record is
FULL, and as MAY: exactly MAX_ROWS rows fill it too, so full does not prove
eviction, and a permanent "older rows are gone" over a ten-row record is the
line that trains a reader to skip the footnote."""

STATE_FRESH = "fresh"            # no file on disk yet
STATE_READ = "read"              # the file parsed
STATE_UNREADABLE = "unreadable"  # a file is there and is not a ledger; never overwritten


class ShadowLedger:
    """The persisted record. See the module docstring for the three states."""

    def __init__(self, state_file: str, *, max_rows: int = MAX_ROWS) -> None:
        self.state_file = state_file
        self.max_rows = max(1, int(max_rows))
        self._rows: List[Dict[str, Any]] = []
        self.load_state = STATE_FRESH
        self.load_detail = ""
        self.loaded_at = time.time()
        self.recorded_since_load = 0
        self._load()

    # ── persistence ────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            self.load_state = STATE_FRESH
            return
        except Exception as exc:
            self.load_state = STATE_UNREADABLE
            self.load_detail = type(exc).__name__
            logger.warning("ledger %s could not be read (%s); it is kept as it is and "
                           "rows recorded from here on stay in memory",
                           self.state_file, self.load_detail)
            return
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            self.load_state = STATE_UNREADABLE
            self.load_detail = "not a ledger"
            logger.warning("ledger %s is not a ledger; it is kept as it is and rows "
                           "recorded from here on stay in memory", self.state_file)
            return
        self._rows = list(rows)
        self.load_state = STATE_READ

    def _save(self) -> bool:
        if self.load_state == STATE_UNREADABLE:
            # Never write over what could not be read: the file is somebody's
            # evidence until a human looks at it.
            return False
        atomic_write_json(self.state_file, {"rows": self._rows})
        return True

    # ── writes ─────────────────────────────────────────────────────────────
    def record(self, row: Dict[str, Any]) -> bool:
        """Append one row (newest ``max_rows`` kept). Never raises; answers
        whether the row reached disk."""
        try:
            r = dict(row)
            r.setdefault("ts", time.time())
            self._rows.append(r)
            if len(self._rows) > self.max_rows:
                del self._rows[:-self.max_rows]
            self.recorded_since_load += 1
            return self._save()
        except Exception as exc:
            logger.warning("ledger %s: a row could not be recorded (%s)",
                           self.state_file, type(exc).__name__)
            return False

    # ── reads ──────────────────────────────────────────────────────────────
    def rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    @property
    def full(self) -> bool:
        return len(self._rows) >= self.max_rows


def stamp(ts: Optional[float]) -> str:
    """A row's time on a card, or ``?`` for one the row does not carry."""
    from datetime import datetime

    from bot.compat import UTC
    if ts is None:
        return "?"
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"
