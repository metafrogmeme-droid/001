"""A chat draft registers nothing until Stage, and Stage does not confirm.

The ask is the user's own message. A sentence that would be a tool result
is not an argument of the check, so planting "draft a ticket" in a tool
result cannot open one.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.nlp import chat_draft, chat_tools
from bot.skills.manual_trade import build_manual_idea


@pytest.fixture(autouse=True)
def _empty_drafts():
    chat_draft._clear_drafts()
    yield
    chat_draft._clear_drafts()


def test_the_ask_is_the_users_own_message():
    assert chat_draft.user_asks_for_ticket("draft a long ticket on btc")
    assert chat_draft.user_asks_for_ticket("write me a ticket for ETH")
    assert chat_draft.user_asks_for_ticket("please draft a short on SOL")
    assert not chat_draft.user_asks_for_ticket("what is bitcoin doing")
    assert not chat_draft.user_asks_for_ticket("draft the weekly letter")
    # Those words count only when they are the message. run_tool is given
    # that message and nothing from a tool result, which the next test drives.


class _Engine:
    def __init__(self):
        self._pending_ideas = {}
        self._ohlcv_cache = {}
        self.confirmed = []

    def confirm_trade(self, *args, **kwargs):
        self.confirmed.append(args)


class _Registry:
    def __init__(self):
        self._skill = chat_draft.DraftTradeSkill()

    def get(self, name):
        return self._skill if name == "draft_trade" else None


class _Handler:
    def __init__(self, engine):
        self.engine = engine
        self.registry = _Registry()
        self.conversations = None


def _run(engine, text, args):
    return asyncio.run(chat_tools.run_tool(
        _Handler(engine), "user-1", "draft_trade", args,
        offered={"draft_trade"}, user_text=text))


def test_a_turn_that_did_not_ask_computes_nothing(monkeypatch):
    monkeypatch.setattr(chat_draft, "market_for",
                        lambda engine, symbol: {"read_state": "read", "price": 100.0,
                                                "atr": 2.0, "as_of": 1})
    engine = _Engine()
    out = _run(engine, "what did the news say",
               {"symbol": "BTC", "direction": "LONG"})
    assert "nothing was registered" in out.lower()
    assert engine._pending_ideas == {}
    assert chat_draft.offer("user-1") is None
    assert engine.confirmed == []


def test_a_ticket_is_stored_and_not_registered(monkeypatch):
    monkeypatch.setattr(chat_draft, "market_for",
                        lambda engine, symbol: {"read_state": "read", "price": 100.0,
                                                "atr": 2.0, "as_of": 1})
    engine = _Engine()
    out = _run(engine, "draft a long ticket on btc",
               {"symbol": "BTC", "direction": "LONG"})
    assert "not registered" in out.lower()
    assert engine._pending_ideas == {}
    assert engine.confirmed == []
    card = chat_draft.offer("user-1")
    assert card["direction"] == "LONG"
    assert card["symbol"] == "BTC"
    assert card["sl"] < card["entry"] < card["tp"]
    assert card["net_rr"] is not None and card["net_rr"] > 0
    idea = chat_draft.stage_draft(engine, card["id"], "user-1")
    assert idea.source == "manual"
    assert idea.origin == "chat_draft"
    assert idea.id in engine._pending_ideas
    assert engine.confirmed == []
    again = chat_draft.stage_draft(engine, card["id"], "user-1")
    assert isinstance(again, str)
    assert len(engine._pending_ideas) == 1


def test_someone_else_cannot_stage_it(monkeypatch):
    monkeypatch.setattr(chat_draft, "market_for",
                        lambda engine, symbol: {"read_state": "read", "price": 100.0,
                                                "atr": 2.0, "as_of": 1})
    engine = _Engine()
    _run(engine, "draft a short trade on eth",
         {"symbol": "ETH", "direction": "SHORT"})
    card = chat_draft.offer("user-1")
    said = chat_draft.stage_draft(engine, card["id"], "user-2")
    assert isinstance(said, str)
    assert engine._pending_ideas == {}


def test_an_expired_draft_registers_nothing(monkeypatch):
    monkeypatch.setattr(chat_draft, "market_for",
                        lambda engine, symbol: {"read_state": "read", "price": 100.0,
                                                "atr": 2.0, "as_of": 1})
    engine = _Engine()
    _run(engine, "make a ticket for SOL", {"symbol": "SOL", "direction": "LONG"})
    card = chat_draft.offer("user-1")
    monkeypatch.setattr(chat_draft, "_now", lambda: 10**9)
    said = chat_draft.stage_draft(engine, card["id"], "user-1")
    assert isinstance(said, str)
    assert engine._pending_ideas == {}


def test_an_unread_market_is_not_a_two_percent_stop():
    engine = _Engine()
    tag, prose = chat_draft.draft_ticket(engine, "user-1", "BTC/USDT", "LONG")
    assert tag == "UNREAD"
    assert "nothing was registered" in prose.lower()
    assert engine._pending_ideas == {}
    assert chat_draft.offer("user-1") is None


def test_cached_candles_are_a_reading_and_a_short_cache_is_not():
    engine = _Engine()
    assert chat_draft.market_for(engine, "BTC/USDT")["read_state"] == "unread"
    rows = []
    price = 100.0
    for i in range(20):
        price += 0.5
        rows.append([1_700_000_000_000 + i * 60_000, price - 1, price + 1,
                     price - 1.2, price, 10])
    engine._ohlcv_cache["BTC/USDT:1h:100"] = (1.0, rows, 120.0)
    read = chat_draft.market_for(engine, "BTC/USDT")
    assert read["read_state"] == "read"
    assert read["price"] == rows[-1][4]
    assert read["atr"] > 0
    short = _Engine()
    short._ohlcv_cache["ETH/USDT:1h:100"] = (1.0, rows[:5], 120.0)
    assert chat_draft.market_for(short, "ETH/USDT")["read_state"] == "unread"


def test_a_typed_trade_has_no_chat_origin():
    idea = build_manual_idea("LONG", "SOL", 100, 90, 120)
    assert idea.source == "manual"
    assert idea.origin is None


def _stage_update(draft_id, uid="4242"):
    from types import SimpleNamespace

    class _Query:
        data = f"stg:{draft_id}:{uid}"

        async def answer(self):
            return None

    return SimpleNamespace(
        callback_query=_Query(),
        effective_user=SimpleNamespace(id=int(uid)),
        effective_chat=SimpleNamespace(id=1),
    )


def _stage_handler(engine, *, may_trade):
    from bot.skills.telegram_handler import TelegramHandler

    class _Users:
        def has_permission(self, tid, perm):
            return may_trade

        def get(self, tid):
            return {"role": "trader" if may_trade else "viewer"}

        def get_tier(self, tid):
            return "free"

        def is_admitted(self, tid):
            return True

        def permission_denial(self, tid, perm):
            return None if may_trade else "role"

        def register(self, *args, **kwargs):
            return None

    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = engine
    h.users = _Users()
    h._limiter = type("L", (), {"allow": staticmethod(lambda uid: True)})()
    h._check_auth = lambda update: True
    h.sent = []

    async def _send(update, text, **kw):
        h.sent.append((text, kw.get("reply_markup")))

    h._send = _send
    return h


def test_the_stage_button_shows_the_review_and_does_not_confirm(monkeypatch):
    monkeypatch.setattr(chat_draft, "market_for",
                        lambda engine, symbol: {"read_state": "read", "price": 100.0,
                                                "atr": 2.0, "as_of": 1})
    planted = {
        "verdict": "caution",
        "score_line": "score 70/100 over 3 of the 4 checks",
        "flags": [{"msg": "Stop is only 0.2% away — wicked out."}],
        "notes": [],
        "unchecked": [],
    }

    async def _review(engine, user_id, trade):
        _review.trade = trade
        return planted

    monkeypatch.setattr("bot.core.copilot_context.review_ticket", _review)
    engine = _Engine()
    tag, _prose = chat_draft.draft_ticket(engine, "4242", "SOL", "LONG")
    assert tag == "READ"
    draft_id = next(iter(chat_draft._DRAFTS))
    h = _stage_handler(engine, may_trade=True)
    asyncio.run(h._handle_callback(_stage_update(draft_id), None))
    assert len(h.sent) == 1
    card, markup = h.sent[0]
    assert "wicked out" in card
    idea = next(iter(engine._pending_ideas.values()))
    assert f"confirm:{idea.id}:4242" in str(markup.to_dict())
    assert idea.origin == "chat_draft"
    assert engine.confirmed == []
    assert _review.trade["entry"] == idea.entry_price


def test_a_viewer_pressing_stage_registers_nothing(monkeypatch):
    monkeypatch.setattr(chat_draft, "market_for",
                        lambda engine, symbol: {"read_state": "read", "price": 100.0,
                                                "atr": 2.0, "as_of": 1})
    engine = _Engine()
    chat_draft.draft_ticket(engine, "4242", "SOL", "LONG")
    draft_id = next(iter(chat_draft._DRAFTS))
    h = _stage_handler(engine, may_trade=False)
    asyncio.run(h._handle_callback(_stage_update(draft_id), None))
    assert engine._pending_ideas == {}
    assert engine.confirmed == []
    assert h.sent and "cannot" in h.sent[0][0].lower()


class TestAnExpiredDraftLeavesTheStore:
    """Nothing removed a draft: every priced ticket stayed in `_DRAFTS` for
    the bot's lifetime and `offer` walked all of them on every turn. An
    expired one is pruned when a new one is stored and when one is offered;
    a live one stays."""

    @staticmethod
    def _draft(monkeypatch, at, user="user-1", symbol="SOL/USDT"):
        monkeypatch.setattr(chat_draft, "market_for",
                            lambda engine, sym: {"read_state": "read", "price": 100.0,
                                                 "atr": 2.0, "as_of": 1})
        monkeypatch.setattr(chat_draft, "_now", lambda: at)
        tag, _ = chat_draft.draft_ticket(_Engine(), user, symbol, "LONG")
        assert tag == "READ"
        return max(chat_draft._DRAFTS.values(), key=lambda d: d.created).id

    def test_a_new_draft_prunes_the_expired_ones(self, monkeypatch):
        old = self._draft(monkeypatch, 1000.0)
        live = self._draft(monkeypatch, 1000.0 + 600, user="user-2")
        newest = self._draft(monkeypatch, 1000.0 + chat_draft._TTL_S + 1, user="user-3")
        assert old not in chat_draft._DRAFTS
        assert {live, newest} <= set(chat_draft._DRAFTS)

    def test_an_offer_prunes_too_and_still_offers_the_live_one(self, monkeypatch):
        old = self._draft(monkeypatch, 1000.0, user="user-9")
        live = self._draft(monkeypatch, 1000.0 + 600)
        monkeypatch.setattr(chat_draft, "_now", lambda: 1000.0 + chat_draft._TTL_S + 5)
        card = chat_draft.offer("user-1")
        assert card is not None and card["id"] == live
        assert old not in chat_draft._DRAFTS and live in chat_draft._DRAFTS

    def test_a_draft_at_its_ttl_is_still_held(self, monkeypatch):
        held = self._draft(monkeypatch, 1000.0)
        monkeypatch.setattr(chat_draft, "_now", lambda: 1000.0 + chat_draft._TTL_S)
        assert chat_draft.offer("user-1")["id"] == held
