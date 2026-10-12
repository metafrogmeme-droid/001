"""When no chat model answers, the reply names what still works on the
reader's own surface, and claims no check nobody made.

The budget, deadline and empty-reply messages ended with "use a specific
command like /scan or /positions", and the empty one with "Your key and
endpoint are fine. Try /llmstatus for the last error". Those reached the web
chat, which runs no slash command, the public chat, whose visitors have no
account, and members who gave no key; /llmstatus is the operator's.

Each message now ends with its surface's door (`LINK_DOOR[surface]
["fallback"]`, through `fallback_door`): commands on Telegram, sentences the
web router answers on the web, signing in for a visitor. What every provider
answering says about the keys goes to the operator, on Telegram, where
/llmstatus opens.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.nlp.intent_router import IntentRouter
from bot.nlp.web_reads import WEB_READS
from bot.skills.chat_runtime import ACT_INTENTS, HALT_INTENTS, LINK_DOOR, fallback_door
from bot.skills.telegram_handler import TelegramHandler as H
from bot.utils.i18n import SUPPORTED_LANGS, t
from tests.test_alerts_name_real_commands import registered_commands

#: Who reads the failure: (surface, public, is_admin).
READERS = {
    "telegram member": ("telegram", False, False),
    "telegram operator": ("telegram", False, True),
    "web member": ("web", False, False),
    "web operator": ("web", False, True),
    "public visitor": ("telegram", True, False),
}
FAILURES = ("budget", "share", "deadline", "empty")


def _slash_commands(text: str) -> list[str]:
    """Slash commands a person would read, markup stripped first: `</b>`
    matches a naive pattern."""
    visible = re.sub(r"</?[a-z]+[^>]*>", " ", text)
    return re.findall(r"(?<![\w/<])/([a-z][a-z0-9_]*)", visible)


class _Conversations:
    def get_recent_as_llm_messages(self, *a, **k):
        return []

    def append(self, *a, **k):
        pass


def _stub(cost):
    return SimpleNamespace(
        engine=SimpleNamespace(cost=cost, analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda uid, user_name="", surface="telegram": "system prompt",
        _PUBLIC_CHAT_SYSTEM_PROMPT=H._PUBLIC_CHAT_SYSTEM_PROMPT,
        _is_admin=lambda u: False,
        _note_chat_llm_failure=lambda reason="": None,
    )


@pytest.fixture
def fail(monkeypatch):
    """Drive one failure for one reader and return what they are told."""
    BYOK.reset()
    monkeypatch.setattr(th_mod, "resolve_tier_config", lambda *a, **kw: LLMConfig(
        provider=LLMProvider.OPENAI, api_key="k", model="test-model"))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY",
                "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "resolve_profile_note", lambda note, uid: "")
    monkeypatch.setattr(th_mod, "CONFIG", replace(th_mod.CONFIG, llm=replace(
        th_mod.CONFIG.llm, api_key="", daily_budget_usd=1.0, chat_budget_share=0.5,
        daily_call_limit=500)))

    def _drive(failure: str, reader: str, lang: str = "") -> str:
        surface, public, is_admin = READERS[reader]
        cost = CostTracker()

        async def _empty(*a, **kw):
            return "   "

        monkeypatch.setattr(th_mod, "llm_complete", _empty)
        monkeypatch.setattr(th_mod, "CHAT_MIN_ATTEMPT_SEC", 0.0)
        if failure == "budget":
            cost.snapshot = lambda: SimpleNamespace(llm_calls=10_000, llm_cost_usd=10_000.0)
        elif failure == "share":
            cost.record_llm(model="test-model", prompt_tokens=0, completion_tokens=0,
                            category="chat")
            snap = cost.snapshot()
            snap.cost_by_category["chat"] = 0.6
            snap.llm_cost_usd = 0.6
            cost.snapshot = lambda: snap
        elif failure == "deadline":
            # No attempt fits in what is left of the deadline.
            monkeypatch.setattr(th_mod, "CHAT_MIN_ATTEMPT_SEC", 10_000.0)
        out = asyncio.run(H._llm_chat(_stub(cost), "hello there, a question",
                                      user_id="" if public else "u1", public=public,
                                      is_admin=is_admin, surface=surface,
                                      reply_lang=lang))
        return out

    yield _drive
    BYOK.reset()


@pytest.mark.parametrize("failure", FAILURES)
def test_the_premise_each_failure_is_the_one_driven(fail, failure):
    key = {"budget": "chat_budget_exhausted", "share": "chat_share_exhausted",
           "deadline": "chat_deadline", "empty": "chat_empty_completions"}[failure]
    assert fail(failure, "telegram member").startswith(t(key, "en"))


@pytest.mark.parametrize("failure", FAILURES)
def test_telegram_names_commands_that_are_registered(fail, failure):
    out = fail(failure, "telegram member")
    named = _slash_commands(out)
    assert {"scan", "positions"} <= set(named), out
    assert not set(named) - registered_commands(), named
    assert "llmstatus" not in named, "an admin command, to a member"


@pytest.mark.parametrize("reader", ["web member", "web operator", "public visitor"])
@pytest.mark.parametrize("failure", FAILURES)
def test_the_web_and_the_public_page_are_told_no_slash_command(fail, failure, reader):
    out = fail(failure, reader)
    assert _slash_commands(out) == [], out


@pytest.mark.parametrize("failure", FAILURES)
def test_the_web_names_the_sentences_its_router_answers(fail, failure):
    assert fail(failure, "web member").endswith(t("chat_door_words", "en"))


@pytest.mark.parametrize("failure", FAILURES)
def test_a_visitor_is_told_signing_in(fail, failure):
    assert fail(failure, "public visitor").endswith(t("chat_door_public", "en"))


@pytest.mark.parametrize("reader", sorted(READERS))
def test_no_reader_is_told_a_key_they_never_gave_is_fine(fail, reader):
    assert "key and endpoint are fine" not in fail("empty", reader)


def test_the_operator_on_telegram_is_told_what_the_answers_say_about_the_keys(fail):
    out = fail("empty", "telegram operator")
    assert "keys and endpoints are not the fault" in out
    assert "llmstatus" in _slash_commands(out)
    # The other arm: the same reply to a member carries neither.
    member = fail("empty", "telegram member")
    assert "not the fault" not in member and "llmstatus" not in _slash_commands(member)


def test_the_operator_note_is_for_the_empty_reply_only(fail):
    assert "not the fault" not in fail("deadline", "telegram operator")


def test_the_door_speaks_the_readers_language(fail):
    es = fail("budget", "web member", lang="es")
    assert es == t("chat_budget_exhausted", "es") + " " + t("chat_door_words", "es")


# ── each door the table names opens ─────────────────────────────────────────

def test_every_web_example_sentence_reaches_a_read_the_web_dispatches():
    """A door named is a door claimed. The examples stay English in every
    language because the router reads English."""
    router = IntentRouter()
    for lang in sorted(SUPPORTED_LANGS):
        examples = [e.strip() for e in re.findall(r"[“«„「]([^”»“」]+)[”»“」]",
                                                  t("chat_door_words", lang))]
        assert sorted(examples) == ["my positions", "scan BTC"], (lang, examples)
        for ex in examples:
            intent = router.classify_rules(ex)
            assert intent.matched and intent.confidence >= 0.8, ex
            assert intent.skill not in WEB_READS and intent.skill not in HALT_INTENTS
            assert intent.skill not in ACT_INTENTS


def test_every_surface_has_a_door_in_every_language():
    for surface, door in LINK_DOOR.items():
        for lang in sorted(SUPPORTED_LANGS):
            assert t(door["fallback"], lang).strip(), (surface, lang)
            if door["diagnose"]:
                assert t(door["diagnose"], lang).strip(), (surface, lang)


def test_a_transport_the_table_does_not_name_reads_telegrams_door():
    """The api bridge is the operator's, as `link_door` has always said."""
    assert fallback_door("api") == fallback_door("telegram")
    assert fallback_door("web", operator=True) == fallback_door("web")
