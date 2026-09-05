"""`UserContext.summary` was read on every turn and written by nothing.

bot/nlp/conversation_store.py's docstring promised "conversation summarization
for long histories"; `build_context_prompt` injected `ctx.summary` into the
system prompt; and no line anywhere assigned it. So the fifty-first message
silently erased the first, and a user who had told the agent their risk
appetite on day one was a stranger by day three.

Three parts now, each pinned here:

1. The STORE keeps what the cap pruned, in message shape, in a bounded queue,
   and hands it out exactly once to whoever will fold it in.
2. The NOTE persists as its own JSONL row and comes back on restart — as a
   note, never as a turn somebody said.
3. The HANDLER folds the queue into the note on the cheapest chat-tier model,
   off the reply path, and gives the turns back when it cannot.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.llm.provider import BYOK, LLMConfig, LLMProvider
from bot.nlp.conversation_store import ConversationStore
from bot.skills.telegram_handler import TelegramHandler as H

# ── 1. the store keeps what it pruned ───────────────────────────────────────

def test_pruned_turns_wait_for_a_summary_and_are_handed_out_once():
    store = ConversationStore(max_messages_per_user=4)
    for i in range(6):
        store.append("u", "user" if i % 2 == 0 else "assistant", f"turn {i}")
    assert [m.content for m in store.get_recent("u", limit=10)] == [
        "turn 2", "turn 3", "turn 4", "turn 5"]
    pending = store.take_pending_summary("u")
    assert pending == [{"role": "user", "content": "turn 0"},
                       {"role": "assistant", "content": "turn 1"}]
    assert store.take_pending_summary("u") == [], "handed out exactly once"


def test_the_queue_is_bounded():
    store = ConversationStore(max_messages_per_user=2)
    for i in range(200):
        store.append("u", "user", f"m{i}")
    pending = store.take_pending_summary("u")
    assert len(pending) == ConversationStore.PENDING_SUMMARY_MAX
    assert pending[-1]["content"] == "m197", "the NEWEST pruned turns are kept"


def test_push_back_returns_turns_in_order_and_bounded():
    store = ConversationStore(max_messages_per_user=2)
    for i in range(5):
        store.append("u", "user", f"m{i}")
    taken = store.take_pending_summary("u")
    store.append("u", "user", "m5")             # prunes m3 while we hold m0..m2
    store.push_back_pending("u", taken)
    again = store.take_pending_summary("u")
    assert [m["content"] for m in again] == ["m0", "m1", "m2", "m3"]


def test_a_user_with_nothing_pruned_has_nothing_pending():
    store = ConversationStore(max_messages_per_user=50)
    store.append("u", "user", "hi")
    assert store.take_pending_summary("u") == []
    assert store.take_pending_summary("nobody") == []


# ── 2. the note persists, and is never a turn ───────────────────────────────

def test_the_summary_reaches_the_prompt_and_survives_a_restart(tmp_path):
    path = tmp_path / "conv.jsonl"
    store = ConversationStore(persist_path=path)
    store.append("u", "user", "hello")
    store.set_summary("u", "The user prefers small positions and asked about SOL.")
    assert "small positions" in store.build_context_prompt("u")

    reloaded = ConversationStore(persist_path=path)
    assert reloaded.get_context("u").summary.startswith("The user prefers")
    assert [m.content for m in reloaded.get_recent("u", limit=10)] == ["hello"], (
        "the note must never come back as something somebody said")
    assert reloaded.get_recent_as_llm_messages("u", limit=10) == [
        {"role": "user", "content": "hello"}]


def test_the_last_note_wins_and_compaction_keeps_only_it(tmp_path):
    path = tmp_path / "conv.jsonl"
    store = ConversationStore(persist_path=path, max_messages_per_user=3)
    store.set_summary("u", "first")
    store.set_summary("u", "second")
    assert ConversationStore(persist_path=path).get_context("u").summary == "second"
    # Force a compaction: more raw rows than the threshold.
    store.COMPACT_THRESHOLD_LINES = 2
    for i in range(6):
        store.append("u", "user", f"m{i}")
    store._maybe_compact()
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    notes = [r for r in rows if r["role"] == "summary"]
    assert [n["content"] for n in notes] == ["second"]
    assert ConversationStore(persist_path=path).get_context("u").summary == "second"


def test_an_empty_summary_clears_the_note(tmp_path):
    path = tmp_path / "conv.jsonl"
    store = ConversationStore(persist_path=path)
    store.set_summary("u", "wrong note")
    store.set_summary("u", "")
    assert store.get_context("u").summary == ""
    assert "Previous conversation summary" not in store.build_context_prompt("u")
    assert ConversationStore(persist_path=path).get_context("u").summary == ""


def test_the_note_is_bounded():
    store = ConversationStore()
    store.set_summary("u", "x" * 5000)
    assert len(store.get_context("u").summary) == ConversationStore.SUMMARY_MAX_CHARS


# ── 3. the handler folds the queue into the note ────────────────────────────

def _stub(store):
    return SimpleNamespace(conversations=store,
                           _SUMMARY_SYSTEM_PROMPT=H._SUMMARY_SYSTEM_PROMPT)


@pytest.fixture(autouse=True)
def _reset_byok():
    BYOK.reset()
    yield
    BYOK.reset()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.GROK, api_key="k",
                                   model="grok-4.3"))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())


def _pruned_store():
    store = ConversationStore(max_messages_per_user=2)
    store.append("u", "user", "I only trade small, 1% risk")
    store.append("u", "assistant", "Noted.")
    store.append("u", "user", "what about SOL?")
    store.append("u", "assistant", "SOL looks choppy.")
    return store


def test_pending_turns_are_folded_into_the_note(configured, monkeypatch):
    seen = {}

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        seen["system"] = system_prompt
        seen["user"] = user_prompt
        return "The user trades small at 1% risk and asked about SOL."

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    store = _pruned_store()
    assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is True
    assert store.get_context("u").summary.startswith("The user trades small")
    assert "1% risk" in seen["user"] and "Existing note: (none)" in seen["user"]
    assert "never keep a price" in seen["system"]
    assert store.take_pending_summary("u") == [], "the queue was consumed"


def test_the_existing_note_is_offered_for_merging(configured, monkeypatch):
    prompts = []

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        prompts.append(user_prompt)
        return "merged"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    store = _pruned_store()
    store.set_summary("u", "Prefers ETH.")
    asyncio.run(H._summarize_if_due(_stub(store), "u"))
    assert "Existing note:\nPrefers ETH." in prompts[0]
    assert store.get_context("u").summary == "merged"


def test_nothing_pending_means_no_call(configured, monkeypatch):
    async def _complete(*a, **kw):
        raise AssertionError("must not be called")

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    store = ConversationStore()
    store.append("u", "user", "hi")
    assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is False


def test_no_model_configured_gives_the_turns_back(monkeypatch):
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.OPENAI, api_key=""))
    store = _pruned_store()
    assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is False
    assert store.get_context("u").summary == ""
    assert len(store.take_pending_summary("u")) == 2, "nothing was lost"


def test_a_failed_call_gives_the_turns_back(configured, monkeypatch):
    async def _complete(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    store = _pruned_store()
    assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is False
    assert len(store.take_pending_summary("u")) == 2


def test_an_empty_note_is_not_written(configured, monkeypatch):
    async def _complete(*a, **kw):
        return "   "

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    store = _pruned_store()
    store.set_summary("u", "keep me")
    assert asyncio.run(H._summarize_if_due(_stub(store), "u")) is False
    assert store.get_context("u").summary == "keep me"


def test_a_store_without_the_queue_is_left_alone():
    """The stand-ins other suites use have no queue; the fold is a no-op."""
    plain = SimpleNamespace(conversations=SimpleNamespace())
    assert asyncio.run(H._summarize_if_due(plain, "u")) is False


def test_both_surfaces_schedule_the_fold_after_a_reply():
    """Source-level: the fold is reachable from both chat paths. A method
    nothing calls is the empty-summary defect again, one layer up."""
    import inspect

    from bot.web import user_gateway
    tg = inspect.getsource(H._handle_message)
    web = inspect.getsource(user_gateway._chat_turn)
    assert "_summarize_if_due" in tg
    assert "_summarize_if_due" in web
