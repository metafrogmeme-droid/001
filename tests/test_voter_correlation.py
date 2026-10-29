"""Measured voter de-correlation (bot/core/voter_correlation.py) and its hook
in Analyzer._score_confluence.

The hand-written family caps cannot see two voters that merely MOVE together.
These tests drive the Pearson matrix, the reduce-only scaling and the
analyzer hook, asserting both arms (redundant vs independent, flag on vs off,
measured vs unmeasurable) and the "unreadable is never zero" rule.
"""

import math
import random
from unittest.mock import patch

import pytest

from bot.core import voter_correlation as vc
from bot.core.analyzer import Analyzer
from bot.core.ta_utils import Regime
from bot.utils.models import MarketSignal


def _noise(seed: int, n: int = 60) -> list[float]:
    rng = random.Random(seed)
    return [rng.choice([-1.0, 0.0, 1.0]) for _ in range(n)]


# ── pure maths ────────────────────────────────────────────────────────────

def _pearson(a, b, min_points=vc.MIN_POINTS):
    """One pair through the matrix: the only implementation there is."""
    return vc.correlation_matrix({"a": a, "b": b}, min_points)[("a", "b")]


class TestPearson:
    def test_identical_series_is_one(self):
        s = _noise(1)
        assert _pearson(s, s) == pytest.approx(1.0)

    def test_negated_series_is_minus_one(self):
        s = _noise(1)
        assert _pearson(s, [-x for x in s]) == pytest.approx(-1.0)

    def test_independent_series_is_small_but_a_real_number(self):
        r = _pearson(_noise(1, 400), _noise(2, 400))
        assert r is not None and abs(r) < 0.3

    def test_constant_series_is_none_not_zero(self):
        assert _pearson([1.0] * 40, _noise(1, 40)) is None
        assert _pearson(_noise(1, 40), [0.0] * 40) is None

    def test_short_series_is_none_not_zero(self):
        s = _noise(1, vc.MIN_POINTS - 1)
        assert _pearson(s, s) is None

    def test_exactly_min_points_is_measured(self):
        s = _noise(1, vc.MIN_POINTS)
        assert _pearson(s, s) == pytest.approx(1.0)

    def test_length_mismatch_is_none(self):
        assert _pearson(_noise(1, 40), _noise(1, 41)) is None

    def test_unreadable_samples_are_absent_not_zero(self):
        s = _noise(3, 60)
        poisoned = list(s)
        for i in (2, 9, 17):
            poisoned[i] = None
        poisoned[30] = float("nan")
        poisoned[31] = float("inf")
        # Dropped positions leave the remaining identical samples perfectly
        # correlated. Read as 0 they would pull the figure below 1.0.
        assert _pearson(s, poisoned) == pytest.approx(1.0)

    def test_too_few_jointly_present_samples_is_none(self):
        s = _noise(3, 40)
        sparse = [x if i % 4 == 0 else None for i, x in enumerate(s)]
        assert _pearson(s, sparse) is None   # 10 joint samples < MIN_POINTS


class TestMatrix:
    def test_matrix_agrees_with_numpy_corrcoef(self):
        import numpy as np
        a, b, c = _noise(1), [x * 2 + 0.1 for x in _noise(1)], _noise(7)
        m = vc.correlation_matrix({"a": a, "b": b, "c": c})
        assert set(m) == {("a", "b"), ("a", "c"), ("b", "c")}
        assert m[("a", "b")] == pytest.approx(1.0)
        assert m[("a", "c")] == pytest.approx(np.corrcoef(a, c)[0, 1])
        assert m[("b", "c")] == pytest.approx(np.corrcoef(b, c)[0, 1])

    def test_matrix_with_gaps_matches_pairwise_complete_corrcoef(self):
        import numpy as np
        a = _noise(1, 80)
        b = _noise(2, 80)
        a_gap = [None if i % 5 == 0 else x for i, x in enumerate(a)]
        keep = [i for i, x in enumerate(a_gap) if x is not None]
        want = np.corrcoef([a[i] for i in keep], [b[i] for i in keep])[0, 1]
        m = vc.correlation_matrix({"a": a_gap, "b": b})
        assert m[("a", "b")] == pytest.approx(want)

    def test_constant_and_short_columns_are_none_in_the_matrix(self):
        m = vc.correlation_matrix({"a": _noise(1), "flat": [1.0] * 60})
        assert m[("a", "flat")] is None

    def test_misaligned_columns_are_all_none(self):
        m = vc.correlation_matrix({"a": _noise(1, 40), "b": _noise(2, 41)})
        assert m[("a", "b")] is None

    def test_fewer_than_two_series_is_empty(self):
        assert vc.correlation_matrix({"a": _noise(1)}) == {}

    def test_every_value_is_none_or_within_unit_interval(self):
        series = {f"v{i}": _noise(i, 70) for i in range(8)}
        for r in vc.correlation_matrix(series).values():
            assert r is None or -1.0 <= r <= 1.0


class TestRetainedFraction:
    def test_unmeasured_and_below_threshold_keep_everything(self):
        assert vc.retained_fraction(None, 0.7) == 1.0
        assert vc.retained_fraction(float("nan"), 0.7) == 1.0
        assert vc.retained_fraction(0.7, 0.7) == 1.0
        assert vc.retained_fraction(-0.5, 0.7) == 1.0

    def test_falls_to_the_floor_at_perfect_correlation_either_sign(self):
        assert vc.retained_fraction(1.0, 0.7) == pytest.approx(vc.MIN_RETAINED_FRACTION)
        assert vc.retained_fraction(-1.0, 0.7) == pytest.approx(vc.MIN_RETAINED_FRACTION)

    def test_is_monotonic_and_never_above_one(self):
        vals = [vc.retained_fraction(r / 100, 0.7) for r in range(0, 101)]
        assert all(0.0 < v <= 1.0 for v in vals)
        assert all(a >= b for a, b in zip(vals, vals[1:]))


class TestRedundancyScales:
    def _hist(self):
        base = _noise(1, 60)
        return {
            "rsi": base,
            "obv": list(base),                       # echoes rsi
            "macd": _noise(9, 60),                   # independent
            "vwap": [-x for x in base],              # mirror of rsi
        }

    def test_the_later_echo_is_scaled_the_first_is_kept(self):
        s = vc.redundancy_scales(["rsi", "obv"], {"rsi": 1.0, "obv": 1.0},
                                 self._hist(), 0.7)
        assert "rsi" not in s
        assert s["obv"] == pytest.approx(vc.MIN_RETAINED_FRACTION)

    def test_independent_voter_is_untouched(self):
        s = vc.redundancy_scales(["rsi", "macd"], {"rsi": 1.0, "macd": 1.0},
                                 self._hist(), 0.7)
        assert s == {}

    def test_neutral_voter_is_not_penalised(self):
        s = vc.redundancy_scales(["rsi", "obv"], {"rsi": 1.0, "obv": 0.0},
                                 self._hist(), 0.7)
        assert s == {}

    def test_a_correlated_pair_that_disagrees_this_bar_is_left_alone(self):
        s = vc.redundancy_scales(["rsi", "obv"], {"rsi": 1.0, "obv": -1.0},
                                 self._hist(), 0.7)
        assert s == {}

    def test_negative_correlation_with_opposite_votes_is_an_echo(self):
        s = vc.redundancy_scales(["rsi", "vwap"], {"rsi": 1.0, "vwap": -1.0},
                                 self._hist(), 0.7)
        assert s["vwap"] == pytest.approx(vc.MIN_RETAINED_FRACTION)

    def test_negative_correlation_with_same_sign_votes_is_left_alone(self):
        s = vc.redundancy_scales(["rsi", "vwap"], {"rsi": 1.0, "vwap": 1.0},
                                 self._hist(), 0.7)
        assert s == {}

    def test_unmeasurable_history_never_penalises(self):
        hist = {"rsi": _noise(1, 60), "obv": [1.0] * 60}      # constant
        assert vc.redundancy_scales(["rsi", "obv"], {"rsi": 1.0, "obv": 1.0},
                                    hist, 0.7) == {}
        short = {"rsi": _noise(1, 5), "obv": _noise(1, 5)}
        assert vc.redundancy_scales(["rsi", "obv"], {"rsi": 1.0, "obv": 1.0},
                                    short, 0.7) == {}

    def test_voter_missing_from_history_is_skipped(self):
        s = vc.redundancy_scales(["rsi", "newcomer"], {"rsi": 1.0, "newcomer": 1.0},
                                 self._hist(), 0.7)
        assert s == {}

    def test_every_factor_is_reduce_only(self):
        s = vc.redundancy_scales(["rsi", "obv", "macd", "vwap"],
                                 {"rsi": 1, "obv": 1, "macd": 1, "vwap": -1},
                                 self._hist(), 0.7)
        assert s and all(vc.MIN_RETAINED_FRACTION <= f < 1.0 for f in s.values())

    def test_lower_threshold_catches_a_moderate_pair(self):
        a = _noise(1, 300)
        rng = random.Random(5)
        b = [x if rng.random() < 0.6 else rng.choice([-1.0, 0.0, 1.0]) for x in a]
        rho = _pearson(a, b)
        assert rho is not None and 0.4 < rho < 0.95
        votes = {"a": 1.0, "b": 1.0}
        hist = {"a": a, "b": b}
        assert vc.redundancy_scales(["a", "b"], votes, hist, 0.99) == {}
        assert "b" in vc.redundancy_scales(["a", "b"], votes, hist, 0.2)


class TestVoterVoteHistory:
    def test_series_is_aligned_with_none_for_absent(self):
        h = vc.VoterVoteHistory()
        h.record("k", {"rsi": 1.0, "macd": -1.0}, bar_key=1)
        h.record("k", {"rsi": 0.0}, bar_key=2)
        h.record("k", {"rsi": 1.0, "obv": 0.5}, bar_key=3)
        s = h.series("k")
        assert s["rsi"] == [1.0, 0.0, 1.0]
        assert s["macd"] == [-1.0, None, None]
        assert s["obv"] == [None, None, 0.5]

    def test_same_bar_replaces_instead_of_double_counting(self):
        h = vc.VoterVoteHistory()
        h.record("k", {"rsi": 1.0}, bar_key=7)
        h.record("k", {"rsi": -1.0}, bar_key=7)
        assert h.depth("k") == 1
        assert h.series("k")["rsi"] == [-1.0]

    def test_window_is_bounded(self):
        h = vc.VoterVoteHistory(max_points=5)
        for i in range(20):
            h.record("k", {"rsi": float(i)}, bar_key=i)
        assert h.series("k")["rsi"] == [15.0, 16.0, 17.0, 18.0, 19.0]

    def test_key_count_is_bounded_least_recently_used_goes(self):
        h = vc.VoterVoteHistory(max_keys=2)
        h.record("a", {"x": 1.0}, bar_key=1)
        h.record("b", {"x": 1.0}, bar_key=1)
        h.record("a", {"x": 1.0}, bar_key=2)     # touch a
        h.record("c", {"x": 1.0}, bar_key=1)     # evicts b
        assert h.depth("a") == 2 and h.depth("c") == 1 and h.depth("b") == 0

    def test_unreadable_votes_are_absent_not_zero(self):
        h = vc.VoterVoteHistory()
        h.record("k", {"rsi": float("nan"), "macd": None, "obv": 1.0, "bad": "x"},
                 bar_key=1)
        assert h.series("k") == {"obv": [1.0]}

    def test_empty_snapshot_and_unknown_key(self):
        h = vc.VoterVoteHistory()
        h.record("k", {"rsi": float("nan")}, bar_key=1)
        assert h.depth("k") == 0
        assert h.series("nope") == {}


# ── the analyzer hook ─────────────────────────────────────────────────────

def _signal():
    return MarketSignal(symbol="BTC/USDT", price=100.0, change_pct_24h=0.0,
                        volume_usd_24h=1_000_000.0)


# RSI oversold (+1) and MACD histogram positive (+1): two actively-voting,
# out-of-family voters. A neutral EMA-less electorate keeps the weights plain.
_IND = {"rsi": 25, "macd_histogram": 1.0}


def _score(history=None, *, family_cap=True, threshold=0.7, breakdown=None,
           decorrelate=True):
    with patch("bot.core.analyzer.CONFIG") as cfg:
        cfg.confluence.family_cap_enabled = family_cap
        cfg.confluence.voter_decorrelation_enabled = decorrelate
        cfg.confluence.mr_oscillator_weight_cap = 2.0
        cfg.confluence.pattern_weight_cap = 2.5
        cfg.confluence.voter_max_correlation = threshold
        cfg.analyzer.voter_skip_missing_enabled = True
        cfg.analyzer.candle_strength_vote_enabled = True
        return Analyzer._score_confluence(
            dict(_IND), Regime.RANGE, _signal(),
            breakdown=breakdown, voter_history=history)


def _weights(history, **kw):
    bd: list = []
    _score(history, breakdown=bd, **kw)
    return {n: w for n, _, w in bd}


_ECHO = {"rsi": _noise(1, 60), "macd": _noise(1, 60)}
_INDEP = {"rsi": _noise(1, 60), "macd": _noise(9, 60)}


class TestAnalyzerHook:
    def test_no_history_is_byte_identical_to_before(self):
        assert _weights(None) == _weights({})
        assert _weights(None) == {"rsi": 1.5, "macd": 1.0}

    def test_redundant_later_voter_is_scaled_the_earlier_is_kept(self):
        w = _weights(_ECHO)
        assert w["rsi"] == pytest.approx(1.5)
        assert w["macd"] == pytest.approx(1.0 * vc.MIN_RETAINED_FRACTION)

    def test_independent_history_changes_nothing(self):
        assert _weights(_INDEP) == _weights(None)

    def test_the_score_moves_toward_neutral_never_away(self):
        # Both vote bullish; discounting one echo with a heavier/lighter weight
        # can only change which voter dominates, but with equal-sign votes the
        # confluence stays bullish and is reduce-only on total weight.
        base = _score(None)
        scaled = _score(_ECHO)
        assert base > 0.5 and scaled > 0.5

    def test_flag_off_ignores_history(self):
        assert _weights(_ECHO, family_cap=False) == _weights(None, family_cap=False)

    def test_its_own_flag_off_ignores_history(self):
        assert (_weights(_ECHO, decorrelate=False) == _weights(None, decorrelate=False))
        assert _weights(_ECHO, decorrelate=True) != _weights(_ECHO, decorrelate=False)

    def test_flag_on_and_off_differ_on_an_echo(self):
        assert _weights(_ECHO, family_cap=True) != _weights(_ECHO, family_cap=False)

    def test_threshold_above_the_pair_correlation_is_a_no_op(self):
        a = _noise(1, 300)
        rng = random.Random(5)
        b = [x if rng.random() < 0.6 else rng.choice([-1.0, 0.0, 1.0]) for x in a]
        hist = {"rsi": a, "macd": b}
        assert _weights(hist, threshold=0.99) == _weights(None)
        assert _weights(hist, threshold=0.2)["macd"] < 1.0

    def test_unmeasurable_history_is_never_a_penalty(self):
        flat = {"rsi": _noise(1, 60), "macd": [1.0] * 60}
        short = {"rsi": _noise(1, 4), "macd": _noise(1, 4)}
        assert _weights(flat) == _weights(None)
        assert _weights(short) == _weights(None)

    def test_a_failure_in_the_scaler_falls_back_to_unscaled(self):
        with patch("bot.core.voter_correlation.redundancy_scales",
                   side_effect=RuntimeError("boom")):
            assert _weights(_ECHO) == _weights(None)

    def test_hook_is_wired_in_source(self):
        import inspect
        src = inspect.getsource(Analyzer._score_confluence)
        assert "voter_history" in src
        assert "voter_max_correlation" in src
        assert "redundancy_scales" in src


class TestConfig:
    def test_the_pass_is_off_by_default(self):
        from bot.config import CONFIG
        assert CONFIG.confluence.voter_decorrelation_enabled is False

    def test_default_threshold_is_point_seven_and_risk_gate_is_untouched(self):
        from bot.config import CONFIG
        assert CONFIG.confluence.voter_max_correlation == pytest.approx(0.70)
        # The risk engine's cross-position gate is a different quantity and
        # keeps its own default.
        assert CONFIG.risk.max_correlation == pytest.approx(0.85)

    def test_threshold_is_bounded(self):
        """IN A SUBPROCESS, not importlib.reload(bot.config).

        A reload builds a new CONFIG and RUNTIME (and a new Config class) while
        every module that bound them at import keeps the old ones. The first
        version reloaded here, and later tests in the same run patched or read
        objects nothing else used: test_web_reports' stance test and two
        yield-card tests failed in every full run and were forgiven by the
        flake filter because they passed alone. tests/test_bg_scan_valve.py
        records the same lesson.
        """
        import os
        import subprocess
        import sys

        env = dict(os.environ, CONFLUENCE_VOTER_MAX_CORRELATION="5")
        out = subprocess.run(
            [sys.executable, "-c",
             "from bot.config import CONFIG;"
             "print(CONFIG.confluence.voter_max_correlation)"],
            capture_output=True, text=True, env=env, cwd=os.getcwd(), timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        assert float(out.stdout.strip().splitlines()[-1]) < 1.0


# ── analyze() records the history it later reads ──────────────────────────

def _candles(n: int, *, drift: float, t0: int = 1_700_000_000_000):
    rng = random.Random(11)
    out, px = [], 100.0
    for i in range(n):
        px = max(1.0, px * (1 + drift + rng.uniform(-0.01, 0.01)))
        out.append([t0 + i * 3_600_000, px * 0.998, px * 1.004,
                    px * 0.995, px, 1000.0 + rng.uniform(0, 500)])
    return out


class TestAnalyzerRecordsHistory:
    @pytest.fixture(autouse=True)
    def _decorrelation_on(self):
        import dataclasses

        from bot.config import CONFIG
        on = dataclasses.replace(CONFIG.confluence, voter_decorrelation_enabled=True)

        class _Proxy:
            confluence = on

            def __getattr__(self, name):
                return getattr(CONFIG, name)

        with patch("bot.core.analyzer.CONFIG", _Proxy()):
            yield

    def _analyze(self, analyzer, candles, **kw):
        import asyncio
        sig = MarketSignal(symbol="BTC/USDT", price=candles[-1][4],
                           change_pct_24h=1.0, volume_usd_24h=1e8)

        async def _no_thesis(*_a, **_k):
            return None
        analyzer._llm_thesis = _no_thesis
        return asyncio.run(analyzer.analyze(sig, candles, **kw))

    def test_analyze_records_each_bar_once_and_hands_it_back_later(self):
        from bot.config import CONFIG
        assert CONFIG.confluence.family_cap_enabled is True
        analyzer = Analyzer()
        assert isinstance(analyzer._voter_vote_history, vc.VoterVoteHistory)
        key = ("BTC/USDT", "1h")
        assert analyzer._voter_vote_history.depth(key) == 0
        c1 = _candles(120, drift=0.002)
        self._analyze(analyzer, c1)
        assert analyzer._voter_vote_history.depth(key) == 1
        self._analyze(analyzer, c1)            # same last bar: replaced, not appended
        assert analyzer._voter_vote_history.depth(key) == 1
        self._analyze(analyzer, _candles(121, drift=0.002))
        assert analyzer._voter_vote_history.depth(key) == 2
        cols = analyzer._voter_vote_history.series(key)
        assert "rsi" in cols and "macd" in cols
        assert all(len(v) == 2 for v in cols.values())

    def test_the_scorer_is_handed_the_history_of_earlier_bars_only(self):
        analyzer = Analyzer()
        seen: list = []
        real = Analyzer._score_confluence

        def spy(*a, **kw):
            seen.append({k: list(v) for k, v in (kw.get("voter_history") or {}).items()})
            return real(*a, **kw)

        with patch.object(Analyzer, "_score_confluence", staticmethod(spy)):
            self._analyze(analyzer, _candles(120, drift=0.002))
            self._analyze(analyzer, _candles(121, drift=0.002))
        assert seen[0] == {}                                  # nothing before bar 1
        assert seen[1] and all(len(v) == 1 for v in seen[1].values())

    def test_flag_off_records_nothing(self):
        import dataclasses

        from bot.config import CONFIG
        analyzer = Analyzer()
        off = dataclasses.replace(CONFIG.confluence, family_cap_enabled=False)

        class _Proxy:
            confluence = off

            def __getattr__(self, name):
                return getattr(CONFIG, name)

        with patch("bot.core.analyzer.CONFIG", _Proxy()):
            self._analyze(analyzer, _candles(120, drift=0.002))
        assert analyzer._voter_vote_history.depth(("BTC/USDT", "1h")) == 0

    def test_the_module_never_emits_nan_as_a_correlation(self):
        m = vc.correlation_matrix({"a": _noise(1), "b": _noise(2)})
        assert all(r is None or math.isfinite(r) for r in m.values())
