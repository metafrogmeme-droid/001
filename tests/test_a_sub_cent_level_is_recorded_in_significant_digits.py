"""A price level is recorded in significant digits, whatever the price.

The analyzer rounded every level it computes to six decimal PLACES: the VWAP
and its bands, the EMAs, SMA50, the Bollinger, Keltner and Donchian channels,
the fib ladder, the session range and MACD. That is the ATR's defect
(`record_atr`) across forty readings. On a sub-cent asset every level landed
on a grid a few percent of the price wide. Driven on one series and on the
same series scaled by 1e-6 (a BONK-like price near 2e-05):

- MACD, its signal and its histogram read exactly 0.0, so the MACD voter and
  the Keltner squeeze's direction abstained on every such asset;
- EMA9 and EMA21 read one number, and the model's evidence called the tie
  "bearish";
- Bollinger %B read 0.83 for a price above the upper band;
- every fib and VWAP level sat on the grid, up to 3.9% from where it was
  computed.

The volume profile (POC and value area) and the analyzer's fallback limit
entry rounded to eight places, and the executor's last-resort tick rounding,
the multi-timeframe EMAs, the /positions rows and the dashboard pusher to six.
`record_level` is the one reading: six places or six significant digits,
whichever keeps more, so no reading of a price at or above 0.1 moves.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from bot.core.analyzer import Analyzer, _apply_vwap_setup_anchoring
from bot.core.multi_timeframe import _analyze_single_tf
from bot.core.signal_levels import record_level
from bot.core.volume_profile import compute_volume_profile

ROOT = Path(__file__).resolve().parents[1]
SCALE = 1e-6


def _series(seed: int, n: int = 200):
    rng = np.random.default_rng(seed)
    closes = 23.0 * np.exp(np.cumsum(rng.normal(0.0008, 0.012, n)))
    highs = closes * (1 + np.abs(rng.normal(0, 0.004, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.004, n)))
    opens = np.r_[closes[0], closes[:-1]]
    vols = rng.uniform(1e5, 2e5, n)
    times = np.arange(n) * 3_600_000 + 1_700_000_000_000
    return highs, lows, closes, vols, opens, times


def _indicators(seed: int, scale: float) -> dict:
    h, lo, c, v, o, t = _series(seed)
    out = Analyzer._compute_indicators(h * scale, lo * scale, c * scale, v,
                                       o * scale, t)
    assert out is not None
    return out


class TestTheReading:
    @pytest.mark.parametrize("v", [0.1, 0.1234567, 1.23456789, 23.456789012,
                                   63000.1234567, 1e9 + 0.1234567,
                                   -0.1, -7.891234567, -63000.1234567])
    def test_at_or_above_a_tenth_it_is_six_places(self, v):
        assert record_level(v) == round(v, 6)

    def test_a_grid_of_prices_from_a_tenth_up_is_unchanged(self):
        for v in np.geomspace(0.1, 1e7, 500):
            assert record_level(float(v)) == round(float(v), 6)
            assert record_level(-float(v)) == round(-float(v), 6)

    @pytest.mark.parametrize("v,want", [
        (1.14957123e-5, 1.14957e-5),
        (2.30518765e-5, 2.30519e-5),
        (-2.4474712e-7, -2.44747e-7),
        (0.0999999, 0.0999999),
        (5e-7, 5e-7),
    ])
    def test_below_a_tenth_it_keeps_six_significant_digits(self, v, want):
        assert record_level(v) == want

    def test_a_tiny_negative_keeps_its_sign(self):
        assert record_level(-3.3e-9) < 0

    def test_min_places_is_a_floor_on_the_places(self):
        assert record_level(0.123456789, 8) == round(0.123456789, 8)
        assert record_level(1.23456789e-5, 8) == 1.23457e-5

    @pytest.mark.parametrize("v", [0.0, -0.0, math.inf, -math.inf])
    def test_zero_and_infinity_are_what_round_answers(self, v):
        assert record_level(v) == round(v, 6)

    def test_nan_stays_nan(self):
        assert math.isnan(record_level(float("nan")))

    def test_it_answers_a_python_float(self):
        assert type(record_level(np.float64(2.3e-5))) is float


class TestTheIndicatorsScale:
    """Every indicator either scales with the price or does not move at all."""

    @pytest.mark.parametrize("seed", [7, 11, 23])
    @pytest.mark.parametrize("scale", [1e-6, 1e-4])
    def test_every_numeric_indicator_scales_or_holds(self, seed, scale):
        big = _indicators(seed, 1.0)
        small = _indicators(seed, scale)
        wrong = []
        for key, a in big.items():
            b = small.get(key)
            if isinstance(a, bool) or not isinstance(a, (int, float)):
                continue
            if not isinstance(b, (int, float)) or isinstance(b, bool):
                wrong.append((key, a, b))
                continue
            if a == 0 and b == 0:
                continue
            as_level = abs(b - a * scale) / max(abs(a * scale), 1e-300)
            as_ratio = abs(b - a) / max(abs(a), 1e-300)
            if min(as_level, as_ratio) > 1e-4:
                wrong.append((key, a, b))
        assert not wrong, wrong


class TestTheVotesReadAMovement:
    def test_macd_is_not_rounded_to_zero(self):
        big, small = _indicators(7, 1.0), _indicators(7, SCALE)
        for key in ("macd", "macd_signal", "macd_histogram"):
            assert big[key] != 0
            assert small[key] == pytest.approx(big[key] * SCALE, rel=1e-4)
            assert math.copysign(1, small[key]) == math.copysign(1, big[key])

    def test_the_two_emas_are_two_numbers(self):
        big, small = _indicators(7, 1.0), _indicators(7, SCALE)
        assert big["ema_9"] != big["ema_21"]
        assert (small["ema_9"] > small["ema_21"]) == (big["ema_9"] > big["ema_21"])

    def test_bollinger_pct_b_is_the_large_series_own(self):
        big, small = _indicators(7, 1.0), _indicators(7, SCALE)
        assert small["bb_pct_b"] == pytest.approx(big["bb_pct_b"], abs=1e-3)


class TestTheAnchorKeepsItsBands:
    def test_recentred_bands_keep_significant_digits(self):
        ind = {"vwap": 2.30518e-5, "vwap_upper_1": 2.36518e-5,
               "vwap_50": 2.28123e-5}
        _apply_vwap_setup_anchoring(ind, "swing")
        assert ind["vwap"] == 2.28123e-5
        assert ind["vwap_upper_1"] == pytest.approx(2.34123e-5, rel=1e-5)
        assert ind["vwap_lower_1"] == pytest.approx(2.22123e-5, rel=1e-5)
        assert ind["vwap_upper_2"] == pytest.approx(2.40123e-5, rel=1e-5)
        assert ind["vwap_lower_2"] == pytest.approx(2.16123e-5, rel=1e-5)


class TestTheOtherReaders:
    def test_the_multi_timeframe_emas_scale(self):
        h, lo, c, v, o, t = _series(11)

        def candles(k):
            return [[float(t[i]), float(o[i] * k), float(h[i] * k),
                     float(lo[i] * k), float(c[i] * k), float(v[i])]
                    for i in range(len(c))]

        big = _analyze_single_tf(candles(1.0), "1h")
        small = _analyze_single_tf(candles(SCALE), "1h")
        assert big is not None and small is not None
        for key in ("ema20", "ema50", "price"):
            assert small[key] == pytest.approx(big[key] * SCALE, rel=1e-5)
        assert small["trend"] == big["trend"]

    def test_the_volume_profile_levels_scale(self):
        h, lo, c, v, _o, _t = _series(11)
        big = compute_volume_profile(h, lo, c, v)
        small = compute_volume_profile(h * SCALE, lo * SCALE, c * SCALE, v)
        assert big is not None and small is not None
        for key in ("poc", "vah", "val"):
            assert getattr(small, key) == pytest.approx(
                getattr(big, key) * SCALE, rel=1e-5)

    def test_the_executors_last_resort_keeps_a_sub_cent_limit(self):
        from bot.core.live_executor import LiveExecutor

        ex = LiveExecutor.__new__(LiveExecutor)
        got = ex._round_limit_price_to_tick(None, None, "BONK/USDT", 2.30518765e-5)
        assert got == pytest.approx(2.30519e-5, rel=1e-9)

    def test_the_executors_last_resort_above_a_cent_is_unchanged(self):
        from bot.core.live_executor import LiveExecutor

        ex = LiveExecutor.__new__(LiveExecutor)
        assert ex._round_limit_price_to_tick(None, None, "X/USDT", 0.0512345) == 0.0512
        assert ex._round_limit_price_to_tick(None, None, "X/USDT", 2.345678) == 2.346

    def test_the_dashboard_snapshot_keeps_a_sub_cent_position(self, monkeypatch):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace as NS

        from bot.core import dashboard_pusher as dp

        monkeypatch.setattr(dp, "CONFIG", NS(is_live=lambda: False))
        now = datetime.now(timezone.utc)
        long_ = NS(value="LONG")
        pos = NS(asset="BONK/USDT", direction=long_, entry_price=2.30518765e-5,
                 quantity=1e6, stop_loss=2.26612345e-5,
                 take_profit=2.41987654e-5, opened_at=now - timedelta(hours=1))
        closed = NS(asset="BONK/USDT", direction=long_, entry_price=2.30518765e-5,
                    exit_price=2.37891234e-5, quantity=1e6, gross_pnl=0.74,
                    commission=0.03, pnl=0.71, opened_at=now - timedelta(hours=3),
                    closed_at=now - timedelta(hours=2))
        snap = NS(balance_usd=100.0, equity_usd=100.7, open_positions=1,
                  total_trades=1, win_rate=1.0, total_pnl=0.71, daily_pnl=0.71,
                  max_drawdown_pct=0.0, total_commission=0.03)
        book = NS(open_positions=[pos], trade_history=[closed],
                  _last_prices={"BONK/USDT": 2.33456789e-5},
                  snapshot=lambda: snap)
        multi = NS(all_portfolios=lambda: {"77": book},
                   combined_snapshot=lambda: snap,
                   total_open_positions=lambda: 1)
        out = dp.DashboardPusher(NS(user_portfolios=multi))._build_snapshot()
        row = out["traders"][0]["positions"][0]
        assert row["entry"] == pytest.approx(2.30519e-5, rel=1e-9)
        assert row["current"] == pytest.approx(2.33457e-5, rel=1e-9)
        assert row["sl"] == pytest.approx(2.26612e-5, rel=1e-9)
        assert row["tp"] == pytest.approx(2.41988e-5, rel=1e-9)
        trade = out["traders"][0]["recent_trades"][0]
        assert trade["entry"] == pytest.approx(2.30519e-5, rel=1e-9)
        assert trade["exit"] == pytest.approx(2.37891e-5, rel=1e-9)


class TestTheModelsEvidence:
    """Four decimals printed a sub-cent POC as $0.0000 in the prompt."""

    def _prompt(self, poc: float) -> str:
        from bot.utils.models import MarketSignal

        sig = MarketSignal(symbol="BONK/USDT", price=poc, change_pct_24h=1.0,
                           volume_usd_24h=1e6, volume_spike=False)
        return Analyzer._build_prompt(sig, {"poc_price": poc,
                                            "price_vs_poc": "above"})

    def test_a_sub_cent_poc_keeps_its_digits(self):
        out = self._prompt(2.30518765e-5)
        assert "POC=$2.30519e-05" in out
        assert "POC=$0.0000" not in out

    def test_a_poc_above_a_cent_prints_as_it_did(self):
        assert "POC=$63000.1235" in self._prompt(63000.12345678)
        assert "POC=$0.0500" in self._prompt(0.05)


# ── the rule over the class ─────────────────────────────────────────────

#: Files whose level readings go through `record_level`. A `round(x, 6)` or
#: `round(x, 8)` left in one of them is a level on a decimal grid, unless its
#: result is a scale-free figure named here with the reason.
_FILES = ("bot/core/analyzer.py", "bot/core/multi_timeframe.py",
          "bot/core/volume_profile.py", "bot/core/dashboard_pusher.py")

_SCALE_FREE = {
    ("bot/core/analyzer.py", '"bb_width"'): "a width over the mid, a ratio",
    ("bot/core/analyzer.py", '"vwap_slope_pct"'): "a percent",
}

#: Functions in bigger files that hold a level rounding the fix converted.
_FUNCS = (("bot/core/live_executor.py", "_round_limit_price_to_tick"),
          ("bot/skills/trading_commands.py", "_cmd_open_positions"))


def _decimal_grid_rounds(tree: ast.AST, src: list[str]):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "round" and len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in (6, 8)):
            yield node.lineno, src[node.lineno - 1]


def _offences(rel: str, tree: ast.AST, src: list[str]):
    out = []
    for lineno, line in _decimal_grid_rounds(tree, src):
        if any(f == rel and key in line for (f, key) in _SCALE_FREE):
            continue
        out.append(f"{rel}:{lineno}: {line.strip()}")
    return out


class TestTheRule:
    @pytest.mark.parametrize("rel", _FILES)
    def test_no_level_is_rounded_to_a_decimal_grid(self, rel):
        text = (ROOT / rel).read_text()
        assert _offences(rel, ast.parse(text), text.splitlines()) == []

    @pytest.mark.parametrize("rel,fn", _FUNCS)
    def test_the_converted_functions_hold_none_either(self, rel, fn):
        text = (ROOT / rel).read_text()
        tree = ast.parse(text)
        defs = [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == fn]
        assert len(defs) == 1
        assert list(_decimal_grid_rounds(defs[0], text.splitlines())) == []

    def test_each_scale_free_exemption_still_names_a_site(self):
        for (rel, key), reason in _SCALE_FREE.items():
            assert reason
            text = (ROOT / rel).read_text()
            lines = [line for _, line in
                     _decimal_grid_rounds(ast.parse(text), text.splitlines())]
            assert any(key in line for line in lines), (rel, key)

    def test_the_rule_reports_a_planted_level(self):
        src = 'x = {}\nx["ema_9"] = round(ema9, 6)\nx["bb_width"] = round(w, 6)\n'
        got = _offences("bot/core/analyzer.py", ast.parse(src), src.splitlines())
        assert got == ['bot/core/analyzer.py:2: x["ema_9"] = round(ema9, 6)']

    def test_the_rule_reads_eight_places_too(self):
        src = "poc = round(p, 8)\n"
        assert _offences("bot/core/volume_profile.py", ast.parse(src),
                         src.splitlines())
