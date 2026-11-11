"""A resting limit fills on the touch, before its bar has closed.

`decide_resting_fill` read the fill bar's close to decide a resting fill. The
reclaim knob kept only the fills whose bar closed back through the limit, and
the adverse-cancel knob cancelled an order whose bar closed through it. Both
were then booked, or refused, at the limit price. A live order that rested
there filled on the touch, before that close printed, so it could not refuse
the fills that kept going; and a bot that waits for the close to confirm a
reclaim cannot buy at the limit any more, because the close is past it.

So a resting fill reads only what printed at the touch (the bar's low or
high). The close decides two things, both at the close: a reclaim entry, which
is booked AT that close as a taker order and is not exposed to the range that
printed before it, and a cancel of an order this bar did not fill.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot.backtest.limit_fill import decide_resting_fill
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester
from bot.core import limit_scanner as scanner
from bot.core.limit_scanner import LONG, ScannerParams
from bot.core.limit_shadow import REALISTIC, _Cfg, _mark_to_market
from bot.core.trade_costs import TAKER, leg_rate_pct


class _Bar:
    def __init__(self, ts, o, h, low, c):
        self.timestamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        self.open, self.high, self.low, self.close = o, h, low, c


class _Idea:
    direction = "LONG"
    asset = "ETH"


def _order():
    return {"idea": _Idea(), "px": 100.0, "placed_ts": 1_000.0}


def _cfg(**kw):
    base = dict(limit_through_bp=None, limit_queue_p=None, limit_queue_sure_bp=None,
                limit_reclaim=False, limit_cancel_adverse_bp=None)
    base.update(kw)
    return SimpleNamespace(**base)


RESTING = {
    "through": _cfg(limit_through_bp=5),
    "queue": _cfg(limit_through_bp=5, limit_queue_p=0.5, limit_queue_sure_bp=10),
    "cancel": _cfg(limit_through_bp=5, limit_cancel_adverse_bp=10),
    "queue_cancel": _cfg(limit_through_bp=5, limit_queue_p=0.5, limit_queue_sure_bp=10,
                         limit_cancel_adverse_bp=10),
}
RECLAIM = {
    "reclaim": _cfg(limit_through_bp=5, limit_reclaim=True),
    "reclaim_queue": _cfg(limit_through_bp=5, limit_queue_p=0.5, limit_queue_sure_bp=10,
                          limit_reclaim=True),
    "reclaim_cancel": _cfg(limit_through_bp=5, limit_reclaim=True, limit_cancel_adverse_bp=10),
}
LOWS = (99.97, 99.92, 99.0)               # short of 5 bp, between 5 and 10 bp, deep
CLOSES = (None, 99.5, 99.95, 100.0, 100.4, 101.0)   # None: the bar closes at its low


def _decisions(cfg, low):
    out = {}
    for close in CLOSES:
        c = low if close is None else max(close, low)
        out[c] = decide_resting_fill(cfg, _order(), _Bar(2_000, 100.5, 101.0, low, c))
    return out


@pytest.mark.parametrize("name", sorted({**RESTING, **RECLAIM}))
def test_whether_a_resting_order_filled_does_not_depend_on_its_bars_close(name):
    cfg = {**RESTING, **RECLAIM}[name]
    filled_somewhere = False
    for low in LOWS:
        seen = _decisions(cfg, low)
        fills = {c for c, d in seen.items() if d == "fill"}
        assert fills in (set(), set(seen)), (name, low, seen)
        filled_somewhere |= bool(fills)
    if name in RESTING:
        assert filled_somewhere, f"{name}: no bar filled, so the invariant measured nothing"


def test_a_reclaim_is_an_entry_at_the_close_that_showed_it():
    cfg = RECLAIM["reclaim"]
    pierced_and_back = _Bar(2_000, 100.5, 101.0, 99.0, 100.3)
    pierced_and_through = _Bar(2_000, 100.5, 101.0, 99.0, 99.5)
    never_pierced = _Bar(2_000, 100.5, 101.0, 99.97, 100.3)
    assert decide_resting_fill(cfg, _order(), pierced_and_back) == "fill_at_close"
    assert decide_resting_fill(cfg, _order(), pierced_and_through) == "rest"
    assert decide_resting_fill(cfg, _order(), never_pierced) == "rest"
    # A watched level whose bar closed well through it is dropped at that close.
    assert decide_resting_fill(RECLAIM["reclaim_cancel"], _order(),
                               _Bar(2_000, 100.5, 101.0, 98.8, 99.0)) == "cancel"


def test_an_order_the_touch_filled_is_not_cancelled_by_its_bars_close():
    deep_and_closed_through = _Bar(2_000, 100.5, 101.0, 99.0, 99.0)
    assert decide_resting_fill(RESTING["cancel"], _order(), deep_and_closed_through) == "fill"
    # The other arm: an order this bar did not fill (20 bp of trade-through
    # asked, 15 bp printed) may be cancelled by the close that follows.
    unfilled = _Bar(2_000, 100.5, 101.0, 99.85, 99.85)
    cfg = _cfg(limit_through_bp=20, limit_cancel_adverse_bp=10)
    assert decide_resting_fill(cfg, _order(), unfilled) == "cancel"


def _shadow_order(**extra):
    order = {
        "symbol": "ETH", "side": LONG, "px": 100.0, "stop": 99.0, "take_profit": 105.0,
        "atr": 1.0, "points": 80.0, "placed_ts": 1_700_000_000.0,
        "placed_ms": 1_700_000_000_000, "placed_close": 100.6, "class": "crypto",
        "bars_waited": 0,
    }
    order.update(extra)
    return order


# Pierces the limit, prints below the stop, and closes back above the limit.
FILL_BAR = (1_700_003_600_000, 100.5, 101.0, 98.5, 100.3, 1.0)
NEXT_BAR = (1_700_007_200_000, 100.3, 100.4, 98.0, 98.2, 1.0)


def test_the_shadow_books_a_reclaim_at_its_close_and_charges_it_as_a_taker():
    params = ScannerParams()
    spec = dataclasses.replace(REALISTIC, name="reclaim", reclaim=True)
    positions, meta, trades = {}, {}, []
    _mark_to_market([_shadow_order(reclaim=True)], positions, meta, {"ETH": FILL_BAR},
                    params, spec, _Cfg(spec), trades)
    assert trades == [], "stopped by a low that printed before the entry existed"
    assert positions["ETH"].entry == pytest.approx(100.3)
    _mark_to_market([], positions, meta, {"ETH": NEXT_BAR}, params, spec, _Cfg(spec), trades)
    (trade,) = trades
    assert trade["fee_bp"] == pytest.approx(leg_rate_pct(TAKER) * 100.0 + spec.exit_fee_bp)


def test_the_shadow_still_fills_a_resting_limit_at_its_price_and_exposes_it_to_the_bar():
    params = ScannerParams()
    spec = REALISTIC
    positions, meta, trades = {}, {}, []
    _mark_to_market([_shadow_order()], positions, meta, {"ETH": FILL_BAR},
                    params, spec, _Cfg(spec), trades)
    (trade,) = trades
    assert trade["fee_bp"] == pytest.approx(spec.entry_fee_bp + spec.exit_fee_bp)
    assert trade["gross_bp"] < 0, "a resting fill at 100 sees the same bar's low of 98.5"


T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
SYMS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
N = 140
SIGNAL = 110
PLAN = scanner.EntryPlan("ETH/USDT:USDT", scanner.LONG, 99.5, 97.0, 104.5, 0.025, 1.0, 82.0)


def _bars(sym, fill_bar):
    out = []
    for i in range(N):
        low, close = (96.5, 100.0) if fill_bar and i == SIGNAL + 1 else (99.7, 100.0)
        out.append(BacktestBar(timestamp=T0 + timedelta(hours=i), open=100.0, high=100.3,
                               low=low, close=close, volume=1_000.0, symbol=sym))
    return out


@pytest.fixture
def one_plan(monkeypatch):
    real = scanner.scan_pass
    calls: list = []

    def scan(snaps, params, held=frozenset()):
        calls.append(held)
        if len(calls) == SIGNAL - 99:
            return scanner.Pass(scanner.Regime(scanner.LONG, 1.0, 2, True, "test"), (), PLAN, "")
        return real(snaps, params, held)

    monkeypatch.setattr(scanner, "scan_pass", scan)


def _eth_trades(**cfg):
    data = {s: _bars(s, s.startswith("ETH")) for s in SYMS}
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0, scanner="limit_entry",
                                            limit_through_bp=5.0, **cfg), symbols=list(SYMS))
    try:
        result = asyncio.run(pb.run(data))
    finally:
        pb.cleanup()
    return [t for t in result.trades if t.symbol.startswith("ETH")]


def test_the_backtest_enters_a_reclaim_at_its_close_after_that_bars_stop_check(one_plan):
    (trade,) = _eth_trades(limit_reclaim=True)
    assert trade.entry_time == T0 + timedelta(hours=SIGNAL + 1)
    assert trade.entry_price == pytest.approx(100.0, rel=1e-3), \
        "a reclaim was booked at the limit, a price gone by the close that confirmed it"
    assert trade.exit_time is None or trade.exit_time > trade.entry_time, \
        "stopped by the fill bar's own low, which printed before the entry"


def test_the_backtest_still_fills_a_resting_limit_on_its_touch(one_plan):
    (trade,) = _eth_trades()
    assert trade.entry_time == T0 + timedelta(hours=SIGNAL + 1)
    assert trade.entry_price == pytest.approx(99.5, rel=1e-3)


def _single_engine_trades(monkeypatch, **cfg):
    """The single-symbol `BacktestEngine.run()` loop, one limit rested at SIGNAL."""
    from bot.backtest.engine import BacktestEngine
    from bot.utils.models import Direction, TradeIdea

    eng = BacktestEngine(BacktestConfig(symbol="ETH/USDT", timeframe="1h",
                                        initial_balance=100_000.0, slippage_pct=0.0,
                                        commission_pct=0.0, limit_through_bp=5.0, **cfg))
    idea = TradeIdea(id="TI-reclaim", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=99.5, stop_loss=97.0, take_profit=104.5, confidence=0.8,
                     reasoning="t", source="t", timestamp=T0, order_type="limit")
    check = SimpleNamespace(position_size_usd=1_000.0, verdict=SimpleNamespace(value="APPROVED"))

    async def rest_once(bar, window, i):
        if i == SIGNAL:
            eng._rest_limit(idea, check, bar)

    monkeypatch.setattr(eng, "_process_bar", rest_once)
    monkeypatch.setattr(eng, "_wants_signal_bar", lambda i: True)
    try:
        result = asyncio.run(eng.run(_bars("ETH/USDT", True)))
    finally:
        cleanup = getattr(eng, "cleanup", None)
        if callable(cleanup):
            cleanup()
    return result.trades


def test_the_single_engine_run_enters_a_reclaim_at_its_close_after_the_stop_check(monkeypatch):
    (trade,) = _single_engine_trades(monkeypatch, limit_reclaim=True)
    assert trade.entry_time == T0 + timedelta(hours=SIGNAL + 1)
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_time is None or trade.exit_time > trade.entry_time


def test_the_single_engine_run_still_fills_a_resting_limit_on_its_touch(monkeypatch):
    (trade,) = _single_engine_trades(monkeypatch)
    assert trade.entry_price == pytest.approx(99.5)
