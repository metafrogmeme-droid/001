"""Audit batch 4: the OTHER surface making a claim, and an R with no 1R.

Two findings, one of which is a correction to batch 1 rather than a new site.

**The corollary rule, applied to my own fix.** Batch 1 taught the Telegram
emergency card to count accounts that actually FLATTENED rather than accounts
attempted, because `close_all_positions` reports a per-position failure in its
TEXT and never raises. `_maybe_flatten_web_requests` — the WEBSITE emergency
stop — makes the identical claim and was missed: it acked `ok: True` with
`closed: len(messages)`, so the website deleted the `pending_flatten` row and
reported success over exposure that is still open. The ack handler was already
built for the truth (`if (!a.ok) continue;` leaves the row for the next poll);
the bot simply never sent `ok: false`.

**An R multiple computed from a stop that does not exist.** `r_denominator`
fell back to `abs(entry - stop)`, and an adopted orphan carries `stop_loss=0`
until its safety default lands — so 1R became the entry price itself and a 3%
move read as 0.03R. Worse, the caller wrote `r_mult = pnl_raw / risk if risk >
0 else 0.0`, which is not a guard: 0.0 is a MEASURED value to
`should_time_exit`, `check_signal_hold_limit` and `should_volume_decay_exit`,
all of which close a flat trade held long enough. A position whose R nobody
could compute was force-closed at market on that number.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from bot.core.position_telemetry import r_denominator
from bot.formatters.drift_offer import flatten_account_ok, flatten_failed_messages
from tests.source_scan import code_only

# ── the flatten predicate, now shared by three surfaces ────────────────

def test_the_failure_messages_are_named_not_just_counted():
    msgs = ["Closed BTC", "Failed to close ETH: 502", "Closed SOL"]
    assert flatten_failed_messages(msgs) == ["Failed to close ETH: 502"]
    assert flatten_failed_messages([]) == []
    assert flatten_failed_messages(None) == []


def test_the_whole_account_predicate_is_the_same_rule():
    # One predicate, so the card, the engine rollup and the website ack cannot
    # drift apart on what counts as flat.
    for msgs in ([], ["Closed BTC"], ["Failed to close ETH: 502"],
                 ["close_all_positions failed: timeout"],
                 ["Closed BTC", "Failed to close ETH"]):
        assert flatten_account_ok(msgs) is (not flatten_failed_messages(msgs))


def test_a_close_all_wide_failure_is_a_failure():
    assert flatten_account_ok(["close_all_positions failed: timeout"]) is False


# ── the website emergency stop acks what actually closed ───────────────

def _web_flatten_code() -> str:
    from bot.core.engine import RuneClawEngine
    return code_only(inspect.getsource(RuneClawEngine._maybe_flatten_web_requests))


def test_the_website_ack_is_computed_not_hardcoded():
    code = _web_flatten_code()
    assert "flatten_failed_messages" in code
    assert '"ok": not _failed' in code
    # The literal that deleted the row regardless of what happened. The
    # per-user SKIP branch above legitimately acks True (nothing was attempted
    # on that account), so this asserts on the CLOSE branch's own line.
    i = code.index("close_all_positions(")
    assert '"ok": True' not in code[i:], (
        "the branch that actually closes must not hardcode success")


def test_the_acked_count_excludes_the_failures():
    code = _web_flatten_code()
    assert "_closed = len(_msgs) - len(_failed)" in code
    assert "len(closed)" not in code, (
        "a count of MESSAGES is not a count of closed positions")


def test_a_partial_flatten_is_audited_as_partial():
    code = _web_flatten_code()
    i = code.index("close_all_positions(")
    block = code[i:i + 1400]
    assert 'result="OK" if not _failed else "PARTIAL"' in block


def test_the_website_ack_handler_retries_a_not_ok_row():
    # Why `ok: False` is the right signal rather than a new field: the web side
    # already leaves the row in place for the next poll.
    from pathlib import Path
    src = Path("app/routes/sync.js").read_text(encoding="utf-8")
    i = src.index("router.post('/flatten/ack'")
    block = src[i:i + 700]
    # `if (!a || a.user_id == null || !a.ok) continue;` — spaces stripped from
    # BOTH sides, or the needle never matches the haystack.
    assert "!a.ok)continue" in block.replace(" ", "")


# ── an R multiple needs a real 1R ──────────────────────────────────────

def _pos(entry, stop, trailing=None):
    return SimpleNamespace(entry_price=entry, stop_loss=stop,
                           trailing_state=trailing)


def test_no_stop_is_unknown_not_a_1r_of_the_entire_entry_price():
    # THE BUG. An adopted orphan carries stop_loss=0 until its safety default
    # lands; abs(100 - 0) made 1R = 100, so a 3% move read as 0.03R.
    assert r_denominator(_pos(100.0, 0.0, None)) == 0.0
    assert r_denominator(_pos(100.0, -1.0, None)) == 0.0


def test_a_stored_initial_risk_still_wins_even_without_a_stop():
    # The control that matters most: a real trailing state is a MEASURED 1R,
    # and a ratcheted stop must not collapse it.
    assert r_denominator(_pos(100.0, 0.0, {"initial_risk": 2.5})) == 2.5


def test_a_real_stop_is_unchanged():
    assert r_denominator(_pos(100.0, 98.0, None)) == 2.0
    assert r_denominator(_pos(100.0, 100.0, None)) == 0.0


def test_an_unknown_r_skips_the_r_based_rules_and_keeps_the_stop_check():
    from bot.core.engine import RuneClawEngine
    code = code_only(inspect.getsource(
        RuneClawEngine._evaluate_live_smart_exits))
    i = code.index("risk = r_denominator(pos)")
    block = code[i:i + 2600]
    assert "else None" in block, "an unmeasurable R must be None, not 0.0"
    assert "if r_mult is None:" in block
    # All three R-based rules sit inside the else.
    guard = block.index("if r_mult is None:")
    for rule in ("should_time_exit(", "check_signal_hold_limit(",
                 "should_volume_decay_exit("):
        assert block.index(rule, guard) > guard, rule
    # The VWAP rule takes no R and must still run.
    assert "check_vwap_reversion_exit(" in code


def test_the_unknown_case_does_not_feed_the_entry_breaker():
    # record_warning() trips the warning-rate breaker after five of a key in an
    # hour. A persistent orphan would fire every tick and halt ENTRIES for a
    # condition that is only "these exit rules are inert on one position".
    from bot.core.engine import RuneClawEngine
    code = code_only(inspect.getsource(
        RuneClawEngine._evaluate_live_smart_exits))
    assert "record_warning" not in code
    assert "_r_unknown_logged" in code, "log it once per symbol instead"
