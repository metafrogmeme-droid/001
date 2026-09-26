"""The live exchange sync does not read the paper book, and feeds it nothing.

`sync_portfolio_with_exchange` runs at boot and on every tick, and both engine
callers sit under ``CONFIG.is_live()``. Phase 1 walked ``engine.portfolio`` --
the PAPER book -- and "ghost closed" every paper position the venue did not
list, at a venue price. In live mode the paper book is never the venue's mirror
(no live fill writes it), so what it holds is whatever paper trading left
there, and ``engine.portfolio._on_trade_close`` is the composite that calls
``self.risk.record_trade_result``: the LIVE operator engine's loss streak,
cooldown and breaker, fed the P&L of a paper position priced off the live
ticker. Driven on the unfixed tree: a paper ETH long entered at 2000, a venue
holding nothing, the ticker at 2500 -- the sweep booked "Ghost closed ...
PnL=$+625.0000 (ticker)" and handed ``621.62`` to the risk callback.

Phase 2 read the same book to decide what was tracked, so a stale paper ETH
long made a real, untracked ETH long on the venue read as tracked and adoption
was never asked.

Driven through the real sync with a real ``PortfolioTracker`` whose close
callback records what it would have fed the risk engine.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot.core import exchange_sync as xs
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, TradeIdea


def _idea(asset="ETH/USDT", direction=Direction.LONG, price=2000.0):
    return TradeIdea(asset=asset, direction=direction, entry_price=price,
                     stop_loss=price * 0.95, take_profit=price * 1.15,
                     confidence=0.8, reasoning="x")


def _venue(rows=(), ticker=2500.0):
    x = AsyncMock()
    x.fetch_positions = AsyncMock(return_value=list(rows))
    x.fetch_my_trades = AsyncMock(return_value=[])
    x.fetch_closed_orders = AsyncMock(return_value=[])
    x.fetch_ticker = AsyncMock(return_value={"last": ticker})
    return x


def _engine(portfolio, venue, adopted=()):
    le = NS(_positions={}, open_positions=[], _is_uta=False,
            _get_exchange=AsyncMock(return_value=venue),
            adopt_exchange_positions=AsyncMock(return_value=list(adopted)),
            adopt_exchange_limit_orders=AsyncMock(return_value=[]))
    return NS(portfolio=portfolio, live_executor=le)


@pytest.fixture
def live(monkeypatch):
    from bot.config import CONFIG
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)
    monkeypatch.setattr(xs, "_live_paper_book_noted", False)


@pytest.fixture
def paper(monkeypatch):
    from bot.config import CONFIG
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: False)


def _book(fed):
    pf = PortfolioTracker(initial_balance=10_000.0, on_trade_close=fed.append)
    trade = pf.open_position(_idea(), 500.0, leverage=5)
    return pf, trade


def test_in_live_mode_a_stale_paper_position_is_not_closed_and_feeds_nothing(live):
    fed: list = []
    pf, trade = _book(fed)
    msgs = asyncio.run(xs.sync_portfolio_with_exchange(_engine(pf, _venue())))
    assert trade.trade_id in pf._positions, (
        "the live sync closed a paper position against the live venue")
    assert fed == [], (
        f"a paper P&L reached the live risk engine's streak feed: {fed}")
    assert not any("Ghost" in m for m in msgs), msgs


def test_in_live_mode_a_stale_paper_position_does_not_hide_a_live_orphan(live):
    fed: list = []
    pf, _trade = _book(fed)
    venue = _venue(rows=[{"symbol": "ETH/USDT:USDT", "side": "long",
                          "contracts": 1.0, "entryPrice": 2400.0}])
    eng = _engine(pf, venue, adopted=["ETH/USDT:USDT"])
    msgs = asyncio.run(xs.sync_portfolio_with_exchange(eng))
    assert eng.live_executor.adopt_exchange_positions.await_count == 1, (
        "a paper ETH long made the venue's untracked ETH long read as tracked")
    assert any("Orphan detected on exchange: ETH long" in m for m in msgs), msgs
    assert fed == []


def test_the_live_executor_book_still_decides_what_is_tracked(live):
    """The fix narrows the tracked set to the live book; it must not empty it."""
    fed: list = []
    pf = PortfolioTracker(initial_balance=10_000.0, on_trade_close=fed.append)
    venue = _venue(rows=[{"symbol": "ETH/USDT:USDT", "side": "long",
                          "contracts": 1.0, "entryPrice": 2400.0}])
    eng = _engine(pf, venue)
    eng.live_executor.open_positions = [NS(symbol="ETH/USDT:USDT", direction="LONG")]
    msgs = asyncio.run(xs.sync_portfolio_with_exchange(eng))
    assert eng.live_executor.adopt_exchange_positions.await_count == 0
    assert not any("Orphan" in m for m in msgs), msgs


def test_it_is_said_once_per_process_and_not_per_tick(live, monkeypatch):
    said: list = []
    real = xs.audit

    def _spy(logger, msg, **kw):
        if kw.get("result") == "PAPER_BOOK_LEFT_ALONE":
            said.append(msg)
        return real(logger, msg, **kw)

    monkeypatch.setattr(xs, "audit", _spy)
    fed: list = []
    pf, _trade = _book(fed)
    for _ in range(3):
        asyncio.run(xs.sync_portfolio_with_exchange(_engine(pf, _venue())))
    assert len(said) == 1, said
    assert "paper book" in said[0] and "live risk engine" in said[0]


def test_in_paper_mode_the_ghost_sweep_is_what_it_was(paper):
    """No engine caller reaches this mode today; the sweep is left as it was
    there, and this pins that the live gate is the only thing that moved."""
    fed: list = []
    pf, trade = _book(fed)
    msgs = asyncio.run(xs.sync_portfolio_with_exchange(_engine(pf, _venue())))
    assert trade.trade_id not in pf._positions
    assert any("Ghost closed" in m for m in msgs), msgs
    assert len(fed) == 1
