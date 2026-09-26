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
