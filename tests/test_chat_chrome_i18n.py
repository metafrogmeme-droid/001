"""The chat's own words follow the user's language, not just the model's.

`chat_language_name` tells the model to answer in any of thirty-four languages,
and it does. But everything the CHAT ITSELF says around the model was an
English literal: the thinking phrase before the answer, the four failure
messages when no model answered, the quota wall, and the public scan gate. A
Chinese user got a Chinese answer wrapped in English chrome — and on the day
the model was down, an English apology with no answer at all.

The dictionary carries the fourteen web languages (`SUPPORTED_LANGS`), so
that is the parity the chrome offers; the helper that maps a chat language to
a dictionary language (`ui_lang`) is where a code the dictionary lacks — the
model answers in thirty-four — falls back to English chrome.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.telegram_handler import TelegramHandler as H
from bot.utils.i18n import _STRINGS, SUPPORTED_LANGS, t, ui_lang
from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore
from bot.web import user_gateway as ug

CHROME_KEYS = (
    "chat_no_model_admin", "chat_budget_exhausted", "chat_deadline",
    "chat_empty_completions", "chat_unavailable", "chat_public_scan_gate",
)


def _run(coro):
    return asyncio.run(coro)


# ── the table ───────────────────────────────────────────────────────────────

def test_every_chrome_key_exists_in_every_dictionary_language():
    for key in CHROME_KEYS:
        assert key in _STRINGS, key
        for lang in SUPPORTED_LANGS:
            assert _STRINGS[key][lang].strip(), f"{key}.{lang}"
    phrases = th_mod.THINKING_PHRASE_KEYS
    assert len(phrases) >= 5
    for key in phrases:
        assert key in _STRINGS and _STRINGS[key]["zh"].strip(), key


def test_ui_lang_maps_chat_codes_onto_the_dictionary():
    assert ui_lang("") == "en"
    assert ui_lang("es") == "es", "Spanish is a dictionary language now"
    assert ui_lang("pt-BR") == "pt"
    assert ui_lang("sw") == "en", "a language the dictionary lacks reads English chrome"
    assert ui_lang("zh") == "zh"
    assert ui_lang("zh-TW") == "zh"
    assert ui_lang("ZH_HK") == "zh"
    assert ug._ui_lang is ui_lang, "one mapping, not a copy per surface"


# ── the failure messages ────────────────────────────────────────────────────

class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False):
        return []

    def append(self, *a, **kw):
        pass


def _stub(exhausted=False):
    cost = CostTracker()
    if exhausted:
        cost.snapshot = lambda: SimpleNamespace(llm_calls=10_000, llm_cost_usd=10_000.0)
    return SimpleNamespace(
        engine=SimpleNamespace(cost=cost, analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="": "system prompt",
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
    )


@pytest.fixture(autouse=True)
def _reset_byok():
    BYOK.reset()
    yield
    BYOK.reset()


@pytest.fixture
def chat_tier(monkeypatch):
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.GROK, api_key="k", model="grok-4.3"))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="")))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "resolve_profile_note", lambda note, uid: "")


def test_budget_exhausted_speaks_the_users_language(chat_tier):
    en = _run(H._llm_chat(_stub(exhausted=True), "hi", user_id="u1"))
    zh = _run(H._llm_chat(_stub(exhausted=True), "hi", user_id="u1", reply_lang="zh"))
    assert en == t("chat_budget_exhausted", "en")
    assert zh == t("chat_budget_exhausted", "zh")
    assert "AI" in zh and any("一" <= ch <= "鿿" for ch in zh)


def test_all_providers_failed_speaks_the_users_language(chat_tier, monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(th_mod, "llm_complete", _boom)
    assert _run(H._llm_chat(_stub(), "hi", user_id="u1")) == t("chat_unavailable", "en")
    assert _run(H._llm_chat(_stub(), "hi", user_id="u1", reply_lang="zh")) == t(
        "chat_unavailable", "zh")


def test_empty_completions_speak_the_users_language(chat_tier, monkeypatch):
    async def _empty(*a, **kw):
        return "   "

    monkeypatch.setattr(th_mod, "llm_complete", _empty)
    assert _run(H._llm_chat(_stub(), "hi", user_id="u1", reply_lang="zh")) == t(
        "chat_empty_completions", "zh")


def test_a_language_the_dictionary_lacks_falls_back_to_english(chat_tier):
    assert _run(H._llm_chat(_stub(exhausted=True), "hi", user_id="u1", reply_lang="sw")) == t(
        "chat_budget_exhausted", "en")


def test_a_file_backed_dictionary_language_gets_its_own_chrome(chat_tier):
    es = _run(H._llm_chat(_stub(exhausted=True), "hi", user_id="u1", reply_lang="es"))
    assert es == t("chat_budget_exhausted", "es")
    assert es != t("chat_budget_exhausted", "en") and "/scan" in es


def test_the_english_texts_are_the_ones_the_source_scans_pin():
    """Two suites pin the English wording in the handler's own source. The
    dictionary's English entry must be that wording, or a reader of the table
    and a reader of the code are told different things."""
    assert "answered but returned nothing" in t("chat_empty_completions", "en")
    assert "trouble thinking" in t("chat_unavailable", "en")
    assert "stopped waiting" in t("chat_deadline", "en")


# ── the thinking phrase ─────────────────────────────────────────────────────

def test_thinking_phrases_are_localised_and_still_random():
    en = {th_mod.thinking_phrase("en") for _ in range(60)}
    zh = {th_mod.thinking_phrase("zh") for _ in range(60)}
    assert len(en) > 1 and len(zh) > 1, "still varied"
    assert all("<i>" in p for p in en | zh), "still italic HTML"
    assert all(any("一" <= ch <= "鿿" for ch in p) for p in zh)
    assert not any(any("一" <= ch <= "鿿" for ch in p) for p in en)


# ── the Telegram surface end to end ─────────────────────────────────────────

OPERATOR = "111"
STRANGER = "999"


class _Risk:
    circuit_breaker_active = False

    def pending_retrip_reason(self):
        return ""


class _Engine:
    from bot.core.engine import RuneClawEngine as _E
    _is_operator_user = _E._is_operator_user
    risk_for = _E.risk_for
    del _E

    def __init__(self):
        self.risk = _Risk()
        self._halted = False
        self._pending_ideas: dict = {}
        self._user_risk: dict = {}
        self._user_store = None
        self.pending_ideas: list = []


def _handler(tmp_path):
    h = H.__new__(H)
    h.users = UserStore(tmp_path / "users.json")
    h.engine = _Engine()
    h.engine._user_store = h.users
    h.registry = SimpleNamespace(get=lambda n: None,
                                 dispatch=lambda *a, **kw: asyncio.sleep(0))
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h.conversations = SimpleNamespace(append=lambda *a, **kw: None, get=lambda *a, **kw: [])
    h.forwarder = SimpleNamespace(detect_group=lambda *a, **kw: None)
    h._pending_limit_input = {}
    from bot.nlp.intent_router import IntentRouter
    h.intent_router = IntentRouter()
    h.sent: list[str] = []

    async def _send(update, text, **kwargs):
        h.sent.append(text)

    async def _llm_chat(question, **kw):
        return "answer", {"provider": "grok", "model": "grok-4.3"}

    async def _false(*a, **kw):
        return False

    h._send = _send
    h._llm_chat = _llm_chat
    h._request_operator_admission = _false
    h._send_photo = _false
    h.users.authorize(OPERATOR, role="admin", by=OPERATOR)
    h.users.register(STRANGER, name="Walkin")
    h.users.authorize(STRANGER, role=SELF_ADMISSION_ROLE, by=SELF_ADMISSION_BY)
    return h


def _update(uid, text):
    msg = SimpleNamespace(text=text)

    async def _reply(*a, **kw):
        return None

    msg.reply_text = _reply
    chat = SimpleNamespace(id=int(uid), type="private", title="")

    async def _action(*a, **kw):
        pass

    chat.send_chat_action = _action
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=int(uid), first_name="T", language_code="en"),
        effective_chat=chat, message=msg, callback_query=None)


@pytest.fixture
def config():
    for mod in ("bot.skills.telegram_handler", "bot.core.engine"):
        mc = patch(f"{mod}.CONFIG").start()
        mc.telegram.chat_id = OPERATOR
        mc.telegram.admin_ids = ""
        mc.telegram.live_trader_ids = ""
        mc.paper_auto_accept = False
        mc.per_user_live_enabled = False
        mc.is_live.return_value = False
        mc.llm.chat_streaming_enabled = False
    yield
    patch.stopall()


@pytest.mark.asyncio
async def test_the_thinking_phrase_is_in_the_users_language(tmp_path, config, monkeypatch):
    from bot.web import chat_quota
    monkeypatch.setattr(chat_quota, "quota_enabled", lambda: False)
    h = _handler(tmp_path)
    from bot.utils.i18n import set_user_lang
    set_user_lang(h.users, STRANGER, "zh")
    await h._handle_message(_update(STRANGER, "tell me a story about a patient trader"), None)
    thinking = h.sent[0]
    assert "<i>" in thinking and any("一" <= ch <= "鿿" for ch in thinking)


# ── the public scan gate ────────────────────────────────────────────────────

def _request(handler, body):
    async def _json():
        return body

    return SimpleNamespace(app={"tg_handler": handler, "engine": SimpleNamespace()}, json=_json)


def test_the_public_scan_gate_speaks_the_visitors_language():
    handler = SimpleNamespace()
    en = _run(ug._public_chat_turn(_request(handler, {"text": "scan BTC"})))
    zh = _run(ug._public_chat_turn(_request(handler, {"text": "scan BTC", "lang": "zh-TW"})))
    en_body = json.loads(en.text)
    zh_body = json.loads(zh.text)
    assert en_body["intent"] == zh_body["intent"] == "public_scan_gate"
    assert en_body["reply_html"] == t("chat_public_scan_gate", "en")
    assert zh_body["reply_html"] == t("chat_public_scan_gate", "zh")
    assert 'href="/dashboard"' in zh_body["reply_html"], "the sign-in link survives translation"
