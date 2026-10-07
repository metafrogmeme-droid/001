"""The chart-pattern sweep is liquidity_sweep.detect_sweeps.

There used to be a second definition in detect_liquidity_sweep, switched by
LIQUIDITY_SWEEP_OWN_CLOSE. The off position checked an older bar's wick against
the latest close, so a wick that never reclaimed could still print a sweep.
That switch is gone. One function decides, and a bar is a sweep only when its
own close reclaims the level.
"""

import numpy as np

import bot.core.chart_patterns as cp
from bot.core.chart_patterns import detect_liquidity_sweep, scan_all_chart_patterns
from bot.core.liquidity_sweep import SweepSignal, detect_sweeps


def _book(*, kind, close, low=None, high=None, volume=10.0):
    """40 bars. One swing at index 15, one candidate at index 37."""
    n = 40
    closes = np.full(n, 100.0)
    opens = np.full(n, 100.0)
    volumes = np.full(n, 10.0)
    if kind == "bull":
        highs = np.full(n, 100.5)
        lows = np.full(n, 100.0)
        lows[15] = 99.0
        lows[37] = 98.7 if low is None else low
        highs[37] = 100.4
    else:
        highs = np.full(n, 100.0)
        lows = np.full(n, 99.5)
        highs[15] = 101.0
        highs[37] = 101.3 if high is None else high
        lows[37] = 99.6
    closes[37] = close
    opens[37] = 100.2
    volumes[37] = volume
    return opens, highs, lows, closes, volumes


def _signal(sweep_type, description, confidence=0.62):
    return SweepSignal(
        sweep_type=sweep_type,
        level_price=100.0,
        sweep_low=99.5,
        sweep_high=101.0,
        close_price=100.4,
        depth_pct=0.2,
        reversal_strength=0.5,
        volume_ratio=1.5,
        level_touches=2,
        confidence=confidence,
        suggested_entry=100.1,
        suggested_sl=99.2,
        description=description,
        bars_ago=1,
    )


class TestTheChartPatternCallsTheSweepDetector:
    def test_a_bullish_signal_is_the_pattern(self, monkeypatch):
        seen = {}

        def fake(opens, highs, lows, closes, volumes, **kwargs):
            seen["ohlcv"] = (opens, highs, lows, closes, volumes)
            seen["kwargs"] = kwargs
            return [_signal("bullish_sweep", "Bullish liquidity sweep: planted")]

        monkeypatch.setattr(cp, "detect_sweeps", fake)
        opens, highs, lows, closes, volumes = _book(kind="bull", close=99.5)
        res = detect_liquidity_sweep(
            highs, lows, closes, lookback=3, swings={"swing_lows": [(1, 1.0)]},
            opens=opens, volumes=volumes,
        )
        assert res is not None
        assert res["signal"] == "bullish"
        assert res["name"] == "Liquidity Sweep (Bullish)"
        assert res["description"] == "Bullish liquidity sweep: planted"
        assert res["confidence"] == 0.62
        assert res["key_levels"]["swept_level"] == 100.0
        assert res["key_levels"]["reclaim_close"] == 100.4
        assert res["key_levels"]["wick_low"] == 99.5
        assert seen["ohlcv"][1] is highs
        assert seen["ohlcv"][4] is volumes
        # The chart lookback is a swing order, not the sweep window.
        assert "lookback" not in seen["kwargs"]

    def test_a_bearish_signal_is_the_pattern(self, monkeypatch):
        monkeypatch.setattr(
            cp, "detect_sweeps",
            lambda *a, **k: [_signal("bearish_sweep", "Bearish liquidity sweep: planted")],
        )
        opens, highs, lows, closes, volumes = _book(kind="bear", close=100.4)
        res = detect_liquidity_sweep(highs, lows, closes, opens=opens, volumes=volumes)
        assert res is not None
        assert res["signal"] == "bearish"
        assert res["name"] == "Liquidity Sweep (Bearish)"
        assert res["key_levels"]["wick_high"] == 101.0
        assert res["description"] == "Bearish liquidity sweep: planted"

    def test_no_signal_is_no_pattern(self, monkeypatch):
        monkeypatch.setattr(cp, "detect_sweeps", lambda *a, **k: [])
        opens, highs, lows, closes, volumes = _book(kind="bull", close=99.5)
        assert detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        ) is None

    def test_an_unknown_sweep_type_is_not_filed_as_bearish(self, monkeypatch):
        monkeypatch.setattr(
            cp, "detect_sweeps",
            lambda *a, **k: [_signal("sideways", "not a direction")],
        )
        opens, highs, lows, closes, volumes = _book(kind="bull", close=99.5)
        assert detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        ) is None


class TestOneDefinitionOnTheBars:
    def test_bullish_pattern_matches_the_detector(self):
        opens, highs, lows, closes, volumes = _book(kind="bull", close=99.5)
        sigs = detect_sweeps(opens, highs, lows, closes, volumes)
        res = detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        )
        assert sigs and res is not None
        assert res["signal"] == "bullish"
        assert res["confidence"] == sigs[0].confidence
        assert res["description"] == sigs[0].description
        assert res["key_levels"]["swept_level"] == sigs[0].level_price
        assert res["key_levels"]["reclaim_close"] == sigs[0].close_price
        assert res["key_levels"]["reclaim_close"] != 0

    def test_bearish_pattern_matches_the_detector(self):
        opens, highs, lows, closes, volumes = _book(kind="bear", close=100.4)
        sigs = detect_sweeps(opens, highs, lows, closes, volumes)
        res = detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        )
        assert sigs and res is not None
        assert res["signal"] == "bearish"
        assert res["description"] == sigs[0].description
        assert res["key_levels"]["swept_level"] == sigs[0].level_price
        assert res["key_levels"]["reclaim_close"] == sigs[0].close_price

    def test_a_wick_that_does_not_reclaim_on_its_own_bar_is_not_a_sweep(self, monkeypatch):
        # Latest close sits back above the level. The wick bar itself closed
        # below. The retired off-switch used to call that a bullish sweep.
        opens, highs, lows, closes, volumes = _book(kind="bull", close=98.85)
        closes[-1] = 101.0
        for flag in ("0", "1"):
            monkeypatch.setenv("LIQUIDITY_SWEEP_OWN_CLOSE", flag)
            assert detect_sweeps(opens, highs, lows, closes, volumes) == []
            assert detect_liquidity_sweep(
                highs, lows, closes, opens=opens, volumes=volumes,
            ) is None

    def test_an_unreadable_close_is_not_a_sweep_and_not_zero(self):
        opens, highs, lows, closes, volumes = _book(kind="bull", close=99.5)
        readable = detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        )
        assert readable is not None
        assert readable["key_levels"]["reclaim_close"] == 99.5
        closes[37] = np.nan
        assert detect_sweeps(opens, highs, lows, closes, volumes) == []
        assert detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        ) is None

    def test_absent_volume_is_not_printed_as_a_multiple(self):
        opens, highs, lows, closes, volumes = _book(kind="bull", close=99.5, volume=80.0)
        with_vol = detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        )
        without = detect_liquidity_sweep(highs, lows, closes, opens=opens)
        assert with_vol is not None and without is not None
        assert "vol " in with_vol["description"]
        assert "vol " not in without["description"]
        assert with_vol["confidence"] > without["confidence"]
        bare = detect_sweeps(opens, highs, lows, closes, None)
        measured = detect_sweeps(opens, highs, lows, closes, volumes)
        assert bare and bare[0].volume_ratio is None
        assert measured and measured[0].volume_ratio is not None
        from bot.formatters.market_cards import render_sweeps
        quiet = render_sweeps("BTC/USDT", 100.0, bare)
        loud = render_sweeps("BTC/USDT", 100.0, measured)
        assert "vol   —" in quiet
        assert "vol   —" not in loud
        assert "×" in loud

    def test_a_supplied_swing_map_cannot_invent_or_hide_a_sweep(self):
        opens, highs, lows, closes, volumes = _book(kind="bull", close=99.5)
        bogus = {"swing_highs": [], "swing_lows": [(7, 50.0)]}
        plain = detect_liquidity_sweep(
            highs, lows, closes, opens=opens, volumes=volumes,
        )
        forced = detect_liquidity_sweep(
            highs, lows, closes, swings=bogus, opens=opens, volumes=volumes,
        )
        assert plain == forced
        assert plain is not None and plain["key_levels"]["swept_level"] != 50.0

        # A 1% pierce of a handed-in level used to be enough. The one
        # definition's tolerance refuses it, and the handed-in swings do not
        # overrule that.
        opens, highs, lows, closes, volumes = _book(kind="bull", close=101.0, low=98.0)
        # 98 vs the real swing at 99 is more than half a percent, so the
        # detector is silent. A caller-supplied swing at 100 would have fired
        # under the old chart-pattern rule (low < 100 * 0.998, close > 100).
        assert detect_sweeps(opens, highs, lows, closes, volumes) == []
        assert detect_liquidity_sweep(
            highs, lows, closes,
            swings={"swing_highs": [], "swing_lows": [(15, 100.0)]},
            opens=opens, volumes=volumes,
        ) is None

    def test_a_short_series_is_absent(self):
        closes = np.full(10, 100.0)
        highs = closes + 1
        lows = closes - 1
        assert detect_liquidity_sweep(highs, lows, closes) is None


class TestTheScanHandsTheSweepItsMeasurements:
    def test_measured_volume_is_passed_through(self, monkeypatch):
        seen = {}

        def fake(opens, highs, lows, closes, volumes, **kwargs):
            seen["volumes"] = volumes
            seen["opens"] = opens
            return []

        monkeypatch.setattr(cp, "detect_sweeps", fake)
        n = 25
        opens = np.ones(n)
        highs = opens + 1
        lows = opens - 0.5
        closes = opens.copy()
        volumes = np.full(n, 3.0)
        scan_all_chart_patterns(opens, highs, lows, closes, volumes=volumes)
        assert seen["opens"] is opens
        assert seen["volumes"] is volumes

    def test_absent_volume_is_passed_as_absent(self, monkeypatch):
        seen = {}

        def fake(opens, highs, lows, closes, volumes, **kwargs):
            seen["volumes"] = volumes
            return []

        monkeypatch.setattr(cp, "detect_sweeps", fake)
        n = 25
        opens = np.ones(n)
        scan_all_chart_patterns(opens, opens + 1, opens - 0.5, opens.copy())
        assert seen["volumes"] is None


def test_the_chart_marker_does_not_pass_a_lookback_the_detector_discards():
    # `detect_liquidity_sweep` takes `lookback` only so the shared scan can
    # call every detector alike, and `del`s it. The chart passed 3, which
    # read as narrowing the marker's swing window. A shape no drive can see:
    # the result is the same with or without it. Raw source, parsed: an AST
    # has no comments to quote the keyword back.
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "bot/skills/chart_renderer.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_liquidity_sweep")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
             and getattr(c.func, "id", None) == "detect_liquidity_sweep"]
    assert len(calls) == 1
    assert "lookback" not in {k.arg for k in calls[0].keywords}
