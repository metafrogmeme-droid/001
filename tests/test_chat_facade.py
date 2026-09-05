"""The chat brain, reachable from the REST bridge.

`api_bridge.py` owned a `RuneClawEngine` and no `TelegramHandler`, so the one
surface an external PROGRAM reaches could scan, analyze and read risk, and
could not be asked a question. `bot/nlp/chat_facade.py` builds a headless
handler — `__new__` plus exactly what `_llm_chat` reads, the way the test
suites do — and `POST /chat` on the bridge runs a turn on it as the operator,
whose bearer token it is.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import secrets
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from bot.core.cost import CostTracker
from bot.nlp import chat_facade
from bot.nlp.sanitize import MAX_CHAT_INPUT_LEN
from bot.skills import telegram_handler as th_mod
from bot.skills.telegram_handler import TelegramHandler

# api_bridge mounts the auth router, which refuses to import without a real
# JWT secret — the same line tests/test_http_gate_parity.py carries.
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
import api_bridge  # noqa: E402


def _engine():
    return SimpleNamespace(cost=CostTracker(), analyzer=None)


def _run(coro):
    return asyncio.run(coro)


def _answering(h, reply, meta, seen=None):
    async def _llm_chat(question, **kw):
        if seen is not None:
            seen.update(kw, question=question)
        return reply, meta
    h._llm_chat = _llm_chat
    return h


# ── the headless handler ────────────────────────────────────────────────────

def test_a_headless_handler_is_a_real_handler_with_no_telegram_in_it():
    h = chat_facade.headless_handler(_engine())
    assert isinstance(h, TelegramHandler)
    assert h.users is None and h.registry is None
    for telegram_only in ("_limiter", "monitor", "forwarder", "signal_tracker"):
        assert not hasattr(h, telegram_only), telegram_only


def test_it_offers_the_model_no_tools():
    """No user store means no readable role, and an unreadable role holds
    nothing — the chat_tools rule, reached through the real gate."""
    h = chat_facade.headless_handler(_engine())
    assert th_mod._chat_tools_for(h, "111", "api", False) == []


# ── one turn ────────────────────────────────────────────────────────────────

def test_ask_remembers_both_turns_and_reports_the_model():
    seen: dict = {}
    h = _answering(chat_facade.headless_handler(_engine()), "<b>BTC</b> is range-bound.",
                   {"provider": "gemini", "model": "gemini-3.5-flash",
                    "tools": [{"name": "get_portfolio", "ok": True, "ms": 3}]}, seen)
    out = _run(chat_facade.ask(h, "  how is BTC?  ", user_id="111", is_admin=True,
                               reply_lang="es", surface="api"))
    assert out == {"reply_html": "<b>BTC</b> is range-bound.", "provider": "gemini",
                   "model": "gemini-3.5-flash", "tools": ["get_portfolio"],
                   "answered_by": "model"}
    assert seen["question"] == "how is BTC?"
    assert seen["user_id"] == "111" and seen["is_admin"] is True
    assert seen["reply_lang"] == "es" and seen["surface"] == "api"
    assert seen["return_meta"] is True and seen["public"] is False
    msgs = h.conversations.get_recent("111", limit=10)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "how is BTC?"
    assert msgs[1].metadata["model"] == "gemini-3.5-flash"
    assert msgs[1].metadata["tools"] == ["get_portfolio"]


def test_no_model_is_reported_as_none_not_as_a_model():
    h = _answering(chat_facade.headless_handler(_engine()),
                   "The AI is having trouble thinking right now.", {})
    out = _run(chat_facade.ask(h, "hi", user_id="111"))
    assert out["answered_by"] == "none"
    assert out["model"] == "" and out["provider"] == "" and out["tools"] == []
    last = h.conversations.get_recent("111", limit=10)[-1]
    assert last.metadata == {"surface": "api", "answered_by": "none"}


def test_a_public_turn_is_account_free_and_remembered_nowhere():
    seen: dict = {}
    h = _answering(chat_facade.headless_handler(_engine()), "answer",
                   {"provider": "grok", "model": "grok-4.3"}, seen)
    _run(chat_facade.ask(h, "hi", user_id="111", public=True))
    assert seen["public"] is True
    assert h.conversations.get_recent("111", limit=10) == []


def test_the_second_turn_sees_the_first():
    h = _answering(chat_facade.headless_handler(_engine()), "ok",
                   {"provider": "gemini", "model": "gemini-3.5-flash"})
    _run(chat_facade.ask(h, "first", user_id="111"))
    _run(chat_facade.ask(h, "second", user_id="111"))
    history = h.conversations.get_recent_as_llm_messages("111", limit=9, drop_trailing_user=True)
    assert [m["content"] for m in history] == ["first", "ok", "ok"] or \
        [m["content"] for m in history][:2] == ["first", "ok"]


@pytest.mark.parametrize("bad", ["", "   ", None, "x" * (MAX_CHAT_INPUT_LEN + 1)])
def test_the_bounds_are_refused_before_any_model_runs(bad):
    h = chat_facade.headless_handler(_engine())

    async def _boom(*a, **kw):
        raise AssertionError("must not run")

    h._llm_chat = _boom
    with pytest.raises(ValueError):
        _run(chat_facade.ask(h, bad, user_id="111"))
    assert h.conversations.get_recent("111", limit=10) == []


# ── the bridge route ────────────────────────────────────────────────────────

def _bridge(monkeypatch, reply="ok", meta=None, chat_id="424242"):
    seen: dict = {}
    monkeypatch.setattr(api_bridge, "engine", _engine())
    h = _answering(chat_facade.headless_handler(api_bridge.engine), reply,
                   {"provider": "gemini", "model": "gemini-3.5-flash"} if meta is None else meta,
                   seen)
    monkeypatch.setattr(api_bridge, "_chat_handler", h)
    monkeypatch.setattr(api_bridge, "CONFIG", replace(
        api_bridge.CONFIG, telegram=replace(api_bridge.CONFIG.telegram, chat_id=chat_id)))
    return api_bridge, seen


def test_the_bridge_chat_runs_as_the_operator(monkeypatch):
    api_bridge, seen = _bridge(monkeypatch)
    out = _run(api_bridge.chat(api_bridge.ChatRequest(question="what is my exposure?", lang="es"),
                               _token="t", _rl=None))
    assert out["reply_html"] == "ok" and out["answered_by"] == "model"
    assert out["model"] == "gemini-3.5-flash"
    assert seen["user_id"] == "424242", "the operator's own portfolio context"
    assert seen["is_admin"] is True, "the bearer of the dashboard token is the operator"
    assert seen["public"] is False and seen["surface"] == "api"
    assert seen["reply_lang"] == "es"


def test_the_bridge_chat_without_a_seeded_operator_still_has_an_identity(monkeypatch):
    api_bridge, seen = _bridge(monkeypatch, chat_id="")
    _run(api_bridge.chat(api_bridge.ChatRequest(question="hi"), _token="t", _rl=None))
    assert seen["user_id"] == "operator"


def test_the_bridge_refuses_what_the_facade_refuses(monkeypatch):
    api_bridge, _seen = _bridge(monkeypatch)
    for bad in ("", "   ", "x" * (MAX_CHAT_INPUT_LEN + 1)):
        with pytest.raises(HTTPException) as e:
            _run(api_bridge.chat(api_bridge.ChatRequest(question=bad), _token="t", _rl=None))
        assert e.value.status_code == 400


def test_a_malformed_language_tag_is_dropped_not_forwarded(monkeypatch):
    api_bridge, seen = _bridge(monkeypatch)
    _run(api_bridge.chat(api_bridge.ChatRequest(question="hi", lang="x!; drop"),
                         _token="t", _rl=None))
    assert seen["reply_lang"] == ""


def test_no_engine_is_503_not_an_answer(monkeypatch):
    monkeypatch.setattr(api_bridge, "engine", None)
    with pytest.raises(HTTPException) as e:
        _run(api_bridge.chat(api_bridge.ChatRequest(question="hi"), _token="t", _rl=None))
    assert e.value.status_code == 503


def test_a_failing_brain_is_a_logged_500_not_a_leaked_exception(monkeypatch):
    monkeypatch.setattr(api_bridge, "engine", _engine())
    h = chat_facade.headless_handler(api_bridge.engine)

    async def _boom(*a, **kw):
        raise RuntimeError("https://user:secret@provider.example/v1 refused")

    h._llm_chat = _boom
    monkeypatch.setattr(api_bridge, "_chat_handler", h)
    with pytest.raises(HTTPException) as e:
        _run(api_bridge.chat(api_bridge.ChatRequest(question="hi"), _token="t", _rl=None))
    assert e.value.status_code == 500
    assert "secret" not in str(e.value.detail)


def test_the_route_is_token_gated_and_rate_limited():
    """The same pin `tests/test_http_gate_parity.py` keeps on /risk/status:
    the dependency fails CLOSED (503) when DASHBOARD_TOKEN is unset, and
    dropping it would make the operator's chat public without anything else
    changing."""
    src = inspect.getsource(api_bridge)
    i = src.index("async def chat(")
    head = src[i:i + 260]
    assert "Depends(require_dashboard_token)" in head
    assert "Depends(_require_rate_limit)" in head
    assert '@app.post("/chat")' in src[i - 120:i]
