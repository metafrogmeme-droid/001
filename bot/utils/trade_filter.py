"""What counts as a trade — one definition, used by every surface.

The website reported Net PnL +10.19 over 50 trades at 46% while Telegram
reported -10.74 over 95 at 57%, for the same account on the same day. Not a
sync lag: the two were measuring different SETS and both called the result
"Net PnL".

Telegram excluded orphan-adopted and diagnostic-injected positions, and
closes that are not trades at all (cancelled, expired, price-drift, rejected
— an order that never became a position has no P&L to report). The website
summed every CLOSED row it had been sent, because the sync sent all of them
and the web schema stores neither `close_reason` nor `trade_id`, so it could
not have filtered even if it wanted to.

Filtering at the SOURCE is what makes the two agree by construction: the
website is sent only countable trades, so its plain SUM is right without
needing to know the rule. That also means no schema change and no second
implementation to drift.

The rule itself was already in telegram_handler, written out inline at four
call sites. Duplicated logic is how definitions diverge, so it lives here now
and every caller reads from it.
"""

from __future__ import annotations

from typing import Any, Iterable

from bot.utils.close_reason import NON_FILL_CLOSE_REASONS

# Orphan-adopted and diagnostic-injected positions. These are not decisions
# the engine made, so they do not belong in the engine's record.
ORPHAN_PREFIXES: tuple[str, ...] = ("TI-adopted", "TI-injected")

# Closes where no position ever existed. An order that was cancelled, expired,
# rejected, or abandoned on price drift has no P&L and no outcome — counting
# it as a trade dilutes the win rate with events that were never trades.
#
# The same OBJECT as `close_reason.NON_FILL_CLOSE_REASONS`, kept under the
# name the six card readers import. This used to be a second copy, five words
# to the other's six, so `stale_pending` and `duplicate_fill_suppressed` rows
# counted as trades here and `rejected` counted as a fill there.
NON_TRADE_CLOSE_REASONS: frozenset[str] = NON_FILL_CLOSE_REASONS


def _field(trade: Any, name: str) -> Any:
    """One field of a closed trade, whether it is an object or a dict.

    `getattr` on a dict answers its default for every key, so the rule read
    a dict as a trade with no id and no reason and counted it, whatever it
    held. The live website sync and the scan payload both hand it dicts (the
    engine's rows, and the closed-trade file), so on those two paths the
    rule had never run.
    """
    if isinstance(trade, dict):
        return trade.get(name)
    return getattr(trade, name, None)


def is_adopted(trade: Any) -> bool:
    """Was this position adopted or injected rather than opened by a decision?"""
    tid = str(_field(trade, "trade_id") or "")
    return any(tid.startswith(p) for p in ORPHAN_PREFIXES)


def is_countable(trade: Any) -> bool:
    """Does this closed position belong in P&L, win rate and trade count?

    Fail-SAFE toward counting: a trade with no readable close_reason is
    counted. Under-reporting a real loss is worse than including an oddity,
    and the absence of a reason is not evidence that nothing happened.
    """
    if is_adopted(trade):
        return False
    reason = str(_field(trade, "close_reason") or "").strip().lower()
    return reason not in NON_TRADE_CLOSE_REASONS


def countable(trades: Iterable[Any]) -> list:
    """The subset every surface should report on."""
    return [t for t in (trades or []) if is_countable(t)]


def adopted(trades: Iterable[Any]) -> list:
    """The adopted/injected subset — reported SEPARATELY where it is shown at
    all, never folded into the engine's own record."""
    return [t for t in (trades or []) if is_adopted(t)]
