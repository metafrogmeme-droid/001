"""The free-chat spend fence protects the door people actually use.

`bot/web/chat_quota.py` bounds the operator-funded free-chat model per user per
day. It was consulted by the web gateway and by nothing else, so an allowlisted
Telegram user — the surface most users are on — could spend the whole shared
daily budget from a chat window the fence never saw. The fence protected the
door nobody was walking through.

Telegram's free-text fallback now consumes from the SAME store with the SAME
exemptions, refunds when no model answered (the web already did), and records
which model answered on the assistant turn (the web already did; Telegram
passed return_meta=False and stored a reply nobody could attribute).

The harness is the one `test_free_text_obeys_the_role_gate.py` uses: a real
UserStore, a real IntentRouter, a handler built without __init__.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore
from bot.web import chat_quota

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


class _Registry:
    def get(self, name):
        return None

    async def dispatch(self, name, engine, **kwargs):
        return "dispatched"


class _Conversations:
    def __init__(self):
        self.rows = []

    def append(self, uid, role, content, metadata=None):
        self.rows.append((uid, role, content, metadata or {}))

    def get(self, *a, **kw):
        return []


def _handler(tmp_path, llm_answer):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    h.engine = _Engine()
    h.engine._user_store = h.users
    h.registry = _Registry()
    h._limiter = SimpleNamespace(allow=lambda uid: True)
    h.conversations = _Conversations()
    h.forwarder = SimpleNamespace(detect_group=lambda *a, **kw: None)
    h._pending_limit_input = {}
    from bot.nlp.intent_router import IntentRouter
    h.intent_router = IntentRouter()
    h.sent: list[str] = []
    h.asked: list[str] = []

    async def _send(update, text, **kwargs):
        h.sent.append(text)

    async def _llm_chat(question, **kw):
        h.asked.append(question)
        assert kw.get("return_meta") is True
        return llm_answer

    h._send = _send
    h._llm_chat = _llm_chat
    h._request_operator_admission = _noop_false
    h._send_photo = _noop_false
    h.users.authorize(OPERATOR, role="admin", by=OPERATOR)
    h.users.register(STRANGER, name="Walkin")
    h.users.authorize(STRANGER, role=SELF_ADMISSION_ROLE, by=SELF_ADMISSION_BY)
    return h


async def _noop_false(*a, **kw):
    return False


def _update(uid, text):
    msg = SimpleNamespace(text=text)

    async def _reply(*a, **kw):
        pass

    msg.reply_text = _reply
    chat = SimpleNamespace(id=int(uid), type="private", title="")

    async def _action(*a, **kw):
        pass

    chat.send_chat_action = _action
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=int(uid), first_name="T",
                                       language_code="en"),
        effective_chat=chat, message=msg, callback_query=None)


# A question no router rule matches, so it reaches the LLM fallback.
QUESTION = "tell me a story about a patient trader"


@pytest.fixture
def quota(tmp_path, monkeypatch):
    monkeypatch.setenv("FREE_CHAT_QUOTA_ENABLED", "1")
    monkeypatch.setenv("FREE_CHAT_DAILY_LIMIT", "1")
    monkeypatch.setattr(chat_quota, "_STORE_PATH", tmp_path / "quota.json")
    yield


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
    yield
    patch.stopall()


ANSWERED = ("Once upon a time…", {"provider": "grok", "model": "grok-4.3",
                                   "tools": [{"name": "get_portfolio", "ok": True,
                                              "ms": 4}]})
NO_MODEL = ("I'm having trouble thinking right now.", {})


@pytest.mark.asyncio
async def test_the_second_free_question_is_refused_before_any_model_call(
        tmp_path, quota, config):
    h = _handler(tmp_path, ANSWERED)
    await h._handle_message(_update(STRANGER, QUESTION), None)
    assert h.asked == [QUESTION]
    await h._handle_message(_update(STRANGER, QUESTION), None)
    assert h.asked == [QUESTION], "the capped user must not reach the model"
    assert "free AI questions" in h.sent[-1]
    assert "/scan" in h.sent[-1], "Telegram's wall points at commands, not plans"
    assert "See plans" not in h.sent[-1]


@pytest.mark.asyncio
async def test_the_wall_is_in_the_users_language(tmp_path, quota, config):
    h = _handler(tmp_path, ANSWERED)
    from bot.utils.i18n import set_user_lang
    set_user_lang(h.users, STRANGER, "zh")
    await h._handle_message(_update(STRANGER, QUESTION), None)
    await h._handle_message(_update(STRANGER, QUESTION), None)
    assert "免費" in h.sent[-1]


@pytest.mark.asyncio
async def test_the_operator_is_exempt(tmp_path, quota, config):
    h = _handler(tmp_path, ANSWERED)
    for _ in range(3):
        await h._handle_message(_update(OPERATOR, QUESTION), None)
    assert len(h.asked) == 3


@pytest.mark.asyncio
async def test_an_answer_no_model_produced_is_refunded(tmp_path, quota, config):
    h = _handler(tmp_path, NO_MODEL)
    await h._handle_message(_update(STRANGER, QUESTION), None)
    assert chat_quota.status(STRANGER, "basic")["used"] == 0
    # …so the next question is not the wall.
    await h._handle_message(_update(STRANGER, QUESTION), None)
    assert len(h.asked) == 2


@pytest.mark.asyncio
async def test_the_assistant_turn_records_which_model_and_tools(
        tmp_path, quota, config):
    h = _handler(tmp_path, ANSWERED)
    await h._handle_message(_update(STRANGER, QUESTION), None)
    uid, role, content, meta = h.conversations.rows[-1]
    assert (uid, role, content) == (STRANGER, "assistant", "Once upon a time…")
    assert meta["provider"] == "grok" and meta["model"] == "grok-4.3"
    assert meta["tools"] == ["get_portfolio"]


@pytest.mark.asyncio
async def test_no_model_is_recorded_as_no_model(tmp_path, quota, config):
    h = _handler(tmp_path, NO_MODEL)
    await h._handle_message(_update(STRANGER, QUESTION), None)
    meta = h.conversations.rows[-1][3]
    assert meta == {"answered_by": "none"}


@pytest.mark.asyncio
async def test_a_dormant_quota_meters_nobody(tmp_path, monkeypatch, config):
    monkeypatch.setenv("FREE_CHAT_QUOTA_ENABLED", "0")
    monkeypatch.setattr(chat_quota, "_STORE_PATH", tmp_path / "quota.json")
    h = _handler(tmp_path, ANSWERED)
    for _ in range(3):
        await h._handle_message(_update(STRANGER, QUESTION), None)
    assert len(h.asked) == 3


@pytest.mark.asyncio
async def test_an_unreadable_tier_is_not_metered_as_basic(tmp_path, quota, config):
    """Unreadable is not the cheapest tier — the web path's own lesson."""
    h = _handler(tmp_path, ANSWERED)

    def _boom(uid):
        raise RuntimeError("store unreadable")

    h.users.get_tier = _boom
    for _ in range(3):
        await h._handle_message(_update(STRANGER, QUESTION), None)
    assert len(h.asked) == 3


def test_both_surfaces_render_the_same_wall_from_one_place():
    q = {"allowed": False, "limit": 5, "reset_in_seconds": 3 * 3600}
    tg = chat_quota.exhausted_notice(q, "en", surface="telegram")
    web = chat_quota.exhausted_notice(q, "en", surface="web")
    assert "5 free" in tg and "5 free" in web
    assert "about 3 hours" in tg and "about 3 hours" in web
    assert "See plans" in web and "See plans" not in tg
    zh = chat_quota.exhausted_notice(q, "zh", surface="web")
    assert "查看方案" in zh and "3 小時" in zh


def test_the_web_wall_language_maps_chat_codes_to_the_dictionary():
    from bot.web.user_gateway import _ui_lang
    assert _ui_lang("") == "en"
    assert _ui_lang("es") == "en"
    assert _ui_lang("zh") == "zh"
    assert _ui_lang("zh-TW") == "zh"
    assert _ui_lang("ZH_HK") == "zh"
