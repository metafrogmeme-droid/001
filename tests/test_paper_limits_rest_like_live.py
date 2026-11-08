"""A paper limit rests, and fills, expires or drifts away on live's rules.

A paper confirm of a limit idea opened the position at once, at the limit
price, whether or not the market ever came back. Live rests the order: 123 of
the operator's 407 live records from 2026-08-18 to 2026-10-14 never filled.
Paper booked every one of them, at the better price, so the paper record was
not a reading of what live would have done (`bot/core/paper_limits.py`).

Driven through the real `PortfolioTracker`, the real `sweep_paper_limits`, the
real `drift_chase_decision` on bars it can decide from, and the real
`RuneClawEngine._simulate_paper_fill`. Each rule both ways.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.compat import UTC
from bot.config import CONFIG
from bot.core import paper_limits as pl
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, RiskCheck, RiskVerdict, TradeIdea

NOW = 1_800_000_000.0


def _idea(direction=Direction.LONG, entry=100.0, stop=98.0, target=106.0,
          order_type="limit", source="scan", tid="T1"):
    return TradeIdea(id=tid, asset="BTC/USDT", direction=direction, entry_price=entry,
                     stop_loss=stop, take_profit=target, confidence=0.8,
                     reasoning="paper", source=source, order_type=order_type,
                     timestamp=datetime.now(UTC))


def _book(**rest_kw):
    """A tracker holding one resting limit (LONG 100, placed with the market at 101)."""
    pf = PortfolioTracker(initial_balance=10_000.0)
    kw = dict(placed_market=101.0, expires_in_seconds=4 * 3600, chase_allowed=True)
    kw.update(rest_kw)
    idea = kw.pop("idea", None) or _idea()
    pf.rest_limit(idea, 100.0, leverage=2, **kw)
    return pf


def _no_bars(_asset):
    raise AssertionError("bars fetched for a row that may not be chased")


async def _none(_asset):
    return None


def _sweep(pf, prices, *, now, fetch_bars=_none):
    return asyncio.run(pl.sweep_paper_limits(pf, prices, fetch_bars=fetch_bars, now=now))


def _fallback(monkeypatch, on: bool):
    """`LIMIT_DRIFT_MARKET_FALLBACK` as the sweep reads it (the config is frozen)."""
    monkeypatch.setattr(pl, "CONFIG", SimpleNamespace(limit_orders=dataclasses.replace(
        CONFIG.limit_orders, drift_market_fallback=on)))


def _trend(up: bool, n: int = 30):
    """Closed 15m bars in one steady direction: the DI agrees, the DX is high."""
    out = []
    for i in range(n):
        c = 100.0 + (i if up else -i) * 0.3
        out.append([i * 900_000, c, c + 0.2, c - 0.2, c, 1.0])
    return out


# ── whether a confirm rests ─────────────────────────────────────────────────

class TestWhetherAConfirmRests:
    def test_a_limit_on_its_own_side_of_the_market_rests(self):
        assert pl.paper_limit_rests(_idea(entry=100.0), 101.0) is True
        assert pl.paper_limit_rests(_idea(Direction.SHORT, entry=100.0, stop=102.0,
                                          target=94.0), 99.0) is True

    def test_a_limit_at_or_through_the_market_fills_now(self):
        assert pl.paper_limit_rests(_idea(entry=100.0), 100.0) is False
        assert pl.paper_limit_rests(_idea(entry=100.0), 99.0) is False

    def test_a_market_order_fills_now(self):
        assert pl.paper_limit_rests(_idea(order_type="market"), 101.0) is False

    def test_no_market_price_is_the_old_immediate_fill(self):
        assert pl.paper_limit_rests(_idea(), None) is False

    def test_a_fill_now_is_at_the_market_and_a_limit_is_no_worse(self):
        assert pl.paper_entry_price(_idea(order_type="market"), 101.5) == 101.5
        assert pl.paper_entry_price(_idea(entry=100.0), 99.0) == 99.0
        assert pl.paper_entry_price(_idea(entry=100.0), 100.5) == 100.0
        short = _idea(Direction.SHORT, entry=100.0, stop=102.0, target=94.0)
        assert pl.paper_entry_price(short, 101.0) == 101.0
        assert pl.paper_entry_price(short, 99.0) == 100.0
        assert pl.paper_entry_price(_idea(entry=100.0), None) == 100.0


# ── the book ────────────────────────────────────────────────────────────────

class TestTheBook:
    def test_a_resting_limit_is_not_a_position_and_holds_nothing(self):
        pf = _book()
        assert pf.open_positions == []
        assert pf.balance == 10_000.0
        assert [r["trade_id"] for r in pf.pending_limits] == ["T1"]

    def test_a_second_rest_of_one_id_is_refused(self):
        pf = _book()
        with pytest.raises(ValueError):
            pf.rest_limit(_idea(), 100.0, placed_market=101.0,
                          expires_in_seconds=60, chase_allowed=True)

    def test_a_fill_opens_at_the_fill_price_with_the_rows_size_and_leverage(self):
        pf = _book()
        trade = pf.fill_limit("T1", 99.5)
        assert pf.pending_limits == []
        [pos] = pf.open_positions
        assert pos.entry_price == 99.5 and pos.stop_loss == 98.0
        assert trade.trade_id == "T1"

    def test_a_fill_given_levels_opens_with_them(self):
        pf = _book()
        pf.fill_limit("T1", 103.0, stop_loss=101.0, take_profit=109.0)
        [pos] = pf.open_positions
        assert (pos.stop_loss, pos.take_profit) == (101.0, 109.0)

    def test_a_drop_leaves_nothing(self):
        pf = _book()
        assert pf.drop_limit("T1", "expired")["trade_id"] == "T1"
        assert pf.pending_limits == [] and pf.open_positions == []
        assert pf.drop_limit("T1", "expired") is None

    def test_it_survives_a_restart_by_both_savers(self, tmp_path):
        pf = _book()
        path = tmp_path / "pf.json"
        pf.save_state(str(path))
        assert json.loads(path.read_text())["pending_limits"]["T1"]["placed_market"] == 101.0
        back = PortfolioTracker(initial_balance=10_000.0)
        assert back.load_state(str(path)) is True
        assert [r["trade_id"] for r in back.pending_limits] == ["T1"]
        again = PortfolioTracker(initial_balance=10_000.0)
        again._load_from_state_dict(pf._export_state_dict())
        assert [r["trade_id"] for r in again.pending_limits] == ["T1"]


# ── the sweep ───────────────────────────────────────────────────────────────

class TestExpiry:
    def test_past_its_horizon_it_is_dropped_even_unpriced(self):
        pf = _book(expires_in_seconds=60)
        row_exp = pf.pending_limits[0]["expires_at"]
        ev = _sweep(pf, {}, now=row_exp)
        assert [e["kind"] for e in ev] == [pl.EXPIRED] and pf.pending_limits == []

    def test_inside_its_horizon_it_rests(self):
        pf = _book(expires_in_seconds=60)
        ev = _sweep(pf, {"BTC/USDT": 100.5}, now=pf.pending_limits[0]["expires_at"] - 1)
        assert ev == [] and len(pf.pending_limits) == 1


class TestTheTouch:
    def test_a_buy_limit_the_market_reaches_fills_at_the_limit(self):
        pf = _book()
        t0 = pf.pending_limits[0]["placed_at"]
        assert _sweep(_book(), {"BTC/USDT": 100.0}, now=t0)[0]["kind"] == pl.FILLED, (
            "a limit AT its price fills")
        ev = _sweep(pf, {"BTC/USDT": 99.0}, now=t0)
        assert ev == [{"kind": pl.FILLED, "trade_id": "T1", "asset": "BTC/USDT",
                       "price": 100.0}]
        assert pf.open_positions[0].entry_price == 100.0

    def test_a_buy_limit_the_market_has_not_reached_rests(self):
        pf = _book()
        assert _sweep(pf, {"BTC/USDT": 100.01}, now=pf.pending_limits[0]["placed_at"]) == []
        assert pf.open_positions == []

    def test_a_sell_limit_fills_at_or_above(self):
        short = _idea(Direction.SHORT, entry=100.0, stop=102.0, target=94.0)
        pf = _book(idea=short, placed_market=99.0)
        t0 = pf.pending_limits[0]["placed_at"]
        assert _sweep(pf, {"BTC/USDT": 99.99}, now=t0) == []
        assert _sweep(pf, {"BTC/USDT": 100.0}, now=t0)[0]["kind"] == pl.FILLED

    def test_an_unread_price_decides_nothing(self):
        pf = _book()
        t0 = pf.pending_limits[0]["placed_at"]
        assert _sweep(pf, {}, now=t0) == [] and _sweep(pf, {"BTC/USDT": 0.0}, now=t0) == []
        assert len(pf.pending_limits) == 1

    def test_a_fill_the_book_refuses_is_said_and_the_row_is_gone(self):
        pf = PortfolioTracker(initial_balance=10.0)
        pf.rest_limit(_idea(), 10_000.0, placed_market=101.0,
                      expires_in_seconds=60, chase_allowed=True)
        ev = _sweep(pf, {"BTC/USDT": 99.0}, now=pf.pending_limits[0]["placed_at"])
        assert ev[0]["kind"] == pl.FILL_FAILED and ev[0]["error"] == "ValueError"
        assert pf.pending_limits == [] and pf.open_positions == []


class TestDrift:
    def test_under_the_drift_bound_it_rests(self):
        pf = _book(chase_allowed=False)
        # 1.9% above the placement market of 101.
        ev = _sweep(pf, {"BTC/USDT": 101.0 * 1.019}, now=pf.pending_limits[0]["placed_at"],
                    fetch_bars=_no_bars)
        assert ev == []

    def test_past_it_a_row_that_may_not_be_chased_is_cancelled(self):
        pf = _book(chase_allowed=False)
        ev = _sweep(pf, {"BTC/USDT": 103.5}, now=pf.pending_limits[0]["placed_at"],
                    fetch_bars=_no_bars)
        assert [e["kind"] for e in ev] == [pl.DRIFT_CANCELLED]
        assert pf.pending_limits == [] and pf.open_positions == []

    def test_a_typed_ticket_is_never_drift_read(self):
        pf = _book(idea=_idea(source="manual"), chase_allowed=False)
        ev = _sweep(pf, {"BTC/USDT": 104.9}, now=pf.pending_limits[0]["placed_at"],
                    fetch_bars=_no_bars)
        assert ev == [] and len(pf.pending_limits) == 1

    def test_an_engine_idea_in_a_trend_is_chased_with_its_levels_shifted(self, monkeypatch):
        _fallback(monkeypatch, True)
        pf = _book()

        async def bars(_a):
            return _trend(up=True)
        ev = _sweep(pf, {"BTC/USDT": 103.5}, now=pf.pending_limits[0]["placed_at"],
                    fetch_bars=bars)
        assert [e["kind"] for e in ev] == [pl.CHASED] and ev[0]["price"] == 103.5
        [pos] = pf.open_positions
        assert pos.entry_price == 103.5
        assert pos.stop_loss == pytest.approx(98.0 * 1.035)
        assert pos.take_profit == pytest.approx(106.0 * 1.035)

    def test_against_the_trend_it_is_cancelled_not_chased(self, monkeypatch):
        _fallback(monkeypatch, True)
        pf = _book()

        async def bars(_a):
            return _trend(up=False)
        ev = _sweep(pf, {"BTC/USDT": 103.5}, now=pf.pending_limits[0]["placed_at"],
                    fetch_bars=bars)
        assert [e["kind"] for e in ev] == [pl.DRIFT_CANCELLED]

    def test_unread_bars_decide_no_chase(self, monkeypatch):
        _fallback(monkeypatch, True)
        pf = _book()
        ev = _sweep(pf, {"BTC/USDT": 103.5}, now=pf.pending_limits[0]["placed_at"])
        assert [e["kind"] for e in ev] == [pl.DRIFT_CANCELLED]

    def test_the_fallback_off_cancels_without_reading_bars(self, monkeypatch):
        _fallback(monkeypatch, False)
        pf = _book()
        ev = _sweep(pf, {"BTC/USDT": 103.5}, now=pf.pending_limits[0]["placed_at"],
                    fetch_bars=_no_bars)
        assert [e["kind"] for e in ev] == [pl.DRIFT_CANCELLED]

    def test_past_the_chase_bound_it_is_cancelled_without_reading_bars(self, monkeypatch):
        _fallback(monkeypatch, True)
        monkeypatch.setenv("DRIFT_MARKET_MAX_CHASE_PCT", "3")
        pf = _book()
        ev = _sweep(pf, {"BTC/USDT": 103.5}, now=pf.pending_limits[0]["placed_at"],
                    fetch_bars=_no_bars)
        assert [e["kind"] for e in ev] == [pl.DRIFT_CANCELLED]


# ── the confirm ─────────────────────────────────────────────────────────────

def _engine():
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine()
    eng.live_executor = MagicMock()
    eng.live_executor.execute = AsyncMock(
        side_effect=AssertionError("a paper fill never calls live execute"))
    return eng


def _recheck():
    return RiskCheck(trade_id="T1", verdict=RiskVerdict.APPROVED,
                     position_size_usd=100.0, position_pct=1.0)


class TestTheConfirm:
    def test_a_limit_above_the_market_rests_and_says_so(self):
        from bot.core.confirm_result import left_resting, placed_nothing
        eng = _engine()
        eng._pending_ideas["T1"] = _idea()
        msg = asyncio.run(eng._simulate_paper_fill(_idea(), _recheck(), "user-1", "T1",
                                                   market_price=101.0))
        pf = eng.user_portfolios.get("user-1")
        assert pf.open_positions == [] and [r["trade_id"] for r in pf.pending_limits] == ["T1"]
        assert left_resting(msg) and not placed_nothing(msg)
        assert "comes back to it" in msg and "T1" not in eng._pending_ideas
        eng.live_executor.execute.assert_not_called()

    def test_a_market_order_fills_at_the_market(self):
        eng = _engine()
        msg = asyncio.run(eng._simulate_paper_fill(
            _idea(order_type="market"), _recheck(), "user-1", "T1", market_price=101.0))
        [pos] = eng.user_portfolios.get("user-1").open_positions
        assert pos.entry_price == 101.0 and "Simulated" in msg

    def test_with_no_market_price_it_fills_at_the_entry_as_before(self):
        eng = _engine()
        asyncio.run(eng._simulate_paper_fill(_idea(), _recheck(), "user-1", "T1"))
        [pos] = eng.user_portfolios.get("user-1").open_positions
        assert pos.entry_price == 100.0

    def test_the_monitor_fills_it_when_the_market_comes_back(self):
        eng = _engine()
        asyncio.run(eng._simulate_paper_fill(_idea(), _recheck(), "user-1", "T1",
                                             market_price=101.0))
        assert eng.user_portfolios.pending_limit_books()
        assert asyncio.run(eng._sweep_paper_limits({"BTC/USDT": 101.0})) == []
        ev = asyncio.run(eng._sweep_paper_limits({"BTC/USDT": 99.9}))
        assert [e["kind"] for e in ev] == [pl.FILLED]
        assert eng.user_portfolios.get("user-1").open_positions[0].entry_price == 100.0
        assert eng.user_portfolios.pending_limit_books() == []

    def test_the_positions_pass_runs_for_a_book_holding_only_a_resting_limit(self):
        """No open position anywhere: the pass still prices the limit's symbol
        and sweeps it, or a resting limit would wait for a fill nobody checks."""
        eng = _engine()
        asyncio.run(eng._simulate_paper_fill(_idea(), _recheck(), "user-1", "T1",
                                             market_price=101.0))
        asked: list = []

        async def prices(positions):
            asked.append(sorted({p.asset for p in positions}))
            return {"BTC/USDT": 99.9}
        eng._fetch_prices_by_category = prices
        eng.ws_feed.is_connected = lambda: False
        asyncio.run(eng._run_positions_pass())
        assert asked == [["BTC/USDT"]]
        assert eng.user_portfolios.get("user-1").open_positions[0].entry_price == 100.0


# ── through the whole confirm ───────────────────────────────────────────────

def _confirm_at(market: float, order_type: str):
    """A practice confirm through the real `confirm_trade`, the perp's last
    price at ``market`` (the scaffolding the practice-confirm suite drives)."""
    import uuid

    from tests.test_a_paper_users_first_confirm_is_practice import _armed
    from tests.test_a_practice_confirm_reads_the_practice_book import _confirm
    host, engine, idea, _ = _armed("paper")
    idea = idea.model_copy(update={"order_type": order_type})
    engine._pending_ideas[idea.id] = idea
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": market})
    engine.scanner._get_exchange = AsyncMock(return_value=ex)
    engine.scanner._get_futures_exchange = AsyncMock(return_value=ex)
    uid = str(424242000 + uuid.uuid4().int % 10**6)
    result = _confirm(host, engine, idea, uid=uid)
    return result, engine.user_portfolios.get(uid), idea


@pytest.mark.usefixtures("members_may_trade_live")
class TestThroughTheConfirm:
    def test_a_limit_below_the_market_rests_on_the_practice_book(self):
        result, book, idea = _confirm_at(65000 * 1.005, "limit")
        assert "PENDING FILL" in result, result
        assert book.open_positions == []
        assert [r["placed_market"] for r in book.pending_limits] == [65000 * 1.005]

    def test_a_market_order_opens_at_the_market(self):
        result, book, _ = _confirm_at(65000 * 1.005, "market")
        assert "Simulated" in result, result
        assert book.pending_limits == []
        assert book.open_positions[0].entry_price == pytest.approx(65000 * 1.005)
