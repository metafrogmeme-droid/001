"""The hold-time card recommended widening a take-profit from fabricated zeros.

`engine.py`'s position-monitor close loop carried its OWN R arithmetic:

    if LONG:  risk = entry - stop
    else:     risk = stop - entry
    final_r = pnl / (risk * qty) if risk > 0 and qty > 0 else 0

That is a SECOND COPY of what `trade_journal.r_multiple_for` exists to
replace, and it carried both halves that seam's own docstring names.

THE `else 0`. Its docstring: "0R is a REAL outcome -- a trade that ended
exactly at its risk distance -- so an unmeasurable close entered the record
indistinguishable from a measured break-even."

AND A STOP OF ZERO BROKE ASYMMETRICALLY, which the journal's version did not
and no reader could have guessed. With `stop_loss = 0.0` -- an orphan or
adopted close, precisely the kind whose stop cannot be read:

    LONG:  risk = entry - 0 = entry > 0   -> guard PASSES -> pnl / (entry*qty)
                                             which is not an R at all
    SHORT: risk = 0 - entry     < 0       -> guard FAILS  -> 0R

One absent field, two different wrong answers, decided by direction.

Those Rs reached `HoldTimeAnalytics`, whose `get_analysis` averages them into
`avg_r_win` -- and whose recommendation fires "Average win is small -- hold
winners longer or widen TP" on `avg_r_win < 1.5`. Advice about take-profit
placement, derived from measurements nobody made.

The collector's type was the other half: it stored `(hours, float, bool)`, so
`is_win = pnl > 0` had already collapsed win/loss/flat/unscored to two before
any reader could ask -- the shape the weekly-review slice removed one card
over. A collector cannot restore a distinction its own type threw away.
"""
from __future__ import annotations

from bot.core.smart_exits import HoldTimeAnalytics
from bot.core.trade_journal import r_multiple_for
from bot.utils.win_rate import (
    OUTCOME_FLAT,
    OUTCOME_LOSS,
    OUTCOME_UNSCORED,
    OUTCOME_WIN,
    outcome_of,
)


def _old_arithmetic(entry, stop, pnl, qty, direction):
    """What the engine used to record. Kept as a PINNED assertion of the
    defect, the way the analyze-budget slice keeps `done - gave_up`."""
    risk = (entry - stop) if direction == "LONG" else (stop - entry)
    return pnl / (risk * qty) if risk > 0 and qty > 0 else 0


# ── 1. one classifier ─────────────────────────────────────────────────────

class TestOneClassifier:
    def test_it_is_four_valued(self):
        assert outcome_of(5.0) == OUTCOME_WIN
        assert outcome_of(-5.0) == OUTCOME_LOSS
        assert outcome_of(0.0) == OUTCOME_FLAT
        assert outcome_of(None) == OUTCOME_UNSCORED

    def test_nan_and_inf_are_not_measurements(self):
        assert outcome_of(float("nan")) == OUTCOME_UNSCORED
        assert outcome_of(float("inf")) == OUTCOME_UNSCORED
        assert outcome_of("junk") == OUTCOME_UNSCORED       # type: ignore[arg-type]

    def test_win_stats_counts_by_it_rather_than_restating_it(self, monkeypatch):
        # A byte-identical copy agrees with every fixture. Patch the rule and
        # a reader that ASKS it follows; one that restated it would not.
        import bot.utils.win_rate as wr
        monkeypatch.setattr(wr, "outcome_of", lambda p: OUTCOME_FLAT)
        s = wr.win_stats([{"pnl": 5.0}, {"pnl": -5.0}])
        assert (s["wins"], s["losses"], s["flat"]) == (0, 0, 2)


# ── 2. the R the record site now asks for ─────────────────────────────────

class TestTheStopOfZero:
    def test_the_old_arithmetic_answered_two_different_wrong_things(self):
        # Pinned, so the defect is NAMED rather than remembered.
        assert _old_arithmetic(100.0, 0.0, 50.0, 10.0, "LONG") == 0.05
        assert _old_arithmetic(100.0, 0.0, 50.0, 10.0, "SHORT") == 0

    def test_the_seam_refuses_both(self):
        assert r_multiple_for(100.0, 0.0, 50.0, 10.0) is None

    def test_a_real_stop_still_measures(self):
        # 50 / (|100-95| * 10) = +1.0R
        assert r_multiple_for(100.0, 95.0, 50.0, 10.0) == 1.0

    def test_an_unread_size_is_not_an_r_either(self):
        assert r_multiple_for(100.0, 95.0, 50.0, None) is None


# ── 3. the collector keeps what it was told ───────────────────────────────

def _book(rows, strategy="swing"):
    a = HoldTimeAnalytics()
    for h, r, p in rows:
        a.record(strategy, h, r, p)
    return a


_WINS_WITH_R = [(3, 2.0, 100.0), (4, 1.8, 90.0), (3, 2.2, 110.0), (4, 1.9, 95.0)]
_WINS_NO_R = [(5, None, 120.0), (6, None, 80.0)]
_LOSSES = [(9, -1.0, -50.0), (10, -1.0, -55.0), (8, -0.9, -40.0), (11, -1.1, -60.0)]


class TestTheAnalysis:
    def test_r_is_averaged_over_the_closes_that_have_one(self):
        a = _book(_WINS_WITH_R + _WINS_NO_R + _LOSSES)
        an = a.get_analysis("swing")
        # 2.0+1.8+2.2+1.9 over FOUR, not over six with two zeros in the mean
        assert an["avg_r_win"] == 1.98
        assert an["r_scored"] == 8 and an["r_total"] == 10

    def test_an_unmeasurable_r_is_not_a_measured_zero(self):
        a = _book(_WINS_WITH_R + _WINS_NO_R + _LOSSES)
        an = a.get_analysis("swing")
        # Six winners; a mean that had counted the two unreadable as 0R:
        assert round((2.0 + 1.8 + 2.2 + 1.9) / 6, 2) == 1.32
        assert an["avg_r_win"] != 1.32

    def test_no_readable_win_r_is_none_not_zero(self):
        a = _book(_WINS_NO_R * 3 + _LOSSES)
        an = a.get_analysis("swing")
        assert an["avg_r_win"] is None
        assert an["r_scored"] == 4 and an["r_total"] == 10

    def test_a_flat_close_is_neither_a_win_nor_a_loss(self):
        a = _book(_WINS_WITH_R + _LOSSES + [(5, 0.0, 0.0), (5, 0.0, 0.0)])
        an = a.get_analysis("swing")
        assert an["flat"] == 2
        assert an["wins"] == 4 and an["losses"] == 4

    def test_the_counts_close(self):
        a = _book(_WINS_WITH_R + _WINS_NO_R + _LOSSES
                  + [(5, 0.0, 0.0), (7, None, None)])
        an = a.get_analysis("swing")
        assert (an["wins"] + an["losses"] + an["flat"]
                + an["unscored"]) == an["total_trades"]

    def test_the_rate_is_over_scored_closes(self):
        # 6 wins, 4 losses, 1 unpriced -> 6/10, not 6/11
        a = _book(_WINS_WITH_R + _WINS_NO_R + _LOSSES + [(7, None, None)])
        an = a.get_analysis("swing")
        assert an["win_rate"] == 60.0


class TestTheRecommendationAbstains:
    def test_it_does_not_advise_on_win_size_it_never_measured(self):
        # Hold times deliberately balanced so neither hold rule fires, which
        # is what leaves the R rule as the one that decides.
        a = _book([(5, None, 100.0)] * 6 + [(5, -1.0, -50.0)] * 4)
        an = a.get_analysis("swing")
        assert an["avg_r_win"] is None
        assert "widen TP" not in an["recommendation"]
        assert "no win had a readable R" in an["recommendation"]

    def test_it_still_advises_when_the_r_was_measured(self):
        a = _book([(5, 0.5, 100.0)] * 6 + [(5, -1.0, -50.0)] * 4)
        an = a.get_analysis("swing")
        assert "widen TP" in an["recommendation"]

    def test_a_hold_time_rule_still_fires_with_no_r_at_all(self):
        # Holds ARE measured, so those two rules are unaffected.
        a = _book([(2, None, 100.0)] * 6 + [(20, None, -50.0)] * 4)
        an = a.get_analysis("swing")
        assert "tighten time exits" in an["recommendation"]


# ── 4. the card, rendered ─────────────────────────────────────────────────

class TestTheCard:
    def _swing(self, a) -> str:
        s = a.summary()
        return s[s.index("SWING"):].split("POSITION")[0]

    def test_it_never_prints_a_formatted_none(self):
        a = _book([(5, None, 100.0)] * 6 + [(5, -1.0, -50.0)] * 4)
        out = self._swing(a)
        assert "None" not in out
        assert "Win R: —" in out

    def test_it_states_the_r_coverage_when_partial(self):
        a = _book(_WINS_WITH_R + _WINS_NO_R + _LOSSES)
        assert "R over 8 of 10 decided closes" in self._swing(a)

    def test_it_says_nothing_about_coverage_when_complete(self):
        a = _book(_WINS_WITH_R + _LOSSES + _WINS_WITH_R[:2])
        assert "R over" not in self._swing(a)

    def test_the_flat_and_unpriced_closes_reach_the_operator(self):
        a = _book(_WINS_WITH_R + _WINS_NO_R + _LOSSES
                  + [(5, 0.0, 0.0), (7, None, None)])
        out = self._swing(a)
        assert "1 flat" in out and "1 unpriced" in out

    def test_a_clean_book_says_neither(self):
        out = self._swing(_book(_WINS_WITH_R + _LOSSES + _WINS_WITH_R[:2]))
        assert "flat" not in out and "unpriced" not in out


# ── 5. the record site asks the seam ──────────────────────────────────────

class TestTheEngineRecordSite:
    """A scan, and it says so: the call sits inside a 9,000-line monitor
    method behind a live close loop, so driving it would mean standing up the
    whole tick. What it pins is the SHAPE -- that the private arithmetic is
    gone and the seam's keyword is passed -- and the behaviour it produces is
    driven above through `r_multiple_for` itself."""

    def _src(self) -> str:
        import bot.core.engine as eng
        from tests.source_scan import code_only
        return code_only(open(eng.__file__, encoding="utf-8").read())

    def test_the_private_r_arithmetic_is_gone(self):
        src = self._src()
        assert "risk * c.quantity" not in src
        assert "risk = c.entry_price - c.stop_loss" not in src

    def test_it_passes_the_pnl_rather_than_a_boolean(self):
        src = self._src()
        i = src.index("self.hold_analytics.record(")
        call = src[i:i + 320]
        assert "pnl=c.pnl" in call
        assert "is_win" not in call
        assert "r_multiple=final_r" in call

    def test_final_r_comes_from_the_seam(self):
        src = self._src()
        assert "final_r = r_multiple_for(" in src
