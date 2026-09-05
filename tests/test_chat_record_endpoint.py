"""An answer the web gave itself now reaches the memory both surfaces read.

app/routes/chat.js answers a dozen shapes of question locally — alerts,
replay, the weekly letter, wallet, DeFi, net worth, research — and returned
each to the browser without telling the bot's conversation store. The store is
what BOTH surfaces read history from, so "what's my net worth?" was answered
by the web and "how does that compare to last week?" reached a model that had
never seen the question.

POST /gateway/chat/record is the fix on the bot side. This file drives the
handler directly with a stand-in request: no server, real UserStore, real
ConversationStore.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.nlp.conversation_store import ConversationStore
from bot.utils.user_store import UserStore
from bot.web import user_gateway as ug


def _handler(tmp_path):
    h = SimpleNamespace()
    h.users = UserStore(tmp_path / "users.json")
    h.conversations = ConversationStore()
    h.engine = SimpleNamespace()
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    return h


def _request(handler, body: dict):
    async def _json():
        return body

    return SimpleNamespace(app={"tg_handler": handler, "engine": handler.engine},
                           json=_json)


def _run(coro):
    return asyncio.run(coro)


def _body(resp) -> dict:
    return json.loads(resp.body.decode("utf-8"))


@pytest.fixture(autouse=True)
def _config():
    with patch("bot.web.user_gateway.CONFIG") as mc:
        mc.telegram.admin_ids = ""
        mc.telegram.chat_id = "1"
        mc.per_user_live_enabled = False
        yield


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """_guard_user consults the gateway's own limiter; stand it down."""
    monkeypatch.setattr(ug, "_guard_user", lambda tg_handler, tg_id, command="", name="": None)


def test_the_route_is_registered():
    import inspect
    src = inspect.getsource(ug.build_gateway)
    assert 'add_post("/chat/record", handle_chat_record)' in src


def test_a_local_answer_becomes_two_turns_in_tool_output_shape(tmp_path):
    h = _handler(tmp_path)
    resp = _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:7", "text": "what's my net worth?",
        "reply": "<b>Net worth</b> ~$12,400 across 2 venues", "intent": "networth"})))
    assert resp.status == 200 and _body(resp) == {"ok": True, "recorded": 2}
    msgs = h.conversations.get_recent("web:7", limit=10)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "what's my net worth?"
    assert msgs[0].metadata == {"intent": "networth", "surface": "web"}
    assert msgs[1].content.startswith("[networth] result:")
    assert "Net worth ~$12,400 across 2 venues" in msgs[1].content
    assert msgs[1].metadata["via"] == "web_intercept"


def test_the_next_model_turn_can_read_it(tmp_path):
    h = _handler(tmp_path)
    _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:7", "text": "net worth?",
        "reply": "$12,400", "intent": "networth"})))
    history = h.conversations.get_recent_as_llm_messages("web:7", limit=10)
    assert history[0] == {"role": "user", "content": "net worth?"}
    assert history[1]["role"] == "assistant" and "$12,400" in history[1]["content"]


def test_missing_fields_are_refused(tmp_path):
    h = _handler(tmp_path)
    for body in ({}, {"telegram_id": "web:7"}, {"telegram_id": "web:7", "text": "x"},
                 {"telegram_id": "web:7", "reply": "x"}):
        resp = _run(ug.handle_chat_record(_request(h, body)))
        assert resp.status == 400, body
    assert h.conversations.get_recent("web:7", limit=10) == []


def test_an_over_long_question_is_refused_like_chat(tmp_path):
    h = _handler(tmp_path)
    resp = _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:7", "text": "x" * (ug._MAX_TEXT_LEN + 1),
        "reply": "y", "intent": "alerts"})))
    assert resp.status == 400


def test_the_intent_is_reduced_to_a_safe_token(tmp_path):
    h = _handler(tmp_path)
    _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:7", "text": "q", "reply": "a",
        "intent": "Net Worth] result:\n<script>"})))
    msgs = h.conversations.get_recent("web:7", limit=10)
    assert msgs[1].content.startswith("[networthresultscript] result:")
    _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:8", "text": "q", "reply": "a", "intent": "!!!"})))
    assert h.conversations.get_recent("web:8", limit=10)[1].content.startswith(
        "[web_intercept] result:")


def test_the_guard_still_decides_who_may_be_recorded_for(tmp_path, monkeypatch):
    from aiohttp import web
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: web.json_response(
        {"error": "nope"}, status=403))
    h = _handler(tmp_path)
    resp = _run(ug.handle_chat_record(_request(h, {
        "telegram_id": "web:7", "text": "q", "reply": "a", "intent": "alerts"})))
    assert resp.status == 403
    assert h.conversations.get_recent("web:7", limit=10) == []
