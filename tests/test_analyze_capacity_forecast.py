"""Say the universe is wider than the budget BEFORE the phase dies for it.

For 37 consecutive ticks the operator saw `Tick phase timed out: analyze
(exceeded its 300s)` and had to work backwards by hand to the actual cause: a
universe that had grown to 161 symbols when the measured throughput only
covered ~90 inside the cap. Every input to that arithmetic was already on the
engine — done, of, elapsed, cap — and none of it was multiplied out.

#984 made the dead phase account for itself and #988 broke the work down by
stage. Both are post-mortem. This is the forecast: at batch start, from the
LAST batch's measured rate, how many of these signals will actually be looked
at.

The honesty constraint is the whole design. There is no default rate and no
placeholder shape — unmeasured means absent, because a forecast off a guessed
rate is a fabricated number on an operator surface, and guessed causes already
shipped two wrong fixes for this exact phase.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine

from tests.source_scan import code_only


def _eng(throughput=None):
    """Bare instrument surface — the forecaster must not need a live engine."""
    e = SimpleNamespace()
    e._analyze_throughput = throughput
    e._forecast_analyze_capacity = (
        RuneClawEngine._forecast_analyze_capacity.__get__(e))
    e._record_analyze_throughput = (
        RuneClawEngine._record_analyze_throughput.__get__(e))
    return e


def _forecast(of, throughput=None, cap=300.0):
    """Forecast against a given phase cap. CONFIG is frozen, so the module
    reference is patched rather than the dataclass field."""
    e = _eng(throughput)
    stub = SimpleNamespace(
        monitoring=SimpleNamespace(tick_phase_timeout_sec=cap))
    with patch.object(engine_mod, "CONFIG", stub):
        return e._forecast_analyze_capacity(of)


class TestUnmeasuredMeansAbsent:
    def test_no_throughput_yet_returns_none(self):
        assert _forecast(161, throughput=None) is None

    def test_a_zero_rate_returns_none_not_infinity(self):
        # done=0 divided into elapsed is not a rate. Reporting it as one would
        # forecast that nothing fits, on every first tick.
        assert _forecast(161, {"per_signal_s": 0.0, "done": 0, "of": 161}) is None

    def test_no_phase_cap_returns_none(self):
        assert _forecast(
            161, {"per_signal_s": 3.3, "done": 90, "of": 161}, cap=0.0) is None

    def test_empty_batch_returns_none(self):
        assert _forecast(0, {"per_signal_s": 3.3, "done": 90, "of": 161}) is None


class TestTheLiveIncidentArithmetic:
    """The numbers that actually occurred: 90 of 161 in 300s, ~3.3s/signal."""

    def test_it_names_the_shortfall_the_operator_had_to_derive(self):
        rec = _forecast(161, {"per_signal_s": 300.0 / 90.0, "done": 90, "of": 161})
        assert rec["of"] == 161
        assert rec["fits"] == 90              # cap / per_signal
        assert rec["shortfall"] == 71         # never looked at
        assert 520 < rec["needed_s"] < 540    # ~531s against a 300s cap

    def test_the_operators_chosen_fix_forecasts_as_fitting(self):
        # TOP_MOVERS_COUNT=70 measured ~231s of a 300s cap. A forecast that
        # still cried shortfall at the setting that FIXED it would be noise.
        rec = _forecast(70, {"per_signal_s": 231.0 / 70.0, "done": 70, "of": 70})
        assert rec["shortfall"] == 0

    def test_shortfall_is_a_count_not_a_percentage(self):
        # "90 of 161 (57%)" was read as progress rather than as a shortfall.
        rec = _forecast(161, {"per_signal_s": 3.3, "done": 90, "of": 161})
        assert isinstance(rec["shortfall"], int)
        assert rec["shortfall"] == rec["of"] - rec["fits"]

    def test_shortfall_never_goes_negative(self):
        # A batch far inside budget must report 0, not a negative surplus.
        rec = _forecast(5, {"per_signal_s": 0.1, "done": 70, "of": 70})
        assert rec["shortfall"] == 0
        assert rec["fits"] >= rec["of"]


class TestThroughputMeasurement:
    def test_a_timed_out_batch_still_yields_a_usable_rate(self):
        # 90 of 161 in 300s measured a REAL rate for those 90, under exactly
        # the conditions worth forecasting. Discarding it because the phase
        # died would leave the next tick guessing again.
        e = _eng()
        e._record_analyze_throughput(90, 161, 300.0)
        assert e._analyze_throughput["per_signal_s"] == 300.0 / 90.0
        assert e._analyze_throughput["done"] == 90
        assert e._analyze_throughput["of"] == 161

    def test_zero_completed_records_nothing(self):
        e = _eng()
        e._record_analyze_throughput(0, 161, 300.0)
        assert e._analyze_throughput is None

    def test_zero_elapsed_records_nothing(self):
        e = _eng()
        e._record_analyze_throughput(90, 161, 0.0)
        assert e._analyze_throughput is None

    def test_the_rate_carries_concurrency_rather_than_per_call_latency(self):
        # done/elapsed is effective wall-clock throughput, so the 12-way
        # concurrency is already in it. Hand-dividing per-analysis latency
        # lands ~12x off — which is how a serial-chain theory survived a whole
        # round of fixes.
        e = _eng()
        e._record_analyze_throughput(120, 120, 120.0)   # 12 concurrent, 12s each
        assert e._analyze_throughput["per_signal_s"] == 1.0


class TestItReachesTheOperator:
    def test_the_phase_cap_is_read_from_where_phase_enforces_it(self):
        # analysis_timeout_sec bounds ONE analysis; the phase cap kills the
        # batch. Confusing them is what made "exceeded its 300s" read as a
        # per-symbol limit.
        src = code_only(open("bot/core/engine.py", encoding="utf-8").read())
        fn = src.split("def _forecast_analyze_capacity", 1)[1].split(
            "def _record_analyze_throughput", 1)[0]
        assert "tick_phase_timeout_sec" in fn
        assert "analysis_timeout_sec" not in fn

    def test_health_reports_the_forecast(self):
        src = code_only(open("api_bridge.py", encoding="utf-8").read())
        assert "analyze_capacity" in src

    def test_health_omits_it_rather_than_zeroing_it(self):
        src = code_only(open("api_bridge.py", encoding="utf-8").read())
        assert "if _analyze_capacity(engine) is not None else {}" in src

    def test_status_card_states_the_two_levers(self):
        src = code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert "Analyze budget short" in src
        assert "TOP_MOVERS_COUNT" in src
        assert "SCAN_ANALYSIS_CONCURRENCY" in src

    def test_status_card_stays_quiet_when_the_budget_fits(self):
        # A warning that fires on healthy ticks gets ignored on the tick that
        # matters.
        src = code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())
        assert 'get("shortfall") or 0) > 0' in src

    def test_no_prose_symbol_count_in_the_scanner_docstring(self):
        # The docstring said top_movers_count was "(80)"; the default is 200.
        # A number written in prose beside the config that owns it goes stale
        # and then gets quoted in a live diagnosis, which already happened
        # once with the bridge's universe list.
        src = open("bot/core/market_scanner.py", encoding="utf-8").read()
        assert "top_movers_count`` (80)" not in src
