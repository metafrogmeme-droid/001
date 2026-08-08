"""
The Daily Duel's Telegram surface.

Two things are pinned here, and the second is the one that matters.

1. The card's honesty. A duel call has four states and only two are results;
   rendering either absence as a result is how a call that is merely waiting
   starts reading as a loss.

2. That the button a player taps is a button the handler can read. CLAUDE.md
   records the failure this exists to prevent: #999 added a per-position
   outcome to the Telegram adoption card, source-scanned it, shipped it — and
   it rendered zero times in production, because the callback received prose
   where the lookup expected symbols. The code was present. It was never
   reached, and no source scan can tell those two apart. Only a round trip can.
"""

from __future__ import annotations

import re

from bot.formatters.duel_card import (
    accuracy_line,
    agent_line,
    call_line,
    marks_line,
    parse_callback_data,
    pick_callback_data,
    render_card,
    render_pick_result,
)


# ── the button actually reaches the handler ─────────────────────────────────

def test_callback_token_round_trips():
    """What the keyboard emits is what the callback parses. The #999 lesson."""
    for round_id in (1, 7, 12345):
        for pick in ("long", "short", "pass"):
            data = pick_callback_data(round_id, pick)
            assert parse_callback_data(data) == (round_id, pick)


def test_callback_token_is_symbols_not_prose():
    data = pick_callback_data(42, "long")
    assert data == "duel:42:long"
    # Telegram caps callback_data at 64 bytes; prose would blow past it and be
    # silently truncated into something that no longer parses.
    assert len(data.encode()) <= 64


def test_a_malformed_token_is_refused_not_guessed():
    for bad in ("", "duel", "duel:x:long", "duel:1:maybe", "duel:1", "other:1:long",
                "duel:1:long:extra", None):
        assert parse_callback_data(bad) is None, bad
    # An unparseable tap must not become a call on a default round in a
    # default direction.


# ── accuracy: absent is never a measurement ─────────────────────────────────

def test_accuracy_with_nothing_settled_is_a_dash_not_zero():
    line = accuracy_line({"pct": None, "scored": 0, "correct": 0, "unresolved": 0})
    assert "—" in line
    assert "0%" not in line, "0% reports a measured failure where there is only absence"


def test_a_day_of_dead_settles_is_not_a_wipeout():
    line = accuracy_line({"pct": None, "scored": 0, "correct": 0, "unresolved": 3})
    assert "—" in line
    assert "0%" not in line
    assert "nothing has settled" in line.lower()


def test_a_real_rate_is_shown_with_its_denominator():
    line = accuracy_line({"pct": 80.0, "scored": 5, "correct": 4, "unresolved": 0})
    assert "80%" in line
    assert "5 settled" in line


def test_unresolved_calls_are_reported_beside_the_rate_not_inside_it():
    line = accuracy_line({"pct": 100.0, "scored": 4, "correct": 4, "unresolved": 2})
    assert "100%" in line
    assert "4 settled" in line
    assert "2 unresolved" in line


# ── marks: a partial total travels with what it excluded ────────────────────

def test_marks_report_what_is_still_running():
    line = marks_line({"marks": 7, "pending": 2, "beat_agent": 1})
    assert "7" in line
    assert "2 still running" in line, "a bare total is a sum over a set that excluded two rows"
    assert "beat the Claw 1" in line


def test_a_zero_total_with_everything_pending_says_so():
    line = marks_line({"marks": 0, "pending": 18, "beat_agent": 0})
    assert "18 still running" in line


def test_marks_absent_entirely_is_a_dash():
    assert "—" in marks_line({})


# ── the agent's stance: three sentences, not two ────────────────────────────

def test_hidden_and_passed_are_different_sentences():
    hidden = agent_line({"symbol": "BTCUSDT"})                 # field absent
    passed = agent_line({"symbol": "BTCUSDT", "agent_direction": None})
    called = agent_line({"symbol": "BTCUSDT", "agent_direction": "long"})
    assert "hidden" in hidden.lower()
    assert "passed" in passed.lower()
    assert "LONG" in called
    # The API omits rather than nulls the field precisely so these can be told
    # apart; collapsing them here would throw that distinction away.
    assert hidden != passed


# ── one call's line ─────────────────────────────────────────────────────────

def test_a_running_call_says_running_and_shows_no_move():
    line = call_line({
        "my_call": {"pick": "long", "pending": True, "outcome": None},
        "my_result": {"state": "unresolved", "marks": 0, "beat_agent": False},
    })
    assert "running" in line.lower()
    assert "%" not in line, "no move may be printed for a price we never read"
    assert "wrong" not in line.lower()


def test_an_unresolved_call_is_never_a_loss():
    line = call_line({
        "my_call": {"pick": "long", "unresolved": True, "outcome": None},
        "my_result": {"state": "unresolved", "marks": 0, "beat_agent": False},
    })
    assert "unresolved" in line.lower()
    assert "wrong" not in line.lower()
    assert "❌" not in line


def test_a_win_over_the_agent_says_so():
    line = call_line({
        "my_call": {"pick": "long", "outcome": "up", "move_pct": 3.21},
        "my_result": {"state": "correct", "marks": 2, "beat_agent": True},
    })
    assert "beat the Claw" in line
    assert "+3.21%" in line


def test_a_push_picks_neither_side():
    line = call_line({
        "my_call": {"pick": "long", "outcome": "flat", "move_pct": 0.01},
        "my_result": {"state": "push", "marks": 0, "beat_agent": False},
    })
    assert "no result" in line.lower()
    assert "wrong" not in line.lower() and "correct" not in line.lower()


def test_an_uncalled_round_has_no_call_line():
    assert call_line({"symbol": "BTCUSDT", "callable": True}) is None


# ── the whole card ──────────────────────────────────────────────────────────

CARD = {
    "day": "2026-08-08",
    "horizon_hours": 24,
    "rounds": [
        {"id": 1, "symbol": "BTCUSDT", "callable": True},
        {"id": 2, "symbol": "ETHUSDT", "callable": True, "agent_direction": "short",
         "my_call": {"pick": "long", "pending": True, "outcome": None},
         "my_result": {"state": "unresolved", "marks": 0, "beat_agent": False}},
    ],
    "accuracy": {"pct": None, "scored": 0, "correct": 0, "unresolved": 1},
    "marks": {"marks": 0, "pending": 1, "beat_agent": 0},
    "streak": {"current": 3, "best": 3},
}


def test_the_card_states_each_round_and_the_record():
    out = render_card(CARD)
    assert "BTCUSDT" in out and "ETHUSDT" in out
    assert "hidden until you call" in out.lower()      # the uncalled round
    assert "The Claw: SHORT" in out                     # the called one
    assert "Streak: 🔥 3 days" in out
    assert "24h" in out


def test_the_card_never_shows_a_zero_percent_for_an_unmeasured_player():
    out = render_card(CARD)
    assert "0%" not in out
    assert "—" in out


def test_an_unreadable_card_says_so_rather_than_showing_an_empty_one():
    out = render_card(None)
    assert "could not be read" in out.lower()
    # "No rounds are open" is a claim about the game that a failed read does
    # not support.
    assert "no rounds" not in out.lower()


def test_the_card_carries_no_money():
    out = render_card(CARD) + render_pick_result(
        {"ok": True, "pick": "long", "symbol": "BTCUSDT", "entry_price": 60000.0,
         "agent_direction": None, "resolves_at": "2026-08-09T00:00:00.000Z"})
    # Substring matching would be wrong here: every symbol on the card ends in
    # USDT, so a bare "usd" check flags BTCUSDT and proves nothing. What §4
    # forbids is an account-money FIELD, so match those as words.
    for banned in ("balance", "equity", "margin", "pnl", "usd_value", "notional",
                   "stake", "wager", "vusdt"):
        assert not re.search(rf"\b{banned}\b", out, re.I), banned
    assert "$" not in out


# ── the reply after a call ──────────────────────────────────────────────────

def test_a_recorded_call_confirms_the_price_it_was_snapped_at():
    out = render_pick_result({
        "ok": True, "pick": "short", "symbol": "SOLUSDT", "entry_price": 150.5,
        "agent_direction": "long", "resolves_at": "2026-08-09T12:00:00.000Z",
    })
    assert "SHORT" in out and "SOLUSDT" in out
    assert "150.5" in out
    assert "The Claw called LONG" in out


def test_an_agent_that_passed_is_reported_as_having_passed():
    out = render_pick_result({
        "ok": True, "pick": "long", "symbol": "BTCUSDT", "entry_price": 1.0,
        "agent_direction": None, "resolves_at": "2026-08-09T00:00:00.000Z",
    })
    assert "passed on this one" in out.lower()


def test_every_refusal_reads_as_a_refusal_never_as_success():
    already = render_pick_result({"error": "You have already called this round."}, 409)
    dead = render_pick_result({"error": "Cannot read a BTCUSDT price right now — "
                                        "your call was not recorded."}, 503)
    broken = render_pick_result(None, None)
    for out in (already, dead, broken):
        assert "✅" not in out, "a refused call must not wear the confirmation glyph"
        assert not re.search(r"call recorded", out, re.I)
    assert "already called" in already.lower()
    assert "not recorded" in dead.lower()


def test_a_missing_ok_flag_is_not_treated_as_success():
    # A body that arrived without the flag is not a confirmation.
    out = render_pick_result({"round_id": 3, "pick": "long"}, 200)
    assert "✅" not in out


def test_an_unreadable_denominator_is_omitted_not_printed_as_zero():
    # "of 0 settled calls" beside a real percentage is a claim, and a wrong
    # one. The two optional clauses may be dropped; the denominator may not be
    # invented.
    line = accuracy_line({"pct": 80.0})
    assert "80%" in line
    assert "0 settled" not in line
