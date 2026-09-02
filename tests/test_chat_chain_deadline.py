"""The chat fallback chain gets ONE wall-clock budget, and says so when it ends.

Operator report, 2026-08-17: "response from bot seems slow."

Every timeout in `_llm_chat`'s provider chain is PER ATTEMPT — `bot/llm/provider.py`
does `asyncio.wait_for(_call(), timeout=config.timeout_seconds)` on each
candidate — and the loop had no wall-clock deadline, so the timeouts ADDED:

    BYOK -> chat_tier (15s) -> GEMINI (20s) -> ANTHROPIC (20s) -> ALIBABA (20s)
    -> primary (15s)   =   90 SECONDS before an admin was told ANYTHING

THE PROPERTY THAT MAKES THE DEADLINE SAFE, and the reason it is 45 and not 25:
the budget is strictly greater than the largest single attempt in the chain
(20.0, hardcoded at telegram_handler.py:1750), and `_env_float_bounded` floors
the knob at 20.0. So the deadline can never cut short an answer the per-attempt
timeout would itself have allowed — it only stops the ADDITION. A deadline tight
enough to kill a healthy slow answer is worse than the disease; this one cannot,
by construction rather than by luck. `test_the_deadline_cannot_truncate_a_healthy_
single_attempt` is that property, asserted.

TWO HONESTY FIXES RIDE ALONG, both the house rule applied to sentences instead
of numbers:

1. Running out of budget is NOT "every provider failed". The skipped providers
   were never asked and may be perfectly healthy — reporting them as
   unavailable is a confident negative about something never measured.
2. An empty completion was returned verbatim, painting a blank bubble that reads
   as "the model answered and had nothing to say". Same shape as a 0.00% over an
   unfetchable price. It is now this candidate FAILING, and the chain moves on.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.telegram_handler import CHAT_MIN_ATTEMPT_SEC
from bot.skills.telegram_handler import TelegramHandler as H


def _run(coro):
    return asyncio.run(coro)


class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8):
        return []


def _stub():
    return SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="": "system prompt",
        _is_admin=lambda update: False,
        # The chat failure exits now tell the brain-health signal that a call
        # fell through — /llmstatus said "no LLM analysis attempted since
        # restart" straight after two chat failures, because the counter is
        # fed by the analysis sweep alone. Real method on the real handler;
        # this stub is a SimpleNamespace, so it needs it spelled out.
        _note_chat_llm_failure=lambda reason="": None,
    )


@pytest.fixture(autouse=True)
def _reset_byok():
    BYOK.reset()
    yield
    BYOK.reset()


@pytest.fixture(autouse=True)
def _no_tier_config(monkeypatch):
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY",
                "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(
        th_mod, "CONFIG",
        replace(th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="")))


def _with_deadline(monkeypatch, seconds: float):
    monkeypatch.setattr(
        th_mod, "CONFIG",
        replace(th_mod.CONFIG,
                llm=replace(th_mod.CONFIG.llm, api_key="",
                            chat_deadline_seconds=seconds)))


class _Clock:
    """A monotonic clock the test drives, so no test sleeps for real."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


# ── the budget is enforced across the chain, not per attempt ─────────────────

def test_each_attempt_is_clamped_to_the_remaining_budget(monkeypatch):
    """The core of the fix: attempt N+1 may not use its full timeout if the
    chain has already spent most of the budget."""
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    monkeypatch.setenv("ALIBABA_API_KEY", "k2")
    _with_deadline(monkeypatch, 30.0)

    clock = _Clock()
    monkeypatch.setattr(th_mod.time, "monotonic", clock)
    seen: list[float] = []

    async def _complete(client, cfg, *a, **kw):
        seen.append(cfg.timeout_seconds)
        clock.advance(18.0)             # this attempt burns 18s, then fails
        raise RuntimeError("provider down")

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)

    _run(H._llm_chat(_stub(), "hello"))

    assert len(seen) >= 2, f"expected at least two attempts, got {seen}"
    assert seen[0] == 20.0, (
        f"the first attempt should get its own full timeout, got {seen[0]}")
    assert seen[1] == pytest.approx(12.0), (
        f"the second attempt must be clamped to the 12s left of a 30s budget, "
        f"not given its own 20s — got {seen[1]}")


def test_the_chain_stops_at_the_deadline_instead_of_trying_every_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k2")
    monkeypatch.setenv("ALIBABA_API_KEY", "k3")
    _with_deadline(monkeypatch, 25.0)

    clock = _Clock()
    monkeypatch.setattr(th_mod.time, "monotonic", clock)
    calls = []

    async def _complete(client, cfg, *a, **kw):
        calls.append(cfg.provider)
        clock.advance(20.0)
        raise RuntimeError("provider down")

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)

    _run(H._llm_chat(_stub(), "hello", is_admin=True))

    assert len(calls) == 1, (
        f"after 20s of a 25s budget only 5s remains, below the "
        f"{CHAT_MIN_ATTEMPT_SEC}s floor — the chain must stop, not keep "
        f"dialling. Tried: {calls}")


def test_an_attempt_is_never_started_with_a_sliver_of_budget(monkeypatch):
    """Starting a provider with 0.2s left bills the prompt tokens and buys a
    near-certain timeout. It is not an attempt, it is a slower way to fail."""
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    monkeypatch.setenv("ALIBABA_API_KEY", "k2")
    _with_deadline(monkeypatch, 20.0)

    clock = _Clock()
    monkeypatch.setattr(th_mod.time, "monotonic", clock)
    seen = []

    async def _complete(client, cfg, *a, **kw):
        seen.append(cfg.timeout_seconds)
        clock.advance(19.9)
        raise RuntimeError("down")

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)

    _run(H._llm_chat(_stub(), "hello"))

    assert len(seen) == 1
    assert all(t >= CHAT_MIN_ATTEMPT_SEC for t in seen), (
        f"no attempt may start below the {CHAT_MIN_ATTEMPT_SEC}s floor: {seen}")


def test_the_deadline_cannot_truncate_a_healthy_single_attempt():
    """THE SAFETY PROPERTY, asserted rather than asserted-in-a-comment.

    The knob's floor must exceed the largest single per-attempt timeout in the
    chain (20.0, hardcoded at telegram_handler.py:1750). Above that line the
    deadline only ever stops the SUM. If someone lowers the floor to 5.0, a
    healthy 15s answer starts getting cut off and this fails.
    """
    import os

    from bot.config import _env_float_bounded
    prev = os.environ.get("LLM_CHAT_DEADLINE_SEC")
    try:
        os.environ["LLM_CHAT_DEADLINE_SEC"] = "1"     # try to set it absurdly low
        floored = _env_float_bounded("LLM_CHAT_DEADLINE_SEC", 45.0, 20.0, 180.0)
    finally:
        if prev is None:
            os.environ.pop("LLM_CHAT_DEADLINE_SEC", None)
        else:
            os.environ["LLM_CHAT_DEADLINE_SEC"] = prev

    assert floored >= 20.0, (
        "the deadline can be clamped below the largest single attempt (20s), "
        "so an operator can now configure a budget that truncates a healthy "
        "answer — which is worse than the 90s chain it replaced")


# ── the two endings are different facts ──────────────────────────────────────

def test_the_deadline_reply_names_time_and_never_claims_the_models_are_down(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k2")
    monkeypatch.setenv("ALIBABA_API_KEY", "k3")
    _with_deadline(monkeypatch, 25.0)

    clock = _Clock()
    monkeypatch.setattr(th_mod.time, "monotonic", clock)

    async def _complete(client, cfg, *a, **kw):
        clock.advance(20.0)
        raise RuntimeError("provider down")

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)

    answer = _run(H._llm_chat(_stub(), "hello", is_admin=True))

    assert answer and answer.strip(), "the deadline path must never be blank"
    low = answer.lower()
    assert "stopped waiting" in low or "timeout" in low, (
        f"the reply must name TIME as the cause: {answer!r}")
    assert "unavailable" not in low, (
        "providers that were never asked must not be reported as unavailable — "
        "that is a confident negative about something never measured")
    assert "nothing was analyzed" in low, (
        "the user must be told no analysis happened, so a timeout is never "
        "mistaken for a considered 'no'")


def test_all_providers_failing_inside_the_budget_still_says_unavailable(monkeypatch):
    """The control. If the deadline branch swallowed this case too, the bot
    would blame the clock for genuine provider outages."""
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    _with_deadline(monkeypatch, 120.0)

    clock = _Clock()
    monkeypatch.setattr(th_mod.time, "monotonic", clock)

    async def _complete(client, cfg, *a, **kw):
        clock.advance(0.5)
        raise RuntimeError("provider down")

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)

    answer = _run(H._llm_chat(_stub(), "hello"))
    assert "unavailable" in answer.lower(), answer
    assert "stopped waiting" not in answer.lower(), (
        "every candidate was tried and failed inside the budget — that is a "
        "provider outage, not a timeout on our side")


def test_no_user_facing_reply_leaks_the_provider_error(monkeypatch):
    """F-15. last_error can carry a credential-bearing URL or a 4xx body
    echoing a key; it belongs in the audit log only."""
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    _with_deadline(monkeypatch, 120.0)

    async def _complete(client, cfg, *a, **kw):
        raise RuntimeError("401 https://api.example.com?key=sk-SECRET-abc123")

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)

    answer = _run(H._llm_chat(_stub(), "hello"))
    assert "sk-SECRET" not in answer and "api.example.com" not in answer, answer
    assert "gemini" not in answer.lower(), (
        "provider names stay in the log, not the reply")


# ── an empty completion is not an answer ─────────────────────────────────────

def test_an_empty_completion_falls_through_to_the_next_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    monkeypatch.setenv("ALIBABA_API_KEY", "k2")
    _with_deadline(monkeypatch, 120.0)

    replies = ["   ", "a real answer"]

    async def _complete(client, cfg, *a, **kw):
        return replies.pop(0)

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)

    answer = _run(H._llm_chat(_stub(), "hello"))
    assert answer.strip() == "a real answer", (
        f"a whitespace-only completion must not be served as the reply: {answer!r}")


def test_every_provider_returning_empty_does_not_render_as_a_blank_reply(monkeypatch):
    """The defect itself: a blank bubble reads as 'the model answered and had
    nothing to say' — a confident negative manufactured from a failed read."""
    monkeypatch.setenv("GEMINI_API_KEY", "k1")
    _with_deadline(monkeypatch, 120.0)

    async def _complete(client, cfg, *a, **kw):
        return ""

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "llm_complete", _complete)

    answer = _run(H._llm_chat(_stub(), "hello"))
    assert answer and answer.strip(), "an all-empty chain must not reply blank"


# ── the shared provider timeout is NOT collateral damage ─────────────────────

def test_the_chat_deadline_never_reaches_the_shared_provider_timeout():
    """Three other callers share CONFIG.llm.timeout_seconds and provider.py's
    wait_for — the trade thesis, the /scan summary and the self-audit. Two are
    background paths where a chat-shaped deadline would be a regression, so the
    clamp must ride a COPY of the config.
    """
    import inspect

    from tests.source_scan import code_only
    src = code_only(inspect.getsource(H._llm_chat))
    assert "_dc_replace(" in src, (
        "the per-attempt clamp must use dataclasses.replace on a copy — "
        "LLMConfig is frozen, and mutating the shared config would change the "
        "timeout for the analyzer, scan summary and self-audit too")
    assert "CONFIG.llm.timeout_seconds =" not in src

    provider_src = code_only(
        (__import__("pathlib").Path(th_mod.__file__).parent.parent
         / "llm" / "provider.py").read_text(encoding="utf-8"))
    assert "chat_deadline_seconds" not in provider_src, (
        "provider.py is shared by every LLM caller; the chat deadline must "
        "not reach it")
