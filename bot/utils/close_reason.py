"""Shared close-reason labelling for stop-side exits.

A LONG stop that sits ABOVE entry (or a SHORT stop BELOW entry) is a
profit-locking stop — it can only get there by trailing/breakeven ratcheting,
because the entry-time validators (``bot/utils/models.py``) forbid an inverted
ORIGINAL stop. When such a stop fills, the position closes in PROFIT, so
recording it as a bare "SL HIT" (which every dashboard/tally reads as a loss)
is wrong. This helper is the single place both close/reconcile paths
(``live_executor`` and ``exchange_sync``) use to tell a profit-locking
(trailing) stop apart from a protective loss stop.

Pure functions, no imports — safe to import from anywhere without a cycle.

Incident: TI-a4ba8a82 — a LONG opened at 0.5638 whose trailing stop had
ratcheted to 0.5679 (above entry) filled for a GAIN, but the reconcile path
labelled it "SL HIT (inferred)".
"""

from __future__ import annotations

from typing import Optional


def is_profit_locking_stop(
    is_long: bool,
    entry_price: float,
    stop_loss: float,
    exit_price: Optional[float] = None,
    trailing_active: bool = False,
    realized_pnl: Optional[float] = None,
) -> bool:
    """True when a stop-side exit is a profit-lock (trailing/breakeven), not a loss.

    Signals, strongest first:
      1. ``trailing_active`` — the position explicitly had a live trailing stop.
      2. ``realized_pnl`` > 0 — the exchange says the close booked a profit; a
         genuine protective stop can only fill at a loss.
      3. Geometry — the stop is on the PROFIT side of entry (LONG: at/above
         entry; SHORT: at/below entry). This is the load-bearing test for
         adopted positions that have no ``trailing_state``.
      4. Fallback — the exit price itself is a gain versus entry.
    """
    if trailing_active:
        return True
    if realized_pnl is not None and realized_pnl > 0:
        return True
    if entry_price and stop_loss and stop_loss > 0:
        if is_long and stop_loss >= entry_price:
            return True
        if (not is_long) and stop_loss <= entry_price:
            return True
    if exit_price is not None and entry_price:
        move = (exit_price - entry_price) if is_long else (entry_price - exit_price)
        if move > 0:
            return True
    return False


# Close reasons that mean the order NEVER FILLED — no capital was ever at
# risk. The live parity report counted 75 of these (expired / canceled /
# price_drift / stale_pending, all $0 PnL) among 292 "trades", diluting the
# headline win rate from ~48% of real fills down to 36%. Performance stats
# must exclude them; trade-history views may still show them.
#
# THE ONE DEFINITION. Three modules each held a copy of this set and each
# called itself the definition — this one, `trade_filter.NON_TRADE_CLOSE_
# REASONS` (the cards') and `live_stats.NON_TRADE_REASONS` (the win rate's)
# — and they differed by three words. `live_executor` writes `close_reason =
# order_status` for canceled / cancelled / rejected / expired, so `rejected`
# is a real non-fill this set did not know and `is_filled_close` counted; and
# it books `stale_pending` and `duplicate_fill_suppressed`, which the other
# two did not know, so those rows counted as trades on every card that read
# them. The other two names are this object now, and the guard reads every
# reason the executor writes for a never-filled order out of its source and
# checks each is here — a writer and a reader that agree by construction.
NON_FILL_CLOSE_REASONS = frozenset({
    "expired", "canceled", "cancelled", "rejected", "price_drift",
    "stale_pending", "duplicate_fill_suppressed",
})


# Close reasons that mean the bot OPENED the position and FLATTENED it seconds
# later for an EXECUTION reason — the post-fill guards: the fill came in at a
# leverage the venue would not correct, the stop could not be placed, the fill
# slipped past the guard. Capital WAS at risk (the fees are real money), so
# `is_filled_close` keeps them, and they are not strategy exits: the strategy
# never chose to get out, the executor refused to stay in. A backtest cannot
# have one, so a live-vs-benchmark comparison that counts them is comparing
# unlike things — the 2026-09-21 parity card carried eleven of them inside
# its 194 "filled trades", its win rate, its PF and its net.
#
# DERIVED, the way NON_FILL_CLOSE_REASONS is: a guard reads every literal
# `reason="…"` the executor hands its OWN close_position and pins the set
# equal, so the fourth flatten guard written tomorrow fails a test rather than
# being counted as a losing trade on every card.
EXECUTION_ABORT_REASONS = frozenset({
    "leverage_overshoot", "sl_placement_failed", "slippage_guard",
})


def is_execution_abort(close_reason: Optional[str]) -> bool:
    """True when the record is a post-fill flatten by one of the executor's
    own guards — real money, not a strategy outcome."""
    return (close_reason or "").strip().lower() in EXECUTION_ABORT_REASONS


def is_filled_close(close_reason: Optional[str], pnl: Optional[float]) -> bool:
    """True when a closed-trade record represents a REAL fill (capital was
    deployed). Zero-PnL records whose close reason is a non-fill marker are
    order-lifecycle noise, not trades. Non-zero PnL always counts — even
    under a non-fill label — so a mislabeled real trade can never be
    silently dropped from the stats."""
    reason = (close_reason or "").strip().lower()
    if reason in NON_FILL_CLOSE_REASONS and abs(pnl or 0.0) < 1e-9:
        return False
    return True


def stop_exit_label(
    is_long: bool,
    entry_price: float,
    stop_loss: float,
    exit_price: Optional[float] = None,
    trailing_active: bool = False,
    realized_pnl: Optional[float] = None,
) -> str:
    """Return "TRAILING SL HIT" for a profit-locking stop, else "SL HIT".

    Callers append their own provenance suffix (e.g. " (inferred)",
    " (exchange)") to preserve the existing label format.
    """
    if is_profit_locking_stop(
        is_long, entry_price, stop_loss, exit_price, trailing_active, realized_pnl
    ):
        return "TRAILING SL HIT"
    return "SL HIT"
