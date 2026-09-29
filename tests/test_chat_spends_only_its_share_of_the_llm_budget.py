"""Chat stops at its share of the daily LLM budget; the rest is the engine's.

Chat and trade analysis book their spend on one `CostTracker`, and the
analyzer's dollar guard compares the TOTAL against the whole daily budget. So
a busy chat day spent the budget and every later trade thesis ran on the rule
engine instead of the model (`RULE_ENGINE_BUDGET`), with nothing on any card
saying the analysis had changed engines because of chat.

`LLM_CHAT_BUDGET_SHARE` (default 0.5) is chat's share. Chat refuses once its
own spend reaches it, and the analyzer keeps the rest. The total bound and the
call limit still apply to chat as before.
"""

from __future__ import annotations

import ast
import asyncio
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostSummary, CostTracker, chat_spend_usd
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.skills.telegram_handler import TelegramHandler as H

ROOT = Path(__file__).resolve().parents[1]

#: $3.00 per million prompt tokens, so 50k tokens is $0.15.
_MODEL = "claude-sonnet-4-6"


def _spend(cost: CostTracker, usd: float, category: str) -> None:
    tokens = int(round(usd / 3.0 * 1_000_000))
    cost.record_llm(model=_MODEL, prompt_tokens=tokens, completion_tokens=0,
                    category=category)


class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8):
        return []


def _stub(cost: CostTracker):
    return SimpleNamespace(
        engine=SimpleNamespace(cost=cost),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": "system prompt",
        _is_admin=lambda update: False,
    )


@pytest.fixture(autouse=True)
def _isolated_chat(monkeypatch):
    BYOK.reset()
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY",
                "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    yield
    BYOK.reset()


def _budget(monkeypatch, *, usd=1.0, share=0.5, calls=500):
    monkeypatch.setattr(th_mod, "CONFIG", replace(th_mod.CONFIG, llm=replace(
        th_mod.CONFIG.llm, api_key="", daily_call_limit=calls,
        daily_budget_usd=usd, chat_budget_share=share)))


def _ask(monkeypatch, cost: CostTracker, *, on_call=None):
    """One chat turn. Returns (answer, whether the model was asked)."""
    async def _complete(*a, **kw):
        if on_call is not None:
            on_call()
        return "a reply"

    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    llm = AsyncMock(side_effect=_complete)
    monkeypatch.setattr(th_mod, "llm_complete", llm)
    answer = asyncio.run(H._llm_chat(_stub(cost), "hello"))
    return answer, llm.await_count > 0


def test_chat_refuses_once_its_own_spend_reaches_its_share(monkeypatch):
    """$0.60 of chat on a $1.00 budget: the total is under the budget, so the
    old guard answered, and chat's share of $0.50 is spent."""
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.60, "chat")
    assert cost.snapshot().llm_cost_usd < 1.0
    answer, asked = _ask(monkeypatch, cost)
    assert not asked
    assert "budget" in answer.lower()


def test_spend_by_the_engine_does_not_count_against_chats_share(monkeypatch):
    """The same $0.60 booked by trade analysis leaves chat its whole share."""
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.60, "thesis")
    answer, asked = _ask(monkeypatch, cost)
    assert asked
    assert answer == "a reply"


def test_a_share_of_one_is_the_old_behaviour(monkeypatch):
    _budget(monkeypatch, share=1.0)
    cost = CostTracker()
    _spend(cost, 0.60, "chat")
    _, asked = _ask(monkeypatch, cost)
    assert asked


def test_a_share_of_zero_turns_the_model_off_for_chat(monkeypatch):
    _budget(monkeypatch, share=0.0)
    _, asked = _ask(monkeypatch, CostTracker())
    assert not asked


def test_the_total_still_bounds_chat(monkeypatch):
    """Chat has spent nothing, and the engine has spent the whole budget."""
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 1.01, "thesis")
    _, asked = _ask(monkeypatch, cost)
    assert not asked


def test_the_refusal_names_the_bound_that_was_reached(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(th_mod, "audit",
                        lambda log, msg, **kw: seen.append(msg))
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.60, "chat")
    _ask(monkeypatch, cost)
    assert any("chat's share of the dollar budget" in m for m in seen), seen


def test_a_chat_day_leaves_the_engine_the_rest_of_the_budget(monkeypatch):
    """Chat asks until it is refused, each turn costing $0.15. It stops at its
    share, so the total is still under the budget, which is the condition the
    analyzer's dollar guard reads (pinned below) for the model to run."""
    _budget(monkeypatch)
    cost = CostTracker()
    answered = 0
    for _ in range(20):
        _, asked = _ask(monkeypatch, cost,
                        on_call=lambda: _spend(cost, 0.15, "chat"))
        if not asked:
            break
        answered += 1
    assert answered == 4, answered  # 0, 0.15, 0.30, 0.45 were under $0.50
    snap = cost.snapshot()
    assert snap.cost_by_category["chat"] >= 0.5
    assert snap.llm_cost_usd < th_mod.CONFIG.llm.daily_budget_usd


def test_the_analyzers_dollar_guard_reads_the_total():
    """A SCAN, stated as one: `_llm_thesis` sits behind a model client, the
    semantic cache, the adaptive-frequency gate and the tiered pipeline. The
    claim is that its dollar guard compares the TOTAL spend against the whole
    budget, so the share chat leaves is the engine's."""
    tree = ast.parse((ROOT / "bot" / "core" / "analyzer.py").read_text(encoding="utf-8"))
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.AsyncFunctionDef) and n.name == "_llm_thesis"]
    assert len(fns) == 1
    compares = [ast.unparse(n) for n in ast.walk(fns[0]) if isinstance(n, ast.Compare)]
    assert "snap.llm_cost_usd >= CONFIG.llm.daily_budget_usd" in compares, compares


class TestTheChatSpendReading:
    def test_the_chat_category_is_read(self):
        s = CostSummary(llm_cost_usd=0.9)
        s.cost_by_category["chat"] = 0.2
        assert chat_spend_usd(s) == 0.2

    def test_a_measured_zero_is_zero(self):
        s = CostSummary(llm_cost_usd=0.9)
        assert chat_spend_usd(s) == 0.0

    @pytest.mark.parametrize("value", [None, True, "0.2", math.nan, math.inf, -0.1])
    def test_an_unreadable_figure_is_the_total(self, value):
        """Every dollar may have been chat's, so chat stops at its share of
        the total. Zero would let chat spend the whole budget unseen."""
        s = CostSummary(llm_cost_usd=0.9)
        s.cost_by_category["chat"] = value
        assert chat_spend_usd(s) == 0.9

    def test_a_summary_without_the_category_is_the_total(self):
        s = CostSummary(llm_cost_usd=0.9)
        s.cost_by_category = {}
        assert chat_spend_usd(s) == 0.9
        s.cost_by_category = None  # type: ignore[assignment]
        assert chat_spend_usd(s) == 0.9


def test_the_share_is_bounded_to_zero_through_one():
    src = (ROOT / "bot" / "config.py").read_text(encoding="utf-8")
    assert '_env_float_bounded("LLM_CHAT_BUDGET_SHARE", 0.5, 0.0, 1.0)' in src


# ---------------------------------------------------------------------------
# The rolling-note fold is a chat call too.
# ---------------------------------------------------------------------------

def _pruned_store():
    from bot.nlp.conversation_store import ConversationStore
    store = ConversationStore(max_messages_per_user=2)
    store.append("u", "user", "I only trade small, 1% risk")
    store.append("u", "assistant", "Noted.")
    store.append("u", "user", "what about SOL?")
    store.append("u", "assistant", "SOL looks choppy.")
    return store


def _fold(monkeypatch, cost: CostTracker, *, usage=None):
    """One `_summarize_if_due` on a stand-in that HAS an engine with a cost
    tracker. Returns (wrote a note, whether the model was asked, the store)."""
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key="k",
                                   model=_MODEL))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    asked: list = []

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        asked.append(user_prompt)
        if usage is not None and kw.get("usage_out") is not None:
            kw["usage_out"].update(usage)
        return "The user trades small."

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    store = _pruned_store()
    host = SimpleNamespace(conversations=store, engine=SimpleNamespace(cost=cost),
                           _SUMMARY_SYSTEM_PROMPT=H._SUMMARY_SYSTEM_PROMPT)
    wrote = asyncio.run(H._summarize_if_due(host, "u"))
    return wrote, bool(asked), store


def test_a_fold_is_booked_as_chat(monkeypatch):
    """It used to call the chat model after every reply and book nothing, so
    chat could spend past its share unseen by /costs and by the bound."""
    _budget(monkeypatch)
    cost = CostTracker()
    wrote, asked, _ = _fold(monkeypatch, cost, usage={"in": 50_000, "out": 0})
    assert wrote and asked
    snap = cost.snapshot()
    assert snap.llm_calls == 1
    assert snap.cost_by_category.get("chat") == pytest.approx(0.15)


def test_a_fold_with_no_usage_books_an_estimate_not_nothing(monkeypatch):
    _budget(monkeypatch)
    cost = CostTracker()
    _fold(monkeypatch, cost)
    assert cost.snapshot().cost_by_category.get("chat", 0) > 0


def test_a_fold_past_chats_share_asks_no_model_and_keeps_the_turns(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(th_mod, "audit", lambda log, msg, **kw: seen.append(msg))
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.60, "chat")
    wrote, asked, store = _fold(monkeypatch, cost)
    assert not wrote and not asked
    assert len(store.take_pending_summary("u")) == 2, "the turns were lost"
    assert any("chat's share of the dollar budget" in m for m in seen), seen


def test_a_fold_under_the_share_is_asked(monkeypatch):
    """The other arm: the refusal above passes against a fold that never
    asks anything."""
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.60, "thesis")
    wrote, asked, _ = _fold(monkeypatch, cost)
    assert wrote and asked


def test_the_share_refusal_says_the_rest_is_the_engines(monkeypatch):
    """"I've used up today's AI budget" was false when only chat's share was
    spent: trade analysis still had the rest."""
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 0.60, "chat")
    answer, _ = _ask(monkeypatch, cost)
    assert "share" in answer and "Trade analysis keeps the rest" in answer
    assert "used up today's AI budget" not in answer


def test_the_whole_budget_refusal_is_the_old_sentence(monkeypatch):
    _budget(monkeypatch)
    cost = CostTracker()
    _spend(cost, 1.01, "thesis")
    answer, _ = _ask(monkeypatch, cost)
    assert "used up today's AI budget" in answer


class TestTheBound:
    def _cfg(self, **kw):
        base = dict(daily_call_limit=10, daily_budget_usd=1.0, chat_budget_share=0.5)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_none_reached(self):
        from bot.core.cost import chat_budget_bound
        s = CostSummary(llm_cost_usd=0.2, llm_calls=1)
        s.cost_by_category["chat"] = 0.2
        assert chat_budget_bound(s, self._cfg()) == ""

    def test_the_share_exactly_reached_is_reached(self):
        from bot.core.cost import CHAT_SHARE_BOUND, chat_budget_bound
        s = CostSummary(llm_cost_usd=0.5, llm_calls=1)
        s.cost_by_category["chat"] = 0.5
        assert chat_budget_bound(s, self._cfg()) == CHAT_SHARE_BOUND

    def test_the_call_limit_is_named_first(self):
        from bot.core.cost import chat_budget_bound
        s = CostSummary(llm_cost_usd=2.0, llm_calls=10)
        s.cost_by_category["chat"] = 2.0
        assert chat_budget_bound(s, self._cfg()) == "daily call limit"

    def test_the_total_is_named_before_the_share(self):
        from bot.core.cost import chat_budget_bound
        s = CostSummary(llm_cost_usd=1.0, llm_calls=1)
        s.cost_by_category["chat"] = 1.0
        assert chat_budget_bound(s, self._cfg()) == "daily dollar budget"
