"""`standard` is the fallthrough, and a fallthrough in a language the detector
cannot read is not a reading.

`_detect_reply_mode` tries five regex sets in order and returns `standard` when
none of them fires. No pattern produces that word — it is only ever what is
left when nothing matched, which the first test below proves by driving every
branch rather than asserting it. So one value carries two different facts:

    in English       the five had a fair chance and none matched. That IS a
                     reading: this is a general question.
    in any other     the patterns are English. They could not have matched
    language         whatever was typed, and nothing was measured at all.

The contract selected off the second one asserted a specific shape — "answer in
3-8 lines, add a closing what-to-watch line only if…" — on thirteen of the
fourteen languages this product ships in. That is the defect this module fixed
loudly two changes ago, appearing quietly: absent rendered as a measurement.

The fix is not to translate five regex sets into fourteen languages. That would
be a heuristic dressed as a verdict — unverifiable prose in scripts nobody here
can review, carrying the same word-boundary problem the FAQ matcher needed
solving for. It is to say so, and let the model, which does read the language,
choose the shape. `_UNREAD_CONTRACT` prescribes no length; it keeps the
discipline (source every number, earn the closing line) and hands back the
judgement that was never actually made.

A POSITIVE MATCH IS STILL TRUSTED IN EVERY LANGUAGE. "scan BTC" typed by a
Spanish reader really did match `_SCAN_PATTERNS`, and a match is evidence
wherever it happens. Only the fallthrough is empty.
"""
from __future__ import annotations

import pytest

from bot.nlp.intent_router import detect_reply_mode
from bot.skills.chat_runtime import (
    _REPLY_CONTRACTS,
    _UNREAD_CONTRACT,
    DEFAULT_REPLY_MODE,
    reply_contract,
)
from bot.utils.i18n import chat_language_name

#: One message per mode, chosen to fire that mode's patterns and no earlier
#: one. `standard` is deliberately absent: it has no pattern to fire.
FIRES_A_PATTERN = {
    "quick": "long or short?",
    "bot": "grid bot",
    "execution": "where do i enter",
    "full_scan": "scan btc",
    "beginner": "explain what a liquidity sweep is",
}


def _is_unread(text: str) -> bool:
    return "NOT classified" in text


# ── the premise, driven ─────────────────────────────────────────────────────

def test_every_mode_except_standard_is_produced_by_a_pattern():
    """Half of the claim: the five modes are reachable, so a fallthrough is
    genuinely the absence of all five rather than an unreached branch."""
    for mode, text in FIRES_A_PATTERN.items():
        assert detect_reply_mode(text) == mode, text
    assert set(FIRES_A_PATTERN) | {DEFAULT_REPLY_MODE} == set(_REPLY_CONTRACTS)


def test_standard_is_only_ever_the_fallthrough():
    """The other half, and the one the whole file rests on. If any pattern
    could return `standard`, then `mode == "standard"` would sometimes be a
    positive match and treating it as unread would be wrong."""
    import inspect

    from bot.nlp import intent_router
    from tests.source_scan import code_only

    body = code_only(inspect.getsource(intent_router._detect_reply_mode))
    returns = [ln.strip() for ln in body.splitlines() if "return" in ln]
    assert returns, "the function stopped returning anything"
    # Every `return "standard"` must be the LAST statement — i.e. guarded by no
    # `if` above it in the same breath. Driving it is stronger: nothing that
    # matches a pattern may come back as the default.
    for text in FIRES_A_PATTERN.values():
        assert detect_reply_mode(text) != DEFAULT_REPLY_MODE, text
    assert returns[-1].startswith("return"), returns[-1]
    assert DEFAULT_REPLY_MODE in returns[-1], (
        f"the fallthrough no longer returns {DEFAULT_REPLY_MODE!r}: {returns[-1]}")


def test_the_patterns_really_are_english_only():
    """The reason the fallthrough is empty elsewhere. These are real questions
    in each language, each the translation of a phrase that DOES fire a pattern
    in English — and not one of them is read."""
    translated = {
        "es": "¿Cómo funciona el apalancamiento?",     # beginner in English
        "fr": "Comment fonctionne l’effet de levier ?",
        "de": "Wie funktioniert Hebel?",
        "ja": "レバレッジはどう機能しますか？",
        "zh": "槓桿是如何運作的？",
        "ru": "Как работает кредитное плечо?",
        "ar": "كيف تعمل الرافعة المالية؟",
        "hi": "लीवरेज कैसे काम करता है?",
        "ko": "레버리지는 어떻게 작동하나요?",
        "pt": "Como funciona a alavancagem?",
    }
    assert detect_reply_mode("How does leverage work?") == "beginner"
    for lang, text in translated.items():
        assert detect_reply_mode(text) == DEFAULT_REPLY_MODE, (
            f"{lang}: unexpectedly matched a pattern — good, but the fixture "
            "no longer demonstrates what it is here for")


# ── the contract that follows from it ───────────────────────────────────────

@pytest.mark.parametrize("lang", ["es", "fr", "de", "ja", "zh", "ru", "ar",
                                  "hi", "ko", "pt", "it", "nl", "tr", "pl"])
def test_a_fallthrough_in_another_language_prescribes_no_shape(lang):
    assert _is_unread(reply_contract("", reply_lang=lang)), lang
    assert _is_unread(reply_contract("standard", reply_lang=lang)), lang


@pytest.mark.parametrize("lang", ["", "en", "EN", "en-US"])
def test_a_fallthrough_in_english_is_still_a_reading(lang):
    assert not _is_unread(reply_contract("", reply_lang=lang)), lang
    assert _REPLY_CONTRACTS[DEFAULT_REPLY_MODE] in reply_contract(
        "", reply_lang=lang)


@pytest.mark.parametrize("lang", ["sw", "xx", "zzz"])
def test_a_language_the_model_is_not_told_to_use_is_an_english_turn(lang):
    """`chat_language_name` answers '' for a code it does not know, so no
    LANGUAGE directive is issued and the reply comes back in English — which
    is exactly when the English detector had a fair chance. The predicate is
    about what the MODEL will write, not about the reader's locale."""
    assert not chat_language_name(lang)
    assert not _is_unread(reply_contract("", reply_lang=lang)), lang


@pytest.mark.parametrize("mode", sorted(FIRES_A_PATTERN))
@pytest.mark.parametrize("lang", ["es", "ja", "ar"])
def test_a_positive_match_is_trusted_in_every_language(mode, lang):
    """A match is evidence wherever it happens. "scan BTC" typed by a Spanish
    reader really did match; only the fallthrough is empty."""
    out = reply_contract(mode, reply_lang=lang)
    assert not _is_unread(out), f"{mode}/{lang} was thrown away"
    assert _REPLY_CONTRACTS[mode] in out


def test_the_unread_contract_prescribes_no_length():
    """The point of it. Every other contract bounds the answer; this one
    cannot, because the bound would be the invented part."""
    import re
    assert not re.search(r"\b(under \d+ words|\d+-\d+ lines)\b",
                         _UNREAD_CONTRACT, re.IGNORECASE)


def test_the_unread_contract_keeps_the_discipline_it_can_keep():
    """Not knowing the SHAPE is not licence to invent CONTENT."""
    low = _UNREAD_CONTRACT.lower()
    assert "leave out" in low and "estimate" in low
    assert "what to watch" in low


def test_it_says_that_nothing_was_read_rather_than_guessing():
    assert "NOT classified" in _UNREAD_CONTRACT
    assert "fell through" in _UNREAD_CONTRACT


def test_one_turn_still_carries_exactly_one_shape():
    for mode in list(_REPLY_CONTRACTS) + ["", "nonsense"]:
        for lang in ("", "es", "ja"):
            out = reply_contract(mode, reply_lang=lang)
            assert out.count("HOW LONG AND WHAT SHAPE") == 1, (mode, lang)
            assert out.count("THIS TURN:") == 1, (mode, lang)


def test_the_public_override_still_wins_where_it_applies():
    """`full_scan` and `execution` on the public surface must keep their
    no-feed refusal whatever the language — the override is about what can be
    SOURCED, which no amount of language knowledge changes."""
    from bot.skills.chat_runtime import _PUBLIC_REPLY_CONTRACTS
    for mode in _PUBLIC_REPLY_CONTRACTS:
        for lang in ("", "es", "ja"):
            out = reply_contract(mode, public=True, reply_lang=lang)
            assert _PUBLIC_REPLY_CONTRACTS[mode] in out, (mode, lang)


# ── the seam, driven end to end ─────────────────────────────────────────────

def test_the_prompt_a_spanish_reader_gets_says_no_shape_was_read():
    """Through `_llm_chat`, because the argument that carries the language to
    `reply_contract` is one line at one call site — and `_ui` was the wrong
    reading to pass there. `ui_lang('pl')` is 'en' (there is no Polish
    dictionary) while the model IS told to answer in Polish, so `_ui` would
    have handed a Polish reader the English fallthrough as a measurement."""
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace

    import bot.skills.telegram_handler as th_mod
    from bot.core.cost import CostTracker
    from bot.llm.provider import BYOK, LLMConfig, LLMProvider
    from bot.skills.chat_runtime import _CHAT_NO_TOOLS_RULE
    from bot.skills.telegram_handler import TelegramHandler as H
    from bot.utils.i18n import ui_lang

    assert ui_lang("pl") == "en" and chat_language_name("pl") == "Polish", (
        "the Polish divergence this test rests on is gone")

    seen = []

    async def _complete(client, cfg, sys_p, q, **kw):
        seen.append(sys_p)
        return "answer"

    class _C:
        def get_recent_as_llm_messages(self, *a, **k):
            return []

        def append(self, *a, **k):
            pass

    stub = SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_C(),
        _build_chat_system_prompt=lambda uid, user_name="": _CHAT_NO_TOOLS_RULE,
        _PUBLIC_CHAT_SYSTEM_PROMPT=H._PUBLIC_CHAT_SYSTEM_PROMPT,
        _is_admin=lambda u: False,
        _note_chat_llm_failure=lambda reason="": None,
    )

    saved = (th_mod.CONFIG, th_mod.resolve_tier_config,
             th_mod.create_llm_client, th_mod.llm_complete,
             th_mod.resolve_profile_note)
    BYOK.reset()
    try:
        th_mod.llm_complete = _complete
        th_mod.create_llm_client = lambda cfg: object()
        th_mod.resolve_profile_note = lambda note, uid: ""
        th_mod.resolve_tier_config = lambda *a, **kw: LLMConfig(
            provider=LLMProvider.GROK, api_key="k", model="grok-4.3")
        th_mod.CONFIG = replace(
            th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key=""))
        for lang, unread in (("es", True), ("pl", True), ("", False)):
            seen.clear()
            asyncio.run(H._llm_chat(stub, "hola, una pregunta", user_id="u1",
                                    reply_lang=lang))
            assert len(seen) == 1, lang
            assert _is_unread(seen[0]) is unread, (
                f"{lang or 'en'}: expected unread={unread}")
    finally:
        (th_mod.CONFIG, th_mod.resolve_tier_config, th_mod.create_llm_client,
         th_mod.llm_complete, th_mod.resolve_profile_note) = saved
        BYOK.reset()
