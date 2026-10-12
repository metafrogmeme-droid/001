"""A visitor who asks for levels in another language is not handed a contract
that invites them.

The public chat has no live feed, and two layers keep a scan-shaped answer
off it: the gate (`needs_live_market_data`, the router's patterns) refuses
before any model runs, and the public contracts tell the model to print no
levels for what the gate misses. Both read ENGLISH patterns. A Spanish
"análisis completo de BTC con entrada, stop y objetivo" passed the gate,
detected as the `standard` fallthrough, and `reply_contract` returned the
members' unread contract BEFORE it looked at `public` — whose own words are
"a request for a full analysis gets sections".

The public contract is chosen first now, and a public turn whose shape was
not read gets one that asks for no entry, stop, target or size, whatever the
question turns out to be. Driven through the real gateway turn and the real
`_llm_chat`, reading the prompt the model is handed.
"""
from __future__ import annotations

import asyncio
import functools
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.chat_runtime import (
    _PUBLIC_REPLY_CONTRACTS,
    _PUBLIC_UNREAD_CONTRACT,
    _REPLY_CONTRACTS,
    _UNREAD_CONTRACT,
    reply_contract,
)
from bot.skills.telegram_handler import TelegramHandler as H
from bot.web import user_gateway as ug

#: The sentence of the members' unread contract that invites sections.
INVITES_SECTIONS = "a request for a full analysis gets sections"
#: The public no-levels rule, in the words a reader of the prompt sees.
NO_LEVELS = "print no entry, stop, target, size, price level, score or scan headings"

#: "A full analysis of BTC with entry, stop and target", in the languages
#: the site ships in. The German and French cognates ("Analyse", "analyse")
#: happen to match the router's English word, so the gate refuses those
#: before any model runs; a second phrasing in each reaches the model, which
#: is the path this file is about.
FULL_ANALYSIS = [
    ("es", "análisis completo de BTC con entrada, stop y objetivo"),
    ("pt", "análise completa do BTC com entrada, stop e alvo"),
    ("de", "vollständige Analyse von BTC mit Einstieg, Stop und Ziel"),
    ("de", "komplette Auswertung von BTC mit Einstieg, Stop und Ziel"),
    ("ja", "BTCの完全な分析をエントリー、ストップ、ターゲット付きで"),
    ("ko", "BTC 전체 분석을 진입가, 손절가, 목표가와 함께 알려줘"),
    ("fr", "analyse complète du BTC avec entrée, stop et objectif"),
    ("fr", "étude complète du BTC avec entrée, stop et objectif"),
]
#: The phrasings the gate does not catch, which reach the model.
REACH_THE_MODEL = {("es", FULL_ANALYSIS[0][1]), ("pt", FULL_ANALYSIS[1][1]),
                   ("de", FULL_ANALYSIS[3][1]), ("ja", FULL_ANALYSIS[4][1]),
                   ("ko", FULL_ANALYSIS[5][1]), ("fr", FULL_ANALYSIS[7][1])}


class _Conversations:
    def get_recent_as_llm_messages(self, *a, **k):
        return []

    def append(self, *a, **k):
        pass


@pytest.fixture
def prompts(monkeypatch):
    """The system prompts `_llm_chat` hands a model, driven through the
    gateway's public turn."""
    seen: list[str] = []

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        seen.append(system_prompt)
        return "an answer"

    BYOK.reset()
    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "resolve_profile_note", lambda note, uid: "")
    monkeypatch.setattr(th_mod, "resolve_tier_config", lambda *a, **kw: LLMConfig(
        provider=LLMProvider.OPENAI, api_key="k", model="test-model"))
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="")))
    yield seen
    BYOK.reset()


def _public_turn(text: str, lang: str) -> dict:
    stub = SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_Conversations(),
        _PUBLIC_CHAT_SYSTEM_PROMPT=H._PUBLIC_CHAT_SYSTEM_PROMPT,
        _is_admin=lambda u: False,
        _note_chat_llm_failure=lambda reason="": None,
    )
    handler = SimpleNamespace(_llm_chat=functools.partial(H._llm_chat, stub))
    body = {"text": text, **({"lang": lang} if lang else {})}

    async def _json():
        return body

    req = SimpleNamespace(app={"tg_handler": handler, "engine": SimpleNamespace()},
                          json=_json, headers={}, remote="127.0.0.1")
    return json.loads(asyncio.run(ug._public_chat_turn(req)).text)


@pytest.mark.parametrize("lang, text", FULL_ANALYSIS)
def test_a_full_analysis_asked_in_another_language_is_never_invited_to_levels(
        prompts, lang, text):
    """Either the gate refuses it before a model runs, or the model is told
    to print no levels. Never the invitation."""
    out = _public_turn(text, lang)
    if (lang, text) in REACH_THE_MODEL:
        # The premise: the gate reads English, so the model runs.
        assert out["intent"] == "chat", out
    if out["intent"] == "public_scan_gate":
        assert prompts == []
        return
    assert len(prompts) == 1
    assert NO_LEVELS in prompts[0]
    assert INVITES_SECTIONS not in prompts[0]


def test_the_english_control_is_refused_by_the_gate_before_any_model(prompts):
    out = _public_turn("full analysis of BTC with entry, stop and target", "")
    assert out["intent"] == "public_scan_gate"
    assert prompts == []


def test_an_english_general_question_keeps_its_measured_shape(prompts):
    """The other arm: an English fallthrough IS a reading, so the public
    page keeps the standard contract there, not the unread one."""
    out = _public_turn("what does a stop loss protect against", "")
    assert out["intent"] == "chat"
    assert _REPLY_CONTRACTS["standard"] in prompts[0]
    assert NO_LEVELS not in prompts[0]


def test_a_signed_in_turn_in_another_language_keeps_the_members_unread_contract():
    """Only the public page loses the invitation: a member's turn has a
    ticker block and tools to source a scan from."""
    assert _UNREAD_CONTRACT in reply_contract("standard", public=False, reply_lang="es")
    assert _PUBLIC_UNREAD_CONTRACT in reply_contract("standard", public=True, reply_lang="es")
    assert INVITES_SECTIONS in _UNREAD_CONTRACT
    assert NO_LEVELS in _PUBLIC_UNREAD_CONTRACT


@pytest.mark.parametrize("mode", sorted(_PUBLIC_REPLY_CONTRACTS))
def test_the_public_overrides_hold_in_every_language(mode):
    for lang in ("", "es", "ja"):
        assert _PUBLIC_REPLY_CONTRACTS[mode] in reply_contract(mode, public=True,
                                                               reply_lang=lang)


def test_a_positive_match_is_still_trusted_on_the_public_page():
    """"long or short?" matched a pattern whatever language the visitor
    reads; a match is evidence, so the quick contract stands."""
    assert _REPLY_CONTRACTS["quick"] in reply_contract("quick", public=True, reply_lang="es")
