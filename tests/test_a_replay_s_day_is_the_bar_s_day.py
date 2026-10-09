"""A replay's day is the bar's day; --breaker-reset-bars works on a portfolio run.

The house strategy's card (Full Scan: majors_1h, BTC/ETH/SOL, the last 1,500
bars, honest) read -4.15% on 9 trades, and its equity curve was flat for the
last three quarters of the run. Two measurement defects sat under it:

- THE PAPER BOOK KEYED "TODAY" BY THE WALL CLOCK. `PortfolioTracker` wrote and
  read its daily P&L under `datetime.now(UTC)`, and a two-month replay runs in
  seconds, so every close landed on one "today": the 5% daily-loss cap became
  a cap on the whole run, and the breaker's day rollover (read in bar time by
  the risk engine) was re-tripped by this reader at once.
- `--breaker-reset-bars` CHANGED NOTHING ON A PORTFOLIO RUN. The reset lived in
  `BacktestEngine.run()` only; `PortfolioBacktester`, which every `--symbols`
  and `--dataset` run takes, accepted the flag and ignored it. The card run
  with `--breaker-reset-bars 24` refused the same 86 ideas at CIRCUIT_BREAKER.

Neither moves a recorded figure on its own: the reset defaults to 0, and the
record and every committed scorecard reproduce unchanged with the fix.
Driven: the real tracker, and the real portfolio loop on synthetic bars.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine, BreakerResetClock
from bot.backtest.models import BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester
from bot.risk.portfolio import PortfolioTracker

DAY1 = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)


# ── the tracker: the day is the bar's under a replay, the wall clock's live ──

def test_a_replay_keys_the_daily_pnl_by_the_bar_s_day():
    t = PortfolioTracker(initial_balance=10_000.0)
    t.pin_replay_day(DAY1)
    t._record_daily_pnl(-600.0)
    assert t.snapshot().daily_pnl == -600.0
    # The next replayed day starts at zero; the loss stays on its own day.
    t.pin_replay_day(DAY1 + timedelta(hours=13))
    assert t.snapshot().daily_pnl == 0.0
    t._record_daily_pnl(-100.0)
    assert t.snapshot().daily_pnl == -100.0
    assert t._daily_pnl == {"2026-05-07": -600.0, "2026-05-08": -100.0}


def test_live_keeps_the_wall_clock_s_day():
    t = PortfolioTracker(initial_balance=10_000.0)
    t._record_daily_pnl(-50.0)
    today = datetime.now(timezone.utc).date().isoformat()
    assert t._daily_pnl == {today: -50.0}
    assert t.snapshot().daily_pnl == -50.0


def test_the_bar_s_day_is_its_utc_day():
    t = PortfolioTracker(initial_balance=10_000.0)
    # 23:30 at UTC-5 is 04:30 the next day in UTC.
    t.pin_replay_day(datetime(2026, 5, 7, 23, 30, tzinfo=timezone(timedelta(hours=-5))))
    t._record_daily_pnl(-1.0)
    assert list(t._daily_pnl) == ["2026-05-08"]


# ── the portfolio loop pins the book's clock ────────────────────────────────

def _data(n=400):
    return {
        "BTC/USDT": DataLoader.generate_synthetic(bars=n, seed=3),
        "ETH/USDT": DataLoader.generate_synthetic(bars=n, seed=6, start_price=3000.0),
    }


@pytest.mark.asyncio
async def test_the_portfolio_loop_keys_the_book_by_the_replayed_day():
    data = _data()
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0), symbols=list(data))
    try:
        await pb.run(data)
        last = max(b.timestamp for bars in data.values() for b in bars)
        assert pb._portfolio._sim_day == last.astimezone(timezone.utc).date().isoformat()
    finally:
        pb.cleanup()


@pytest.mark.asyncio
async def test_the_single_symbol_loop_does_too():
    bars = DataLoader.generate_synthetic(bars=400, seed=11)
    eng = BacktestEngine(BacktestConfig(symbol="BTC/USDT"))
    try:
        await eng.run(bars)
        assert eng.portfolio._sim_day == bars[-1].timestamp.astimezone(timezone.utc).date().isoformat()
    finally:
        eng.cleanup()


# ── --breaker-reset-bars on the portfolio path, both arms ───────────────────

async def _tripped_run(reset_bars: int):
    data = _data(n=700)
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0,
                                            breaker_reset_bars=reset_bars),
                             symbols=list(data))
    try:
        # A breaker already open when the run starts: only a reset reopens it.
        pb._risk._trip_circuit_breaker("test: tripped before the run", cause="streak")
        res = await pb.run(data)
        return res, pb._risk
    finally:
        pb.cleanup()


@pytest.mark.asyncio
async def test_without_a_reset_a_tripped_breaker_halts_the_whole_run():
    res, risk = await _tripped_run(0)
    assert res.total_trades == 0
    assert risk.circuit_breaker_active is True


@pytest.mark.asyncio
async def test_with_a_reset_the_portfolio_run_trades_again():
    res, risk = await _tripped_run(24)
    assert getattr(risk, "_circuit_cleared_how", None) == "manual", \
        "the portfolio loop never reset the breaker"
    assert res.total_trades > 0


async def _tripped_single(reset_bars: int):
    bars = DataLoader.generate_synthetic(bars=700, seed=3)
    eng = BacktestEngine(BacktestConfig(symbol="BTC/USDT", initial_balance=10_000.0,
                                        breaker_reset_bars=reset_bars))
    try:
        eng.risk._trip_circuit_breaker("test: tripped before the run", cause="streak")
        res = await eng.run(bars)
        return res, eng.risk
    finally:
        eng.cleanup()


@pytest.mark.asyncio
async def test_the_single_symbol_loop_halts_without_a_reset():
    # `test_breaker_reset_bars.py` asserts only "reset trades >= halted", which
    # a loop that never resets satisfies with equal counts. These are the arms.
    res, risk = await _tripped_single(0)
    assert res.total_trades == 0 and risk.circuit_breaker_active is True


@pytest.mark.asyncio
async def test_the_single_symbol_loop_resets_with_one():
    res, risk = await _tripped_single(24)
    assert getattr(risk, "_circuit_cleared_how", None) == "manual"
    assert res.total_trades > 0


# ── the one reset rule ──────────────────────────────────────────────────────

class _Risk:
    """Records what the clock asked of it; the breaker state is set per step."""

    def __init__(self):
        self.circuit_breaker_active = False
        self.resets = 0

    def reset_circuit_breaker(self):
        self.resets += 1
        self.circuit_breaker_active = False


def test_the_clock_resets_after_n_bars_of_a_trip_and_not_before():
    risk, clock = _Risk(), BreakerResetClock(3)
    clock.step(risk, 0)
    risk.circuit_breaker_active = True
    for i in (1, 2, 3):
        clock.step(risk, i)
    assert risk.resets == 0 and risk.circuit_breaker_active
    clock.step(risk, 4)                     # three bars after the trip was seen
    assert risk.resets == 1 and not risk.circuit_breaker_active


def test_a_breaker_that_closed_on_its_own_restarts_the_count():
    risk, clock = _Risk(), BreakerResetClock(3)
    risk.circuit_breaker_active = True
    clock.step(risk, 0)
    risk.circuit_breaker_active = False
    clock.step(risk, 1)
    risk.circuit_breaker_active = True
    clock.step(risk, 2)
    clock.step(risk, 4)
    assert risk.resets == 0                 # only two bars into the new trip
    clock.step(risk, 5)
    assert risk.resets == 1


def test_zero_never_resets():
    risk, clock = _Risk(), BreakerResetClock(0)
    risk.circuit_breaker_active = True
    for i in range(100):
        clock.step(risk, i)
    assert risk.resets == 0


# ── the breaker's trip day is the engine's clock's day ──────────────────────

def _risk(tmp_path):
    from bot.risk.risk_engine import RiskEngine
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=str(tmp_path / "risk_state.json"))


def _idea():
    from bot.utils.models import Direction, TradeIdea
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=97.0, take_profit=106.0, confidence=0.7,
                     reasoning="x", source="unknown")


def test_a_replayed_daily_loss_trip_holds_until_the_bar_s_day_ends(tmp_path):
    # The trip day was the wall clock's, which never equals a replayed bar's
    # day, so the rollover cleared a backtest's daily-loss trip at the next idea.
    r = _risk(tmp_path)
    r.set_sim_time(DAY1)
    r._trip_circuit_breaker("test: daily loss", cause="daily_loss")
    assert r._circuit_trip_day == "2026-05-07"
    later = DAY1 + timedelta(hours=3)
    r.set_sim_time(later)
    r.evaluate(_idea(), as_of=later)
    assert r.circuit_breaker_active is True, "cleared inside the day it tripped"
    nextday = DAY1 + timedelta(days=1)
    r.set_sim_time(nextday)
    r.evaluate(_idea(), as_of=nextday)
    assert r.circuit_breaker_active is False, "held past the replayed day's end"


def test_live_stamps_the_wall_clock_s_day(tmp_path):
    r = _risk(tmp_path)
    r._trip_circuit_breaker("test: daily loss", cause="daily_loss")
    assert r._circuit_trip_day == datetime.now(timezone.utc).date().isoformat()
