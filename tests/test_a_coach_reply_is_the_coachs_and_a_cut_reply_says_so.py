"""Two defects one Pi coach reply showed on 22 Oct 2026.

Asked in Dutch "why did the agent call LTC SHORT here, and what would prove
it wrong?", from the signal card's Why? button, a signed-in reader got:

  "Let op: deze pagina heeft geen live marktdata ... Met een ingelogd
   account krijg je de live versie. ... en NEW betekent"

1. THE PUBLIC PAGE'S CONTRACT SPOKE FOR THE COACH. The coach turn runs as a
   public (account-free, tool-free) turn, and `reply_contract(public=True)`
   chose the anonymous website page's contract: a Dutch question falls
   through the English shape detector to `_PUBLIC_UNREAD_CONTRACT`, which
   says to open with "this page has no live data" and to send the reader to
   a signed-in account for "the live one". Said beside the card the app had
   read for them, to a person already signed in, on an app with no such door.

2. THE REPLY WAS CUT AND SHOWN WHOLE. The answer stopped mid-sentence. The
   provider reported why (Anthropic's `stop_reason == "max_tokens"`, an
   OpenAI-compatible `finish_reason == "length"`), and nothing read it: the
   text came back the same either way. The chat model's thinking counts
   toward the cap, so a longer language reaches it sooner.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm import provider as prov
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills import chat_runtime as CR
from bot.skills.telegram_handler import TelegramHandler as H
from bot.web import pi_coach

DUTCH = "Waarom callde de agent hier LTC SHORT, en wat zou bewijzen dat hij ongelijk heeft?"
CARD = "Signal card: LTCUSDT SHORT, entry 88.10, stop 90.40, target 83.50, regime NEUTRAL, status NEW"
CUT = ("Een <b>SHORT</b> betekent dat de agent verwacht dat de prijs daalt. "
       "Het label <b>NEUTRAL</b> staat voor de marktsituatie, en <b>NE")


def _run(coro):
    return asyncio.run(coro)


class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False, token_budget=None):
        return []


def _stub():
    return SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker()),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": "system prompt",
        _PUBLIC_CHAT_SYSTEM_PROMPT=H._PUBLIC_CHAT_SYSTEM_PROMPT,
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
    )


@pytest.fixture
def model(monkeypatch):
    """A stand-in provider. `state["reply"]` is what it answers and
    `state["cut"]` whether it reports stopping at its cap, through the same
    `usage_out` the real `llm_complete` fills."""
    BYOK.reset()
    monkeypatch.setattr(th_mod, "resolve_tier_config",
                        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    llm = replace(th_mod.CONFIG.llm, api_key="", daily_call_limit=500, daily_budget_usd=1.0,
                  chat_budget_share=0.5, public_chat_budget_share=0.2)
    cfg = replace(th_mod.CONFIG, llm=llm)
    monkeypatch.setattr(th_mod, "CONFIG", cfg)
    import bot.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG", cfg)
    state: dict = {"reply": "A reply.", "cut": False, "prompts": []}

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        state["prompts"].append(system_prompt)
        finish = "length" if state["cut"] else "stop"
        prov._hand_back_usage(kw.get("usage_out"), SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50)))
        return state["reply"]

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    yield state
    BYOK.reset()


# ── 1. the coach's own contract ─────────────────────────────────────────────

def test_the_coach_turn_is_handed_the_coachs_contract_and_not_the_public_pages(model):
    _run(H._llm_chat(_stub(), DUTCH, public=True, surface="pi_coach",
                     coach_context=CARD, reply_lang="nl"))
    prompt = model["prompts"][0]
    assert prompt.endswith(pi_coach.REPLY_CONTRACT)
    for public_only in (CR._PUBLIC_UNREAD_CONTRACT, *CR._PUBLIC_REPLY_CONTRACTS.values()):
        assert public_only not in prompt, "the public page's contract rode into the coach"
    assert "signed-in account gets the live one" not in prompt
    assert CARD in prompt


@pytest.mark.parametrize("q, lang", [
    (DUTCH, "nl"),
    ("full analysis of LTC with entry, stop and target", "en"),
    ("trade plan for btc", "en"),
])
def test_no_question_shape_or_language_brings_the_public_contract_back(model, q, lang):
    _run(H._llm_chat(_stub(), q, public=True, surface="pi_coach", coach_context=CARD,
                     reply_lang=lang, reply_mode=CR.DEFAULT_REPLY_MODE if lang == "nl" else "full_scan"))
    prompt = model["prompts"][-1]
    assert prompt.endswith(pi_coach.REPLY_CONTRACT)
    assert "HOW LONG AND WHAT SHAPE" in prompt and prompt.count("HOW LONG AND WHAT SHAPE") == 1


def test_the_public_page_still_gets_its_own_contract(model):
    """Both arms: the website's anonymous turn is unchanged."""
    _run(H._llm_chat(_stub(), DUTCH, public=True, reply_lang="nl"))
    prompt = model["prompts"][-1]
    assert CR._PUBLIC_UNREAD_CONTRACT in prompt
    assert pi_coach.REPLY_CONTRACT not in prompt


def test_the_coachs_contract_says_what_the_coach_can_source():
    c = pi_coach.REPLY_CONTRACT
    assert "FACTS" in c and "never say the app has no data" in c
    assert "Never add a level, a size or a plan of your own" in c
    assert "Never tell them to sign in" in c
    assert "end on a complete sentence" in c


# ── 2. a cut reply ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("response, cut", [
    (SimpleNamespace(stop_reason="max_tokens"), True),
    (SimpleNamespace(stop_reason="end_turn"), False),
    (SimpleNamespace(choices=[SimpleNamespace(finish_reason="length")]), True),
    (SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop")]), False),
    (SimpleNamespace(choices=[]), False),
    (SimpleNamespace(), False),
])
def test_the_provider_reads_a_reply_that_stopped_at_its_cap(response, cut):
    assert prov.stopped_at_cap(response) is cut


def test_a_tool_loop_is_judged_by_its_last_round():
    out: dict = {}
    prov._hand_back_usage(out, SimpleNamespace(stop_reason="max_tokens"))
    assert out["cut"] is True
    prov._hand_back_usage(out, SimpleNamespace(stop_reason="end_turn"))
    assert "cut" not in out


@pytest.mark.parametrize("text, want", [
    (CUT, "Een <b>SHORT</b> betekent dat de agent verwacht dat de prijs daalt. …"),
    ("Een SHORT betekent <b>NE", "Een SHORT betekent …"),
    ("買いです。損切りは", "買いです。 …"),
    ("यह शॉर्ट है। स्टॉप", "यह शॉर्ट है। …"),
    ("هل هذا بيع؟ الوقف", "هل هذا بيع؟ …"),
    ("It is <b>short.</b> The stop", "It is <b>short.</b> …"),
    ("Version 1.5 is", "Version 1.5 is …"),
    # Back to the LAST whole sentence, not the first.
    ("One. Two. Three half", "One. Two. …"),
    # Full-width question and exclamation marks end a sentence too.
    ("本当？それは", "本当？ …"),
    ("すごい！でも", "すごい！ …"),
])
def test_a_cut_reply_goes_back_to_its_last_whole_sentence_and_says_it_was_cut(text, want):
    assert CR.trim_cut_reply(text) == want


def test_a_cut_coach_reply_is_trimmed_marked_and_flagged(model):
    model["reply"], model["cut"] = CUT, True
    text, meta = _run(H._llm_chat(_stub(), DUTCH, public=True, surface="pi_coach",
                                  coach_context=CARD, reply_lang="nl", return_meta=True))
    assert text == "Een <b>SHORT</b> betekent dat de agent verwacht dat de prijs daalt. …"
    assert meta.get("cut") is True


def test_a_whole_reply_is_left_alone(model):
    """Both arms: the same text, reported as finished, is shown as written."""
    model["reply"], model["cut"] = CUT, False
    text, meta = _run(H._llm_chat(_stub(), DUTCH, public=True, surface="pi_coach",
                                  coach_context=CARD, reply_lang="nl", return_meta=True))
    assert text == CUT
    assert "cut" not in meta


def test_every_chat_surface_gets_the_same_trim(model):
    """The seam is `_chat_ret`, so the website's chat and Telegram are covered
    by the same line, not by a copy of it in the coach."""
    model["reply"], model["cut"] = "First sentence. Second half", True
    assert _run(H._llm_chat(_stub(), "hello", public=True)) == "First sentence. …"
