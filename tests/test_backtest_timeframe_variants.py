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


# ── fold history (Addendum 1) ──────────────────────────────────────────────

def test_context_rows_come_before_the_folds_own_and_are_read_as_history():
    rows = _rows(1300, drift=0.05)
    eng = _engine(rows[-100:], daily_trend_days=50)
    # Without context a 100-bar fold never forms a 50-day reading.
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), 99) is None
    eng._context_raw = rows[:-100]
    assert eng._history_upto(99) == rows
    assert eng._apply_timeframe_variants(_idea(Direction.LONG), 99) is not None
    assert eng._apply_timeframe_variants(_idea(Direction.SHORT), 99) is None


def _fold_contexts(n_bars, **cfg_kw):
    """Drive the real portfolio_walk_forward and record what each fold's
    backtester was handed (the run itself is a recorder)."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from bot.backtest import portfolio_engine as pe
    from bot.backtest.models import BacktestBar
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [BacktestBar(timestamp=t0 + timedelta(hours=i), open=1, high=1, low=1, close=1,
                        volume=1, symbol="AAA/USDT:USDT") for i in range(n_bars)]
    seen = []

    async def _record(self, data, context=None):
        seen.append((min(b.timestamp for b in data["AAA/USDT:USDT"]), context))
        return SimpleNamespace(total_trades=0, total_return_pct=0.0, win_rate=0.0,
                               max_drawdown_pct=0.0, profit_factor=0.0, sharpe_ratio=0.0,
                               total_ideas_generated=0, total_limits_filled=0,
                               total_limits_expired=0, total_limits_cancelled_drift=0,
                               total_entries_skipped_random=0, trades=[])
    orig = pe.PortfolioBacktester.run
    pe.PortfolioBacktester.run = _record
    try:
        cfg = BacktestConfig(symbol="AAA/USDT:USDT", **cfg_kw)
        asyncio.run(pe.portfolio_walk_forward({"AAA/USDT:USDT": bars}, cfg, n_folds=3))
    finally:
        pe.PortfolioBacktester.run = orig
    return seen


def test_the_walk_forward_hands_each_fold_only_earlier_bars_and_no_more_than_asked():
    seen = _fold_contexts(1000, fold_history_bars=150)
    assert seen and all(ctx is not None for _, ctx in seen)
    for start, ctx in seen:
        bars = ctx["AAA/USDT:USDT"]
        assert len(bars) <= 150 and all(b.timestamp < start for b in bars)
    assert len(seen[-1][1]["AAA/USDT:USDT"]) == 150


def test_no_fold_history_is_the_old_hand_off():
    assert all(ctx is None for _, ctx in _fold_contexts(1000))


def test_the_analyzer_gets_daily_context_from_fold_history():
    """Through the real `_process_bar` on a 1h fold of 100 bars: the 1d
    context exists only when the fold's earlier bars are handed in."""
    import asyncio
    from datetime import datetime, timezone

    from bot.backtest.models import BacktestBar
    rows = _rows(1400)

    def handed(with_context: bool):
        eng = _engine(rows[-100:])
        if with_context:
            eng._context_raw = rows[:-100]
        seen = {}

        async def _record(signal, candles, **kw):
            seen["mtf"] = kw.get("mtf_candles") or {}
            return None
        eng.analyzer.analyze = _record
        window = [BacktestBar(timestamp=datetime.fromtimestamp(r[0] / 1000, timezone.utc),
                              open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5],
                              symbol="BTC/USDT") for r in rows[-100:]]
        asyncio.run(eng._process_bar(window[-1], window, 99))
        return seen["mtf"]

    assert "1d" in handed(True) and "1d" not in handed(False)
