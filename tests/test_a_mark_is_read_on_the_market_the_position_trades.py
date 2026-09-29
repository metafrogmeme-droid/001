"""A price about a position or an order is read on the market it trades.

A position records ``BTC/USDT``. Every order goes out through
``self._venue.order_symbol(...)``, and three readers of a PRICE did not:

* the position cards (``/livepositions``' picture and its text fallback,
  ``/positions``) called ``executor._get_exchange().fetch_ticker(p.symbol)``.
  Driven against the pinned ccxt with UTA markets loaded, that asks
  ``category=SPOT``: a Bitget card priced a perp position off the SPOT book
  (59,000 against the perp's 60,050 in the fixture). On Hyperliquid the
  recorded spelling names no market, so every card had no mark at all;
* the confirm's drift check read the scanner's client with the recorded
  spelling, which reaches ``/api/v2/spot/market/tickers``, and the drift, the
  past-stop and the R:R checks judged a perp order off the spot price;
* and a ticker that stated no price made the confirm SKIP all three checks and
  place the order, although the same block refuses a read that raised.

``LiveExecutor.last_price`` and ``RuneClawEngine.market_price`` are the two
readings, each in its venue's spelling, and the venue-symbol rule now walks
the three files, so a raw read there fails the rule.
"""
from __future__ import annotations

import ast
import asyncio
import math
from datetime import datetime, timezone
from types import MethodType
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import ccxt.async_support as ca
import pytest

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue
from bot.formatters import signal_card as sc
from bot.skills import trading_commands as tc
from bot.utils.models import AgentState, Direction, TradeIdea
from tests import venue_symbol_reads as rule
from tests.png_text import strings
from tests.test_a_seal_failure_does_not_unplace_a_trade import _confirm, _engine
from tests.test_a_seal_failure_does_not_unplace_a_trade import (
    _no_website_sync as _seal_no_website_sync,
)
from tests.test_bitget_reads_the_perp import _bitget, _mk, _router, _stub

_no_website_sync = _seal_no_website_sync  # autouse binds per module

UTC = timezone.utc
SPOT, PERP = 59000.0, 60050.0


def _pos(sym="BTC/USDT", tid="A", status="open"):
    return LivePosition(trade_id=tid, symbol=sym, direction="LONG",
                        entry_price=60000.0, quantity=0.01, stop_loss=59000.0,
                        take_profit=62000.0, status=status, leverage=5,
                        cost_usd=120.0, opened_at=datetime.now(UTC))


class _Exec:
    """An executor stand-in that reads through the REAL `last_price`."""

    last_price = LiveExecutor.last_price

    def __init__(self, exchange, venue="bitget"):
        self._ex = exchange
        self._venue = get_venue(venue)

    async def _get_exchange(self):
        return self._ex

    def _last_sltp_reason(self, sym):
        return ""


class _Asked:
    """A ticker that records the spelling it was asked for."""

    def __init__(self, marks):
        self.marks = marks
        self.asked: list = []

    async def fetch_ticker(self, sym):
        self.asked.append(sym)
        if sym not in self.marks:
            raise RuntimeError("no such market")
        return self.marks[sym]


def _hyperliquid():
    hl = ca.hyperliquid({"walletAddress": "0x" + "1" * 40,
                         "privateKey": "0x" + "2" * 64})
    hl.set_markets([hl.safe_market_structure(dict(
        id="0", symbol="BTC/USDC:USDC", base="BTC", quote="USDC", settle="USDC",
        baseId="BTC", quoteId="USDC", settleId="USDC", type="swap", spot=False,
        swap=True, contract=True, linear=True, inverse=False, contractSize=1,
        active=True, precision={"amount": 0.00001, "price": 0.1},
        info={"name": "BTC", "szDecimals": 5}))])
    return hl


def _run(coro):
    return asyncio.run(coro)


# ── the executor's reading ─────────────────────────────────────────────────

class TestTheExecutorReadsItsVenuesMarket:
    def test_a_bitget_uta_read_asks_the_perp_not_the_spot_book(self):
        ex = _bitget()
        calls = _stub(ex, _router())
        assert _run(_Exec(ex).last_price("BTC/USDT")) == PERP
        asked = [c[1] for c in calls if "tickers" in c[1]]
        assert len(asked) == 1 and "category=USDT-FUTURES" in asked[0]

    def test_the_recorded_spelling_reads_the_spot_book(self):
        """The precondition, driven: this is what the cards used to read."""
        ex = _bitget()
        calls = _stub(ex, _router())
        t = _run(ex.fetch_ticker("BTC/USDT"))
        assert t["last"] == SPOT
        assert "category=SPOT" in [c[1] for c in calls if "tickers" in c[1]][0]

    def test_hyperliquid_is_asked_in_its_own_spelling(self):
        ex = _Asked({"BTC/USDC:USDC": {"last": 61000.0}})
        assert _run(_Exec(ex, "hyperliquid").last_price("BTC/USDT")) == 61000.0
        assert ex.asked == ["BTC/USDC:USDC"]

    def test_the_recorded_spelling_names_no_hyperliquid_market(self):
        with pytest.raises(Exception) as e:
            _hyperliquid().market("BTC/USDT")
        assert type(e.value).__name__ == "BadSymbol"

    def test_a_perp_only_symbol_keeps_its_spelling(self):
        ex = _Asked({"NATGAS/USDT:USDT": {"last": 3.1}})
        assert _run(_Exec(ex).last_price("NATGAS/USDT:USDT")) == 3.1
        assert ex.asked == ["NATGAS/USDT:USDT"]

    @pytest.mark.parametrize("last", [None, 0, 0.0, -1.0, "junk", math.nan, math.inf])
    def test_a_ticker_that_states_no_price_is_none(self, last):
        ex = _Asked({"BTC/USDT:USDT": {"last": last}})
        assert _run(_Exec(ex).last_price("BTC/USDT")) is None

    def test_a_ticker_that_is_not_a_mapping_is_none(self):
        ex = _Asked({"BTC/USDT:USDT": ["60000"]})
        assert _run(_Exec(ex).last_price("BTC/USDT")) is None

    def test_a_read_that_fails_raises(self):
        with pytest.raises(RuntimeError):
            _run(_Exec(_Asked({})).last_price("BTC/USDT"))


# ── the position cards ─────────────────────────────────────────────────────

class TestTheCardsReadThePerp:
    def _card_marks(self, monkeypatch, executor, filled, pending=()):
        got: list = []
        monkeypatch.setattr(tc, "live_position_card_data",
                            lambda p, cur, now: (got.append(cur), {})[1])
        monkeypatch.setattr(sc, "render_position_card", lambda d: b"PNG")
        pend: list = []
        monkeypatch.setattr(tc, "live_pending_order_row",
                            lambda p, cur: (pend.append(cur), {})[1])
        monkeypatch.setattr(sc, "render_orders_card", lambda rows, **k: b"PNG")

        async def _photo(update, png, cap, reply_markup=None):
            return True

        async def _send(update, text, **kw):
            pass
        host = NS(_lang=lambda u: "en", _send_photo=_photo, _send=_send)
        _run(tc.TradingCommands._render_livepositions_cards(
            host, None, list(filled), list(pending), executor))
        return got, pend

    def test_the_picture_card_prices_a_bitget_perp_off_the_perp(self, monkeypatch):
        ex = _bitget()
        _stub(ex, _router())
        got, _ = self._card_marks(monkeypatch, _Exec(ex), [_pos()])
        assert got == [PERP]

    def test_the_picture_card_reads_a_hyperliquid_mark(self, monkeypatch):
        ex = _Asked({"BTC/USDC:USDC": {"last": 61000.0}})
        got, _ = self._card_marks(monkeypatch, _Exec(ex, "hyperliquid"), [_pos()])
        assert got == [61000.0]

    def test_a_resting_order_row_reads_the_perp_too(self, monkeypatch):
        ex = _Asked({"BTC/USDT:USDT": {"last": PERP}})
        _, pend = self._card_marks(monkeypatch, _Exec(ex), [],
                                   [_pos(status="pending_fill", tid="R")])
        assert pend == [PERP]

    def test_an_unread_mark_is_none_on_the_card(self, monkeypatch):
        ex = _Asked({"BTC/USDT:USDT": {"last": None}})
        got, _ = self._card_marks(monkeypatch, _Exec(ex), [_pos()])
        assert got == [None]

    def test_positions_prices_the_row_off_the_perp(self, monkeypatch):
        from bot.skills import telegram_handler as th
        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)
        cards: list = []
        monkeypatch.setattr(sc, "render_position_card",
                            lambda d: (cards.append(d), b"PNG")[1])
        ex = _Asked({"BTC/USDT:USDT": {"last": 63000.0},
                     "BTC/USDT": {"last": 57000.0}})
        executor = _Exec(ex)
        executor.open_positions = [_pos()]
        h = object.__new__(th.TelegramHandler)
        h.engine = NS(user_portfolios={}, position_watch=lambda: None, pending_ideas=[])
        h._get_tg_id = lambda update: 7
        h._lang = lambda update: "en"
        h._caller_executor = lambda update: executor

        async def _allow(update, command="", ctx=None):
            return True
        h._guard = _allow

        async def _send(update, text, **kw):
            pass
        h._send = _send

        async def _send_photo(update, png, caption, reply_markup=None):
            return True
        h._send_photo = _send_photo
        _run(h._cmd_open_positions(None, None))
        assert ex.asked == ["BTC/USDT:USDT"]
        assert cards and cards[0]["now"] == 63000.0

    def test_the_text_fallback_reads_the_perp(self, monkeypatch):
        """With the picture path failing, `/livepositions` prints the text
        readout, and its current price is the perp's."""
        from bot.skills import telegram_handler as th
        monkeypatch.setattr(type(CONFIG), "is_live", lambda self: True)
        ex = _Asked({"BTC/USDT:USDT": {"last": 63000.0},
                     "BTC/USDT": {"last": 57000.0}})
        executor = _Exec(ex)
        executor._positions = {"A": _pos()}
        texts: list = []
        h = object.__new__(th.TelegramHandler)
        h.engine = NS(live_executor=executor)
        h._lang = lambda update: "en"
        h._caller_executor = lambda update: executor

        async def _allow(update, command="", ctx=None):
            return True
        h._guard = _allow

        async def _no_cards(*a, **k):
            return False
        h._render_livepositions_cards = _no_cards

        async def _send(update, text, **kw):
            texts.append(text)
        h._send = _send
        _run(h._cmd_livepositions(None, None))
        out = "\n".join(texts)
        assert "BTC/USDT:USDT" in ex.asked and "BTC/USDT" not in ex.asked
        assert "63,000.0000" in out and "57,000" not in out


# ── the confirm ────────────────────────────────────────────────────────────

class TestTheOrdersPictureNamesItsVenue:
    """The orders picture's footer read "Bitget USDT-M Futures" whatever
    venue held the order: a Bybit or Hyperliquid resting limit was labelled
    as a Bitget order under the /livepositions and /orders cards."""

    def test_the_footer_names_the_venue_it_is_handed(self):
        row = {"sym": "BTC/USDT", "side": "BUY", "price": 59000.0,
               "current_price": PERP, "amount": 0.01, "oid": "o1",
               "type": "limit", "dist_pct": 1.0}
        drawn = strings(sc.render_orders_card, [row], timestamp="12:00 UTC",
                        source="Bybit")
        assert "Source: Bybit" in drawn
        assert not any("Bitget" in t for t in drawn)

    def test_no_venue_named_draws_no_footer(self):
        row = {"sym": "BTC/USDT", "side": "BUY", "price": 59000.0,
               "current_price": PERP, "amount": 0.01, "oid": "o1",
               "type": "limit", "dist_pct": 1.0}
        drawn = strings(sc.render_orders_card, [row], timestamp="12:00 UTC")
        assert not any(t.startswith("Source:") or "Bitget" in t for t in drawn)

    def test_the_pending_card_names_the_executors_venue(self, monkeypatch):
        seen: list = []
        monkeypatch.setattr(tc, "live_pending_order_row", lambda p, cur: {})
        monkeypatch.setattr(sc, "render_orders_card",
                            lambda rows, **k: (seen.append(k.get("source")), b"PNG")[1])

        async def _photo(update, png, cap, reply_markup=None):
            return True

        async def _send(update, text, **kw):
            pass
        host = NS(_lang=lambda u: "en", _send_photo=_photo, _send=_send)
        ex = _Asked({"BTC/USDC:USDC": {"last": 61000.0}})
        _run(tc.TradingCommands._render_livepositions_cards(
            host, None, [], [_pos(status="pending_fill", tid="R")],
            _Exec(ex, "hyperliquid")))
        assert seen == ["Hyperliquid"]

    def test_the_source_is_the_venues_display_name_or_the_exchange(self):
        from bot.core.open_orders import order_source
        assert order_source(NS(_venue=get_venue("bybit"))) == "Bybit"
        assert order_source(NS()) == "the exchange"
        assert order_source(NS(_venue=NS(display_name=""))) == "the exchange"


def _signal(asset="BTC/USDT", entry=60000.0, stop=59500.0, target=62000.0):
    return TradeIdea(id="TI-PERP", asset=asset, direction=Direction.LONG,
                     entry_price=entry, stop_loss=stop, take_profit=target,
                     confidence=0.8, reasoning="r", signals_used=["x"],
                     source="unknown", timestamp=datetime.now(UTC),
                     order_type="market")


def _scanner_bitget():
    """The scanner's own construction: a plain Bitget client, spot and swap
    markets loaded, answering 59,000 on the spot book and 60,050 on the perp."""
    ex = ca.bitget({})
    ex.set_markets([
        _mk(ex, id="BTCUSDT", symbol="BTC/USDT", base="BTC", quote="USDT", baseId="BTC",
            quoteId="USDT", type="spot", spot=True, swap=False, contract=False, active=True,
            precision={"amount": 0.000001, "price": 0.01}, info={}),
        _mk(ex, id="BTCUSDT", symbol="BTC/USDT:USDT", base="BTC", quote="USDT", settle="USDT",
            baseId="BTC", quoteId="USDT", settleId="USDT", type="swap", spot=False, swap=True,
            contract=True, linear=True, inverse=False, contractSize=1, active=True,
            precision={"amount": 0.001, "price": 0.1}, info={}),
    ])
    urls: list = []

    async def fetch(url, method="GET", headers=None, body=None):
        urls.append(url)
        last = str(SPOT) if "/spot/" in url else str(PERP)
        return {"code": "00000", "data": [{"symbol": "BTCUSDT", "lastPr": last, "ts": "1"}]}
    ex.fetch = fetch
    return ex, urls


def _pending(tmp_path, idea, exchange):
    engine, _seal = _engine(tmp_path)
    engine._pending_ideas.clear()
    engine._pending_ideas[idea.id] = idea
    engine._pending_atr[idea.id] = 500.0
    engine.scanner._get_exchange = AsyncMock(return_value=exchange)
    engine.scanner._get_futures_exchange = AsyncMock(return_value=exchange)
    return engine


class TestTheConfirmReadsThePerp:
    def test_a_stop_the_spot_book_is_through_is_not_refused_for_a_perp_order(self, tmp_path):
        """The stop at 59,500 is above the spot book's 59,000 and below the
        perp's 60,050. Read off the spot book the confirm refused a perp order
        as 'already below SL'; read off the perp it places."""
        ex, urls = _scanner_bitget()
        idea = _signal()
        engine = _pending(tmp_path, idea, ex)
        out = _confirm(engine, idea)
        assert "already below" not in out
        assert engine.live_executor.execute.await_count == 1
        assert [u for u in urls if "ticker" in u] == [
            "https://api.bitget.com/api/v2/mix/market/ticker?symbol=BTCUSDT&productType=USDT-FUTURES"]

    def test_a_perp_through_the_stop_is_refused(self, tmp_path):
        ex, _ = _scanner_bitget()
        idea = _signal(entry=60500.0, stop=60200.0, target=62000.0)
        engine = _pending(tmp_path, idea, ex)
        out = _confirm(engine, idea)
        assert "already below" in out
        assert engine.live_executor.execute.await_count == 0

    @pytest.mark.parametrize("last", [None, 0, "junk", math.nan])
    def test_a_ticker_that_states_no_price_refuses(self, tmp_path, last):
        ex = AsyncMock()
        ex.fetch_ticker = AsyncMock(return_value={"last": last})
        idea = _signal()
        engine = _pending(tmp_path, idea, ex)
        engine._pending_pyramid[idea.id] = True
        # A confirm tapped while the tick is mid-cycle: nothing in the confirm
        # moves the state before the drift check, so an engine that starts
        # IDLE cannot show whether the refusal transitions. The state is
        # planted, the way the tick leaves it.
        engine.state = AgentState.ANALYZING
        audits: list = []
        with patch("bot.core.engine.audit",
                   lambda log, message, **kw: audits.append({"message": message, **kw})):
            out = _confirm(engine, idea)
        assert out == "Trade REJECTED: unable to verify current price. Try again."
        assert engine.live_executor.execute.await_count == 0
        # The refusal leaves the engine idle and drops the pyramid flag, as
        # every sibling refusal in that block does.
        assert engine.state == AgentState.IDLE
        assert any(t.reason == f"price unread for {idea.id}"
                   for t in engine._state_history)
        assert idea.id not in engine._pending_pyramid
        drift = [a for a in audits if a.get("action") == "price_drift"]
        assert drift and drift[0]["result"] == "REJECTED"
        assert drift[0]["data"]["reason"] == "price_unread"
        assert idea.id in engine._pending_ideas

    def test_a_failed_read_names_the_class_and_not_the_text(self, tmp_path):
        ex = AsyncMock()
        ex.fetch_ticker = AsyncMock(side_effect=RuntimeError("apiKey=SECRETVALUE"))
        idea = _signal()
        engine = _pending(tmp_path, idea, ex)
        audits: list = []
        with patch("bot.core.engine.audit",
                   lambda log, message, **kw: audits.append({"message": message, **kw})):
            out = _confirm(engine, idea)
        assert out == "Trade REJECTED: unable to verify current price. Try again."
        msgs = " ".join(a["message"] for a in audits if a.get("action") == "price_drift")
        assert "RuntimeError" in msgs and "SECRETVALUE" not in msgs


# ── the engine's reading ───────────────────────────────────────────────────

def _host(spot, fut):
    h = NS(scanner=NS(_get_exchange=AsyncMock(return_value=spot),
                      _get_futures_exchange=AsyncMock(return_value=fut)))
    h.get_exchange = MethodType(RuneClawEngine.get_exchange, h)
    return h


class TestTheEngineReading:
    def test_a_crypto_asset_is_read_on_the_perp_through_the_spot_client(self):
        spot = _Asked({"BTC/USDT:USDT": {"last": PERP}})
        fut = _Asked({})
        assert _run(RuneClawEngine.market_price(_host(spot, fut), "BTC/USDT")) == PERP
        assert spot.asked == ["BTC/USDT:USDT"] and fut.asked == []

    def test_a_tradfi_perp_is_read_on_the_futures_client(self):
        spot = _Asked({})
        fut = _Asked({"NATGAS/USDT:USDT": {"last": 3.1}})
        assert _run(RuneClawEngine.market_price(_host(spot, fut), "NATGAS/USDT:USDT")) == 3.1
        assert fut.asked == ["NATGAS/USDT:USDT"] and spot.asked == []

    def test_the_spelling_is_on_a_market_the_client_resolves_as_a_swap(self):
        ex, _ = _scanner_bitget()
        assert ex.market(get_venue("bitget").order_symbol("BTC/USDT"))["type"] == "swap"

    def test_no_price_is_none(self):
        spot = _Asked({"BTC/USDT:USDT": {"last": None}})
        assert _run(RuneClawEngine.market_price(_host(spot, _Asked({})), "BTC/USDT")) is None

    def test_a_failed_read_raises(self):
        with pytest.raises(RuntimeError):
            _run(RuneClawEngine.market_price(_host(_Asked({}), _Asked({})), "BTC/USDT"))


# ── the callback buttons ───────────────────────────────────────────────────

def _handler_tree():
    return ast.parse((rule.ROOT / "bot/skills/callback_handler.py").read_text())


class TestTheButtonsAskTheOneReading:
    def test_the_drift_re_offer_is_priced_by_the_engine_reading(self):
        """The re-offer's entry is the price the confirm just refused on, so
        it is the same reading, and a ticker that states none offers nothing."""
        blocks = [n for n in ast.walk(_handler_tree()) if isinstance(n, ast.If)
                  and "price drifted" in ast.unparse(n.test)]
        assert len(blocks) == 1
        body = ast.unparse(blocks[0])
        assert "self.engine.market_price(original_idea.asset)" in body
        assert "reanalyzed_idea(original_idea, new_price) if new_price is not None else None" in body
        assert "fetch_ticker(" not in body

    def test_the_paper_close_is_priced_by_the_engine_reading(self):
        fns = [n for n in ast.walk(_handler_tree())
               if isinstance(n, ast.For) and ast.unparse(n.iter) == "list(portfolio.open_positions)"]
        assert len(fns) == 1
        body = ast.unparse(fns[0])
        assert "self.engine.market_price(pos.asset)" in body
        assert "fetch_ticker(" not in body


# ── the rule covers the three files ────────────────────────────────────────

class TestTheRuleWalksTheReaders:
    @pytest.mark.parametrize("rel", ["bot/skills/trading_commands.py",
                                     "bot/core/engine.py",
                                     "bot/skills/callback_handler.py"])
    def test_the_file_is_in_the_rule(self, rel):
        assert rel in rule.FILES

    def test_a_raw_card_read_would_be_reported(self):
        src = ("async def _cmd_x(self, executor, p):\n"
               "    exchange = await executor._get_exchange()\n"
               "    return await exchange.fetch_ticker(p.symbol)\n")
        assert [f.key for f in rule.findings_for(src)] == ["_cmd_x.fetch_ticker(p.symbol)"]

    def test_the_readers_hold_no_unmapped_price_read(self):
        found = rule.tree_findings()
        for rel in ("bot/skills/trading_commands.py", "bot/core/engine.py",
                    "bot/skills/callback_handler.py"):
            keys = [k for k in found if k.startswith(rel + ":") and "fetch_ticker(" in k]
            assert all("ep_sym" in k for k in keys), keys
