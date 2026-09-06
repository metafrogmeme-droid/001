"""Every provider returned 200 and nothing, and the reader was told the AI was
temporarily unavailable.

Live, 2026-09-02. The proxy log for the same minutes:

    POST /v1/chat/completions HTTP/1.1" 200
    POST /v1/chat/completions HTTP/1.1" 200

The AI was entirely available. It answered, with nothing — `2,100 in / 21 out`
on the one call /llmstatus had recorded. The operator checked the cloudflared
tunnel and rotated nothing but looked at the key, twice, on the strength of a
sentence that named the wrong fault.

Two defects, one incident:

  * The REPLY folded "could not be reached" and "answered with nothing" into
    one message. The loop already tells them apart; only the wording did not.
    Different faults, different next step: unreachable is infrastructure,
    empty is the model or the prompt.

  * /llmstatus said "Brain: untested — no LLM analysis attempted since
    restart" immediately after two chat failures. True of the SWEEP. The
    health counter lives on the analyzer and the chat path never touched it,
    so the surface a person uses could fail all day without moving the signal
    a person checks.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _handler_text() -> str:
    """Every file the handler class is made of, handler first: the chat brain
    stays in telegram_handler.py and /llmstatus lives in the LLM mixin since
    the handler split. Joined in MRO order, so `index()` still finds the
    handler's own text first and the counts below can only grow."""
    from tests.source_scan import handler_sources
    return "\n".join(p.read_text(encoding="utf-8") for p in handler_sources())


HANDLER = _handler_text()


def _code(src: str) -> str:
    return "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))


# ── the analyzer records chat failures, separately ───────────────────────
def _analyzer():
    from bot.core.analyzer import Analyzer
    a = object.__new__(Analyzer)
    a._llm_degraded_streak = 0
    a._llm_degraded_since_monotonic = 0.0
    a._llm_last_error = ""
    a._llm_last_ok_monotonic = 0.0
    a._llm_chat_failures = 0
    a._llm_chat_last_error = ""
    a._llm_chat_last_monotonic = 0.0
    a._llm_sweep_calls = 0
    return a


def test_a_chat_failure_is_recorded():
    a = _analyzer()
    a.note_llm_chat_failed("runeclaw: empty completion")
    h = a.llm_health()
    assert h["chat_failures"] == 1
    assert "empty completion" in h["chat_last_error"]
    assert h["chat_seconds_ago"] is not None


def test_chat_failures_do_not_move_the_analysis_streak():
    """The streak's documented meaning is consecutive THESES where every
    provider failed, and it drives the degraded alert and the rule-engine
    fallback story. Advancing it from chat would change what that number
    claims."""
    a = _analyzer()
    for _ in range(3):
        a.note_llm_chat_failed("boom")
    h = a.llm_health()
    assert h["degraded_streak"] == 0
    assert h["chat_failures"] == 3


def test_no_chat_failure_reads_as_none_not_as_health():
    h = _analyzer().llm_health()
    assert h["chat_failures"] == 0
    assert h["chat_seconds_ago"] is None, "an unset clock is not 'just now'"


def test_recording_never_raises_into_the_failure_path():
    a = _analyzer()
    a._llm_chat_failures = object()          # unusable
    a.note_llm_chat_failed("boom")           # must not raise


def test_it_still_records_on_an_analyzer_built_without_init():
    """Several suites build an Analyzer with `object.__new__`, and production
    code paths can hold a partially-constructed one. Instrumentation that
    quietly stops recording there is worse than one that never ran: the count
    reads 0, which is indistinguishable from "no failures happened".

    A mutation swapping the getattr for direct attribute access survives a
    test that only checks for absence of an exception — the try/except eats
    the AttributeError and the failure vanishes.
    """
    from bot.core.analyzer import Analyzer
    bare = object.__new__(Analyzer)          # no __init__, no fields at all
    bare.note_llm_chat_failed("empty completion")
    assert int(getattr(bare, "_llm_chat_failures", 0) or 0) == 1, (
        "the failure was swallowed on an analyzer without pre-set fields")


# ── the reply names the right fault ──────────────────────────────────────
def test_the_empty_case_has_its_own_reply():
    code = _code(HANDLER)
    assert re.search(r"_empty_completions and _empty_completions >= _tried", code), (
        "the reply does not branch on every provider having come back empty")


def test_the_empty_branch_actually_increments_the_counter():
    """A branch that can never be true is the same as no branch. Deleting the
    increment left both the counter name and the `if` in place and the suite
    green — the mutation that found this changed nothing a grep could see."""
    code = _code(HANDLER)
    i = code.index('last_error = f"{cfg.provider.value}: empty completion"')
    window = code[i:i + 300]
    assert "_empty_completions += 1" in window, (
        "the empty-completion branch does not count itself, so the reply that "
        "depends on the count is unreachable")


def test_the_empty_reply_does_not_blame_availability():
    code = _code(HANDLER)
    i = code.index("_empty_completions >= _tried")
    branch = code[i:i + 700]
    assert "answered but returned nothing" in branch
    assert "temporarily unavailable" not in branch, (
        "the empty-completion reply still says the AI is unavailable — it is "
        "available, and that sentence sends an operator to the tunnel")
    assert "key and endpoint are fine" in branch


def test_the_unreachable_reply_still_exists_for_the_case_it_fits():
    """The fix must not delete the message that IS right when nothing answers."""
    assert "the AI is temporarily unavailable" in HANDLER


def test_both_chat_failure_exits_feed_the_health_signal():
    code = _code(HANDLER)
    assert code.count("_note_chat_llm_failure(") >= 3, (
        "one of the chat failure exits (deadline / all-failed) does not record"
    )


# ── /llmstatus says it ───────────────────────────────────────────────────
def test_llmstatus_reports_chat_failures_whatever_the_sweep_says():
    """Anchored on the ASSIGNMENT, not on the presence of the string.

    `_chatf = 0  # int(h.get("chat_failures", 0) or 0)` passed an earlier
    version of this test: `_code` strips whole-line comments only, so a
    trailing one kept the searched text alive while the value became a
    constant zero. That is this repo's oldest test trap — a comment quoting
    the thing it forbids — landing in a test written to catch it.
    """
    code = _code(HANDLER)
    m = re.search(r'_chatf\s*=\s*int\(\s*h\.get\(\s*"chat_failures"', code)
    assert m, "the chat-failure count is not read from the health snapshot"
    window = code[m.start():m.start() + 900]
    assert "Chat:" in window and "failed" in window
    assert "chat_last_error" in window, "the reason is not shown"


def test_the_tier_section_no_longer_claims_liveness():
    """✅ there means a credential is set. Under 'What answers right now' that
    read as four working tiers while every call was failing."""
    card = (ROOT / "bot" / "formatters" / "llm_tier_card.py").read_text(encoding="utf-8")
    code = _code(card)
    assert "What answers right now" not in code
    assert "How each tier is routed" in code
    assert "Configuration, not liveness" in code
