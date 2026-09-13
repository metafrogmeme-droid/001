"""A precision rule measured in a unit half the supported languages do not have.

`faq_answer` decides whether a message IS a landing-page starter question or
merely CONTAINS one, and the rule is a word count: the trigger's words, in
order, with at most `_slack_for(trigger_words)` other words in the whole
message. Its own docstring records what that rule is for — a flat allowance let
"who are you bullish on" through on the 3-word trigger "who are you", and the
scaled one closes it.

It closes it in English. `_norm` was `re.sub(r"[^a-z0-9'\\s]", " ", t)`, which
replaces everything it does not name with a space — that is every script this
product ships in except Latin. So a Japanese message arrived at the matcher
holding only whatever English was inside it:

    "who are you 今どのコインに強気ですか"   ->   "who are you"

and the matcher, measuring THAT, concluded the message was the question and
returned the about-page brochure. Six of the fourteen supported languages were
erased outright (ja, zh, ru, ar, hi, ko); the exact miss `_slack_for` exists to
prevent, reproduced in every one of them.

Accented Latin failed the other way from the same character class: Spanish
`qué` became `qu ` and `cómo` became `c mo`, one word becoming two, inflating
the count and pushing a genuine starter question out of range of its own
answer. One expression, both failure directions.

And the count itself needed fixing even with the characters kept, because in
Chinese and Japanese a whitespace token is a clause, not a word. `_norm` and
`_slack_for` have to agree about what a word is or the subtraction between them
is arithmetic on two different units.

THE PROPERTY THAT MATTERS MOST HERE IS THE NEGATIVE ONE: no English outcome
changes. For ASCII input the old and new normalisers are identical, and the
first section below is the whole of the existing behaviour, pinned.
"""
from __future__ import annotations

import pytest

from bot.core import faq_kb
from bot.core.faq_kb import _length_in_words, _norm, faq_answer, public_fallback
from bot.utils.i18n import SUPPORTED_LANGS

# The five landing-page starter questions, verbatim (app/public/js/chat.js).
STARTERS = (
    "What is RUNECLAW?",
    "How does it manage risk?",
    "What is a liquidity sweep?",
    "How does leverage work?",
    "Which exchanges are supported?",
)

#: The four misses `faq_answer`'s own docstring measured against the live
#: triggers when the matcher was substring-anywhere. Each must still defer.
ENGLISH_FREE_FORM = (
    "what are you seeing on BTC right now",
    "who are you bullish on",
    "what are you doing with my money",
    "is my leverage working against me right now",
)


# ── the negative property: English is untouched ─────────────────────────────

@pytest.mark.parametrize("q", STARTERS)
def test_every_starter_question_still_answers(q):
    assert faq_answer(q), q


@pytest.mark.parametrize("q", ENGLISH_FREE_FORM)
def test_every_english_free_form_question_still_defers(q):
    assert faq_answer(q) is None, q


@pytest.mark.parametrize("q", [
    "what is runeclaw", "WHICH EXCHANGES ARE SUPPORTED",
    "how does leverage work???", "what's runeclaw", "whats runeclaw",
])
def test_case_and_punctuation_insensitivity_survives(q):
    assert faq_answer(q), q


@pytest.mark.parametrize("q", [
    "should I use leverage on SOL right now?", "is BTC bullish today?",
    "hello", "", "gm",
])
def test_the_documented_fall_throughs_still_fall_through(q):
    assert faq_answer(q) is None, q


def test_ascii_normalises_exactly_as_the_old_expression_did():
    """The old class, run here, so the claim is measured rather than asserted.
    Any ASCII string must come out of `_norm` byte-identical to what
    `[^a-z0-9'\\s]` produced — that is what makes the section above safe."""
    import re
    def _old(text: str) -> str:
        t = (text or "").lower().replace("’", "'")
        t = re.sub(r"[^a-z0-9'\s]", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    corpus = list(STARTERS) + list(ENGLISH_FREE_FORM) + [
        "", "gm", "hello!!", "what's runeclaw", "BTC/USDT +2.5% -- scan?",
        "  spaced   out  ", "a1 b2 c3", "don't  do   that", "@#$%^&*()",
    ]
    for trig_list in (i["triggers"] for i in faq_kb._FAQ):
        corpus.extend(trig_list)
    for s in corpus:
        assert _norm(s) == _old(s), repr(s)


# ── the scripts the old class erased ────────────────────────────────────────

#: A genuine question in each script, carrying an English trigger's words. Every
#: one of these returned the about-page brochure before the fix.
CARRIES_A_TRIGGER = {
    "ja": "who are you 今どのコインに強気ですか、理由も教えてください",
    "zh": "我很好奇 what are you 打算怎麼處理我帳戶裡的資金",
    "ru": "кто вы what are you такие и чем вы вообще занимаетесь",
    "ar": "من أنتم what are you وماذا تفعلون بأموالي بالضبط",
    "hi": "आप कौन हैं what are you और मेरे पैसे का क्या करते हैं",
    "ko": "누구세요 what are you 제 자금으로 무엇을 하나요",
}


@pytest.mark.parametrize("lang", sorted(CARRIES_A_TRIGGER))
def test_a_real_question_in_another_script_is_not_the_starter_question(lang):
    assert faq_answer(CARRIES_A_TRIGGER[lang]) is None, (
        f"{lang}: a genuine question got the canned about-page answer")


@pytest.mark.parametrize("lang", sorted(CARRIES_A_TRIGGER))
def test_normalising_keeps_the_script_it_was_written_in(lang):
    """The root cause, pinned separately from its consequence: the message must
    still BE there after normalisation. It used to come out as the bare English
    fragment, and everything downstream was reasoning about that fragment."""
    raw = CARRIES_A_TRIGGER[lang]
    normalised = _norm(raw)
    assert normalised not in ("what are you", "who are you"), (
        f"{lang}: normalisation erased the message down to its English words")
    assert _length_in_words(normalised) > 6, (
        f"{lang}: {normalised!r} measured as {_length_in_words(normalised)} words")


#: The cases where `_length_in_words` is the ONLY thing standing between the
#: visitor and the brochure. Every fixture above carries a second whitespace
#: token (a comma splits the clause, or the English sits mid-sentence), so the
#: Unicode-aware `_norm` alone already pushes them out of range — a mutation
#: reverting the measure to `len(q.split())` survived the whole corpus above.
#: Here the trigger is followed by ONE unspaced token and nothing else: four
#: whitespace tokens minus a three-word trigger is one, inside the slack.
ONE_TOKEN_AFTER_THE_TRIGGER = {
    "ja": "who are you 今どのコインに強気ですか",
    "zh": "what are you 打算怎麼處理我帳戶裡的資金",
    "th": "what are you คุณกำลังทำอะไรกับเงินของฉัน",
}


@pytest.mark.parametrize("lang", sorted(ONE_TOKEN_AFTER_THE_TRIGGER))
def test_one_unspaced_token_is_not_one_word(lang):
    text = ONE_TOKEN_AFTER_THE_TRIGGER[lang]
    assert len(_norm(text).split()) == 4, (
        "the fixture must be the tight case, or it proves nothing")
    assert faq_answer(text) is None, (
        f"{lang}: a whole clause counted as one word and let the brochure through")


def test_combining_marks_survive_normalisation():
    """`\\w` is `str.isalnum()`, and a Devanagari matra or an Arabic diacritic is
    category Mn — not alphanumeric alone. Keeping only `\\w` split `क्या` in two
    and inflated the Hindi count: the safe direction, and still wrong."""
    assert "क्या" in _norm("मेरे पैसे का क्या करते हैं")
    assert _norm("منصة") == "منصة"


# ── the measure agrees with itself about what a word is ─────────────────────

@pytest.mark.parametrize("text,expected,why", [
    ("what is runeclaw", 3, "spaced script: one token, one word"),
    ("", 0, "empty"),
    ("今どのコインに強気ですか", 12, "unspaced: one token, twelve words"),
    ("who are you 今どのコイン", 9, "mixed: 3 spaced words + 6 dense characters"),
    ("runeclaw是什麼", 4, "one token, both kinds: 3 dense + 1 for the rest"),
    ("누구세요 무엇을 하나요", 3, "hangul is spaced, so it counts normally"),
])
def test_the_word_measure_is_script_aware(text, expected, why):
    assert _length_in_words(text) == expected, why


def test_a_whitespace_token_count_would_have_called_a_whole_sentence_one_word():
    """The arithmetic the fix replaces, shown rather than described.

    The comma-free sentence is the honest demonstration: `_norm` turns the
    ideographic comma into a space like any other punctuation, so a longer
    Japanese sentence splits into a couple of tokens rather than exactly one —
    which is still a count of clauses, not of words, and still far below what
    `_slack_for` is subtracting from."""
    one_clause = "今どのコインに強気ですか"
    assert len(_norm(one_clause).split()) == 1
    assert _length_in_words(_norm(one_clause)) == 12

    with_comma = "今どのコインに強気ですか、理由も教えてください"
    assert len(_norm(with_comma).split()) == 2, "two clauses, not two words"
    assert _length_in_words(_norm(with_comma)) > 15


# ── the visitor's half of the no-model branch ───────────────────────────────

def test_the_public_fallback_exists_in_every_supported_language():
    from bot.utils.i18n import t
    for code in SUPPORTED_LANGS:
        assert t("chat_public_fallback", code).strip(), code


def test_every_translation_is_actually_translated():
    english = public_fallback("en")
    for code in SUPPORTED_LANGS:
        if code == "en":
            continue
        assert public_fallback(code) != english, f"{code} still reads English"


def test_an_unknown_language_falls_back_to_english_not_to_nothing():
    assert public_fallback("sw") == public_fallback("en")
    assert public_fallback("") == public_fallback("en")


def test_the_visitor_is_told_the_model_is_unreachable():
    """The second half of the same asymmetry. Its admin sibling says the model
    is not connected; this one opened "I'm RUNECLAW, the AI trading agent. I can
    walk you through the essentials" and mentioned nothing — a degraded state
    presented as the normal offering."""
    fb = public_fallback("en").lower()
    assert "reachable" in fb or "unavailable" in fb
    assert not fb.startswith("i'm runeclaw, the ai trading agent")


def test_the_starter_questions_stay_english_in_every_translation():
    """Deliberate, not an oversight: the built-in answers match English
    triggers only, and this branch is reached BECAUSE no model is available to
    answer anything else. Suggesting translated questions would offer five
    things nothing here can answer."""
    for code in SUPPORTED_LANGS:
        text = public_fallback(code)
        for q in STARTERS:
            assert q in text, f"{code} lost the English phrasing of {q!r}"


def test_the_suggestions_it_prints_are_ones_it_can_actually_answer():
    """A card that names a question is claiming it can answer it."""
    for q in STARTERS:
        assert faq_answer(q), f"{q!r} is offered by the fallback and answers nothing"


def test_the_fallback_still_leaks_nothing_in_any_language():
    for code in SUPPORTED_LANGS:
        blob = public_fallback(code).lower()
        assert "$" not in blob, f"{code}: dollar amount on a public surface"
        for leak in (".env", "setllm", "api_key", "llm_api_key", "add a key"):
            assert leak not in blob, f"{code} leaks {leak}"


# ── the call site, driven ───────────────────────────────────────────────────

def test_the_no_model_branch_answers_a_visitor_in_their_language():
    """Driven through `_llm_chat`, because a mutation dropping the argument at
    the call site (`public_fallback()` for `public_fallback(_ui)`) survived
    every test above: they all exercise the function, none of them exercise
    the one line that hands it the language.

    This is the branch, and both of its halves are asserted — the operator is
    told the model is not connected, in their language, and so now is the
    visitor. That asymmetry living inside one `if` is the whole finding."""
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace

    import bot.skills.telegram_handler as th_mod
    from bot.core.cost import CostTracker
    from bot.llm.provider import BYOK, LLMConfig, LLMProvider
    from bot.skills.chat_runtime import _CHAT_NO_TOOLS_RULE
    from bot.skills.telegram_handler import TelegramHandler as H

    class _Conversations:
        def get_recent_as_llm_messages(self, *a, **k):
            return []

        def append(self, *a, **k):
            pass

    stub = SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda uid, user_name="", surface="telegram": _CHAT_NO_TOOLS_RULE,
        _PUBLIC_CHAT_SYSTEM_PROMPT=H._PUBLIC_CHAT_SYSTEM_PROMPT,
        _is_admin=lambda u: False,
        _note_chat_llm_failure=lambda reason="": None,
    )

    saved = (th_mod.CONFIG, th_mod.resolve_tier_config)
    BYOK.reset()
    try:
        # No tier config, no env keys, no primary — `configs_to_try` is empty,
        # which is the only way into the branch under test.
        # An UNCONFIGURED config, not None: the caller asks it
        # `is_configured()`, so None is a crash rather than the state under
        # test — which is itself the distinction this repo keeps making
        # between "absent" and "answered no".
        _unset = LLMConfig(provider=LLMProvider.OPENAI, api_key="", model="")
        th_mod.resolve_tier_config = lambda *a, **kw: _unset
        th_mod.CONFIG = replace(
            th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key=""))
        import os
        cleared = {}
        for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY",
                    "OPENAI_API_KEY", "XAI_API_KEY", "GROQ_API_KEY"):
            if env in os.environ:
                cleared[env] = os.environ.pop(env)
        try:
            # A question that reaches the model path at all: a starter question
            # would be answered by the FAQ long before this branch.
            q = "what do you think about the market this week"
            en = asyncio.run(H._llm_chat(stub, q, user_id="", public=True))
            zh = asyncio.run(H._llm_chat(stub, q, user_id="", public=True,
                                         reply_lang="zh"))
            es = asyncio.run(H._llm_chat(stub, q, user_id="", public=True,
                                         reply_lang="es"))
        finally:
            os.environ.update(cleared)
    finally:
        th_mod.CONFIG, th_mod.resolve_tier_config = saved
        BYOK.reset()

    assert en == public_fallback("en"), "the branch under test was not reached"
    assert zh == public_fallback("zh") != en, "the visitor was answered in English"
    assert es == public_fallback("es") != en
    assert any("一" <= c <= "鿿" for c in zh), "no Chinese in the zh reply"


# ── what this file does NOT claim ───────────────────────────────────────────

def test_every_trigger_is_ascii_which_is_why_one_mutation_is_equivalent():
    """Reverting the TRIGGER side of the comparison to `len(t.split())`
    survives this whole file, and that is correct rather than a gap: every
    phrase in `_FAQ["triggers"]` is ASCII English, so the two expressions
    return the same number for every input that exists.

    It is written as `_length_in_words` on both sides anyway, because the
    comparison subtracts one from the other and a measure that is only
    accidentally symmetric is the defect this file is about. The moment a
    non-ASCII trigger is added the symmetry starts carrying weight — so the
    precondition is pinned here, and whoever adds one is told."""
    for item in faq_kb._FAQ:
        for trig in item["triggers"]:
            assert trig.isascii(), (
                f"{trig!r} is not ASCII — the trigger side of the word-count "
                "comparison now matters; give it its own fixture above")
