"""A guard abort is not a stop-out, and a stop that could not be PLACED is not
a stop that was HIT.

2026-08-17. `humanize_close_reason` classified close reasons by substring:

    if "SL" in r or "STOP" in r:
        return "🛑", "Stop-Loss Hit"

The hazard was already known — the branch immediately above it checks "TIME"
first, with a comment explaining that a reason containing the word "stop" would
otherwise be misread. That is the same bug, patched one instance at a time,
which is what a substring match over an open vocabulary guarantees.

Four real reasons were wrong at once, in BOTH directions:

    sl_placement_failed -> "Stop-Loss Hit"   the stop could NOT be placed
    slippage_guard      -> "Stop-Loss Hit"   the entry was aborted by a guard
    slow_bleed          -> "Stop-Loss Hit"   "SLOW" contains "SL"
    take_profit         -> "Closed"          "TAKE_PROFIT" contains no "TP"

THE FIRST IS AN INVERSION AND THE WORST. A position closed BECAUSE its stop
could not be placed was reported as that stop being hit. The record then says
the market took the position out, when in truth the bot removed a position it
could not protect. Opposite facts about whose decision it was — and the operator
reads the first as "my strategy lost" and the second as "my venue failed".

The guard aborts matter for the same reason one level down. A stop-loss hit is
the market taking a position out; a guard abort is the bot refusing a fill it
had already made. Both end in a small loss, so the PnL column cannot tell them
apart, and the label was the only thing that could.
"""

from __future__ import annotations

from bot.formatters.signal_card import humanize_close_reason as label


def _text(reason, pnl=-1.0):
    return label(reason, pnl)[1]


# ── the four that were wrong ─────────────────────────────────────────────────

def test_a_stop_that_could_not_be_placed_is_not_a_stop_that_was_hit():
    """The inversion. live_executor closes with this reason when _place_sl_tp
    failed — the position is removed BECAUSE it could not be protected."""
    out = _text("sl_placement_failed")
    assert "Stop-Loss Hit" not in out, (
        f"a failed stop PLACEMENT is being reported as a stop being HIT: {out!r}")
    assert "Could Not Be Placed" in out


def test_guard_aborts_are_not_reported_as_stop_outs():
    for reason in ("slippage_guard", "leverage_overshoot"):
        out = _text(reason)
        assert "Stop-Loss Hit" not in out, (reason, out)
        assert "Aborted" in out, (
            f"{reason} should read as the bot refusing its own fill, not as "
            f"the market taking it out: {out!r}")


def test_a_word_merely_containing_sl_is_not_a_stop_loss():
    assert _text("slow_bleed") != "Stop-Loss Hit"
    assert _text("mislabel") != "Stop-Loss Hit"
    assert _text("sleeve") != "Stop-Loss Hit"


def test_take_profit_spelled_out_is_still_a_take_profit():
    """The under-match: the old rule keyed on the substring "TP", which
    "TAKE_PROFIT" does not contain."""
    assert _text("take_profit", 1.0) == "Take-Profit Hit"
    assert _text("tp_hit", 1.0) == "Take-Profit Hit"


# ── everything that was RIGHT must stay right ────────────────────────────────

def test_the_real_exits_still_classify():
    assert _text("sl_hit") == "Stop-Loss Hit"
    assert _text("stop_loss") == "Stop-Loss Hit"
    assert _text("trailing_stop") == "Trailing Stop Hit"
    assert _text("time_stop") == "Time Stop"
    assert _text("liquidation") == "Liquidated"


def test_order_lifecycle_reasons_do_not_read_as_exits():
    """bot/utils/close_reason.py already excludes these from performance stats
    because no capital was ever at risk. The label must agree — "Closed" for an
    order that never filled reads as a trade that happened."""
    for reason in ("expired", "canceled", "cancelled", "price_drift",
                   "stale_pending", "duplicate_fill_suppressed"):
        out = _text(reason, 0.0)
        assert "Stop-Loss Hit" not in out and "Take-Profit" not in out, (reason, out)
        assert out != "Closed", (
            f"{reason} never filled; 'Closed' reads as a completed trade: {out!r}")


# ── the fallback stays honest for reasons nobody has written yet ─────────────

def test_an_unknown_reason_falls_back_to_the_plain_outcome():
    """It must not invent a mechanism. The old code's failure mode was
    confidently naming a trigger that never fired."""
    assert _text("something_nobody_has_written_yet", -1.0) == "Closed"
    assert _text("something_nobody_has_written_yet", 1.0) == "Closed"
    assert _text("", 0.0) == "Closed"
    assert _text(None, 0.0) == "Closed"


def test_the_fallback_matches_whole_tokens_not_substrings():
    """The property that makes the next unknown reason safe."""
    assert _text("unplanned_slide") == "Closed"       # contains "sl"
    assert _text("stopgap_measure") == "Closed"       # contains "stop"
    assert _text("sl_exit") == "Stop-Loss Hit"        # a real "sl" TOKEN
    assert _text("stop_loss_exit") == "Stop-Loss Hit"


def test_case_and_separator_insensitive():
    for spelling in ("SL_HIT", "sl_hit", "SL-HIT", "  sl_hit  "):
        assert _text(spelling) == "Stop-Loss Hit", spelling


def test_every_reason_the_executor_emits_is_in_the_explicit_table():
    """Reachability, the other way round: the table is only useful if it covers
    what the code actually produces. A reason emitted but absent here falls to
    the heuristic, which is exactly where the four defects came from.
    """
    import re
    from pathlib import Path

    from bot.formatters.signal_card import _CLOSE_REASON_LABELS
    from tests.source_scan import code_only

    src = code_only((Path(__file__).resolve().parent.parent / "bot" / "core"
                     / "live_executor.py").read_text(encoding="utf-8"))
    emitted = set(re.findall(r'reason="([a-z_]+)"', src))
    # bot_auto / emergency style operational reasons are deliberately generic
    # and correctly fall through to the plain outcome.
    interesting = {r for r in emitted
                   if "sl" in r or "stop" in r or "tp" in r or "profit" in r}
    missing = sorted(interesting - set(_CLOSE_REASON_LABELS))
    assert missing == [], (
        f"these close reasons contain sl/stop/tp and are NOT in the explicit "
        f"table, so they are classified by pattern: {missing}")
