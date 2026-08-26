"""The /backtest card must not print a funnel that does not close.

WHAT WAS ON THE CARD

    Ideas: 55
    Risk Reject: 26
    ...15 trades

A reader subtracts and gets 29. Fourteen ideas had left the pipeline through
stages the engine COUNTED and the card never showed: `_ideas_rejected_preset`,
entry-timing setups disarmed on invalidation or expiry, and setups still armed
when the run ended. None of them reached `BacktestResult`, so no surface could
have shown them even deliberately.

That is the repo's own rule one level up from a missing measurement: the card
did not print a wrong number, it printed a true subset and let the shape of the
list imply the rest. `Ideas` minus `Risk Reject` reads as trades.

THE DURABLE PART IS THE RECONCILIATION, NOT THE NEW ROWS

Adding three rows fixes today. `pipeline_rows` subtracting its own stages and
reporting the remainder fixes the class: a future exit path that forgets to
report itself surfaces as an explicit UNACCOUNTED line instead of as a silent
gap. Both directions, because a negative remainder means a stage is
double-counted and that is not better news.
"""

from types import SimpleNamespace

import pytest

from bot.backtest import scorecard as sc


def _result(**over):
    """The card's real numbers from the run that exposed this."""
    base = dict(
        total_signals_generated=155,
        total_ideas_rejected_confidence=100,
        total_ideas_rejected_preset=0,
        total_ideas_generated=55,
        total_ideas_rejected_risk=26,
        total_ideas_timing_unfilled=14,
        total_entries_pending_at_end=0,
        total_trades=15,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _rows(r):
    return dict(sc.pipeline_rows(r))


# ── the funnel closes ───────────────────────────────────────────────────────

def test_the_real_run_now_reconciles_and_says_where_the_14_went():
    rows = _rows(_result())
    assert rows["Ideas"] == "55"
    assert rows["  ├ risk reject"] == "26"
    assert rows["  ├ armed, unfilled"] == "14", "the missing stage is still missing"
    assert rows["Trades"] == "15"
    # 55 - 26 - 14 - 0 == 15. Nothing left over, so no alarm row.
    assert "UNACCOUNTED" not in rows
    assert "OVER-COUNTED" not in rows


def test_an_unreported_exit_path_shows_up_as_UNACCOUNTED():
    # Exactly the state the card was in before this change: a stage exists in
    # the engine and reports nothing, so ideas vanish. It must be visible.
    rows = _rows(_result(total_ideas_timing_unfilled=0))
    assert "UNACCOUNTED" in rows, "14 ideas vanished and the card said nothing"
    assert rows["UNACCOUNTED"].startswith("14")
    assert "cannot name" in rows["UNACCOUNTED"]


def test_double_counting_is_reported_too_and_is_not_treated_as_good_news():
    # A negative remainder means a stage is counted twice. Silently clamping it
    # to zero would turn a broken funnel into a tidy one.
    rows = _rows(_result(total_ideas_timing_unfilled=20))
    assert "OVER-COUNTED" in rows
    assert "not trustworthy" in rows["OVER-COUNTED"]
    assert "UNACCOUNTED" not in rows


def test_stages_that_did_not_fire_are_omitted_rather_than_printed_as_zero():
    # A permanent "0" row for a gate that is off in most configurations is
    # noise, and noise is what stops anyone reading a funnel at all.
    rows = _rows(_result(total_ideas_rejected_preset=0, total_entries_pending_at_end=0))
    assert "  └ preset reject" not in rows
    assert "  └ queued at end" not in rows

    fired = _rows(_result(total_ideas_rejected_preset=3, total_ideas_generated=52,
                          total_ideas_timing_unfilled=11))
    assert fired["  └ preset reject"] == "3"


def test_a_missing_field_reads_as_zero_without_raising():
    # An older BacktestResult, or a stub in a test elsewhere, must not crash
    # the operator's card — but it must also not silently balance the funnel.
    bare = SimpleNamespace(total_ideas_generated=10, total_trades=2)
    rows = _rows(bare)
    assert rows["Trades"] == "2"
    assert "UNACCOUNTED" in rows, "8 unexplained ideas were quietly balanced"


# ── the bars stopped lying in two different ways ────────────────────────────

def test_drawdown_is_drawn_so_that_fuller_is_better_like_every_other_row():
    # 0.94% is an excellent drawdown and used to render as the emptiest bar on
    # a card whose other three rows mean the opposite.
    good = sc.drawdown_bar(0.94, 20.0, 8)
    bad = sc.drawdown_bar(19.0, 20.0, 8)
    assert good.count("━") > bad.count("━"), "a better drawdown drew a worse bar"
    assert sc.drawdown_bar(0.0, 20.0, 8) == "━" * 8
    assert sc.drawdown_bar(20.0, 20.0, 8) == "╌" * 8
    assert sc.drawdown_bar(999.0, 20.0, 8) == "╌" * 8, "past worst must not wrap"


def test_an_unreadable_drawdown_is_empty_rather_than_a_full_green_bar():
    for junk in (None, "", "n/a", float("nan")):
        out = sc.drawdown_bar(junk, 20.0, 8)
        assert out.count("━") == 0, f"{junk!r} drew a healthy bar"


def test_a_value_past_the_end_of_its_bar_is_marked():
    # The bar cannot tell 3.0 from 3.76 from 30.0 — and on synthetic data an
    # absurd Sharpe is the single most useful thing on the card.
    assert sc.capped(3.76, 3.0) == "+"
    assert sc.capped(30.0, 3.0) == "+"
    assert sc.capped(3.0, 3.0) == "", "exactly at the cap is not past it"
    assert sc.capped(1.2, 3.0) == ""
    assert sc.capped(None, 3.0) == ""


def test_the_bar_itself_stays_a_fixed_width_and_never_wraps():
    for v in (-5, 0, 0.5, 1, 99):
        assert len(sc.bar(v, 1.0, 8)) == 8
    assert sc.bar(1, 0, 8) == "╌" * 8, "a zero maximum must not divide"


# ── the sample caveat the ratios never carried ──────────────────────────────

def test_a_thin_sample_is_named_under_the_ratios():
    assert "below 10" in sc.sample_note(9)
    assert "shape" in sc.sample_note(9)
    assert sc.sample_note(15) == "", "a sufficient sample needs no caveat"


def test_zero_trades_is_not_a_low_sample_it_is_no_sample():
    note = sc.sample_note(0)
    assert "No trades closed" in note
    assert "not measurements" in note


def test_an_unreadable_trade_count_does_not_pass_as_a_healthy_sample():
    for junk in (None, "many"):
        assert sc.sample_note(junk), f"{junk!r} silently cleared the caveat"


# ── the card is wired to all of it ──────────────────────────────────────────

def test_the_card_uses_the_seam_rather_than_recomputing_it():
    """The renderer runs a live engine, so this is the one property a unit
    test of `scorecard` cannot reach: that the card actually calls it."""
    import inspect

    from bot.skills import skill_registry

    src = inspect.getsource(skill_registry)
    for marker in ("_sc.drawdown_bar(", "_sc.capped(",
                   "_sc.sample_note(", "_sc.pipeline_rows("):
        assert marker in src, f"the card no longer uses {marker}"
    # The old hand-rolled funnel must be gone, not merely bypassed.
    assert "- Risk Reject: <code>" not in src, "the four-line funnel is still being printed"


def test_NaN_never_draws_a_healthy_bar_in_either_argument_order():
    """Found by this file's own drawdown test, in this file's own fix.

    NaN survives `float()`, and then `max(0.0, nan)` is 0.0 while
    `max(nan, 0.0)` is nan. The first silently reports the BEST possible
    drawdown from a number nobody could read; the second raises inside
    `int()`. Both come from a value that should simply have drawn nothing.
    """
    nan = float("nan")
    assert sc.drawdown_bar(nan, 20.0, 8) == "╌" * 8
    assert sc.bar(nan, 1.0, 8) == "╌" * 8
    assert sc.bar(0.5, nan, 8) == "╌" * 8
    assert sc.capped(nan, 3.0) == ""
    assert sc.capped(3.0, nan) == ""
