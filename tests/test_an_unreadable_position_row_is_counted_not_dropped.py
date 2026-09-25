"""A venue position row whose size cannot be read is counted, not dropped.

`exchange_sync._fetch_exchange_positions` filtered the venue's list with
``abs(float(p.get("contracts", 0) or 0)) > 0``. A row the venue listed with a
missing or null ``contracts`` read as flat and disappeared, and a size spelled
as text the parser could not read raised out of the whole fetch. Driven on the
unfixed tree, a venue listing BTC long (0.5), ETH short (``contracts: None``)
and SOL long (``contracts: "n/a"``):

  * the fetch RAISED ValueError, so ``get_exchange_position_count`` -- the
    count the risk engine's slot cap reads -- fell back to the local book and
    answered 0 for a venue listing three rows;
  * without the junk row the count answered 1 for two rows, and the sync
    named the BTC orphan and said nothing at all about ETH.

The count is fail-closed now (an unreadable row may hold a position, and this
count decides whether another may be opened), the drop is audited by symbol,
and the orphan pass names the row as unreadable rather than silently not
adopting it. A measured zero is still flat.

And `invalidate_position_count_cache` wrote ``timestamp = 0.0`` while the
freshness test compares it to ``time.monotonic()``: on a host up less than the
30s TTL the invalidated count read as FRESH. Driven with the clock at 5.0, a
count of 1 invalidated after a position opened was served as 1 while the venue
held 3, and the venue was asked 0 times.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from bot.core import exchange_sync as xs

BTC = {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.5, "entryPrice": 60_000}
ETH_NONE = {"symbol": "ETH/USDT:USDT", "side": "short", "contracts": None, "entryPrice": 2_500}
ETH_ABSENT = {"symbol": "ETH/USDT:USDT", "side": "short", "entryPrice": 2_500}
SOL_JUNK = {"symbol": "SOL/USDT:USDT", "side": "long", "contracts": "n/a"}
DOGE_FLAT = {"symbol": "DOGE/USDT:USDT", "side": "long", "contracts": 0}


def _engine(rows, *, tracked=(), uta=False):
    x = AsyncMock()
    x.fetch_positions = AsyncMock(return_value=list(rows))
    x.fetch_my_trades = AsyncMock(return_value=[])
    x.fetch_closed_orders = AsyncMock(return_value=[])
    x.fetch_ticker = AsyncMock(return_value={"last": 1.0})
    le = NS(_positions={}, open_positions=list(tracked), _is_uta=uta,
            _get_exchange=AsyncMock(return_value=x),
            adopt_exchange_positions=AsyncMock(return_value=[]),
            adopt_exchange_limit_orders=AsyncMock(return_value=[]))
    return NS(portfolio=NS(_positions={}, open_positions=[]), live_executor=le)


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(xs, "_position_count_cache", {"count": None, "timestamp": None})
    from bot.config import CONFIG
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)


class TestTheFetch:
    @pytest.mark.parametrize("bad", [ETH_NONE, ETH_ABSENT, SOL_JUNK,
                                     {"symbol": "X", "contracts": float("nan")}])
    def test_a_size_nobody_could_read_is_kept_apart_not_dropped(self, bad):
        book = asyncio.run(xs._fetch_exchange_positions(_engine([BTC, bad])))
        assert book.rows == [BTC]
        assert book.unreadable == [bad]

    def test_a_measured_zero_is_flat_and_is_dropped(self):
        book = asyncio.run(xs._fetch_exchange_positions(_engine([BTC, DOGE_FLAT])))
        assert book.rows == [BTC] and book.unreadable == []

    def test_a_row_that_is_not_a_mapping_is_unreadable_not_a_crash(self):
        book = asyncio.run(xs._fetch_exchange_positions(_engine([BTC, "garbage"])))
        assert book.rows == [BTC]
        assert len(book.unreadable) == 1 and book.unreadable[0]["raw"] == "garbage"

    def test_the_v3_merge_carries_an_unreadable_size_into_the_same_bucket(self, monkeypatch):
        v3 = [{"symbol": "INTC/USDT:USDT", "side": "long", "contracts": None},
              {"symbol": "AMD/USDT:USDT", "side": "long", "contracts": 3.0},
              # already listed by ccxt, unreadable there: not counted twice
              {"symbol": "ETH/USDT:USDT", "side": "short", "contracts": 1.0}]
        monkeypatch.setattr(xs, "_fetch_v3_positions_direct", lambda: v3)
        book = asyncio.run(xs._fetch_exchange_positions(_engine([BTC, ETH_NONE], uta=True)))
        assert [r["symbol"] for r in book.rows] == ["BTC/USDT:USDT", "AMD/USDT:USDT"]
        assert [r["symbol"] for r in book.unreadable] == ["ETH/USDT:USDT", "INTC/USDT:USDT"]

    def test_the_v3_reader_carries_an_unreadable_size_as_none(self, monkeypatch):
        """Driven through the real v3 reader with its transport planted."""
        class _Client:
            def get(self, path):
                return {"code": "00000", "data": [
                    {"symbol": "INTCUSDT", "holdSide": "long", "totalQty": None},
                    {"symbol": "AMDUSDT", "holdSide": "long", "totalQty": "0"},
                    {"symbol": "NVDAUSDT", "holdSide": "short", "totalQty": "2"}]}

        from bot.core import bitget_v3_client, venues
        monkeypatch.setattr(bitget_v3_client.BitgetV3Client, "from_config",
                            classmethod(lambda cls: _Client()))
        monkeypatch.setattr(venues, "get_venue", lambda *a, **k: NS(id="bitget"))
        monkeypatch.setattr(xs, "CONFIG", NS(exchange=NS(api_key="k", api_secret="s")))
        rows = xs._fetch_v3_positions_direct()
        assert [(r["symbol"], r["contracts"]) for r in rows] == [
            ("INTC/USDT:USDT", None), ("NVDA/USDT:USDT", 2.0)]


class TestTheCount:
    def test_an_unreadable_row_is_counted_as_held(self):
        n = asyncio.run(xs.get_exchange_position_count(_engine([BTC, ETH_NONE, SOL_JUNK])))
        assert n == 3, "the slot cap was told fewer positions than the venue listed"

    def test_the_drop_is_audited_by_symbol(self, monkeypatch):
        said: list = []
        monkeypatch.setattr(xs, "audit", lambda log, msg, **kw: said.append((msg, kw)))
        asyncio.run(xs.get_exchange_position_count(_engine([BTC, ETH_NONE])))
        rows = [(m, kw) for m, kw in said if kw.get("result") == "UNREADABLE_ROWS"]
        assert len(rows) == 1
        assert "ETH short" in rows[0][0] and "counted as held" in rows[0][0]
        assert rows[0][1]["data"] == {"readable": 1, "unreadable": 1}

    def test_a_clean_book_says_nothing(self, monkeypatch):
        said: list = []
        monkeypatch.setattr(xs, "audit", lambda log, msg, **kw: said.append(kw))
        assert asyncio.run(xs.get_exchange_position_count(_engine([BTC, DOGE_FLAT]))) == 1
        assert not any(kw.get("result") == "UNREADABLE_ROWS" for kw in said)


class TestTheOrphanPassNamesIt:
    def test_an_untracked_unreadable_row_is_named_not_silently_skipped(self):
        eng = _engine([BTC, ETH_NONE])
        msgs = asyncio.run(xs.sync_portfolio_with_exchange(eng))
        line = [m for m in msgs if m.startswith("Exchange lists ETH short")]
        assert line and "could not be read" in line[0] and "not adopted" in line[0]
        # The readable orphan is still adopted; the unreadable one is not
        # adoption's to take (adoption needs a quantity).
        assert eng.live_executor.adopt_exchange_positions.await_count == 1

    def test_an_unreadable_row_alone_does_not_ask_adoption(self):
        eng = _engine([ETH_NONE])
        msgs = asyncio.run(xs.sync_portfolio_with_exchange(eng))
        assert eng.live_executor.adopt_exchange_positions.await_count == 0
        assert any(m.startswith("Exchange lists ETH short") for m in msgs)

    def test_a_tracked_unreadable_row_is_not_reported_as_an_orphan(self):
        eng = _engine([ETH_NONE], tracked=[NS(symbol="ETH/USDT:USDT", direction="SHORT")])
        msgs = asyncio.run(xs.sync_portfolio_with_exchange(eng))
        assert not any("ETH" in m for m in msgs), msgs

    def test_the_sync_line_names_the_unreadable_rows(self, monkeypatch):
        said: list = []
        real = xs.audit
        monkeypatch.setattr(xs, "audit",
                            lambda log, msg, **kw: (said.append(msg), real(log, msg, **kw)))
        asyncio.run(xs.sync_portfolio_with_exchange(_engine([BTC, ETH_NONE])))
        assert any("1 open position(s) on exchange, 1 more listed with no readable size"
                   in m for m in said), said

    def test_in_paper_mode_an_unreadable_row_protects_the_local_position(self, monkeypatch):
        """Its key is present: nothing local is ghost-closed against a row the
        venue listed and nobody could size."""
        from bot.config import CONFIG
        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: False)
        closed: list = []
        trade = NS(asset="ETH/USDT", direction=NS(), entry_price=2500.0, quantity=1.0)
        from bot.utils.models import Direction
        trade.direction = Direction.SHORT

        class _P:
            _positions = {"T-1": trade}
            open_positions: list = []

            def close_position(self, tid, px):
                closed.append(tid)

        eng = _engine([ETH_NONE])
        eng.portfolio = _P()
        asyncio.run(xs.sync_portfolio_with_exchange(eng))
        assert closed == []


class TestTheInvalidatedCacheIsNotFresh:
    def test_invalidate_writes_never_fetched_not_time_zero(self):
        xs._position_count_cache.update(count=1, timestamp=4.0)
        xs.invalidate_position_count_cache()
        assert xs._position_count_cache["timestamp"] is None

    def test_on_a_just_booted_host_the_venue_is_asked_after_an_invalidate(self, monkeypatch):
        monkeypatch.setattr(xs.time, "monotonic", lambda: 5.0)
        asked: list = []

        async def _fetch(engine):
            asked.append(1)
            return xs.ExchangeBook([BTC, BTC, BTC], [])

        monkeypatch.setattr(xs, "_fetch_exchange_positions", _fetch)
        xs._position_count_cache.update(count=1, timestamp=4.0)
        xs.invalidate_position_count_cache()
        n = asyncio.run(xs.get_exchange_position_count(NS()))
        assert asked == [1], "an invalidated count was served as fresh"
        assert n == 3

    def test_an_uninvalidated_count_is_still_served_inside_the_ttl(self, monkeypatch):
        monkeypatch.setattr(xs.time, "monotonic", lambda: 5.0)

        async def _boom(engine):
            raise AssertionError("refetched inside the TTL")

        monkeypatch.setattr(xs, "_fetch_exchange_positions", _boom)
        xs._position_count_cache.update(count=1, timestamp=4.0)
        assert asyncio.run(xs.get_exchange_position_count(NS())) == 1
