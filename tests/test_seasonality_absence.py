"""Seasonality: an unobserved hour is not a flat hour.

THE DEFECT, measured before the fix. Every bucket was seeded with 0.0::

    hour_avgs[h] = np.mean(vals) if vals else 0.0

Driven with the minimum input the function accepts — 48 hourly candles, two
calendar days, every observed hour negative — it answered:

    days actually observed : Mon, Tue (2 of 7)
    best_day it reported   : Wednesday          <- never observed
    current_day            : Sunday -> neutral (avg 0.0)
    recommendation         : NEUTRAL            <- scored from invented zeros

`best_day` named a day the data had never seen, because a fabricated 0.0 beats
a real negative return. Three rows of CLAUDE.md's table at once.

The module had NO caller and NO test — nothing imported it but the two
baseline files, which are prose. That is why a defect this loud survived: a
green suite says nothing about code the suite never runs.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.core.seasonality import (
    MIN_BUCKET_SAMPLES,
    UNKNOWN,
    analyze_seasonality,
    detect_session,
)

MONDAY = datetime(2026, 8, 24, tzinfo=timezone.utc)


def hourly(n, start=MONDAY, ret_pct=-0.5):
    """`n` hourly candles from `start`, each with the same signed return."""
    ms = int(start.timestamp() * 1000)
    out = []
    for i in range(n):
        o = 100.0
        c = o * (1 + ret_pct / 100.0)
        out.append([ms + i * 3_600_000, o, max(o, c), min(o, c), c, 1.0])
    return out


class TestTheRegression:
    """The exact scenario that produced the false claims."""

    def test_best_day_can_only_name_a_day_that_was_observed(self):
        r = analyze_seasonality(
            hourly(48), current_time=datetime(2026, 8, 30, 3, tzinfo=timezone.utc))
        assert r.days_observed == 2, "two calendar days of candles"
        assert r.best_day in ("Monday", "Tuesday"), (
            f"best_day={r.best_day} was never observed — a fabricated 0.0 beat "
            f"a real negative return")
        assert r.worst_day in ("Monday", "Tuesday")

    def test_an_unobserved_current_day_is_unknown_not_neutral(self):
        # Sunday appears in no candle. "We have not seen this day" and "this
        # day is flat" are different sentences.
        r = analyze_seasonality(
            hourly(48), current_time=datetime(2026, 8, 30, 3, tzinfo=timezone.utc))
        assert r.current_day == "Sunday"
        assert r.samples_current_day == 0
        assert r.current_day_bias == UNKNOWN
        assert r.current_day_avg_return is None, "never 0.0 for an absent read"

    def test_a_verdict_is_not_manufactured_from_missing_halves(self):
        r = analyze_seasonality(
            hourly(48), current_time=datetime(2026, 8, 30, 3, tzinfo=timezone.utc))
        assert r.recommendation == "UNKNOWN"
        assert r.seasonality_score is None, (
            "0.0 here reads as a measured neutral market")


class TestUnknownIsNotNeutral:
    def test_too_few_samples_is_unknown(self):
        # One candle's "average" is that candle. Calling it a bullish hour is
        # noise with a label on it.
        r = analyze_seasonality(hourly(48, ret_pct=2.0),
                                current_time=MONDAY.replace(hour=0))
        assert r.samples_current_hour < MIN_BUCKET_SAMPLES
        assert r.current_hour_bias == UNKNOWN

    def test_enough_samples_earns_a_real_bias(self):
        # 8 days of candles ⇒ 8 samples in every hour bucket, all positive.
        r = analyze_seasonality(hourly(24 * 8, ret_pct=2.0),
                                current_time=MONDAY.replace(hour=5))
        assert r.samples_current_hour >= MIN_BUCKET_SAMPLES
        assert r.current_hour_bias == "bullish"
        assert r.recommendation == "FAVORABLE"

    def test_a_genuine_flat_hour_still_reads_neutral(self):
        # The mirror. A measured zero is a real answer and must NOT be
        # collapsed into unknown — that would be the same error inverted.
        r = analyze_seasonality(hourly(24 * 8, ret_pct=0.0),
                                current_time=MONDAY.replace(hour=5))
        assert r.samples_current_hour >= MIN_BUCKET_SAMPLES
        assert r.current_hour_bias == "neutral"
        assert r.current_hour_avg_return == 0.0, "a measured zero survives"
        assert r.recommendation == "NEUTRAL"

    def test_a_measured_zero_and_an_absent_read_are_distinguishable(self):
        measured = analyze_seasonality(hourly(24 * 8, ret_pct=0.0),
                                       current_time=MONDAY.replace(hour=5))
        absent = analyze_seasonality(
            hourly(48), current_time=datetime(2026, 8, 30, 3, tzinfo=timezone.utc))
        assert measured.current_hour_avg_return == 0.0
        assert absent.current_day_avg_return is None
        assert measured.current_hour_bias != absent.current_day_bias


class TestTheCompositeUsesWhatItHas:
    def test_a_known_half_still_scores_rather_than_being_dragged_to_zero(self):
        # Treating an unmeasured half as 0.0 pulled the composite toward
        # neutral and then printed NEUTRAL as a verdict — confidence borrowed
        # from the missing half.
        r = analyze_seasonality(hourly(24 * 8, ret_pct=2.0),
                                current_time=MONDAY.replace(hour=5))
        assert r.seasonality_score is not None
        assert r.seasonality_score > 0.2

    def test_no_known_component_means_no_score(self):
        r = analyze_seasonality(
            hourly(48), current_time=datetime(2026, 8, 30, 3, tzinfo=timezone.utc))
        assert r.seasonality_score is None


class TestBoundaries:
    def test_below_the_minimum_window_returns_nothing(self):
        assert analyze_seasonality(hourly(47)) is None
        assert analyze_seasonality([]) is None

    def test_malformed_candles_are_skipped_not_counted(self):
        good = hourly(24 * 8, ret_pct=2.0)
        junk = [[None, 1, 1, 1, 1, 1], ["x"], [], [1, 0, 0, 0, 0, 0]]
        r = analyze_seasonality(good + junk, current_time=MONDAY.replace(hour=5))
        assert r is not None and r.current_hour_bias == "bullish"

    @pytest.mark.parametrize("hour,expected", [
        (3, "ASIA"),        # ASIA 0-8 only
        (10, "EUROPE"),     # EUROPE 7-16; ASIA has ended, US has not begun
        (7, "OVERLAP"),     # ASIA 0-8 and EUROPE 7-16 both cover 07:00
        (14, "OVERLAP"),    # EUROPE 7-16 and US 13-22
        (20, "US"),
        (23, "OFF"),
    ])
    def test_sessions(self, hour, expected):
        assert detect_session(hour) == expected


class TestItIsActuallyReached:
    """The module had no caller at all — that is why the defect survived."""

    def test_the_analyzer_attaches_seasonality_context(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8")
        code = "\n".join(ln for ln in src.split("\n")
                         if not ln.strip().startswith("#"))
        assert "from bot.core.seasonality import analyze_seasonality" in code
        assert 'indicators["seasonality"]' in code

    def test_it_is_context_only_and_not_a_vote(self):
        """It must not reach the confluence score.

        Observation-only is the whole basis for wiring it ungated; if it ever
        becomes a voter it needs the gate-then-shadow treatment its neighbours
        get, and this test is where that conversation starts.
        """
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8")
        code = "\n".join(ln for ln in src.split("\n")
                         if not ln.strip().startswith("#"))
        call = code[code.find("confluence = self._score_confluence("):]
        call = call[:call.find(")")]
        assert "seasonal" not in call.lower(), (
            "seasonality reached _score_confluence — it is context, not a vote")
