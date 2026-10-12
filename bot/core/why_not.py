"""Why the engine did not trade: every analysis's outcome, kept by symbol and time.

`/whynot`, and the chat tool of the same name, read only
`engine._last_rejections`, which is filled when the RISK GATE refuses an idea.
Most analyses never reach the gate. The analyzer skips them first, below the
bar, at the regime gate or at the direction guard, and it recorded each skip
per symbol (`Analyzer._record_no_trade`) where nothing read it. So a day of
skips was answered "nothing was rejected", and the tool's description ("the
gate that rejected or skipped it") described a reading that did not exist.

ONE ROW PER ANALYSIS. The analyzer writes the row where it decides: a skip at
`_record_no_trade`, an idea under the idea's id. The engine then amends the row
of an idea it drops itself (the same-symbol guard, the liquidity gate, the risk
gate), so each analysis is counted once, at the stage that ended it, and the
counts add up to the analyses.

THE ENGINE'S OWN ANALYSES. Rows hold a symbol, a stage and words, and no
figure about anybody's account: the risk gate's row names the checks that
failed, not their numbers. Symbols are public market facts, so every caller
who may ask `/whynot` reads the same analysis rows.

CONFIRM-TIME REFUSALS ARE A PERSON'S OWN. A confirm that placed nothing is kept
with the id of whoever confirmed, and a reading shows a caller only their own.
The engine's autonomous confirms (``auto``) are the operator's book, and only
the operator is shown them.

BOUNDED, AND SAYS SO. Rows older than `HORIZON_S` are dropped and at most
`MAX_ROWS` are kept; the record lives in this process's memory and starts when
it starts. A reading states the window it was asked for and, when the record
does not reach back that far, the moment it does reach back to.

Nothing here is written by a process that is not the bot: `RuneClawEngine`
attaches a log to its own analyzer, and a reader engine's log is that
process's, which `reading_for` refuses to describe as the bot's.
"""
from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from bot.compat import UTC
from bot.core.symbol_form import normalize_symbol

#: What each stage is called where a person reads it. A stage nobody named
#: here is printed as its own key, never dropped.
STAGE_WORDS: dict[str, str] = {
    "confidence": "below the bar",
    "regime_hard_gate": "regime gate",
    "llm_direction_guard": "direction guard",
    "no_thesis": "no directional thesis",
    "thesis_unread": "model reply unreadable",
    "thesis": "thesis named no direction",
    "rsi_block": "RSI block",
    "order_flow_veto": "order-flow veto",
    "signal_type": "signal family skipped",
    "levels": "levels collapsed on rounding",
    "candle_veto": "candle-pattern veto",
    "cascade_veto": "liquidation-cascade veto",
    "data": "candles unusable",
    "indicators": "indicators not computable",
    "held": "symbol already held",
    "liquidity": "liquidity gate",
    "risk": "risk gate",
    "idea": "offered as an idea",
}

#: The window a reading covers when nobody names one.
DEFAULT_WINDOW_S = 6 * 3600.0


def stage_words(stage: str) -> str:
    return STAGE_WORDS.get(stage, stage)


def _clock_words(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _span_words(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds >= 3600:
        hours = seconds / 3600.0
        return f"{hours:.0f}h" if abs(hours - round(hours)) < 0.05 else f"{hours:.1f}h"
    return f"{int(seconds // 60)}m"


def _key(symbol: Any) -> str:
    return normalize_symbol(str(symbol or "").strip()) if symbol else ""


def _short(reason: Any, limit: int = 160) -> str:
    text = " ".join(str(reason or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(slots=True)
class _Row:
    at: float
    symbol: str
    stage: str
    reason: str
    ref: str = ""
    owner: str = ""


class WhyNotLog:
    """The bounded record. Writers: `analysed`, `amend`, `refused_at_confirm`.
    Reader: `reading`."""

    MAX_ROWS = 30_000
    MAX_CONFIRMS = 2_000
    HORIZON_S = 24 * 3600.0

    def __init__(self, *, clock: Callable[[], float] = time.time,
                 max_rows: Optional[int] = None,
                 max_confirms: Optional[int] = None) -> None:
        self._clock = clock
        self._rows: deque[_Row] = deque(maxlen=max_rows or self.MAX_ROWS)
        self._confirms: deque[_Row] = deque(maxlen=max_confirms or self.MAX_CONFIRMS)
        self.started_at = clock()
        # The newest moment a row was pushed out by the cap rather than by age:
        # before it, the record is no longer whole.
        self._cut_at: Optional[float] = None

    # ── writers ──────────────────────────────────────────────────────────

    def _push(self, rows: deque, row: _Row) -> None:
        while rows and row.at - rows[0].at > self.HORIZON_S:
            rows.popleft()
        if rows.maxlen is not None and len(rows) == rows.maxlen:
            self._cut_at = rows[0].at
        rows.append(row)

    def analysed(self, symbol: Any, stage: str, reason: Any = "",
                 ref: Any = "") -> None:
        """One analysis ended at ``stage``. ``ref`` is the idea's id when the
        analysis produced one, so the engine can amend the row if it drops it."""
        self._push(self._rows, _Row(self._clock(), _key(symbol), str(stage),
                                    _short(reason), str(ref or "")))

    def amend(self, ref: Any, stage: str, reason: Any = "") -> bool:
        """The engine dropped the idea ``ref`` at ``stage``. False when no row
        carries that id (the analysis is older than the record)."""
        ref = str(ref or "")
        if not ref:
            return False
        for row in reversed(self._rows):
            if row.ref == ref:
                row.stage = str(stage)
                row.reason = _short(reason)
                return True
        return False

    def refused_at_confirm(self, symbol: Any, owner: Any, reason: Any) -> None:
        """A confirm by ``owner`` placed nothing."""
        self._push(self._confirms, _Row(self._clock(), _key(symbol), "confirm",
                                        _short(reason), owner=str(owner or "")))

    # ── reader ───────────────────────────────────────────────────────────

    def reading(self, window_s: float = DEFAULT_WINDOW_S, *,
                symbol: Any = None, viewer: Any = "",
                operator: bool = False) -> dict:
        """What the record holds for the last ``window_s`` seconds.

        ``from`` is where the record really reaches back to: the window's
        start, unless this process started later or the cap has cut rows
        after it. ``viewer`` sees their own confirm refusals; ``operator``
        also sees the engine's autonomous ones.
        """
        now = self._clock()
        start = now - float(window_s)
        reaches = max(start, self.started_at,
                      self._cut_at if self._cut_at is not None else start)
        key = _key(symbol)
        rows = [r for r in self._rows
                if r.at >= start and (not key or r.symbol == key)]
        viewer = str(viewer or "")
        owners = ({viewer} if viewer else set()) | ({"auto"} if operator else set())
        confirms = [r for r in self._confirms
                    if r.at >= start and r.owner in owners
                    and (not key or r.symbol == key)]
        return {
            "state": "read" if rows or confirms else "absent",
            "window_s": float(window_s),
            "now": now,
            "from": reaches,
            "whole": reaches <= start,
            "started_at": self.started_at,
            "symbol": key,
            "analyses": len(rows),
            "by_stage": Counter(r.stage for r in rows).most_common(),
            "latest": [_row_dict(r) for r in reversed(rows[-5:])],
            "confirms": [_row_dict(r) for r in reversed(confirms[-5:])],
            "confirm_count": len(confirms),
        }


def _row_dict(row: _Row) -> dict:
    return {"at": row.at, "symbol": row.symbol, "stage": row.stage,
            "reason": row.reason, "owner": row.owner}


#: The sentence for a record that could not be read, by cause.
UNREAD_NOT_THE_BOT = (
    "This process is not the bot, so the bot's record of why it did not trade "
    "was not read here. Nothing can be said about what it skipped.")
UNREAD_NO_RECORD = (
    "The record of why the engine did not trade could not be read, so nothing "
    "can be said about what it skipped. That is not the same as nothing skipped.")


def reading_for(engine: Any, *, symbol: Any = None, user_id: Any = "",
                window_s: float = DEFAULT_WINDOW_S) -> dict:
    """The reading for one caller, or ``{"state": "unread", "why": ...}``.

    Three values: ``read`` (rows in the window), ``absent`` (the record was read
    and holds nothing in the window), ``unread`` (no record, or this engine is
    a reader in a process that is not the bot).
    """
    if getattr(engine, "_state_persistence_detached", False) is True:
        return {"state": "unread", "why": UNREAD_NOT_THE_BOT}
    log = getattr(getattr(engine, "analyzer", None), "why_not", None)
    if not isinstance(log, WhyNotLog):
        return {"state": "unread", "why": UNREAD_NO_RECORD}
    operator = False
    if user_id:
        try:
            operator = engine._is_operator_user(user_id) is True
        except Exception:
            operator = False
    try:
        return log.reading(window_s, symbol=symbol, viewer=user_id,
                           operator=operator)
    except Exception:
        return {"state": "unread", "why": UNREAD_NO_RECORD}


def render(reading: dict) -> str:
    """The reading as a card: counts by stage over a stated window."""
    from functools import partial
    from html import escape as _escape

    escape = partial(_escape, quote=False)
    if reading.get("state") == "unread":
        return f"◇ <b>WHY NO TRADE</b>\n\n<i>{escape(reading.get('why') or UNREAD_NO_RECORD)}</i>"
    window = _span_words(reading["window_s"])
    sym = reading.get("symbol") or ""
    on = f" on {escape(sym)}" if sym else ""
    head = f"◇ <b>WHY NO TRADE{(' — ' + escape(sym)) if sym else ''}</b>"
    if reading["whole"]:
        span = f"in the last {window}"
    else:
        span = (f"since {_clock_words(reading['from'])} (asked for the last "
                f"{window}; the record reaches back only that far)")
    lines = [head, ""]
    n = reading["analyses"]
    if n:
        parts = ", ".join(f"{c} {escape(stage_words(s))}"
                          for s, c in reading["by_stage"])
        lines.append(f"Of {n} {'analysis' if n == 1 else 'analyses'}{on} {span}: {parts}.")
        lines.append("")
        lines.append("<b>Latest</b>")
        for row in reading["latest"]:
            why = f" — {escape(row['reason'])}" if row["reason"] else ""
            lines.append(f"  {_clock_words(row['at'])[11:]}  {escape(row['symbol'])}: "
                         f"{escape(stage_words(row['stage']))}{why}")
    else:
        lines.append(f"No analysis{on} is on record {span}. That is not a "
                     "refusal: nothing was analysed then, or the bot was not "
                     "running.")
    if reading["confirm_count"]:
        lines.append("")
        lines.append(f"<b>Confirms that placed nothing</b> ({reading['confirm_count']})")
        for row in reading["confirms"]:
            who = " (autonomous)" if row["owner"] == "auto" else ""
            lines.append(f"  {_clock_words(row['at'])[11:]}  {escape(row['symbol'] or '?')}"
                         f"{who}: {escape(row['reason'])}")
    lines.append("")
    lines.append(f"<i>Kept in the bot's memory since it started at "
                 f"{_clock_words(reading['started_at'])}; at most a day, newest first.</i>")
    return "\n".join(lines)
