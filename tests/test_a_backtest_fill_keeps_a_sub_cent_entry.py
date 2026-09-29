"""The backtest books a fill at the price it filled at, whatever the price.

`_execute_fill` handed the portfolio `round(adjusted_entry, 6)`: six decimal
places, an absolute grid, on a relative quantity. A sub-cent asset's price
sits on that grid at one or two significant digits, so the frozen snapshots'
PEPE, FLOKI, SHIB and TAG rows were sized and priced off a different entry
from the one they filled at. Driven before the fix, on a real
`PortfolioTracker`:

- a PEPE long filled at 0.0000114957 was booked at 0.000011, and a flat exit
  read +$4.38 on $100 (the short mirror, -$4.52);
- a long whose stop sat at 0.000011 was refused as a stop at its own entry,
  and counted as a risk rejection;
- an entry of 4.9e-07 rounded to 0.0 and was refused as a zero price.

Measured over the snapshots' closes, the rounding moved PEPE's price by a
median 6.1% in `alts_1h` and SHIB's by 2.5% in `corr_dense_1h`.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from bot.compat import UTC
from bot.utils.models import Direction, TradeIdea


def _engine(slippage_pct: float = 0.05):
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    return BacktestEngine(BacktestConfig(
        symbol="PEPE/USDT", timeframe="1h", initial_balance=10_000.0,
        slippage_pct=slippage_pct, commission_pct=0.0))


def _bar():
    from bot.backtest.models import BacktestBar
    return BacktestBar(timestamp=datetime(2025, 1, 1, 5, tzinfo=UTC),
                       open=1.1e-05, high=1.2e-05, low=1.0e-05, close=1.15e-05,
                       volume=1e9, symbol="PEPE/USDT")


def _check(size_usd: float = 100.0):
    return SimpleNamespace(position_size_usd=size_usd,
                           verdict=SimpleNamespace(value="APPROVED"))


def _idea(direction, entry, sl, tp, iid="TI-PEPE"):
    return TradeIdea(
        id=iid, asset="PEPE/USDT", direction=direction,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        confidence=0.7, reasoning="x", source="t",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC))


def _fill(eng, idea, fill_price):
    eng._execute_fill(idea, _check(), fill_price=fill_price, bar=_bar())
    positions = eng.portfolio.open_positions
    return positions[0] if positions else None


class TestTheEntryIsTheFill:
    @pytest.mark.parametrize("direction,sl,tp,sign", [
        (Direction.LONG, 1.08e-05, 1.30e-05, 1),
        (Direction.SHORT, 1.20e-05, 1.00e-05, -1),
    ])
    def test_the_booked_entry_is_the_slipped_fill(self, direction, sl, tp, sign):
        eng = _engine()
        try:
            fill = 1.14957e-05
            pos = _fill(eng, _idea(direction, fill, sl, tp), fill)
            assert pos is not None
            want = fill * (1 + sign * 0.05 / 100)
            assert pos.entry_price == pytest.approx(want, rel=1e-12, abs=0)
        finally:
            eng.cleanup()

    @pytest.mark.parametrize("direction,sl,tp,sign", [
        (Direction.LONG, 1.08e-05, 1.30e-05, 1),
        (Direction.SHORT, 1.20e-05, 1.00e-05, -1),
    ])
    def test_an_exit_at_the_price_it_filled_at_books_no_pnl(self, direction, sl, tp, sign):
        eng = _engine()
        try:
            fill = 1.14957e-05
            pos = _fill(eng, _idea(direction, fill, sl, tp), fill)
            slipped = fill * (1 + sign * 0.05 / 100)
            closed = eng.portfolio.close_position(pos.trade_id, slipped)
            assert closed.pnl == pytest.approx(0.0, abs=1e-9)
        finally:
            eng.cleanup()

    def test_a_stop_just_under_a_sub_cent_entry_opens(self):
        """The rounded entry landed on the stop, and the model refused a stop
        at the entry: the fill was counted as a risk rejection."""
        eng = _engine(slippage_pct=0.0)
        try:
            before = eng._ideas_rejected_risk
            pos = _fill(eng, _idea(Direction.LONG, 1.1496e-05, 1.1e-05, 1.3e-05),
                        1.1496e-05)
            assert pos is not None
            assert eng._ideas_rejected_risk == before
        finally:
            eng.cleanup()

    def test_an_entry_under_half_a_millionth_is_not_rounded_to_zero(self):
        eng = _engine(slippage_pct=0.0)
        try:
            pos = _fill(eng, _idea(Direction.LONG, 4.9e-07, 4.5e-07, 6.0e-07, "TI-MICRO"),
                        4.9e-07)
            assert pos is not None
            assert pos.entry_price == pytest.approx(4.9e-07, rel=1e-12, abs=0)
        finally:
            eng.cleanup()
