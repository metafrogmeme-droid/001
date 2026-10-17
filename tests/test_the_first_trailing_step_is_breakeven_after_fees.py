"""The trail's first step is breakeven after the round trip, as TP1's lock is.

At +1R the multistage trail floors the stop at its stage-1 level, which was
the bare entry price (`floor_r = 0.0`). A stop-out there pays the whole round
trip: 0.08% of notional on a limit entry and 0.12% on a market one at the
shipped rates. The books still read it as "breakeven". TP1's lock was moved
past the fees in the breakeven-after-fees chapter; the trail, which binds
first (at 1R, before TP1's 1.5R), was not.

Driven before the fix, a long from 100 with 1R = 2 and ATR 2 reached 102.4.
The live executor sent the venue a stop of 100.0 for a market entry, where a
stop-out costs nothing at 100.12.

Each runtime hands the trail its own round trip, the one TP1's lock reads
there. Live reads `round_trip_pct` of the position's entry leg on every pass.
The backtest uses two legs of its commission, and the paper book uses two
legs of the rate it charges at close. A state recorded without a round trip
floors where it always did.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.core.live_executor as le
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.trade_costs import round_trip_pct
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, TradeIdea
from bot.utils.trailing import make_trailing_state, update_trailing_stop


def _net_pct(entry: float, exit_px: float, rt: float, direction: str) -> float:
    """A close at ``exit_px`` in percent of notional, after the round trip."""
    move = (exit_px / entry - 1.0) * 100.0
    return (move if direction == "LONG" else -move) - rt


# ── the trail itself ────────────────────────────────────────────────────────

class TestTheFloor:
    @pytest.mark.parametrize("rt", [0.08, 0.12])
    def test_a_long_first_step_floors_past_the_fees(self, rt):
        st = make_trailing_state(100.0, "LONG", 2.0, 2.0, fee_round_trip_pct=rt)
        sl, active = update_trailing_stop(st, 102.4, 98.0, "LONG")
        assert active and st["stage"] == 1
        assert sl == pytest.approx(100.0 + rt)
        assert _net_pct(100.0, sl, rt, "LONG") == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.parametrize("rt", [0.08, 0.12])
    def test_a_short_mirrors_it(self, rt):
        st = make_trailing_state(100.0, "SHORT", 2.0, 2.0, fee_round_trip_pct=rt)
        sl, active = update_trailing_stop(st, 97.6, 102.0, "SHORT")
        assert active and st["stage"] == 1
        assert sl == pytest.approx(100.0 - rt)
        assert _net_pct(100.0, sl, rt, "SHORT") == pytest.approx(0.0, abs=1e-9)

    def test_a_state_recorded_without_a_round_trip_floors_at_the_entry(self):
        """A trail state written before the field (or by a hand-built dict)
        behaves as it always did."""
        st = {"entry_price": 100.0, "best_price": 100.0, "trailing_active": False,
              "initial_risk": 2.0, "atr": 2.0, "stage": 0}
        sl, _ = update_trailing_stop(st, 102.4, 98.0, "LONG")
        assert sl == pytest.approx(100.0)

    @pytest.mark.parametrize("bad", [None, float("nan"), -0.1, True, "0.12"])
    def test_an_unreadable_round_trip_is_no_buffer(self, bad):
        st = make_trailing_state(100.0, "LONG", 2.0, 2.0, fee_round_trip_pct=bad)
        sl, _ = update_trailing_stop(st, 102.4, 98.0, "LONG")
        assert sl == pytest.approx(100.0)

    def test_a_later_stage_keeps_its_own_floor_when_it_is_further(self):
        # Stage 2's floor is 0.5R = 1.0 past the entry, past the 0.12% fee.
        st = make_trailing_state(100.0, "LONG", 2.0, 3.0, fee_round_trip_pct=0.12)
        sl, _ = update_trailing_stop(st, 104.2, 98.0, "LONG")
        assert st["stage"] == 2
        assert sl == pytest.approx(101.0)

    def test_a_later_stage_floor_inside_the_fees_is_raised_to_them(self):
        # 1R = 0.1, so stage 2's 0.5R floor (0.05) sits inside the 0.12 fee.
        st = make_trailing_state(100.0, "LONG", 0.1, 0.1, fee_round_trip_pct=0.12)
        sl, _ = update_trailing_stop(st, 100.21, 99.9, "LONG")
        assert st["stage"] == 2
        assert sl == pytest.approx(100.12)

    def test_the_atr_trail_still_wins_when_it_is_tighter(self):
        # Trail at 102.4 - 2 x 0.5 = 101.4 sits above the fee floor.
        st = make_trailing_state(100.0, "LONG", 2.0, 0.5, fee_round_trip_pct=0.12)
        sl, _ = update_trailing_stop(st, 102.4, 98.0, "LONG")
        assert sl == pytest.approx(101.4)


# ── live: the real monitor pass ─────────────────────────────────────────────

def _executor(monkeypatch):
    monkeypatch.setattr(le.asyncio, "sleep", AsyncMock())
    ex = LiveExecutor()
    venue = AsyncMock()
    box = {"p": None}
    venue.fetch_ticker = AsyncMock(
        side_effect=lambda *_a, **_k: {"last": box["p"], "timestamp": time.time() * 1000})
    ex._exchange = venue
    ex.reconcile_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_positions = AsyncMock(return_value=[])
    ex.adopt_exchange_limit_orders = AsyncMock(return_value=[])
    ex._last_exchange_sync = time.time()
    ex._save_positions = lambda *a, **k: None
    ex._record_warning = lambda *a, **k: None
    ex.sync_positions_from_exchange = AsyncMock(return_value=None)
    ex._struct_candles = AsyncMock(return_value=None)
    moves: list = []

    async def _move(_exch, _pos, new_sl):
        moves.append(round(new_sl, 6))
        return True

    ex._update_exchange_sl = _move
    ex.close_position = AsyncMock(return_value="CLOSED")
    return ex, box, moves


def _live_position(ex, direction: str, order_type: str) -> LivePosition:
    entry, sl = 100.0, (98.0 if direction == "LONG" else 102.0)
    p = LivePosition(
        trade_id="TI-fee-trail", symbol="ETH/USDT", direction=direction,
        entry_price=entry, quantity=1.0, cost_usd=20.0, stop_loss=sl,
        take_profit=(120.0 if direction == "LONG" else 80.0), leverage=5,
        opened_at=datetime.now(UTC) - timedelta(minutes=30), status="open",
        atr_at_entry=2.0, order_type=order_type)
    p.strategy_type = "swing"
    p.signal_type = "momentum_confluence"
    p.filled_at = p.opened_at
    p.sl_order_id = "OLD-SL"
    p.tp_order_id = "OLD-TP"
    # Built the way the executor builds it: no round trip at creation.
    p.trailing_state = make_trailing_state(entry, direction, 2.0, 2.0)
    p.partial_tp_state = {"ladder": "off", "reason": "isolate the trail"}
    ex._positions[p.trade_id] = p
    return p


class TestLive:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("order_type", ["limit", "market"])
    @pytest.mark.parametrize("direction,price", [("LONG", 102.4), ("SHORT", 97.6)])
    async def test_the_stop_sent_is_breakeven_after_the_entry_legs_round_trip(
            self, monkeypatch, order_type, direction, price):
        ex, box, moves = _executor(monkeypatch)
        pos = _live_position(ex, direction, order_type)
        box["p"] = price
        await ex.check_positions()
        rt = round_trip_pct(order_type)
        assert pos.trailing_state["fee_round_trip_pct"] == rt
        want = 100.0 + rt if direction == "LONG" else 100.0 - rt
        assert moves == [pytest.approx(want)]
        assert pos.stop_loss == pytest.approx(want)
        assert _net_pct(100.0, pos.stop_loss, rt, direction) == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.asyncio
    async def test_a_market_entry_locks_further_than_a_limit_one(self, monkeypatch):
        got = {}
        for order_type in ("limit", "market"):
            ex, box, moves = _executor(monkeypatch)
            pos = _live_position(ex, "LONG", order_type)
            box["p"] = 102.4
            await ex.check_positions()
            got[order_type] = pos.stop_loss
        assert got["market"] > got["limit"] > 100.0


# ── the backtest: the real fill and the real intrabar check ─────────────────

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _bar(o, h, lo, c, n):
    return BacktestBar(timestamp=T0 + timedelta(hours=n), open=o, high=h,
                       low=lo, close=c, volume=1000.0, symbol="BTC/USDT")


class TestTheBacktest:
    @pytest.mark.parametrize("commission_pct", [0.06, 0.1])
    def test_a_first_step_stop_out_costs_nothing(self, commission_pct):
        eng = BacktestEngine(BacktestConfig(
            symbol="BTC/USDT", initial_balance=10_000.0,
            commission_pct=commission_pct, slippage_pct=0.0))
        assert eng._partial_tp_enabled is False  # the plain trail path
        entry, risk = 100.0, 0.4
        idea = TradeIdea(id="TI-bt", asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=entry, stop_loss=entry - risk, take_profit=110.0,
                         confidence=0.7, reasoning="x", source="t", timestamp=T0)
        check = SimpleNamespace(position_size_usd=1000.0,
                                verdict=SimpleNamespace(value="APPROVED"))
        eng._execute_fill(idea, check, fill_price=entry, bar=_bar(entry, entry, entry, entry, 0))
        rt = 2.0 * commission_pct
        assert eng._open_bt_positions["TI-bt"]["fee_round_trip_pct"] == pytest.approx(rt)

        # The fixture can only show the floor if the ATR trail sits below it
        # when the first step fires: the engine's ATR is 1R / sl_mult.
        high = entry + risk + 0.02
        atr = risk / CONFIG.analyzer.sl_atr_mult_default
        assert high - 2.0 * atr < entry * (1 + rt / 100.0)

        eng._check_stops_intrabar(_bar(100.2, high, 100.2, 100.4, 1))
        pos = eng.portfolio._positions["TI-bt"]
        assert pos.stop_loss == pytest.approx(entry * (1 + rt / 100.0))

        eng._check_stops_intrabar(_bar(100.3, 100.35, 99.9, 100.0, 2))
        closed = [t for t in eng.portfolio._history if t.trade_id == "TI-bt"]
        assert closed, "the stop at breakeven-after-fees was crossed"
        assert closed[0].exit_price == pytest.approx(entry * (1 + rt / 100.0))
        # Breakeven after fees: the gross the move made is the commission, to
        # the cent the book rounds to.
        assert closed[0].gross_pnl > 0
        assert closed[0].gross_pnl == pytest.approx(closed[0].commission, abs=0.01)
        assert abs(closed[0].pnl) <= 0.01


# ── the paper book ──────────────────────────────────────────────────────────

class TestThePaperBook:
    @pytest.mark.parametrize("override", [None, 0.06, 0.1])
    def test_the_trail_holds_the_round_trip_the_book_charges(self, tmp_path, override):
        book = PortfolioTracker(initial_balance=10_000.0,
                                state_file=str(tmp_path / "p.json"),
                                commission_pct=override)
        rate = CONFIG.risk.commission_pct if override is None else override
        idea = TradeIdea(id="TI-paper", asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=100.0, stop_loss=99.6, take_profit=110.0,
                         confidence=0.7, reasoning="x", source="t", timestamp=T0)
        book.open_position(idea, 1000.0)
        book.check_stops({"BTC/USDT": 100.42})
        status = book.get_trailing_status()["TI-paper"]
        assert status["current_sl"] == pytest.approx(100.0 * (1 + 2.0 * rate / 100.0))
        assert math.isclose(book._trailing_state["TI-paper"]["fee_round_trip_pct"], 2.0 * rate)
