""""My open orders" was routed to a skill that did not exist.

`intent_router` has sent "open orders", "pending orders", "my limit orders"
and "what's pending" to a skill named `get_orders` since the rule was written.
Nothing was registered under that name. Three surfaces, three different
answers to the same typed question:

  Telegram free text   special-cased the name to /orders — worked
  web chat             aliased `get_orders` to `get_portfolio`, so a question
                       about ORDERS was answered with the POSITIONS card:
                       "no positions" printed over resting limit orders
  the chat model       (both surfaces) held no tool that asks the exchange,
                       while its own prompt told it "/orders asks the
                       exchange" — a slash command a model cannot run

And /orders itself read `self.engine.live_executor` — the OPERATOR's book —
for every caller, the leak `GetPortfolioSkill` records having fixed one
skill over with `viewer_executor`.

`bot/core/open_orders.py` is the seam: one read every surface asks, three
outcomes kept apart (the venue's list, the venue's NONE, and a venue that
did not answer — RAISED, never an empty list), and every price a reading or
the word "unread". `GetOrdersSkill` registers the name the router already
used, under the permission /orders is guarded with.

THE RED HERRINGS, planted below: an exchange that answers `[]` is a real
"none" and must say so; a desync that resolves to FILLED is not an open
order; and the operator's resting orders must never reach another caller.
"""
from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from bot.compat import UTC
from bot.core import open_orders as oo
from bot.nlp import chat_tools
from bot.nlp.intent_router import IntentRouter
from bot.skills import telegram_handler as th
from bot.skills.skill_permissions import DANGEROUS_SKILLS, SKILL_PERMISSION, WEB_CHAT_SKILLS
from bot.skills.skill_registry import GetOrdersSkill, build_default_registry
from tests.source_scan import code_only

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
ZERO_MONEY = re.compile(r"\$0\.0+\b")


# ── fakes ──────────────────────────────────────────────────────────────────

def _limit(sym="BTC/USDT:USDT", price=60000.0, amount=0.01, filled=0.0, oid="abc123def456ghi"):
    return {"id": oid, "symbol": sym, "type": "limit", "side": "buy", "price": price,
            "amount": amount, "remaining": amount, "filled": filled, "status": "open",
            "datetime": (NOW - timedelta(hours=1)).isoformat()}


def _stop(sym="ETH/USDT:USDT", trigger=2900.0):
    # A market-trigger stop: ccxt hands back price=None.
    return {"id": "stop1", "symbol": sym, "type": "market", "side": "sell", "price": None,
            "amount": 1.0, "filled": None, "status": "open", "triggerPrice": trigger,
            "info": {"planType": "loss_plan"}}


class _Exchange:
    def __init__(self, account=None, per_symbol=None, tickers=None, order=None,
                 raise_account=None, raise_tickers=False):
        self.account = [] if account is None else account
        self.per_symbol = per_symbol or {}
        self.tickers = tickers or {}
        self.order = order
        self.raise_account = raise_account
        self.raise_tickers = raise_tickers
        self.calls: list = []

    async def fetch_open_orders(self, symbol=None, params=None):
        self.calls.append(("fetch_open_orders", symbol, params))
        if symbol is None:
            if self.raise_account:
                raise self.raise_account
            return list(self.account)
        return list(self.per_symbol.get(symbol, []))

    async def fetch_order(self, oid, symbol):
        self.calls.append(("fetch_order", oid, symbol))
        if self.order is None:
            raise RuntimeError("no such order")
        return dict(self.order)

    async def fetch_tickers(self, syms):
        self.calls.append(("fetch_tickers", tuple(syms)))
        if self.raise_tickers:
            raise RuntimeError("ticker feed down")
        return {s: {"last": self.tickers[s]} for s in syms if s in self.tickers}


class _Venue:
    display_name = "Bitget"

    def futures_params(self, **extra):
        p = {"productType": "USDT-FUTURES"}
        p.update(extra)
        return p


class _Executor:
    def __init__(self, exchange, pending=()):
        self._exchange = exchange
        self._positions = {getattr(p, "trade_id", str(i)): p for i, p in enumerate(pending)}
        self._venue = _Venue()

    async def _get_exchange(self):
        return self._exchange


def _pending(oid="1234567890", symbol="FIL/USDT:USDT"):
    return NS(limit_order_id=oid, symbol=symbol, direction="SHORT", entry_price=0.7668,
              quantity=204.2, trade_id="TI-8a86e1a5", opened_at=NOW - timedelta(minutes=5),
              status="pending_fill")


async def _read(ex, pending=()):
    return await oo.read_open_orders(_Executor(ex, pending), now=NOW, expire_sec=4 * 3600)


# ── the premise: the router already names this skill ──────────────────────

class TestTheRouterNamesIt:
    @pytest.mark.parametrize("text", [
        "show my open orders", "what's pending", "my limit orders",
        "pending orders", "active orders",
    ])
    def test_the_words_route_to_get_orders(self, text):
        r = IntentRouter().classify_rules(text)
        assert r.matched and r.skill == "get_orders", (text, r)

    def test_the_skill_is_registered_under_that_name(self):
        assert isinstance(build_default_registry().get("get_orders"), GetOrdersSkill)

    def test_its_permission_is_the_one_orders_is_guarded_with(self):
        """Derived from the decorator, not asserted from memory."""
        src = code_only(inspect.getsource(th.TelegramHandler._cmd_orders.__wrapped__)) \
            if hasattr(th.TelegramHandler._cmd_orders, "__wrapped__") else ""
        assert SKILL_PERMISSION["get_orders"] == "portfolio"
        from bot.skills import trading_commands
        body = trading_commands.__file__ and open(trading_commands.__file__, encoding="utf-8").read()
        m = re.search(r'@guard\("([a-z_]+)"\)\s*\n\s*async def _cmd_orders\(', body)
        assert m, "the /orders decorator moved; re-derive"
        assert m.group(1) == SKILL_PERMISSION["get_orders"]
        assert "get_orders" not in DANGEROUS_SKILLS
        assert "get_orders" in WEB_CHAT_SKILLS
        assert src is not None

    def test_the_tool_is_offered_on_both_surfaces_to_a_portfolio_holder(self, monkeypatch):
        from bot.token import tier_gate
        monkeypatch.setattr(tier_gate, "check_user", lambda users, uid, f: (True, "ok"))

        class _Users:
            def permission_denial(self, uid, perm):
                return None if perm == "portfolio" else "role"

            def get(self, uid):
                return {"role": "trader"}
        for surface in ("telegram", "web"):
            names = {t.name for t in chat_tools.tools_for(_Users(), "u1", surface=surface)}
            assert "get_orders" in names, surface
        assert "get_orders" not in {t.name for t in chat_tools.tools_for(
            type("N", (), {"permission_denial": lambda self, u, p: "role",
                           "get": lambda self, u: {"role": "viewer"}})(), "u1")}

    def test_the_web_no_longer_aliases_orders_to_the_portfolio(self):
        gw = pytest.importorskip("bot.web.user_gateway", reason="web gateway needs aiohttp")
        src = code_only(inspect.getsource(gw))
        assert '"get_orders": "get_portfolio"' not in src
        assert gw._WEB_SKILL_PERMISSION["get_orders"] == "portfolio"

    def test_the_prompt_points_the_model_at_the_tool_not_a_slash_command(self):
        out = th._live_positions_block(NS(open_positions=[NS(
            symbol="SOL/USDT", status="pending_fill", direction="LONG",
            entry_price=140.0, stop_loss=135.0, take_profit=150.0,
            quantity=1.0, cost_usd=14.0, leverage=10)]))
        assert "get_orders tool asks the exchange" in out
        assert "open or pending orders" in th._CHAT_TOOLS_RULE


# ── the read: three outcomes, kept apart ──────────────────────────────────

class TestTheRead:
    @pytest.mark.asyncio
    async def test_orders_are_classified_and_priced(self):
        ex = _Exchange(account=[_limit(), _stop()], tickers={"BTC/USDT:USDT": 61000.0})
        r = await _read(ex)
        assert r.state == "read"
        assert [o["kind"] for o in r.orders] == ["limit", "sl"]
        assert r.prices == {"BTCUSDT": 61000.0} or r.prices == {oo.display_symbol("BTC/USDT:USDT"): 61000.0}
        assert r.prices_read is True
        assert r.source == "Bitget"
        # The account-wide query carried the venue's futures params.
        assert ex.calls[0] == ("fetch_open_orders", None, {"productType": "USDT-FUTURES"})

    @pytest.mark.asyncio
    async def test_a_market_trigger_stop_has_no_price_and_says_so(self):
        """ccxt hands back price=None for a market-trigger stop; the old code
        did float(None or 0) and the card printed $0.0000."""
        ex = _Exchange(account=[_stop()])
        r = await _read(ex)
        row = r.orders[0]
        assert row["price"] is None and row["trigger"] == 2900.0 and row["filled"] is None
        text = oo.render_open_orders_html(r)
        assert "trigger $2,900.0000" in text
        assert not ZERO_MONEY.search(text), text

    @pytest.mark.asyncio
    async def test_the_venue_answering_none_is_a_real_none(self):
        """RED HERRING. An empty list from a venue that answered IS 'no orders'."""
        r = await _read(_Exchange(account=[]))
        assert r.state == "empty" and r.orders == [] and r.notes == []
        text = oo.render_open_orders_html(r)
        assert "No pending orders on Bitget" in text
        assert "COULD NOT" not in text and "unread" not in text

    @pytest.mark.asyncio
    async def test_a_venue_that_does_not_answer_is_not_an_empty_book(self):
        with pytest.raises(RuntimeError):
            await _read(_Exchange(raise_account=RuntimeError("502 from the venue")))

    @pytest.mark.asyncio
    async def test_the_per_symbol_retry_recovers_a_tracked_limit(self):
        p = _pending()
        ex = _Exchange(account=[], per_symbol={p.symbol: [_limit(sym=p.symbol, price=0.7668,
                                                               amount=204.2, oid="1234567890")]})
        r = await _read(ex, pending=[p])
        assert r.state == "read" and not r.desync
        assert r.orders[0]["oid"] == "1234567890"

    @pytest.mark.asyncio
    async def test_a_desync_that_resolves_to_filled_is_not_an_open_order(self):
        """RED HERRING. The venue shows nothing because it FILLED; the note
        says so and the row does not render as resting."""
        p = _pending()
        ex = _Exchange(account=[], order={"status": "closed", "average": 0.7669})
        r = await _read(ex, pending=[p])
        assert r.desync is True
        assert r.orders == []
        assert len(r.notes) == 1 and "FILLED" in r.notes[0] and "$0.7669" in r.notes[0]
        assert ("fetch_order", "1234567890", p.symbol) in ex.calls

    @pytest.mark.asyncio
    async def test_a_desync_the_venue_cannot_verify_stays_visible_with_a_warning(self):
        p = _pending()
        ex = _Exchange(account=[], order=None)          # fetch_order raises
        r = await _read(ex, pending=[p])
        assert r.orders and r.orders[0]["kind"] == "limit"
        assert any("could not be verified" in n for n in r.notes)
        text = oo.render_open_orders_html(r)
        assert "bot's own pending records" in text

    @pytest.mark.asyncio
    async def test_an_unreadable_ticker_withholds_the_distance_not_the_order(self):
        ex = _Exchange(account=[_limit()], raise_tickers=True)
        r = await _read(ex)
        assert r.prices_read is False and r.prices == {}
        text = oo.render_open_orders_html(r)
        assert "Limit: <code>$60,000.0000</code>" in text
        assert "Current: unread" in text and "distance to fill not shown" in text
        assert "to fill" not in text.replace("distance to fill not shown", "")
        assert not ZERO_MONEY.search(text), text

    @pytest.mark.asyncio
    async def test_no_limit_order_means_no_ticker_read_at_all(self):
        ex = _Exchange(account=[_stop()])
        r = await _read(ex)
        assert r.prices_read is None
        assert not any(c[0] == "fetch_tickers" for c in ex.calls)

    @pytest.mark.asyncio
    async def test_an_absent_amount_is_unread_not_zero(self):
        o = _limit()
        o["amount"] = None
        o["remaining"] = None
        o["filled"] = None
        r = await _read(_Exchange(account=[o], tickers={"BTC/USDT:USDT": 61000.0}))
        text = oo.render_open_orders_html(r)
        assert "Qty: <code>unread</code>" in text
        assert "filled)" not in text
        assert "+1.64% to fill" in text

    def test_a_tracked_record_without_a_price_synthesises_an_unread_price(self):
        o = oo.synth_order_from_tracked(NS())
        assert o["price"] is None and o["amount"] is None
        row = oo.classify_order(o, now=NOW, expire_sec=3600)
        assert row["price"] is None
        assert "unread" in oo._px(row["price"])

    def test_a_bitget_plan_stop_is_a_stop_whatever_ccxt_calls_it(self):
        """The old classifier read ccxt's `type` alone and filed every plan
        stop under Other at $0.0000."""
        for ctype, plan, kind in (("market", "loss_plan", "sl"), ("limit", "profit_plan", "tp"),
                                  ("market", "pos_loss", "sl"), ("limit", "", "limit"),
                                  ("stop", "", "sl"), ("take_profit", "", "tp"),
                                  ("market", "", "other")):
            o = {"id": "x", "symbol": "ETH/USDT:USDT", "type": ctype, "side": "sell",
                 "price": None, "triggerPrice": 2900.0, "info": {"planType": plan}}
            assert oo.classify_order(o, now=NOW, expire_sec=3600)["kind"] == kind, (ctype, plan)

    @pytest.mark.parametrize("zero", [0, 0.0, "0", "0.0"])
    def test_a_venue_price_of_zero_is_not_a_price(self, zero):
        """Bitget spells 'no price' as "0" on plan orders. No traded asset has
        a price of zero — the `_protection_level` rule — so it is unread."""
        o = {"id": "x", "symbol": "ETH/USDT:USDT", "type": "market", "side": "sell",
             "price": zero, "triggerPrice": zero, "amount": 1.0,
             "info": {"planType": "loss_plan"}}
        row = oo.classify_order(o, now=NOW, expire_sec=3600)
        assert oo._px(row["price"]) == "unread" and oo._px(row["trigger"]) == "unread"
        text = oo.render_open_orders_html(oo.OpenOrdersReading(orders=[row]))
        assert "trigger unread" in text
        assert not ZERO_MONEY.search(text), text

    def test_ttl_counts_down_from_the_expiry(self):
        row = oo.classify_order(_limit(), now=NOW, expire_sec=4 * 3600)
        assert row["ttl_str"] == " | ⏰ 3h 0m left"
        row = oo.classify_order(_limit(), now=NOW + timedelta(hours=5), expire_sec=4 * 3600)
        assert "expiring" in row["ttl_str"]


# ── the skill: the model's and the web's door ──────────────────────────────

def _engine(executor):
    return NS(viewer_executor=lambda uid: executor if uid == "op" else None)


def _live(monkeypatch, on=True):
    from bot.config import CONFIG
    monkeypatch.setattr(type(CONFIG), "is_live", lambda self: on)


class TestTheSkill:
    @pytest.mark.asyncio
    async def test_paper_mode_has_no_book_to_ask(self, monkeypatch):
        _live(monkeypatch, on=False)
        out = await GetOrdersSkill().execute(_engine(_Executor(_Exchange())), user_id="op")
        assert "Paper mode" in out and "no exchange order book" in out

    @pytest.mark.asyncio
    async def test_no_linked_account_is_said_not_the_operators_orders(self, monkeypatch):
        """RED HERRING at the account level: the operator's book has a resting
        BTC limit. A caller the resolver does not map to it must never see it."""
        _live(monkeypatch)
        eng = _engine(_Executor(_Exchange(account=[_limit()])))
        out = await GetOrdersSkill().execute(eng, user_id="stranger")
        assert "No linked live account" in out
        assert "BTC" not in out

    @pytest.mark.asyncio
    async def test_an_unreadable_book_is_not_no_orders(self, monkeypatch):
        _live(monkeypatch)
        eng = _engine(_Executor(_Exchange(raise_account=RuntimeError("venue 502"))))
        out = await GetOrdersSkill().execute(eng, user_id="op")
        assert "COULD NOT BE READ" in out and "not \"no orders\"" in out
        assert "No pending orders" not in out
        assert "502" not in out, "venue error text reached the model"

    @pytest.mark.asyncio
    async def test_the_venues_none_is_reported_as_none(self, monkeypatch):
        _live(monkeypatch)
        out = await GetOrdersSkill().execute(_engine(_Executor(_Exchange())), user_id="op")
        assert "No pending orders on Bitget" in out
        assert "COULD NOT" not in out

    @pytest.mark.asyncio
    async def test_orders_reach_the_model_with_their_readings(self, monkeypatch):
        _live(monkeypatch)
        ex = _Exchange(account=[_limit(), _stop()], tickers={"BTC/USDT:USDT": 61000.0})
        out = await GetOrdersSkill().execute(_engine(_Executor(ex)), user_id="op")
        assert "Open Orders (2)" in out
        assert "Limit: <code>$60,000.0000</code>" in out
        assert "+1.64% to fill" in out
        assert "trigger $2,900.0000" in out
        assert not ZERO_MONEY.search(out), out

    @pytest.mark.asyncio
    async def test_desync_notes_ride_above_the_list(self, monkeypatch):
        _live(monkeypatch)
        p = _pending()
        ex = _Exchange(account=[], order={"status": "closed", "average": 0.7669})
        out = await GetOrdersSkill().execute(_engine(_Executor(ex, [p])), user_id="op")
        assert "Pending order status" in out and "FILLED" in out
        assert "No pending orders" in out          # nothing is resting any more

    @pytest.mark.asyncio
    async def test_it_runs_as_a_chat_tool(self, monkeypatch):
        """Through run_tool, the door the model actually uses."""
        _live(monkeypatch)
        ex = _Exchange(account=[_limit()], tickers={"BTC/USDT:USDT": 61000.0})
        registry = build_default_registry()
        handler = NS(registry=registry, engine=_engine(_Executor(ex)), conversations=None)
        out = await chat_tools.run_tool(handler, "op", "get_orders", {}, {"get_orders"})
        # run_tool hands the model plain text: the tags are stripped.
        assert "Limit: $60,000.0000" in out and "+1.64% to fill" in out


# ── /orders itself: the same read, the caller's own account ────────────────

def _update(uid="op"):
    return NS(effective_user=NS(id=uid), effective_chat=None, message=None)


def _handler(executor):
    from bot.core.engine import RuneClawEngine
    h = th.TelegramHandler(RuneClawEngine())
    h.engine.viewer_executor = lambda uid: executor if uid == "op" else None
    sent: list = []

    async def _send(update, text, *a, **k):
        sent.append(text)
    h._send = _send
    return h, sent


async def _cmd(h, uid="op"):
    fn = th.TelegramHandler._cmd_orders
    fn = getattr(fn, "__wrapped__", fn)
    await fn(h, _update(uid), None)


class TestTheCommand:
    @pytest.mark.asyncio
    async def test_it_reads_the_callers_own_account(self, monkeypatch):
        _live(monkeypatch)
        ex = _Exchange(account=[_limit()], tickers={"BTC/USDT:USDT": 61000.0})
        h, sent = _handler(_Executor(ex))
        await _cmd(h)
        text = "\n".join(sent)
        assert "Limit: <code>$60,000.0000</code>" in text
        assert "+1.64% to fill" in text

    @pytest.mark.asyncio
    async def test_a_caller_with_no_account_never_sees_the_operators_book(self, monkeypatch):
        _live(monkeypatch)
        h, sent = _handler(_Executor(_Exchange(account=[_limit()])))
        await _cmd(h, uid="stranger")
        text = "\n".join(sent)
        assert "No linked live account" in text and "BTC" not in text

    @pytest.mark.asyncio
    async def test_an_unreadable_book_is_said_in_words_that_rule_out_none(self, monkeypatch):
        _live(monkeypatch)
        h, sent = _handler(_Executor(_Exchange(raise_account=RuntimeError("venue 502"))))
        await _cmd(h)
        text = "\n".join(sent)
        assert "COULD NOT BE READ" in text and "(RuntimeError)" in text
        assert "No pending orders" not in text
        assert "502" not in text

    @pytest.mark.asyncio
    async def test_paper_mode(self, monkeypatch):
        _live(monkeypatch, on=False)
        h, sent = _handler(_Executor(_Exchange()))
        await _cmd(h)
        assert any("Paper mode" in s for s in sent)

    @pytest.mark.asyncio
    async def test_the_venues_none_is_reported_as_none(self, monkeypatch):
        _live(monkeypatch)
        h, sent = _handler(_Executor(_Exchange()))
        await _cmd(h)
        assert any("No pending orders on Bitget" in s for s in sent)

    def test_the_command_no_longer_reads_the_operator_executor_by_hand(self):
        src = code_only(inspect.getsource(th.TelegramHandler._cmd_orders))
        assert "self.engine.live_executor" not in src
        assert "open_orders_for(" in src

    def test_the_command_dispatches_the_skill_for_its_words(self):
        """The reference point tests/test_web_and_scan_authorization.py derives
        the web permission from: a @guard("portfolio") handler that dispatches
        `get_orders`. And it is why the card and the tool cannot disagree."""
        src = code_only(inspect.getsource(th.TelegramHandler._cmd_orders))
        assert 'dispatch("get_orders"' in src

    @pytest.mark.asyncio
    async def test_the_command_and_the_tool_say_the_same_thing(self, monkeypatch):
        _live(monkeypatch)
        ex = _Exchange(account=[_limit(), _stop()], tickers={"BTC/USDT:USDT": 61000.0})
        h, sent = _handler(_Executor(ex))
        await _cmd(h)
        skill_text = await GetOrdersSkill().execute(_engine(_Executor(ex)), user_id="op")
        assert skill_text in sent


class TestTheResolver:
    @pytest.mark.asyncio
    async def test_paper(self, monkeypatch):
        _live(monkeypatch, on=False)
        r = await oo.open_orders_for(_engine(_Executor(_Exchange())), "op")
        assert r.state == "paper" and "Paper mode" in oo.render_open_orders_html(r)

    @pytest.mark.asyncio
    async def test_no_account(self, monkeypatch):
        _live(monkeypatch)
        r = await oo.open_orders_for(_engine(_Executor(_Exchange(account=[_limit()]))), "stranger")
        assert r.state == "no_account" and r.orders == []
        assert "BTC" not in oo.render_open_orders_html(r)

    @pytest.mark.asyncio
    async def test_unreadable_carries_the_kind_never_the_text(self, monkeypatch):
        _live(monkeypatch)
        r = await oo.open_orders_for(_engine(_Executor(_Exchange(raise_account=RuntimeError("secret 502")))), "op")
        assert r.state == "unreadable" and r.error_kind == "RuntimeError"
        text = oo.render_open_orders_html(r)
        assert "COULD NOT BE READ" in text and "(RuntimeError)" in text and "secret" not in text

    @pytest.mark.asyncio
    async def test_the_venues_none_and_the_venues_list(self, monkeypatch):
        _live(monkeypatch)
        assert (await oo.open_orders_for(_engine(_Executor(_Exchange())), "op")).state == "empty"
        r = await oo.open_orders_for(_engine(_Executor(_Exchange(account=[_limit()]))), "op")
        assert r.state == "read" and r.unavailable is None
