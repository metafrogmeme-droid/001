"""The portfolio backtest drains resting limits, as the single-symbol one does.

`BacktestEngine.run()` does three things per bar before it scans: fill a queued
next-open entry, `_drain_pending_limits` (fill on touch, expire, cancel on
drift), then check stops. `PortfolioBacktester._run` carries a comment reading
"Same per-bar pipeline as BacktestEngine.run():" and did the first and the
third. So on the portfolio path with `fill_mode="close"`, a limit its signal
bar did not reach was appended to `_pending_limits` and never read again: not
filled when price came back, not expired, not cancelled, and not counted by
any of the four limit counters the result reports. A resting limit simply
vanished, which is the second copy of a pipeline losing the line that
mattered.

SCOPE, measured rather than assumed: `--honest` (`fill_mode="next_open"`)
queues every approved idea as `_pending_entry` and never reaches
`_place_entry`, so `_pending_limits` stays empty and the frozen benchmark on
record is unchanged by this -- driven below, not asserted from the code.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester
from bot.compat import UTC
from bot.utils.models import Direction, TradeIdea

SYM = "BTC/USDT"
_T0 = datetime(2025, 1, 1, tzinfo=UTC)
#: Inside the live drift band (`price_drift_cancel_pct`, 2%) of a 100 close,
#: or the first drained bar CANCELS it -- which the first draft of this file
#: planted at 95, so its "expiry" test passed on a drift cancel instead.
LIMIT = 98.5


def _bars(n=30, dip_at=7, dip_low=98.0, close=100.0):
    """The limit is placed on bar 5 (the first scan after lookback) and
    expires 4h later, so a dip at bar 7 is inside its window."""
    out = []
    for h in range(n):
        low = dip_low if h == dip_at else 99.0
        out.append(BacktestBar(timestamp=_T0 + timedelta(hours=h), open=100.0,
                               high=101.0, low=min(low, close), close=close,
                               volume=1000.0, symbol=SYM))
    return out


def _idea():
    return TradeIdea(id="TI-REST", asset=SYM, direction=Direction.LONG,
                     entry_price=LIMIT, stop_loss=80.0, take_profit=130.0,
                     confidence=0.7, reasoning="x", source="t", timestamp=_T0,
                     order_type="limit")


async def _run(fill_mode, bars):
    cfg = BacktestConfig(symbol=SYM, timeframe="1h", initial_balance=100_000.0,
                         slippage_pct=0.0, commission_pct=0.0, lookback_size=5,
                         scan_interval=1, fill_mode=fill_mode)
    pb = PortfolioBacktester(cfg, symbols=[SYM])
    eng = pb._engines[SYM]
    placed = []

    async def _one_limit(bar, window, i):
        if placed:
            return
        placed.append(bar.timestamp)
        rc = SimpleNamespace(position_size_usd=1_000.0,
                             verdict=SimpleNamespace(value="APPROVED"))
        if fill_mode == "next_open":
            eng._pending_entry = (_idea(), rc)     # what _process_bar queues
        else:
            eng._place_entry(_idea(), rc, bar)

    eng._process_bar = _one_limit
    try:
        await pb.run({SYM: bars})
    finally:
        pb.cleanup()
    return eng


@pytest.mark.asyncio
async def test_a_resting_limit_fills_when_price_comes_back():
    eng = await _run("close", _bars())
    assert eng._limits_filled == 1, (
        "the portfolio loop never drained the limit its signal bar missed")
    assert eng._limits_filled_same_bar == 0
    assert eng._pending_limits == []


@pytest.mark.asyncio
async def test_it_fills_at_the_limit_on_the_bar_that_reached_it():
    eng = await _run("close", _bars())
    entries = [t.entry_price for t in eng.portfolio.trade_history] + \
              [p.entry_price for p in eng.portfolio.open_positions]
    assert entries == [LIMIT], entries


@pytest.mark.asyncio
async def test_a_limit_nothing_reaches_expires_and_is_counted():
    eng = await _run("close", _bars(n=40, dip_at=10_000))   # never dips
    assert eng._limits_filled == 0
    assert (eng._limits_expired, eng._limits_cancelled_drift) == (1, 0), (
        "a limit nothing reached inside the band must EXPIRE, never be "
        "forgotten")
    assert eng._pending_limits == []


@pytest.mark.asyncio
async def test_a_limit_price_ran_away_from_is_cancelled_on_drift():
    # Placed at 98.5 under a 100 close; the market then closes at 103.
    bars = _bars(n=6, dip_at=10_000) + [
        BacktestBar(timestamp=_T0 + timedelta(hours=h), open=103.0, high=104.0,
                    low=102.5, close=103.0, volume=1000.0, symbol=SYM)
        for h in range(6, 30)]
    eng = await _run("close", bars)
    assert (eng._limits_filled, eng._limits_expired,
            eng._limits_cancelled_drift) == (0, 0, 1)
    assert eng._pending_limits == []


@pytest.mark.asyncio
async def test_the_honest_path_never_rests_a_limit_so_the_benchmark_is_unchanged():
    eng = await _run("next_open", _bars())
    assert eng._limits_filled == 0 and eng._limits_expired == 0
    entries = [t.entry_price for t in eng.portfolio.trade_history] + \
              [p.entry_price for p in eng.portfolio.open_positions]
    assert entries == [100.0], (
        "--honest fills a queued idea at the next bar's open, limit or not; "
        f"got {entries}")
