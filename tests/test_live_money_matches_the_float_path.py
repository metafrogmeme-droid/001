"""The live base size and the price-derived close publish the float path.

Stage C. ``fixed_fractional_margin`` and ``price_close`` are the
arithmetic. A realistic card matches the float formula at the place the
caller publishes (cents for a size, four decimals for a close). A
half-cent boundary is one unit apart, and both are pinned, so a reading
that snapped back onto the float would pass the first and fail the
second.

The engine and the three close sites ask the names they imported. A
patch of the module they did not import leaves the real formula in
place, which is how a second copy hides.
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.size_trace import size_steps
from bot.core.trade_costs import entry_rate_pct, exit_rate_pct
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.live_money import (
    FractionalSize,
    PriceClose,
    fixed_fractional_margin,
    price_close,
)
from bot.utils.models import Direction, TradeIdea

_STUB = PriceClose(gross=12.5, commission=0.25, net=12.25)


def _float_size(equity, risk_pct, entry, stop, floor=0.001):
    """The division ``_evaluate_locked`` used before this reading."""
    dist = abs(entry - stop) / entry if entry > 0 else 0.0
    if dist < floor:
        dist = floor
    return equity * (risk_pct / 100.0) / dist, dist


def _float_close(entry, exit_p, qty, entry_fee, exit_fee, is_long):
    """The two-leg close the three sites used before this reading."""
    gross = (exit_p - entry) * qty if is_long else (entry - exit_p) * qty
    commission = (entry * qty * entry_fee + exit_p * qty * exit_fee) / 100.0
    return gross, commission, gross - commission


# (equity, risk_pct, entry, stop) and the published cents both paths share.
_SIZE_MATCH = [
    ((10_000, 2, 100, 97), 6666.67),
    ((10_000, 2, 100, 80), 1000.0),
    ((200, 1, 50, 48), 50.0),
    ((50, 0.5, 1.5, 1.44), 6.25),
    ((1000, 2, 0, 99), 20_000.0),
    ((1000, 2, -1, 99), 20_000.0),
    ((12345.67, 2, 100, 97), 8230.45),
]

# (entry, exit, qty, entry_fee, exit_fee, is_long) and the published 4dp.
_CLOSE_MATCH = [
    ((50_000, 55_000, 0.004, 0.06, 0.06, True), (20.0, 0.252, 19.748)),
    ((0.0522, 0.061, 958, 0.08, 0.12, True), (8.4304, 0.1101, 8.3203)),
    ((2.367, 2.1, 12.67, 0.06, 0.06, True), (-3.3829, 0.034, -3.4168)),
    ((184.6, 190, 0.276, 0.06, 0.06, False), (-1.4904, 0.062, -1.5524)),
    ((63000.25, 62_900, 0.01, 0.02, 0.06, False), (1.0025, 0.5034, 0.4991)),
    ((4321.5, 4100.25, 0.039, 0.06, 0.06, False), (8.6288, 0.1971, 8.4317)),
]


def _published_close(fig):
    return (round(fig[0], 4), round(fig[1], 4), round(fig[2], 4))


class TestThePublishedPlaceMatchesOnARealisticCard:
    @pytest.mark.parametrize("args,cents", _SIZE_MATCH)
    def test_the_size_publishes_the_float_cents(self, args, cents):
        equity, risk, entry, stop = args
        float_margin, float_dist = _float_size(*args)
        sized = fixed_fractional_margin(*args)
        assert round(float_margin, 2) == cents
        assert round(sized.margin, 2) == cents
        assert sized.stop_distance_pct == pytest.approx(float_dist)

    @pytest.mark.parametrize("args,published", _CLOSE_MATCH)
    def test_the_close_publishes_the_float_four_decimals(self, args, published):
        entry, exit_p, qty, entry_fee, exit_fee, is_long = args
        float_fig = _float_close(entry, exit_p, qty, entry_fee, exit_fee, is_long)
        priced = price_close(
            entry, exit_p, qty, entry_fee, exit_fee, is_long=is_long,
        )
        assert _published_close(float_fig) == published
        assert _published_close(
            (priced.gross, priced.commission, priced.net),
        ) == published


class TestAHalfCentBoundaryIsOneUnitApart:
    def test_the_size_rounds_to_different_cents(self):
        args = (1000, 5, 100, 99.89999999)
        float_margin, _dist = _float_size(*args)
        sized = fixed_fractional_margin(*args)
        assert round(float_margin, 2) == 49999.99
        assert round(sized.margin, 2) == 50_000.0

    def test_the_close_moves_commission_and_net(self):
        args = (100.0, 100.1, 3.5, 0.1, 0.1, True)
        float_fig = _float_close(*args)
        priced = price_close(*args[:-1], is_long=args[-1])
        assert _published_close(float_fig) == (0.35, 0.7003, -0.3504)
        assert _published_close(
            (priced.gross, priced.commission, priced.net),
        ) == (0.35, 0.7004, -0.3503)


class TestAnUnreadableInputRaises:
    @pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, "1%"])
    def test_each_size_argument(self, bad):
        with pytest.raises(ValueError, match="equity is not a money figure"):
            fixed_fractional_margin(bad, 2, 100, 97)
        with pytest.raises(ValueError, match="risk_pct is not a money figure"):
            fixed_fractional_margin(1000, bad, 100, 97)
        with pytest.raises(ValueError, match="entry is not a money figure"):
            fixed_fractional_margin(1000, 2, bad, 97)
        with pytest.raises(ValueError, match="stop is not a money figure"):
            fixed_fractional_margin(1000, 2, 100, bad)

    @pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, "1%"])
    def test_each_close_argument(self, bad):
        with pytest.raises(ValueError, match="entry is not a money figure"):
            price_close(bad, 110, 1, 0.06, 0.06, is_long=True)
        with pytest.raises(ValueError, match="exit is not a money figure"):
            price_close(100, bad, 1, 0.06, 0.06, is_long=True)
        with pytest.raises(ValueError, match="quantity is not a money figure"):
            price_close(100, 110, bad, 0.06, 0.06, is_long=True)
        with pytest.raises(ValueError, match="entry_fee_pct is not a money figure"):
            price_close(100, 110, 1, bad, 0.06, is_long=True)
        with pytest.raises(ValueError, match="exit_fee_pct is not a money figure"):
            price_close(100, 110, 1, 0.06, bad, is_long=True)

    def test_the_side_is_keyword_only(self):
        with pytest.raises(TypeError):
            price_close(100, 110, 1, 0.06, 0.06, True)  # type: ignore[misc]


class TestANonPositiveEntryKeepsTheFloor:
    @pytest.mark.parametrize("entry", [0, -1])
    def test_the_distance_is_the_floor_and_the_margin_follows(self, entry):
        sized = fixed_fractional_margin(1000, 2, entry, 99)
        assert sized.stop_distance_pct == 0.001
        assert sized.margin == 20_000.0

    def test_a_caller_supplied_floor_lifts_a_tighter_stop(self):
        sized = fixed_fractional_margin(1000, 2, 100, 99.99, floor=0.05)
        assert sized.stop_distance_pct == 0.05
        assert sized.margin == 400.0


class TestAZeroFeeAndAZeroQuantityAreReadings:
    def test_a_zero_quantity_is_a_flat_close(self):
        priced = price_close(100, 110, 0, 0.06, 0.06, is_long=True)
        assert (priced.gross, priced.commission, priced.net) == (0.0, 0.0, 0.0)

    def test_a_zero_fee_leaves_the_gross_as_the_net(self):
        priced = price_close(100, 110, 1, 0, 0, is_long=False)
        assert priced.commission == 0.0
        assert priced.gross == priced.net == -10.0


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-live-money-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea():
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=97.0, take_profit=109.0, confidence=0.72,
        reasoning="live money",
    )


class TestTheEngineAsksTheReading:
    def test_the_first_step_is_what_the_reading_returned(self, monkeypatch):
        seen = []

        def planted(equity, risk_pct, entry, stop, *, floor=0.001):
            seen.append((equity, risk_pct, entry, stop))
            return FractionalSize(margin=123.45, stop_distance_pct=0.03)

        monkeypatch.setattr("bot.risk.risk_engine.fixed_fractional_margin", planted)
        idea = _idea()
        _engine().evaluate(idea, atr=2.0)
        assert len(seen) == 1
        equity, risk_pct, entry, stop = seen[0]
        assert equity == 10_000.0
        assert risk_pct == CONFIG.strategy_types.get_max_risk_pct("swing")
        assert (entry, stop) == (100.0, 97.0)
        step = size_steps(idea)[0]
        assert step.label.startswith("fixed-fractional")
        assert step.usd == 123.45
        assert "3.00" in step.label


def _plant_close(monkeypatch):
    calls = []

    def planted(entry, exit_price, quantity, entry_fee, exit_fee, *, is_long):
        calls.append((entry, exit_price, quantity, entry_fee, exit_fee, is_long))
        return _STUB

    monkeypatch.setattr("bot.core.live_executor.price_close", planted)
    return calls


def _limit_pos(trade_id, direction, entry, qty, stop, target):
    return LivePosition(
        trade_id=trade_id, symbol="BTC/USDT", direction=direction,
        entry_price=entry, quantity=qty, cost_usd=100.0,
        stop_loss=stop, take_profit=target, status="open",
        order_type="limit",
    )


def _assert_stub(pos, calls, *, exit_price, quantity, is_long):
    assert len(calls) == 1
    entry, exit_p, qty, entry_fee, exit_fee, side = calls[0]
    assert (entry, exit_p, qty, side) == (pos.entry_price, exit_price, quantity, is_long)
    assert entry_fee == entry_rate_pct("limit")
    assert exit_fee == exit_rate_pct()
    assert entry_fee != exit_fee
    assert pos.gross_pnl == _STUB.gross
    assert pos.commission == _STUB.commission
    assert pos.pnl_usd == _STUB.net


class TestTheCloseTheBotPlacesAsksTheReading:
    @pytest.mark.asyncio
    async def test_no_venue_pnl_prices_the_fill(self, monkeypatch):
        calls = _plant_close(monkeypatch)
        executor = LiveExecutor()
        mock_ex = AsyncMock()
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-1", "average": 105_000.0, "filled": 0.001,
            "cost": 105.0, "status": "closed",
        })
        mock_ex.fetch_my_trades = AsyncMock(return_value=[])
        executor._exchange = mock_ex
        pos = _limit_pos("T-PRICE", "LONG", 100_000.0, 0.001, 98_000.0, 110_000.0)
        executor._positions[pos.trade_id] = pos
        executor._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 105_000.0, "fill_qty": 0.001,
            "fees": 0.0, "remaining_qty": 0.0, "failure_stage": "",
        })
        executor._fetch_bitget_close_data = AsyncMock(return_value=None)
        with patch("asyncio.sleep", new=AsyncMock()):
            await executor.close_position(pos.trade_id, reason="manual")
        _assert_stub(pos, calls, exit_price=105_000.0, quantity=0.001, is_long=True)
        assert any(t.trade_id == pos.trade_id for t in executor._closed_trades)

    @pytest.mark.asyncio
    async def test_a_venue_pnl_does_not_ask_the_reading(self, monkeypatch):
        calls = _plant_close(monkeypatch)
        executor = LiveExecutor()
        mock_ex = AsyncMock()
        mock_ex.create_order = AsyncMock(return_value={
            "id": "CLOSE-1", "average": 105_000.0, "filled": 0.001,
            "cost": 105.0, "status": "closed",
        })
        executor._exchange = mock_ex
        pos = _limit_pos("T-VENUE", "LONG", 100_000.0, 0.001, 98_000.0, 110_000.0)
        executor._positions[pos.trade_id] = pos
        executor._verify_position_closed = AsyncMock(return_value={
            "confirmed": True, "fill_price": 105_000.0, "fill_qty": 0.001,
            "fees": 0.0, "remaining_qty": 0.0, "failure_stage": "",
        })
        executor._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 105_000.0, "pnl": 4.8, "fees": 0.2,
            "reason": "TP HIT (exchange)", "source": "bitget_position_history",
            "pnl_is_net": True,
        })
        with patch("asyncio.sleep", new=AsyncMock()):
            await executor.close_position(pos.trade_id, reason="manual")
        assert calls == []
        assert pos.pnl_usd == pytest.approx(4.8)


class TestTheAlreadyClosedPathAsksTheReading:
    def _executor(self, last):
        ex = LiveExecutor()
        mock = AsyncMock()
        mock.fetch_ticker = AsyncMock(return_value={"last": last})
        ex._exchange = mock
        ex._save_positions = MagicMock()
        ex._fire_position_closed = MagicMock()
        return ex

    @pytest.mark.asyncio
    async def test_a_ticker_close_prices_the_short(self, monkeypatch):
        calls = _plant_close(monkeypatch)
        ex = self._executor(99_000.0)
        ex._fetch_bitget_close_data = AsyncMock(return_value=None)
        pos = _limit_pos("T-GONE", "SHORT", 100_000.0, 0.002, 102_000.0, 95_000.0)
        await ex._handle_already_closed_position(pos)
        _assert_stub(pos, calls, exit_price=99_000.0, quantity=0.002, is_long=False)

    @pytest.mark.asyncio
    async def test_a_venue_pnl_does_not_ask_the_reading(self, monkeypatch):
        calls = _plant_close(monkeypatch)
        ex = self._executor(99_000.0)
        ex._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 99_000.0, "pnl": 1.5, "fees": 0.1,
            "reason": "SL HIT (exchange)", "source": "bitget_position_history",
            "pnl_is_net": True,
        })
        pos = _limit_pos("T-GONE-V", "SHORT", 100_000.0, 0.002, 102_000.0, 95_000.0)
        await ex._handle_already_closed_position(pos)
        assert calls == []
        assert pos.pnl_usd == pytest.approx(1.5)


class TestReconcileAsksTheReading:
    def _executor(self, pos, last):
        executor = LiveExecutor()
        executor._hedge_mode = False
        mock = AsyncMock()
        mock.fetch_positions = AsyncMock(return_value=[])
        mock.fetch_ticker = AsyncMock(return_value={"last": last})
        executor._exchange = mock
        executor._positions[pos.trade_id] = pos
        return executor

    @pytest.mark.asyncio
    async def test_the_ticker_after_the_retries_prices_the_long(self, monkeypatch):
        calls = _plant_close(monkeypatch)
        pos = _limit_pos("T-REC", "LONG", 50_000.0, 0.01, 48_000.0, 55_000.0)
        pos._reconcile_retries = 10
        executor = self._executor(pos, 51_000.0)
        executor._fetch_bitget_close_data = AsyncMock(return_value=None)
        with patch("asyncio.sleep", new=AsyncMock()):
            await executor.reconcile_positions()
        _assert_stub(pos, calls, exit_price=51_000.0, quantity=0.01, is_long=True)
        assert any(t.trade_id == pos.trade_id for t in executor._closed_trades)

    @pytest.mark.asyncio
    async def test_a_venue_pnl_does_not_ask_the_reading(self, monkeypatch):
        calls = _plant_close(monkeypatch)
        pos = _limit_pos("T-REC-V", "LONG", 50_000.0, 0.01, 48_000.0, 55_000.0)
        executor = self._executor(pos, 51_000.0)
        executor._fetch_bitget_close_data = AsyncMock(return_value={
            "close_price": 51_000.0, "pnl": 8.0, "fees": 0.2,
            "reason": "TP HIT (exchange)", "source": "bitget_position_history",
            "pnl_is_net": True,
        })
        await executor.reconcile_positions()
        assert calls == []
        assert pos.pnl_usd == pytest.approx(8.0)
