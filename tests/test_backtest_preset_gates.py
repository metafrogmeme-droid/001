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
    assert kw["sl_atr_mult"] is None
    assert kw["tp_atr_mult"] is None
    assert kw["confidence_threshold"] == 0.0
    # And they map onto the config as no-ops.
    cfg = BacktestConfig(symbol="BTC/USDT", **kw)
    assert cfg.volume_spike_min is None and cfg.regime_filter == "" and cfg.rsi_max is None
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
