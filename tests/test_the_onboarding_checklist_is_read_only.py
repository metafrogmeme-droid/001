"""F8's chat checklist names four doors and stages nothing.

"I'm new, what now?" used to be the capability catalogue. A new paper user
needs the next steps: watch a signal, open its page, tap Confirm, link keys
with withdrawal off. Chat does not stage the ticket and does not call the
executor. A trader is not told their Confirm opens a practice row. An
unreadable account is not told it is paper, and places nothing.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

from bot.formatters.onboarding_checklist import onboarding_checklist
from bot.nlp.intent_router import IntentRouter, _is_social_message
from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore

PAPER = "999"
TRADER = "4242"


def _run(coro):
    return asyncio.run(coro)


ONBOARDING = (
    "im new what now",
    "i'm new, what do i do",
    "i am new",
    "how do i get started",
    "where do i start",
    "what should i do first",
    "getting started",
    "first steps",
    "onboarding",
    "onboarding checklist",
    "help me get started",
    "hey im new what now",
    "im new what now thanks",
)

DECOYS = (
    "what can you do",
    "help",
    "how does funding work",
    "i'm new to leverage, how does it work",
    "getting started with my ETH position",
    "what should i do first with SOL",
    "buy eth",
)


def test_a_getting_started_question_is_the_checklist_and_not_small_talk():
    router = IntentRouter()
    for text in ONBOARDING:
        assert not _is_social_message(text), text
        assert router.classify_rules(text).skill == "onboarding", text
    for text in ("what can you do", "help", "how does this work", "capabilities"):
        assert router.classify_rules(text).skill == "help", text
    for text in DECOYS:
        if text in ("what can you do", "help"):
            continue
        assert router.classify_rules(text).skill != "onboarding", text


def test_the_card_names_the_practice_door_only_for_a_paper_account():
    users = {
        PAPER: {"role": SELF_ADMISSION_ROLE},
        TRADER: {"role": "trader"},
    }

    class _Store:
        def get(self, uid):
            return users.get(uid)

    paper = onboarding_checklist("telegram", _Store(), PAPER)
    trader = onboarding_checklist("web", _Store(), TRADER)
    assert "PRACTICE row" in paper
    assert "does not send an order" in paper
    assert "without live permission" not in paper
    assert "live refusal" in trader
    assert "This account is not self-admitted paper" in trader
    assert "opens a PRACTICE row" not in trader
    assert "This chat does not stage a ticket" in paper
    assert "This chat does not stage a ticket" in trader
    assert "This chat does not place an order" in paper


def test_an_unreadable_account_is_not_paper_and_an_absent_one_is_not_either():
    class _Down:
        def get(self, uid):
            raise RuntimeError("store down")

    unread = onboarding_checklist("telegram", _Down(), PAPER)
    absent = onboarding_checklist("web", _Down(), None)
    assert "could not be read" in unread
    assert "places nothing" in unread
    assert "Your account is practice" not in unread
    assert "No account was read" in absent
    assert "Your account is practice" not in absent
    assert "$" not in unread and "$" not in absent


def test_each_surface_names_only_its_own_doors():
    card_web = onboarding_checklist("web")
    card_tg = onboarding_checklist("telegram")
    card_pub = onboarding_checklist("public")
    assert "/connect" not in card_web and "/signals" not in card_web
    assert "/latest_signal" not in card_web
    assert "Connect an exchange" in card_web
    assert "Signals" in card_web
    assert "/signals" in card_tg and "/latest_signal" in card_tg
    assert "/connect" in card_tg
    assert "/connect" not in card_pub and "/signals" not in card_pub
    with pytest.raises(ValueError):
        onboarding_checklist("nonsense")


def test_only_the_door_that_probes_the_key_says_it_reports_withdraw():
    # Telegram's /connect probes the key's scope and says whether withdraw
    # is on. The web form stores the key and reads nothing about it, and the
    # web card promised a readout the form never gives.
    tg = onboarding_checklist("telegram")
    assert "The card /connect sends back says whether withdraw is on." in tg
    for surface in ("web", "public"):
        card = onboarding_checklist(surface)
        assert "says whether withdraw is on" not in card
        assert "This form does not check whether withdraw is on" in card


def _web(monkeypatch, *, get):
    from bot.nlp.conversation_store import ConversationStore
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
    monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
    monkeypatch.setattr(ug, "build_profile_note", lambda p: "")
    confirm = []
    engine = NS(
        firewall_scan=lambda *a, **kw: None,
        _pending_ideas={},
        confirm_calls=confirm,
        confirm_trade=lambda *a, **kw: confirm.append((a, kw)),
        _self_admitted_practice=lambda *a, **kw: confirm.append("practice-read"),
    )
    llm = AsyncMock(return_value="model answer")
    h = NS(
        intent_router=IntentRouter(),
        registry=NS(get=lambda n: None),
        conversations=ConversationStore(),
        users=NS(get=get, get_tier=lambda u: "elite",
                 is_authorized=lambda u: True,
                 permission_denial=lambda u, p: None),
        _llm_chat=llm,
        engine=engine,
    )
    return ug, h, engine, llm


def _turn(ug, h, text, uid=PAPER):
    async def _json():
        return {"telegram_id": uid, "text": text}

    req = NS(app={"tg_handler": h,
                  "engine": h.engine},
             json=_json, headers={}, remote="1.2.3.4")
    resp = asyncio.run(ug._chat_turn(req))
    return resp, json.loads(resp.text)


def test_web_chat_answers_a_paper_user_with_the_checklist_and_places_nothing(monkeypatch):
    ug, h, engine, llm = _web(
        monkeypatch, get=lambda uid: {"role": SELF_ADMISSION_ROLE})
    resp, body = _turn(ug, h, "im new what now")
    assert resp.status == 200
    assert body["intent"] == "onboarding"
    assert "PRACTICE row" in body["reply_html"]
    assert "/connect" not in body["reply_html"]
    assert engine.confirm_calls == []
    llm.assert_not_called()
    _resp, catalogue = _turn(ug, h, "what can you do")
    assert catalogue["intent"] == "help"
    assert "What I can do for you" in catalogue["reply_html"]


def test_web_chat_does_not_tell_a_trader_their_confirm_is_practice(monkeypatch):
    ug, h, engine, llm = _web(
        monkeypatch, get=lambda uid: {"role": "trader"})
    _resp, body = _turn(ug, h, "how do i get started", uid=TRADER)
    assert body["intent"] == "onboarding"
    assert "live refusal" in body["reply_html"]
    assert "opens a PRACTICE row" not in body["reply_html"]
    assert engine.confirm_calls == []
    llm.assert_not_called()


def test_web_chat_says_unknown_when_the_account_cannot_be_read(monkeypatch):
    def _boom(uid):
        raise RuntimeError("store down")

    ug, h, engine, llm = _web(monkeypatch, get=_boom)
    _resp, body = _turn(ug, h, "getting started")
    assert "could not be read" in body["reply_html"]
    assert "places nothing" in body["reply_html"]
    assert "Your account is practice" not in body["reply_html"]
    assert engine.confirm_calls == []
    llm.assert_not_called()


def test_public_chat_shows_the_checklist_without_claiming_an_account(monkeypatch):
    from bot.web import user_gateway as ug
    llm = AsyncMock(return_value="improvised")
    h = NS(intent_router=IntentRouter(), _llm_chat=llm, users=None,
           conversations=NS(append=lambda *a, **kw: None))

    async def _json():
        return {"text": "im new what now"}

    req = NS(app={"tg_handler": h}, json=_json, headers={}, remote="1.2.3.4")
    resp = _run(ug._public_chat_turn(req))
    body = json.loads(resp.text)
    assert body["intent"] == "onboarding"
    assert "No account was read" in body["reply_html"]
    assert "Your account is practice" not in body["reply_html"]
    assert "/connect" not in body["reply_html"]
    llm.assert_not_called()


def test_the_api_bridge_answers_the_checklist_and_does_not_call_the_model():
    from bot.nlp import chat_facade
    seen: dict = {}

    async def _llm(question, **kw):
        seen["question"] = question
        return "improvised"

    h = chat_facade.headless_handler(NS())
    h._llm_chat = _llm
    out = _run(chat_facade.ask(h, "im new what now", user_id="111"))
    assert out["answered_by"] == "none"
    assert out["tools"] == []
    assert seen == {}
    assert "could not be read" in out["reply_html"]
    assert "places nothing" in out["reply_html"]
    turns = h.conversations.get_recent("111", limit=10)
    assert [m.role for m in turns] == ["user", "assistant"]
    assert "Getting started" in turns[1].content or "practice book" in turns[1].content


def _telegram(tmp_path):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    confirm = []

    class _Engine:
        def __init__(self):
            self.confirm_calls = confirm
            self._pending_ideas = {}

        def firewall_scan(self, *a, **kw):
            return None

        async def confirm_trade(self, *a, **kw):
            confirm.append((a, kw))
            raise AssertionError("the checklist must not confirm")

        def _self_admitted_practice(self, *a, **kw):
            confirm.append("practice-read")
            return False

    h.engine = _Engine()
    h.registry = NS(get=lambda n: None, dispatch=AsyncMock())
    h._limiter = NS(allow=lambda uid: True)
    from bot.nlp.conversation_store import ConversationStore
    h.conversations = ConversationStore()
    h.forwarder = NS(detect_group=lambda *a, **kw: None)
    h._pending_limit_input = {}
    h.intent_router = IntentRouter()
    h.sent = []

    async def _send(update, text, **kwargs):
        h.sent.append(text)

    h._send = _send
    h.users.register(PAPER, name="Paper")
    h.users.authorize(PAPER, role=SELF_ADMISSION_ROLE, by=SELF_ADMISSION_BY)
    h.users.register(TRADER, name="Vouched")
    h.users.authorize(TRADER, role="trader", by="1")
    return h


def _update(uid, text):
    msg = NS(text=text)

    async def _reply(*a, **kw):
        pass

    msg.reply_text = _reply
    chat = NS(id=int(uid), type="private", title="")

    async def _action(*a, **kw):
        pass

    chat.send_chat_action = _action
    return NS(
        effective_user=NS(id=int(uid), first_name="T", language_code="en"),
        effective_chat=chat, message=msg, callback_query=None)


@pytest.mark.asyncio
async def test_telegram_answers_both_accounts_and_calls_no_executor(tmp_path):
    h = _telegram(tmp_path)
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = "1"
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = ""
        mc.paper_auto_accept = False
        mc.per_user_live_enabled = False
        mc.is_live.return_value = True
        mc.risk = NS(guardian_firewall_block_high=False)
    try:
        await h._handle_message(_update(PAPER, "im new what now"), None)
        await h._handle_message(_update(TRADER, "how do i get started"), None)
    finally:
        patch.stopall()
    assert len(h.sent) == 2
    assert "PRACTICE row" in h.sent[0]
    assert "does not send an order" in h.sent[0]
    assert "/signals" in h.sent[0] and "/connect" in h.sent[0]
    assert "live refusal" in h.sent[1]
    assert "opens a PRACTICE row" not in h.sent[1]
    assert h.engine.confirm_calls == []
    h.registry.dispatch.assert_not_called()
