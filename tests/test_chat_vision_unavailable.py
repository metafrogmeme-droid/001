"""An image nothing can read must say so, not be silently dropped.

WHY. Vision is Anthropic-only — the image content-block shape is
Anthropic-specific — and `_vision_ok` in `_llm_chat` drops the images for every
other provider. That was harmless while the chat tier WAS Anthropic.

Since 2026-09-04 the chat tier is `runeclaw-chat` (llama3.2, no vision), so the
images are ALWAYS discarded while the caller still sends the vision PROMPT:

    "Read this trading screenshot. If it's a chart, describe the structure,
     trend, key levels and any setup or risk you see."

A text model given that prompt and no picture answers the prompt. On 2026-09-05
a user attached a chart in the web app and got:

    "Ik kan de screenshot niet zien. Als je de screenshot wilt delen, kan ik je
     helpen om de structuur, trend, sleutelniveaus en setup/risico's te
     identificeren."

— asking for the thing they had just sent, in a list lifted straight from the
prompt. It reads like the upload failed. Nothing failed; the capability was
never there. That is the same shape as `ollama ps` reporting 100% GPU through a
14x slowdown: a confident answer to a question the system could not observe.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "bot" / "skills" / "telegram_handler.py"


@pytest.fixture(scope="module")
def src():
    return _SRC.read_text(encoding="utf-8")


class TestTheRefusalExists:
    def test_images_without_a_vision_provider_are_refused(self, src):
        m = re.search(
            r"if images and not any\(c\.provider == LLMProvider\.ANTHROPIC",
            src)
        assert m, "no guard refusing images when no tier can read them"

    def test_it_refuses_before_the_model_is_called(self, src):
        """Spending a call to produce a misleading answer is worse than not."""
        guard = src.index("if images and not any(c.provider == LLMProvider.ANTHROPIC")
        call = src.index("answer = await llm_complete(")
        assert guard < call, \
            "the guard runs after the model call — the misleading answer is "\
            "already paid for and returned"

    def test_the_reply_says_which_part_is_missing(self, src):
        guard = src.index("if images and not any(c.provider == LLMProvider.ANTHROPIC")
        window = src[guard:guard + 1400]
        assert "no vision" in window, \
            "the reply must name the missing capability, not just decline"
        assert "never reached it" in window, \
            "the user needs to know the image was not read, not that it was "\
            "read and found wanting"

    def test_it_offers_the_path_that_does_work(self, src):
        guard = src.index("if images and not any(c.provider == LLMProvider.ANTHROPIC")
        window = src[guard:guard + 1400]
        assert "/analyze" in window, \
            "a refusal with no alternative leaves the user with nothing"

    def test_the_drop_is_audited(self, src):
        guard = src.index("if images and not any(c.provider == LLMProvider.ANTHROPIC")
        window = src[guard:guard + 1400]
        assert "chat_vision_unavailable" in window, \
            "a silently dropped input must at least leave a trace"


class TestItDoesNotBreakWhatWorks:
    def test_anthropic_in_the_candidate_list_is_not_refused(self, src):
        # The guard fires only when NO candidate is Anthropic. With the
        # operator's Claude key present the vision path must still run.
        guard = src.index("if images and not any(c.provider == LLMProvider.ANTHROPIC")
        line = src[guard:src.index("\n", guard + 200)]
        assert "not any(" in line, \
            "the condition must be 'no candidate can see', not 'the first "\
            "candidate cannot'"

    def test_a_text_only_message_is_untouched(self, src):
        # `images` is falsy for an ordinary chat turn, so the guard is inert.
        guard = src.index("if images and not any(c.provider == LLMProvider.ANTHROPIC")
        assert src[guard:guard + 10].startswith("if images"), \
            "the guard must be gated on images first — a text turn must not "\
            "pay for this check"

    def test_the_vision_gate_itself_is_unchanged(self, src):
        # The Anthropic-only rule below is still what decides whether images
        # are attached; this guard only stops the case where it would say no
        # for every candidate.
        assert "_vision_ok = (bool(images) and is_admin and not public" in src
        assert "cfg.provider == LLMProvider.ANTHROPIC)" in src


# ── driven: what the caller is told, and what is never called ─────────────
#
# The checks above read the source; these run `_llm_chat` with the candidate
# list planted, so the refusal is measured by what reaches the caller and
# what never reaches a model.

import asyncio  # noqa: E402
import logging  # noqa: E402
from dataclasses import replace  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import bot.skills.telegram_handler as th_mod  # noqa: E402
from bot.core.cost import CostTracker  # noqa: E402
from bot.llm.provider import BYOK, LLMConfig, LLMProvider  # noqa: E402
from bot.skills.chat_runtime import _CHAT_NO_TOOLS_RULE  # noqa: E402
from bot.skills.telegram_handler import TelegramHandler as H  # noqa: E402

IMG = [{"media_type": "image/jpeg", "data": "QUJD"}]


class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False):
        return []

    def append(self, *a, **k):
        pass


def _stub():
    return SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": (
            "system prompt\n" + _CHAT_NO_TOOLS_RULE),
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
        registry=SimpleNamespace(get=lambda n: None),
        users=None,
    )


@pytest.fixture
def chat(monkeypatch):
    calls = []

    def _plant(provider):
        monkeypatch.setattr(
            th_mod, "resolve_tier_config",
            lambda *a, **kw: LLMConfig(provider=provider, api_key="k", model="m"))

    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="",
                                   chat_tools_enabled=False)))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "resolve_profile_note", lambda note, uid: "")

    async def _complete(client, cfg, sys_p, q, **kw):
        calls.append((cfg.provider, kw.get("images")))
        return "a model answer"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    BYOK.reset()
    yield _plant, calls
    BYOK.reset()


def _ask(**kw):
    out = asyncio.run(H._llm_chat(_stub(), "what does this chart say?",
                                  user_id="u1", images=IMG, **kw))
    return out[0] if isinstance(out, tuple) else out


class TestDriven:
    def test_a_text_model_is_never_asked_about_a_picture(self, chat, monkeypatch):
        plant, calls = chat
        plant(LLMProvider.GROK)
        audits = []
        monkeypatch.setattr(th_mod, "audit",
                            lambda log, msg, **kw: audits.append((msg, kw)))
        out = _ask(is_admin=True)
        assert calls == [], "the model was paid to answer a prompt about a picture"
        assert "no vision" in out and "never reached it" in out
        assert "/analyze BTC" in out
        hit = [kw for _, kw in audits if kw.get("action") == "chat_vision_unavailable"]
        assert hit and hit[0]["level"] == logging.WARNING
        assert hit[0]["data"]["candidates"] == ["grok"]

    def test_a_web_caller_is_given_words_not_a_slash_command(self, chat):
        plant, calls = chat
        plant(LLMProvider.GROK)
        out = _ask(is_admin=True, surface="web")
        assert calls == []
        assert '"analyze BTC"' in out
        assert not re.search(r"(?<!\w)/[a-z]{2,}", out), out

    def test_the_operators_claude_still_reads_the_image(self, chat):
        plant, calls = chat
        plant(LLMProvider.ANTHROPIC)
        out = _ask(is_admin=True)
        assert out == "a model answer"
        assert calls and calls[0][1] == IMG, "the image did not reach Claude"

    def test_claude_in_the_list_does_not_see_for_a_non_admin(self, chat):
        # `_vision_ok` never attaches images for a non-admin caller, so a
        # Claude candidate in the list is no reason to spend a call.
        plant, calls = chat
        plant(LLMProvider.ANTHROPIC)
        out = _ask(is_admin=False)
        assert calls == [] and "no vision" in out

    def test_a_text_turn_is_untouched(self, chat):
        plant, calls = chat
        plant(LLMProvider.GROK)
        out = asyncio.run(H._llm_chat(_stub(), "hello there", user_id="u1",
                                      is_admin=True))
        out = out[0] if isinstance(out, tuple) else out
        assert out == "a model answer" and calls == [(LLMProvider.GROK, None)]
