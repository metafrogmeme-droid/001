"""The timeframe-variant backtest controls: off by default, and each one does
what docs/research/prereg/timeframe_variants_2026-10 says it does.

Driven through the real `BacktestEngine` methods on a real `TradeIdea`, with
the engine's own `_all_raw` history (ccxt rows) set to a series whose 4h ATR
and daily trend are known by construction.
"""
from __future__ import annotations

import pytest

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig
from bot.config import CONFIG
from bot.utils.models import Direction, TradeIdea

_ENGINES: list[BacktestEngine] = []
H = 3_600_000


@pytest.fixture(autouse=True)
def _cleanup_engines():
    yield
    while _ENGINES:
        _ENGINES.pop().cleanup()


def _engine(rows, **cfg_kw):
    eng = BacktestEngine(BacktestConfig(symbol="BTC/USDT", **cfg_kw))
    eng._all_raw = rows
    _ENGINES.append(eng)
    return eng


def _rows(hours: int, *, drift: float = 0.0, swing: float = 1.0):
    """Hourly ccxt rows from a 00:00 UTC start. Every bar's high/low sit
    ``swing`` either side of its close, so every 4h group's true range is
    2*swing (plus the drift across the group) and the ATR is known."""
    out, price = [], 100.0
    for i in range(hours):
        price += drift
        out.append([i * H, price, price + swing, price - swing, price, 1.0])
    return out


def _idea(direction=Direction.LONG, stype="swing", entry=100.0):
    sl, tp = (entry - 2.0, entry + 3.0) if direction == Direction.LONG else (entry + 2.0, entry - 3.0)
    return TradeIdea(id="v1", asset="BTC/USDT", direction=direction, entry_price=entry,
                     stop_loss=sl, take_profit=tp, confidence=0.8, reasoning="x",
                     strategy_type=stype)


def test_all_off_is_the_idea_unchanged():
    eng = _engine(_rows(2000))
    idea = _idea()
    assert eng._apply_timeframe_variants(idea, 1999) is idea


def test_setup_only_keeps_one_setup_and_rejects_the_rest():
    eng = _engine(_rows(100), setup_only="scalp")
    assert eng._apply_timeframe_variants(_idea(stype="scalp"), 99) is not None
    assert eng._apply_timeframe_variants(_idea(stype="swing"), 99) is None


def test_swing_stop_comes_from_the_4h_atr_at_the_swing_multiples():
    eng = _engine(_rows(400, swing=1.0), swing_stop_tf="4h")
    out = eng._apply_timeframe_variants(_idea(), 399)
    st = CONFIG.strategy_types
    # Flat series: every 4h group spans close-1 .. close+1, so its TR is 2.0.
    assert out.stop_loss == pytest.approx(100.0 - st.swing_sl_atr_mult * 2.0)
    assert out.take_profit == pytest.approx(100.0 + st.swing_tp_atr_mult * 2.0)
    short = eng._apply_timeframe_variants(_idea(Direction.SHORT), 399)
    assert short.stop_loss == pytest.approx(100.0 + st.swing_sl_atr_mult * 2.0)


def test_swing_stop_leaves_other_setups_and_refuses_without_history():
    eng = _engine(_rows(400), swing_stop_tf="4h")
    scalp = _idea(stype="scalp")
    assert eng._apply_timeframe_variants(scalp, 399) is scalp
    # 40 hours is 10 closed 4h groups: no ATR(14), no fill at the old stop.
    assert eng._apply_timeframe_variants(_idea(), 39) is None


def test_daily_trend_admits_only_the_side_of_the_trend():
    up = _engine(_rows(24 * 60, drift=0.05), daily_trend_days=50)
    assert up._apply_timeframe_variants(_idea(Direction.LONG), 24 * 60 - 1) is not None
    assert up._apply_timeframe_variants(_idea(Direction.SHORT), 24 * 60 - 1) is None
    down = _engine(_rows(24 * 60, drift=-0.05), daily_trend_days=50)
    assert down._apply_timeframe_variants(_idea(Direction.SHORT), 24 * 60 - 1) is not None
    assert down._apply_timeframe_variants(_idea(Direction.LONG), 24 * 60 - 1) is None


def test_too_few_closed_days_is_no_trend_and_no_entry():
    eng = _engine(_rows(24 * 30, drift=0.05), daily_trend_days=50)
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), 24 * 30 - 1) is None


def test_scaled_context_gives_a_5m_run_its_higher_timeframes():
    rows = [[i * 300_000, 100.0, 101.0, 99.0, 100.0, 1.0] for i in range(12 * 24 * 40)]
    eng = _engine(rows, timeframe="5m", mtf_scaled_history=True)
    ctx = eng._scaled_mtf_context(rows)
    assert set(ctx) == {"15m", "1h", "4h", "1d"}
    assert len(ctx["1h"]) == 200 and len(ctx["4h"]) == 200 and 30 <= len(ctx["1d"]) <= 40
    # The default path on the same rows reaches no 4h or 1d context at all:
    # 824 and 4824 five-minute bars are 68 hours and 16 days.
    from bot.utils.candles import resample_ohlcv
    assert len(resample_ohlcv(rows[-824:], "5m", "4h")) < 30


def test_the_runner_passes_the_controls_and_defaults_them_off():
    from bot.backtest.runner import _preset_gate_kwargs, build_parser
    off = _preset_gate_kwargs(build_parser().parse_args([]))
    assert (off["setup_only"], off["swing_stop_tf"], off["daily_trend_days"],
            off["mtf_scaled_history"]) == ("", "", 0, False)
    on = _preset_gate_kwargs(build_parser().parse_args(
        ["--setup-only", "scalp", "--swing-stop-tf", "4h", "--daily-trend-days", "50",
         "--mtf-scaled-history"]))
    assert (on["setup_only"], on["swing_stop_tf"], on["daily_trend_days"],
            on["mtf_scaled_history"]) == ("scalp", "4h", 50, True)


def _handed_context(flag: bool):
    """Drive the real `_process_bar` on a 5m run and record the context the
    analyzer is handed (the recorder returns no idea, so nothing trades)."""
    import asyncio
    from datetime import datetime, timezone

    from bot.backtest.models import BacktestBar
    rows = [[i * 300_000, 100.0, 101.0, 99.0, 100.0, 1.0] for i in range(12 * 24 * 40)]
    eng = _engine(rows, timeframe="5m", mtf_scaled_history=flag)
    seen = {}

    async def _record(signal, candles, **kw):
        seen["mtf"] = kw.get("mtf_candles")
        return None
    eng.analyzer.analyze = _record
    window = [BacktestBar(timestamp=datetime.fromtimestamp(r[0] / 1000, timezone.utc),
                          open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5],
                          symbol="BTC/USDT") for r in rows[-100:]]
    asyncio.run(eng._process_bar(window[-1], window, len(rows) - 1))
    return seen["mtf"] or {}


def test_the_bar_loop_hands_the_analyzer_the_scaled_context_only_when_asked():
    on, off = _handed_context(True), _handed_context(False)
    assert "4h" in on and "1d" in on and "1h" in on
    assert "4h" not in off and "1d" not in off
