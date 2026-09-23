"""How much of the book a Guardian verdict actually covers.

Both Guardian readers that take a position book — the Digital Twin's stress
simulator and the Risk Sentinel's crowding assessor — silently DROPPED any row
they could not price, and then published a verdict over what was left as the
verdict on the book.

Driven on a two-position book whose second row is an ordinary ADOPTED position
(`live_executor` writes `entry_price=0.0` and `cost_usd=0.0` for a venue that
stated neither, and the restore path reads them back the same way, so this is
the normal shape rather than a corner):

* ``digital_twin.run`` answered ``position_count 1``, risk **low**, worst
  drawdown 1.05%, ``liquidations []``. With the entry readable it answered
  ``position_count 2``, risk **HIGH**, 36% drawdown, and PENDLE liquidating.
  The verdict flip is leverage-driven, not size-driven — an ordinary $50 of
  margin at 20× does it.
* ``risk_sentinel.analyze`` answered ``gross_notional_usd $30.00`` and
  ``top_group BTC 100.0%``. With the row readable: **$1,029.60** and
  ``ALT 97.1%`` — a 34× understatement of gross exposure, and a concentration
  card naming the WRONG correlation group.

Those six figures are not remembered, they are DRIVEN:
`test_the_docstrings_figures_are_the_ones_a_drive_returns` reads them back out
of this docstring and compares them to a live call. An earlier draft quoted
`$8,430.00 / ALT 99.6% / 281x` from a fixture that had moved — a measurement
written into a docstring as the REASON for a design, which is the shape this
repo keeps finding in prose and had not yet guarded in a module header.

The twin's card then contradicted itself in place, because ``fragile`` walks
every row (it needs only leverage, not a price): *"1 position(s) · worst-case
LOW"* directly above *"Most fragile: PENDLE/USDT"* — naming a position the
scenarios never simulated — above *"🟢 sealed to the evidence chain"*.

**A sum over a set that includes unreadable rows, printed as a total, is the
shapes table verbatim.** What is new here is only where it lands: on a safety
verdict, and in a tamper-evident record.

Two things this module does NOT do, stated because a reading whose coverage is
overstated is the failure the repo spends its guards preventing.

It does not decide WHETHER a row is readable. That is a different question in
each module — the twin needs an entry and a quantity to shock, the sentinel
needs either of two notional bases — so each keeps its own predicate and this
holds only the COUNT. One shape, two readings; folding them would be a second
answer about what "priced" means.

And it is deliberately not `live_executor.CommittedMargin`. That reading is
about MARGIN on `LivePosition` objects; these are notional and simulability on
plain dicts, and a cross-package import for a four-field tuple would buy a
shared name over two genuinely different quantities. What travels between them
is the CLAIM — how much of the book the figure covers — which is exactly what
`committed_margin_note`'s own docstring says is the shareable part.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional


def num(v: Any) -> Optional[float]:
    """A real finite float, or None. NaN and ±inf are not measurements.

    One copy: `digital_twin` and `risk_sentinel` each carried this byte for
    byte, which is the second-copy shape sitting under the two readings this
    module exists to keep honest.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def symbol_of(pos: Any) -> str:
    """The row's name for a card, or a placeholder that is not a name."""
    if not isinstance(pos, dict):
        return "?"
    for key in ("symbol", "asset", "pair"):
        v = pos.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "?"


class BookCoverage(NamedTuple):
    """How many rows a figure was computed over, and which were left out.

    `scored` and `counted` are both real counts, so `scored == 0` with
    `counted > 0` is a READING — nothing in this book could be priced — and is
    a different fact from an empty book, where both are 0.
    """

    scored: int
    counted: int
    unpriced: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.scored == self.counted

    @property
    def nothing_read(self) -> bool:
        """Rows were present and not one of them could be priced."""
        return self.counted > 0 and self.scored == 0

    def as_dict(self) -> dict:
        """The shape that rides on a report, and so onto the evidence chain."""
        return {
            "scored_positions": self.scored,
            "counted_positions": self.counted,
            "unpriced_symbols": list(self.unpriced),
        }


def coverage_of(positions: Any, priced: Any) -> BookCoverage:
    """Count the book against a per-module `priced(pos) -> bool` predicate."""
    rows = [p for p in (positions or [])]
    unpriced = tuple(symbol_of(p) for p in rows if not priced(p))
    return BookCoverage(scored=len(rows) - len(unpriced),
                        counted=len(rows),
                        unpriced=unpriced)


def coverage_note(cov: BookCoverage, *, figure: str = "the figures") -> str:
    """The shortfall said out loud, or "" when there is none.

    Printed ONLY when it bites — a permanent "2 of 2" under every healthy card
    is the row that trains a reader to stop reading the line.

    Unlike `committed_margin_note`, which goes SILENT when nothing was read
    (there the figure beside it is already an em dash, so a caveat would be a
    hedge about a figure that is not there), this speaks loudest in exactly
    that case: a Guardian verdict over zero priced rows still renders as real
    numbers — `gross $0.00`, `drawdown 1.05%`, `liquidations []` — so silence
    there is the all-clear this module exists to remove.
    """
    if cov.complete:
        return ""
    named = ", ".join(cov.unpriced[:4])
    if len(cov.unpriced) > 4:
        named += f" and {len(cov.unpriced) - 4} more"
    if cov.nothing_read:
        return (f"none of the {cov.counted} open position(s) could be priced "
                f"({named}), so {figure} describe no part of this book")
    return (f"{figure} cover {cov.scored} of {cov.counted} open position(s) — "
            f"{named} could not be priced")


def verdict_over(cov: BookCoverage, measured: str) -> str:
    """The risk word a partial book is allowed to publish.

    A verdict computed over ZERO rows is not an all-clear, it is the absence of
    one, so it answers "unknown" — the word every Guardian card's icon map
    already carries for a read that did not happen. A partial book keeps its
    MEASURED word (the rows that were priced really do say that much) and the
    card carries `coverage_note` beside it, which is the omit strategy a
    composite view is owed.
    """
    return "unknown" if cov.nothing_read else measured
