"""Three non-fill close-reason vocabularies, each documented as the one definition.

`close_reason.NON_FILL_CLOSE_REASONS` (the parity report's, six words),
`trade_filter.NON_TRADE_CLOSE_REASONS` (the cards', five) and
`live_stats.NON_TRADE_REASONS` (the win rate's, five) each claimed to be THE
definition of "not a trade" and differed by three words. `live_executor`
writes `close_reason = order_status` for canceled / cancelled / rejected /
expired, so `rejected` was a real non-fill `is_filled_close` did not know and
counted as a fill; and it books `stale_pending` and `duplicate_fill_suppressed`,
which the other two sets did not know, so those rows counted as trades on
every card that read them. Found while fixing #83, whose `never_filled` had
to read two of the three.

One object now, under all three names, and the guard reads every reason the
executor writes for a never-filled order out of its own source and checks each
is in the set — writer and reader agreeing by construction rather than by
three people remembering.
"""
from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

from bot.core.trade_postmortem import never_filled
from bot.formatters import signal_card
from bot.skills import live_stats
from bot.utils import close_reason, trade_filter
from tests.source_scan import code_only

ROOT = pathlib.Path(__file__).resolve().parent.parent
SET = close_reason.NON_FILL_CLOSE_REASONS


def test_the_three_names_are_one_object():
    assert trade_filter.NON_TRADE_CLOSE_REASONS is SET
    assert live_stats.NON_TRADE_REASONS is SET
    assert isinstance(SET, frozenset)


def test_every_non_fill_reason_the_executor_writes_is_in_the_set():
    """Read off the writer, not remembered: the assignments `close_reason =
    "<literal>"` and `cancel_reason = "<literal>"` that book an order that
    never became a position, plus the `order_status in (...)` tuple whose
    members are copied into close_reason verbatim."""
    src = code_only((ROOT / "bot/core/live_executor.py").read_text())
    written = set(re.findall(r"\b(?:close_reason|cancel_reason)\s*=\s*\"([a-z_]+)\"", src))
    for tup in re.findall(r"order_status in \(([^)]*)\)", src):
        written |= set(re.findall(r"\"([a-z_]+)\"", tup))
    # Only the lifecycle reasons are the subject: a real exit ("time_stop",
    # "manual") is written through the same assignment and must NOT be here.
    lifecycle = {w for w in written if w in {
        "canceled", "cancelled", "rejected", "expired", "price_drift",
        "stale_pending", "duplicate_fill_suppressed"}}
    assert lifecycle, "the writer scan found nothing — the pattern is stale"
    missing = lifecycle - SET
    assert not missing, f"the executor books these as never-filled and the set does not know them: {sorted(missing)}"
    # and each of the three the copies disagreed about is really written
    assert {"rejected", "stale_pending", "duplicate_fill_suppressed"} <= written


def _row(reason, pnl=0.0, tid="T-1"):
    return SimpleNamespace(trade_id=tid, close_reason=reason, pnl_usd=pnl, pnl=pnl)


def test_a_rejected_order_is_not_a_fill_anywhere():
    assert close_reason.is_filled_close("rejected", 0.0) is False
    assert trade_filter.is_countable(_row("rejected")) is False
    assert live_stats.real_closed_trades([_row("rejected")]) == []
    assert never_filled(_row("rejected")) is True


def test_stale_and_duplicate_rows_are_not_trades_on_the_cards_either():
    for reason in ("stale_pending", "duplicate_fill_suppressed"):
        assert trade_filter.is_countable(_row(reason)) is False, reason
        assert live_stats.real_closed_trades([_row(reason)]) == [], reason
        assert close_reason.is_filled_close(reason, 0.0) is False, reason
        assert never_filled(_row(reason)) is True, reason


def test_a_mislabelled_real_trade_is_still_never_dropped():
    # A non-zero P&L under a non-fill label is capital that moved; the label is
    # wrong, the money is not. Unchanged by the merge, pinned so it stays.
    assert close_reason.is_filled_close("rejected", -12.5) is True
    assert never_filled(_row("rejected", pnl=-12.5)) is False
    # and a reason nobody recognises is COUNTED (fail-safe toward counting)
    assert trade_filter.is_countable(_row("venue_said_something_new")) is True
    assert close_reason.is_filled_close("venue_said_something_new", 0.0) is True


def test_every_member_reads_as_a_lifecycle_event_on_the_card_not_an_exit():
    labels = signal_card._CLOSE_REASON_LABELS
    for reason in SET:
        assert reason in labels, f"{reason} has no card label and would render as an exit"
        icon, text = labels[reason]
        assert text.startswith(("Order", "Duplicate")), f"{reason}: {text!r} reads as an exit"
        assert not re.search(r"\b(hit|stop|profit)\b", text, re.I), f"{reason}: {text!r}"
