"""The LIVE book a new trade joins, as the risk gates read it.

WHY THIS EXISTS. A `RiskEngine` is built over a `PortfolioTracker`: the PAPER
book. Live fills never touch it (`LiveExecutor` keeps its own positions), and
the only writer into a paper book in a live deployment is the practice fill,
which writes a PERSON's practice book. So on the shared operator engine the
paper book is empty in live mode, and every gate that read it evaluated the
empty book: `CORRELATION: no concentrated exposure`, `PORTFOLIO_EXPOSURE: 7.5%
OK`, `CONCENTRATION_PCA: fewer than 2 open positions`, driven with three live
positions open. The configured caps (`MAX_CORRELATION_PER_GROUP`,
`MAX_UNMAPPED_CORRELATED`, the two exposure caps, correlation sizing) bound
every backtest the benchmark measured, and no live entry.

A row is what those gates need and nothing else: the symbol, the side, the
margin committed and the notional, each None where the venue never stated it.
Rows are built where the positions live (`live_executor.held_rows`) and
handed to `RiskEngine.evaluate(live_book=...)`, so this module imports nothing
and the risk engine never learns the executor's position type.
"""
from __future__ import annotations

from typing import Any, NamedTuple, Optional


class HeldRow(NamedTuple):
    """One open (or resting) position on the book a new trade joins."""

    symbol: str
    direction: str                  # "LONG" / "SHORT"; "" when unreadable
    margin_usd: Optional[float]     # committed margin; None: never stated
    notional_usd: Optional[float]   # entry x quantity; None: never stated


def direction_word(value: Any) -> str:
    """"LONG" / "SHORT" from an enum or a string, or "" for anything else.

    An unreadable side is not a side: a row whose direction nobody could read
    counts toward a group's size and toward no same-direction figure.
    """
    raw = getattr(value, "value", value)
    word = str(raw or "").strip().upper()
    return word if word in ("LONG", "SHORT") else ""


class MarginRead(NamedTuple):
    """The committed margin of a set of rows, and how much of it was read."""

    total: float          # the sum over rows whose margin was stated
    scored: int
    counted: int

    @property
    def complete(self) -> bool:
        return self.scored == self.counted


def margin_read(rows: Any) -> MarginRead:
    """Sum the stated margins, and count what was left unread.

    A row with no stated margin adds nothing to ``total`` and is counted in
    ``counted`` alone, so a caller comparing ``total`` against a cap knows it
    holds a FLOOR whenever ``complete`` is False.
    """
    rows = list(rows or ())
    stated = [r.margin_usd for r in rows if r.margin_usd is not None]
    return MarginRead(total=float(sum(stated)), scored=len(stated),
                      counted=len(rows))
