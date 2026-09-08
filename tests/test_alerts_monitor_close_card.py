"""A close card may only picture the close its message is about.

`_last_close_data` is a shared last-write-wins slot written ONLY on a
completed close, and `_on_trade_closed` renders a PNG from it whenever one is
there. Two ways that goes wrong, and the notifier has to rule out both:

  * the slot holds ANOTHER position's close (two closes in one sweep — the
    2026-07-07 incident: "VETUSDT CLOSED" captioned over a BTC card);
  * the message is not a completed close at all — a rejected flatten, one
    close_position kept open, an unprotected escalation — where the slot
    holds an EARLIER close of possibly the same symbol, the symbol guard
    passes, and a green card swallows the only warning that a position is
    live and unprotected.

The decision was inline in a closure, pinned by a source window asserting the
guard's literals appear near each other. `if close_card_is_wrong(msg) and
False:` keeps every one of those literals. `close_card_for` is the seam, and
this file drives it.
"""

from __future__ import annotations

import pytest

from bot.core.order_state import CLOSE_CARD_NOT_RENDERED, CLOSE_KEPT_OPEN_MARKERS
from bot.skills.alerts_monitor import close_card_for

SLOT = {"symbol": "BTC/USDT:USDT", "direction": "LONG", "pnl_usd": 12.5,
        "reason": "TP HIT", "trade_id": "T1"}

CLOSED = "CLOSED LONG BTC/USDT:USDT (TP HIT)\nEntry: $100.0000 → Exit: $110.0000"


def test_a_completed_close_of_this_symbol_gets_its_card():
    assert close_card_for(CLOSED, SLOT) is SLOT


def test_a_close_of_another_symbol_does_not_wear_this_slots_card():
    """The 2026-07-07 incident: two closes in one sweep, and the second
    message rendered the first one's card."""
    assert close_card_for("CLOSED SHORT ETH/USDT:USDT (SL HIT)", SLOT) is None


@pytest.mark.parametrize("marker", CLOSE_KEPT_OPEN_MARKERS)
def test_no_answer_that_kept_the_position_wears_a_card(marker):
    """Every one of close_position's own kept-open answers, as the monitor
    loop forwards them: raw, same symbol, lower-case "kept OPEN". The
    hand-typed list this replaced knew only the guards' upper-case headings
    and let all of these through."""
    msg = f"⚠️ {marker}: LONG BTC/USDT:USDT\nkept OPEN, NOT re-protected. Review on Bitget."
    assert close_card_for(msg, SLOT) is None


@pytest.mark.parametrize("heading", [
    "🚨 URGENT: BTC/USDT:USDT is LIVE with NO stop-loss and the safety close FAILED.",
    "🚨 BTC/USDT:USDT KEPT OPEN: the stop-loss could not be placed",
    "🚨 <b>BTC/USDT:USDT IS OVER-LEVERED — CLOSE DID NOT COMPLETE</b>",
    "🚨 <b>UNPROTECTED POSITION — BTC/USDT:USDT LONG</b>\nNo exchange stop-loss could be placed",
    "CLOSE FAILED for T1: bitget 5xx on BTC/USDT:USDT",
    "⚠️ ENTRY ABORTED: BTC/USDT:USDT filled but the stop-loss could not be placed",
])
def test_no_guard_heading_wears_a_card(heading):
    assert close_card_for(heading, SLOT) is None


def test_a_close_booked_without_a_card_does_not_borrow_an_earlier_one():
    msg = (f"✅ CLOSED LONG BTC/USDT:USDT — booked; {CLOSE_CARD_NOT_RENDERED} "
           f"(KeyError). The ledger row is written.")
    assert close_card_for(msg, SLOT) is None


@pytest.mark.parametrize("empty", [None, {}, "", [], 0])
def test_no_slot_is_no_card(empty):
    assert close_card_for(CLOSED, empty) is None


def test_a_slot_that_states_no_symbol_is_not_matched_away():
    """A slot with no symbol cannot be shown to be the wrong one. It is the
    pre-existing reading and it stays: the suppression above is what keeps a
    not-a-close message from wearing it."""
    assert close_card_for(CLOSED, {"pnl_usd": 1.0}) is not None
    assert close_card_for("CLOSE FAILED for T1: venue 5xx", {"pnl_usd": 1.0}) is None


def test_an_unreadable_message_is_never_a_close():
    assert close_card_for(None, SLOT) is None
