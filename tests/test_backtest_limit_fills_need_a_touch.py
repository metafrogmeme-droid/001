"""RC-2026-018 — the backtest booked entries at prices no bar ever traded.

`CONFIG.limit_orders` defaults to `enabled=True, default_order_type="limit"`,
so the analyzer sets `idea.entry_price` to a pullback level up to 1 ATR BELOW
the close. `bot/backtest/engine.py` then called

    self._execute_fill(idea, risk_check, idea.entry_price, bar)

unconditionally on the signal bar. `bot/backtest/` referenced `order_type`
zero times, so nothing asked whether any bar had traded at that level.

WHICH DIRECTION THE ERROR RUNS, and it is the bad one. A resting limit fills
only when price comes back to it. The runs where price does NOT come back are
exactly the runs where the signal was right and the move ran away. So the
engine was collecting the winners a real limit order would have MISSED, and
booking them at the best price of the bar. The mislabelling went deep enough
that `_execute_fill`'s own docstring said "bar close in legacy mode" while
that call site passed the limit price.

SCOPE, from the register and re-checked here: `runner.py` forces
`fill_mode="next_open"` under `--honest`, which the frozen benchmark and the
marketplace scorecards use, so those were never affected. This is the DEFAULT
path — `backtest_deep_results.json`, `/backtest`, `/walk_forward`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from bot.compat import UTC
from bot.utils.models import Direction, TradeIdea

_T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _engine():
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    return BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", initial_balance=100_000.0,
        slippage_pct=0.0, commission_pct=0.0))


def _bar(low, high, close=None, hours=0, open_=None):
    from bot.backtest.models import BacktestBar
    return BacktestBar(
        timestamp=_T0 + timedelta(hours=hours),
        open=open_ if open_ is not None else (low + high) / 2,
        high=high, low=low,
        close=close if close is not None else (low + high) / 2,
        volume=1000.0, symbol="BTC/USDT")


def _risk_check(size_usd=1_000.0):
    return SimpleNamespace(position_size_usd=size_usd,
                           verdict=SimpleNamespace(value="APPROVED"))


def _idea(entry, direction=Direction.LONG, order_type="limit", tid="TI-1"):
    sl, tp = (entry * 0.95, entry * 1.10) if direction == Direction.LONG \
        else (entry * 1.05, entry * 0.90)
    return TradeIdea(
        id=tid, asset="BTC/USDT", direction=direction,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        confidence=0.7, reasoning="x", source="t", timestamp=_T0,
        order_type=order_type,
    )


def _entries(eng):
    return [round(float(p.entry_price), 6) for p in eng.portfolio.open_positions]


# ── the defect itself ─────────────────────────────────────────────────────

def test_a_limit_below_the_whole_bar_does_not_fill():
    """The finding in one assertion.

    Signal bar trades 99..101; the limit rests at 95. Before this fix the
    engine opened a position at 95 — a price the bar's own range says was
    never printed.
    """
    eng = _engine()
    eng._place_entry(_idea(95.0), _risk_check(), _bar(99.0, 101.0, close=100.0))
    assert eng.portfolio.open_positions == [], "filled at a price the bar never traded"
    assert len(eng._pending_limits) == 1, "the order should be resting, not gone"


def test_it_fills_on_a_LATER_bar_that_reaches_it():
    """Resting, not cancelled: the order is still live and still fillable."""
    eng = _engine()
    eng._place_entry(_idea(95.0), _risk_check(), _bar(99.0, 101.0, close=100.0))
    eng._drain_pending_limits(_bar(94.0, 99.0, close=96.0, hours=1))
    assert _entries(eng) == [95.0]
    assert eng._pending_limits == []
    assert eng._limits_filled == 1


def test_a_limit_the_signal_bar_reaches_fills_at_the_limit():
    """Touched on its own bar: the price IS one the market printed."""
    eng = _engine()
    eng._place_entry(_idea(99.5), _risk_check(), _bar(99.0, 101.0, close=100.0))
    assert _entries(eng) == [99.5]
    assert eng._limits_filled_same_bar == 1


def test_a_market_order_fills_at_the_close_not_at_entry_price():
    """What `fill_mode="close"` has always been named for and never did."""
    eng = _engine()
    eng._place_entry(_idea(95.0, order_type="market"), _risk_check(),
                     _bar(99.0, 101.0, close=100.0))
    assert _entries(eng) == [100.0]
    assert eng._pending_limits == []


def test_a_short_limit_needs_the_bar_to_trade_UP_to_it():
    """The mirror case. A short limit rests ABOVE the market."""
    eng = _engine()
    eng._place_entry(_idea(105.0, direction=Direction.SHORT), _risk_check(),
                     _bar(99.0, 101.0, close=100.0))
    assert eng.portfolio.open_positions == []
    eng._drain_pending_limits(_bar(101.0, 106.0, close=104.0, hours=1))
    assert _entries(eng) == [105.0]


# ── the lifecycle a resting order must have ───────────────────────────────

def test_an_untouched_limit_expires_and_releases_its_intent():
    """Expiry mirrors CONFIG.limit_orders.expire_seconds (4h default).

    The intent matters as much as the order: `_execute_fill` clears it on the
    fill branch, so an expiry that did not would hold size against every later
    idea in the run — a slow leak that reads as the strategy going quiet.
    """
    from bot.config import CONFIG
    eng = _engine()
    idea = _idea(95.0)
    eng._place_entry(idea, _risk_check(), _bar(99.0, 101.0, close=100.0))
    cleared = []
    eng.risk.clear_pending_intent = lambda i: cleared.append(i)

    past = float(CONFIG.limit_orders.expire_seconds) / 3600.0 + 1
    eng._drain_pending_limits(_bar(99.0, 101.0, close=100.0, hours=past))

    assert eng.portfolio.open_positions == []
    assert eng._pending_limits == []
    assert eng._limits_expired == 1
    assert cleared == [idea.id], "the pending intent leaked"


def test_a_limit_left_behind_by_price_is_cancelled_on_drift():
    from bot.config import CONFIG
    eng = _engine()
    idea = _idea(95.0)
    eng._place_entry(idea, _risk_check(), _bar(99.0, 101.0, close=100.0))
    cleared = []
    eng.risk.clear_pending_intent = lambda i: cleared.append(i)

    drift = float(CONFIG.limit_orders.price_drift_cancel_pct)
    away = 95.0 * (1 + (drift + 1) / 100.0)
    eng._drain_pending_limits(_bar(away - 0.5, away + 0.5, close=away, hours=1))

    assert eng.portfolio.open_positions == []
    assert eng._limits_cancelled_drift == 1
    assert cleared == [idea.id]


def test_a_limit_still_inside_the_drift_band_keeps_resting():
    """Guard the guard: a cancel-everything rule would pass the two above."""
    eng = _engine()
    eng._place_entry(_idea(95.0), _risk_check(), _bar(99.0, 101.0, close=100.0))
    eng._drain_pending_limits(_bar(95.5, 96.5, close=96.0, hours=1))
    assert len(eng._pending_limits) == 1
    assert eng._limits_cancelled_drift == 0
    assert eng._limits_expired == 0


@pytest.mark.parametrize("low,high", [(None, 101.0), ("x", 101.0), (99.0, None)])
def test_an_unreadable_bar_is_not_a_touch(low, high):
    """Absent is not a fill. Treating it as one manufactures the entry."""
    from bot.backtest.engine import BacktestEngine
    bar = SimpleNamespace(low=low, high=high, close=100.0)
    assert BacktestEngine._limit_is_touched(bar, 95.0, Direction.LONG) is False
    assert BacktestEngine._limit_is_touched(bar, 105.0, Direction.SHORT) is False


# ── the property the audit asked for ──────────────────────────────────────

def test_every_recorded_entry_lies_inside_some_bar_that_traded_it():
    """The register's own acceptance test, over a whole synthetic run.

    Drives a descending-then-recovering series past four limits at spread
    levels and asserts the invariant directly: no position may carry an entry
    price outside the range of every bar at or after its signal bar.
    """
    eng = _engine()
    # A slow decline, so limits a realistic distance below the close come into
    # range rather than being drift-cancelled first. The levels are chosen
    # against live's own rule (`live_executor.py:6721`): a limit further than
    # `price_drift_cancel_pct` from the price is stale and gets cancelled, in
    # live and therefore here.
    bars = [_bar(100 - i * 0.5, 102 - i * 0.5, close=101 - i * 0.5, hours=i)
            for i in range(12)]
    levels = (99.5, 99.0, 98.0)

    for n, px in enumerate(levels):
        eng._place_entry(_idea(px, tid=f"TI-{n}"), _risk_check(500.0), bars[0])
    for b in bars[1:]:
        eng._drain_pending_limits(b)

    assert eng.portfolio.open_positions, "the run filled nothing — check the fixture"
    lo = min(float(b.low) for b in bars)
    hi = max(float(b.high) for b in bars)
    for px in _entries(eng):
        assert lo <= px <= hi, (
            f"entry {px} lies outside every bar's range [{lo}, {hi}]")

    # CONSERVATION. Every order placed must end in exactly one state. Without
    # this the invariant above is satisfiable by an engine that quietly drops
    # orders it cannot fill — which would read as a clean run.
    accounted = (eng._limits_filled + eng._limits_expired
                 + eng._limits_cancelled_drift + len(eng._pending_limits))
    assert accounted == len(levels), (
        f"{len(levels)} orders placed, {accounted} accounted for")


def test_drift_is_measured_the_way_the_LIVE_executor_measures_it():
    """Same formula, or the backtest diverges from live in a NEW way.

    `bot/core/live_executor.py:6721` computes
    `abs(cur_price - entry_price) / entry_price * 100` and cancels above
    `price_drift_cancel_pct`. Modelling drift differently here would trade one
    fill-assumption defect for another, which is the whole failure this change
    exists to end. Driven at the boundary: just inside stays, just outside goes.
    """
    from bot.config import CONFIG
    band = float(CONFIG.limit_orders.price_drift_cancel_pct)
    # BELOW the signal bar's low, or it fills on placement and the drift
    # branch is never reached — the first draft of this test did exactly that
    # and passed for the wrong reason.
    px = 95.0

    inside = px * (1 + (band - 0.5) / 100.0)
    eng = _engine()
    eng._place_entry(_idea(px), _risk_check(), _bar(99.0, 101.0, close=100.0))
    eng._drain_pending_limits(_bar(inside - 0.1, inside + 0.1, close=inside, hours=1))
    assert len(eng._pending_limits) == 1, "cancelled inside the band"

    outside = px * (1 + (band + 0.5) / 100.0)
    eng2 = _engine()
    eng2._place_entry(_idea(px), _risk_check(), _bar(99.0, 101.0, close=100.0))
    eng2._drain_pending_limits(_bar(outside - 0.1, outside + 0.1, close=outside, hours=1))
    assert eng2._pending_limits == [], "kept outside the band"
    assert eng2._limits_cancelled_drift == 1


def test_the_run_reports_how_many_entries_never_filled():
    """A count that stays 0/0/0/0 cannot distinguish 'all filled' from 'unasked'.

    Before this change the engine had no limit lifecycle at all, so every one
    of these fields was structurally zero — which is exactly how a fill
    assumption hides inside a performance figure.
    """
    eng = _engine()
    eng._place_entry(_idea(99.5, tid="a"), _risk_check(), _bar(99.0, 101.0, close=100.0))
    eng._place_entry(_idea(80.0, tid="b"), _risk_check(), _bar(99.0, 101.0, close=100.0))
    assert eng._limits_filled == 1
    assert eng._limits_filled_same_bar == 1
    assert len(eng._pending_limits) == 1


def test_the_signal_path_places_rather_than_filling_outright():
    """WIRING. The seam above is useless if the caller bypasses it.

    Source-scanned with comments stripped, because the comment explaining this
    fix necessarily quotes the old call — the trap CLAUDE.md records four
    times. What is asserted is the POSITIVE: the default path routes through
    `_place_entry`.
    """
    import inspect
    import re
    import tokenize
    from pathlib import Path

    src_path = Path(inspect.getfile(
        __import__("bot.backtest.engine", fromlist=["x"])))
    out = []
    with open(src_path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            out.append(re.sub(r"\S", " ", tok.string)
                       if tok.type in (tokenize.COMMENT, tokenize.STRING)
                       else tok.string)
            out.append(" ")
    code = "".join(out)
    assert "_place_entry ( idea , risk_check , bar )" in re.sub(r"\s+", " ", code)
    assert "_drain_pending_limits ( current_bar )" in re.sub(r"\s+", " ", code), \
        "resting limits are never drained in the bar loop"
