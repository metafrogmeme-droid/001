"""MARKETPLACE Phase 2a — additive preset entry gates in the backtester.

The backtest engine can replay a NAMED marketplace Strategy-Agent with its real
entry semantics (volume-spike / regime / RSI), so a scorecard measures each
agent's actual design — not a near-identical baseline. Every gate is OFF by
default, so an unconfigured run is byte-identical to before (proven here by the
fast-path no-op) and the gates only ever REMOVE entries, never add or reshape
them.
"""
import numpy as np
import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig
from bot.utils.models import MarketSignal

# These tests build an engine only to call a pure predicate, but constructing
# one forces the learning flags OFF process-wide (see BacktestEngine.__init__).
# Nothing here calls run(), so nothing here hands them back — which left every
# later test in the process seeing CONFIDENCE_CALIBRATION_ENABLED reported OFF.
_ENGINES: list[BacktestEngine] = []


@pytest.fixture(autouse=True)
def _cleanup_engines():
    yield
    while _ENGINES:
        _ENGINES.pop().cleanup()


class _Side:
    def __init__(self, value):
        self.value = value


class _Idea:
    def __init__(self, confidence=0.9, direction="LONG"):
        self.confidence = confidence
        self.direction = _Side(direction)


class _Regime:
    def __init__(self, value):
        self.value = value


def _engine(**cfg_kw):
    eng = BacktestEngine(BacktestConfig(symbol="BTC/USDT", **cfg_kw))
    _ENGINES.append(eng)
    return eng


def _sig(symbol="BTC/USDT", spike_ratio=0.0, spike=False):
    return MarketSignal(symbol=symbol, price=100.0, change_pct_24h=0.0,
                        volume_usd_24h=1000.0, volume_spike=spike,
                        volume_spike_ratio=spike_ratio)


def test_defaults_are_a_strict_no_op():
    eng = _engine()
    # No gates configured -> the fast path returns False without touching data.
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), []) is False
    # The config carries the new fields, all OFF.
    assert eng.config.volume_spike_min is None
    assert eng.config.regime_filter == ""
    assert eng.config.rsi_max is None
    assert eng.config.rsi_min is None
    assert eng.config.direction == ""
    assert eng.config.ma_fast is None
    assert eng.config.ma_slow is None
    assert eng._ma_configured() is False


def test_volume_spike_gate_filters_below_min():
    eng = _engine(volume_spike_min=3.0)
    # Below the 3x ratio -> rejected, even when the 2x boolean flag is set.
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=2.0), []) is True
    assert eng._rejected_by_preset_gate(
        _Idea(), _sig(spike_ratio=2.759, spike=True), []) is True
    # At/above the ratio -> passes. The flag is not what passes it.
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=3.5), []) is False
    assert eng._rejected_by_preset_gate(
        _Idea(), _sig(spike_ratio=3.0, spike=False), []) is False


def test_the_live_run_filter_uses_the_same_volume_reading():
    from bot.core.strategy_gate import signal_clears_volume_min
    assert signal_clears_volume_min(_sig(spike_ratio=2.759, spike=True), 3.0) is False
    assert signal_clears_volume_min(_sig(spike_ratio=3.0, spike=False), 3.0) is True

    class _Unread:
        volume_spike = True
        volume_spike_ratio = None

    assert signal_clears_volume_min(_Unread(), 3.0) is False
    eng = _engine(volume_spike_min=3.0)
    assert eng._rejected_by_preset_gate(_Idea(), _Unread(), []) is True


def test_regime_gate_requires_matching_regime():
    eng = _engine(regime_filter="TREND_DOWN")
    eng.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_UP")
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), []) is True     # wrong regime
    eng.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_DOWN")
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), []) is False    # matches
    # Case-insensitive on the configured value.
    eng2 = _engine(regime_filter="trend_down")
    eng2.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_DOWN")
    assert eng2._rejected_by_preset_gate(_Idea(), _sig(), []) is False


def _window(closes):
    class _Bar:
        def __init__(self, c):
            self.close = c
    return [_Bar(c) for c in closes]


def test_rsi_gate_only_admits_oversold():
    eng = _engine(rsi_max=35.0)
    # Steadily FALLING closes -> low RSI (oversold) -> passes the rsi_max gate.
    falling = _window(list(np.linspace(100, 60, 30)))
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), falling) is False
    # Steadily RISING closes -> high RSI -> rejected by rsi_max=35.
    rising = _window(list(np.linspace(60, 100, 30)))
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), rising) is True
    # Too few bars -> RSI unknown -> never reject on it.
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), _window([100, 101, 102])) is False
    # The bound is the long's capitulation print. A short is not rejected
    # for a high RSI, and is not required to print RSI <= 35.
    assert eng._rejected_by_preset_gate(
        _Idea(direction="SHORT"), _sig(), rising) is False
    assert eng._rejected_by_preset_gate(
        _Idea(direction="SHORT"), _sig(), falling) is False
    # An unreadable side is not a long, so it does not pass the bound.
    assert eng._rejected_by_preset_gate(
        _Idea(direction=""), _sig(), falling) is True


def test_rsi_min_stands_aside_at_capitulation():
    # The dip rule: RSI at or above the floor passes; below it is the bounce.
    eng = _engine(rsi_min=35.0)
    falling = _window(list(np.linspace(100, 60, 30)))
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), falling) is True
    rising = _window(list(np.linspace(60, 100, 30)))
    assert eng._rejected_by_preset_gate(_Idea(), _sig(), rising) is False


def test_direction_gate_rejects_the_other_side_and_an_unknown_spelling():
    long_eng = _engine(direction="long_only")
    assert long_eng._rejected_by_preset_gate(_IdeaDir("SHORT"), _sig(), []) is True
    assert long_eng._rejected_by_preset_gate(_IdeaDir("LONG"), _sig(), []) is False
    # A spelling the gate does not know is a refusal, not both sides.
    bad = _engine(direction="both")
    assert bad._rejected_by_preset_gate(_IdeaDir("LONG"), _sig(), []) is True


class _IdeaDir:
    def __init__(self, direction, confidence=0.9):
        self.confidence = confidence
        self.direction = _Regime(direction)  # .value, same shape as Direction


def test_gates_compose_any_one_rejects():
    # Momentum-hunter-like: volume spike + TREND_UP. A TREND_DOWN bar is rejected
    # even if the volume passes, because the regime gate also applies.
    eng = _engine(volume_spike_min=3.0, regime_filter="TREND_UP")
    eng.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_DOWN")
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=5.0), []) is True
    eng.analyzer._current_regimes["BTC/USDT"] = _Regime("TREND_UP")
    assert eng._rejected_by_preset_gate(_Idea(), _sig(spike_ratio=5.0), []) is False


def test_runner_preset_gate_kwargs_default_off():
    import argparse
    from bot.backtest.runner import _preset_gate_kwargs
    ns = argparse.Namespace()
    kw = _preset_gate_kwargs(ns)
    assert kw["volume_spike_min"] is None
    assert kw["regime_filter"] == ""
    assert kw["rsi_max"] is None
    assert kw["rsi_min"] is None
    assert kw["direction"] == ""
    assert kw["sl_atr_mult"] is None
    assert kw["tp_atr_mult"] is None
    assert kw["confidence_threshold"] == 0.0
    # And they map onto the config as no-ops.
    cfg = BacktestConfig(symbol="BTC/USDT", **kw)
    assert cfg.volume_spike_min is None and cfg.regime_filter == "" and cfg.rsi_max is None
    assert cfg.rsi_min is None and cfg.direction == ""
    assert cfg.sl_atr_mult is None and cfg.tp_atr_mult is None


def test_preset_atr_multiples_replace_the_analyzer_levels():
    from bot.utils.models import Direction, TradeIdea
    idea = TradeIdea(
        id="t1", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=90.0, take_profit=130.0,
        confidence=0.8, reasoning="x")
    eng = _engine(sl_atr_mult=1.5, tp_atr_mult=2.0)
    out = eng._apply_preset_exits(idea, 10.0)
    assert out is not None
    assert out.stop_loss == 85.0
    assert out.take_profit == 120.0
    assert eng._preset_atr_by_idea["t1"] == 10.0
    # Unset multiples leave the analyzer's levels alone.
    plain = _engine()
    same = plain._apply_preset_exits(idea, 10.0)
    assert same.stop_loss == 90.0 and same.take_profit == 130.0
    # An unreadable ATR is not a fill at the analyzer's stop.
    assert eng._apply_preset_exits(idea, None) is None
    short = TradeIdea(
        id="t2", asset="BTC/USDT", direction=Direction.SHORT,
        entry_price=100.0, stop_loss=110.0, take_profit=80.0,
        confidence=0.8, reasoning="x")
    out_s = eng._apply_preset_exits(short, 10.0)
    assert out_s.stop_loss == 115.0 and out_s.take_profit == 80.0


# ── no volume baseline is no ratio (review of the sixty PRs, #484) ────────

def _bars(volumes):
    from datetime import datetime, timedelta, timezone

    from bot.backtest.models import BacktestBar
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [BacktestBar(timestamp=t0 + timedelta(hours=i), open=100.0, high=101.0,
                        low=99.0, close=100.0, volume=v) for i, v in enumerate(volumes)]


def test_a_window_with_no_volume_baseline_has_no_ratio():
    """Five zero-volume bars gave 0.0, which a published trade row printed
    as a measured "no spike". The live scanner sends None for it."""
    eng = _engine()
    window = _bars([0, 0, 0, 0, 0, 500])
    sig = eng._bar_to_signal(window[-1], window)
    assert sig.volume_spike_ratio is None and sig.volume_spike is False
    short = _bars([10, 10, 500])
    assert eng._bar_to_signal(short[-1], short).volume_spike_ratio is None


def test_a_measured_ratio_and_a_measured_zero_stay_measured():
    eng = _engine()
    spike = _bars([10, 10, 10, 10, 10, 35])
    sig = eng._bar_to_signal(spike[-1], spike)
    assert sig.volume_spike_ratio == 3.5 and sig.volume_spike is True
    quiet = _bars([10, 10, 10, 10, 10, 0])
    assert eng._bar_to_signal(quiet[-1], quiet).volume_spike_ratio == 0.0


def test_no_ratio_is_remembered_as_none_and_does_not_clear_a_gate():
    eng = _engine(volume_spike_min=3.0)
    window = _bars([0, 0, 0, 0, 0, 500])
    sig = eng._bar_to_signal(window[-1], window)

    class _I:
        id = "TI-1"
    eng._remember_volume_ratio(_I(), sig)
    assert eng._volume_ratio_by_idea["TI-1"] is None
    assert eng._rejected_by_preset_gate(_Idea(), sig, window) is True
