"""The dollar guard books each LLM call at its list price, cache included.

The daily dollar budget is only as true as the price table `CostTracker`
reads. That table was wrong both ways. ``claude-sonnet-5``, which every chat
and analysis tier routes to, was booked at $3/$15 against a $2/$10 list price,
so the guard tripped at two thirds of the spend it was set for. Haiku 4.5 was
booked at $0.80/$4 against $1/$5, the dangerous direction: an under-booked
model trips the guard late. Every current Anthropic model has its own row
now, priced from Anthropic's first-party model table as of 2026-10-06.

And the guard never saw the prompt cache. Both Anthropic call paths mark the
system prompt ``cache_control``. An Anthropic response reports the cache's
reads and writes BESIDE ``input_tokens``, and they bill at their own rates: a
write at 1.25x the input price, a read at a tenth of it (a twentieth on Opus
5.5, a fortieth on Fable 5.1). Only ``input_tokens`` was booked, so a call
that wrote a 20k-token system prompt was booked as the few hundred tokens of
its question.

The plan's E1. Driven on `llm_call_cost`, `CostTracker.record_llm`, the
provider's usage hand-back, and each place that books an Anthropic response:
the analyzer's thesis, its fallback chain, and the chat's reply and fold.
"""
from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.core.analyzer as analyzer_mod
import bot.core.cost as cost_mod
import bot.skills.telegram_handler as th_mod
from bot.core.analyzer import Analyzer
from bot.core.cost import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    LLM_PRICING,
    LLM_PRICING_FAMILIES,
    CostTracker,
    llm_call_cost,
    resolve_llm_price,
)
from bot.llm.provider import (
    BYOK,
    LLMConfig,
    LLMProvider,
    llm_complete,
    llm_complete_with_tools,
)
from bot.llm.usage import cache_kwargs, cache_pair, cache_token_count
from bot.skills.telegram_handler import TelegramHandler as H
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
M = 1_000_000

#: Anthropic's first-party list prices, USD per million tokens, as of
#: 2026-10-06: (input, output, cache read). Written out here rather than read
#: from the table under test, so a wrong row cannot agree with itself.
LIST = {
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-mythos-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-mythos-5": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
    "claude-haiku-4-5-20251001": (1.00, 5.00, 0.10),
}


def _cost(model, inp=0, out=0, read=0, write=0):
    price, exact = resolve_llm_price(model)
    assert price is not None and exact, model
    return llm_call_cost(price, inp, out, read, write)


# ── the table ───────────────────────────────────────────────────────────────


#: Each row is read at a 100k-token prompt, scaled to a million: the short
#: rate card of a model that has two (Haiku 5.5's second starts above 100k).
_AT = 100_000


@pytest.mark.parametrize("model", sorted(LIST))
def test_every_current_anthropic_model_is_booked_at_its_list_price(model):
    i, o, r = LIST[model]
    scale = M / _AT
    assert _cost(model, inp=_AT) * scale == pytest.approx(i)
    assert _cost(model, out=_AT) * scale == pytest.approx(o)
    assert _cost(model, read=_AT) * scale == pytest.approx(r)
    assert _cost(model, write=_AT) * scale == pytest.approx(i * 1.25)


def test_the_model_every_tier_routes_to_is_two_and_ten():
    """The figure the guard trips on: a day of Sonnet 5 at a million tokens
    each way is $12, where the old row booked $18."""
    assert _cost("claude-sonnet-5", inp=M, out=M) == pytest.approx(12.0)


def test_the_multipliers_are_anthropics():
    assert (CACHE_WRITE_MULTIPLIER, CACHE_READ_MULTIPLIER) == (1.25, 0.10)


def _anthropic_rows():
    return {m: p for m, p in LLM_PRICING.items() if m.startswith("claude-")}


@pytest.mark.parametrize("prefix", ["claude-opus-", "claude-sonnet-", "claude-haiku-", "claude-"])
def test_a_family_price_is_at_the_top_of_its_family(prefix):
    """A family price answers an id with no row: a dated snapshot, or an
    older model someone points a tier at. Under-booking such an id would
    let it spend past the guard, so each family is priced at or above every
    row it covers, on both rate cards."""
    [family] = [p for f, p in LLM_PRICING_FAMILIES if f == prefix]
    rows = {m: p for m, p in _anthropic_rows().items() if m.startswith(prefix)}
    assert rows, f"no exact rows under {prefix}: the comparison measures nothing"
    for model, row in rows.items():
        assert family["in"] >= row.get("long_in", row["in"]), model
        assert family["out"] >= row.get("long_out", row["out"]), model


def test_an_unlisted_dated_id_is_booked_by_its_family_not_its_newest_sibling():
    price, exact = resolve_llm_price("claude-opus-4-1-20250805")
    assert not exact and (price["in"], price["out"]) == (15.00, 75.00)


# ── one call's dollars ──────────────────────────────────────────────────────


def test_a_cached_call_books_input_reads_writes_and_output_apart():
    # Sonnet 5: 2k uncached input, 150k read, 20k written, 1k out.
    got = _cost("claude-sonnet-5", inp=2_000, out=1_000, read=150_000, write=20_000)
    want = (2_000 * 2.00 + 1_000 * 10.00 + 150_000 * 0.20 + 20_000 * 2.50) / M
    assert got == pytest.approx(want)
    # The same call with its cache left out: what used to be booked.
    assert _cost("claude-sonnet-5", inp=2_000, out=1_000) < got / 5


def test_a_model_with_its_own_read_price_is_read_at_it():
    # Opus 5.5 reads at a twentieth of its input, not the usual tenth.
    assert _cost("claude-opus-5-5", read=M) == pytest.approx(0.20)
    # A row with no read price of its own reads at a tenth.
    assert _cost("claude-opus-5", read=M) == pytest.approx(0.50)


@pytest.mark.parametrize("inp,read,write,long_card", [
    (100_000, 0, 0, False),        # at the line: the short card
    (100_001, 0, 0, True),
    (40_000, 50_000, 10_000, False),
    (40_000, 50_000, 10_001, True),  # the cache counts toward the length
])
def test_haiku_5_5_bills_a_long_prompt_on_its_second_card(inp, read, write, long_card):
    i, o = (0.50, 2.50) if long_card else (0.10, 0.50)
    want = (inp * i + 1_000 * o + read * i * 0.10 + write * i * 1.25) / M
    assert _cost("claude-haiku-5-5", inp=inp, out=1_000, read=read, write=write) \
        == pytest.approx(want)


# ── the tracker ─────────────────────────────────────────────────────────────


def _audits(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(cost_mod, "audit",
                        lambda log, msg, **kw: seen.append(kw.get("data") or {}))
    return seen


def test_record_llm_books_the_cache_and_says_so(monkeypatch):
    seen = _audits(monkeypatch)
    cost = CostTracker()
    usd = cost.record_llm(model="claude-sonnet-5", prompt_tokens=2_000,
                          completion_tokens=1_000, category="chat",
                          cache_read_tokens=150_000, cache_write_tokens=20_000)
    assert usd == pytest.approx(_cost("claude-sonnet-5", 2_000, 1_000, 150_000, 20_000))
    snap = cost.snapshot()
    assert snap.llm_cost_usd == pytest.approx(usd)
    assert snap.cost_by_category["chat"] == pytest.approx(usd)
    assert seen[-1]["cache_read_tokens"] == 150_000
    assert seen[-1]["cache_write_tokens"] == 20_000


def test_a_call_with_no_cache_books_and_audits_as_before(monkeypatch):
    seen = _audits(monkeypatch)
    usd = CostTracker().record_llm(model="claude-sonnet-5", prompt_tokens=M,
                                   completion_tokens=0)
    assert usd == pytest.approx(2.00)
    assert "cache_read_tokens" not in seen[-1] and "cache_write_tokens" not in seen[-1]


def test_an_unpriced_model_with_a_cache_is_still_unpriced_not_free():
    cost = CostTracker()
    usd = cost.record_llm(model="acme-frontier-9000", prompt_tokens=10,
                          completion_tokens=10, cache_write_tokens=M)
    assert usd == 0.0 and cost.snapshot().unpriced_calls == 1


@pytest.mark.parametrize("value,count", [
    (None, 0), ("12", 0), (True, 0), (-5, 0), (12, 12), (12.0, 12),
])
def test_a_cache_count_is_a_whole_number_or_none_reported(value, count):
    assert cache_token_count(value) == count


# ── reading a response ──────────────────────────────────────────────────────


def _anthropic(text="ok", *, inp=300, out=40, read=None, write=None, stop="end_turn",
               content=None):
    usage = SimpleNamespace(input_tokens=inp, output_tokens=out)
    if read is not None:
        usage.cache_read_input_tokens = read
    if write is not None:
        usage.cache_creation_input_tokens = write
    return SimpleNamespace(
        content=content or [SimpleNamespace(type="text", text=text)],
        stop_reason=stop, usage=usage)


def test_cache_pair_reads_anthropics_two_fields():
    assert cache_pair(_anthropic(read=150_000, write=20_000)) == (150_000, 20_000)
    # A call that used no cache, an OpenAI-shaped count, no usage at all.
    assert cache_pair(_anthropic()) == (0, 0)
    assert cache_pair(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=9))) == (0, 0)
    assert cache_pair(SimpleNamespace()) == (0, 0)


def test_cache_kwargs_hands_record_llm_its_two_arguments():
    assert cache_kwargs({"in": 1, "cache_read": 7, "cache_write": 3}) == \
        {"cache_read_tokens": 7, "cache_write_tokens": 3}
    assert cache_kwargs({}) == {"cache_read_tokens": 0, "cache_write_tokens": 0}


class _Messages:
    def __init__(self, *responses):
        self.calls: list[dict] = []
        self._responses = list(responses)

    async def create(self, **kw):
        self.calls.append(kw)
        return self._responses.pop(0)


class _Client:
    def __init__(self, *responses):
        self.messages = _Messages(*responses)


def _cfg(model="claude-sonnet-5"):
    return LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="sk-ant-test", model=model)


def test_a_plain_call_hands_back_the_cache_beside_the_pair():
    usage: dict = {}
    client = _Client(_anthropic(inp=300, out=40, read=150_000, write=20_000))
    asyncio.run(llm_complete(client, _cfg(), "sys", "user", usage_out=usage))
    assert usage == {"in": 300, "out": 40, "calls": 1,
                     "cache_read": 150_000, "cache_write": 20_000}
    # And the request did mark the system prompt for the cache.
    [sent] = client.messages.calls
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_a_tool_loop_sums_the_cache_over_its_rounds():
    tool_use = SimpleNamespace(type="tool_use", id="t1", name="portfolio", input={})
    client = _Client(
        _anthropic(inp=300, out=20, write=20_000, stop="tool_use", content=[tool_use]),
        # The second round reads the first's write and writes the turn it
        # appended: a key that recurs is summed, not overwritten.
        _anthropic(text="done", inp=350, out=40, read=20_000, write=1_500),
    )

    async def _run_tool(name, args):
        return "no positions"

    usage: dict = {}
    text = asyncio.run(llm_complete_with_tools(
        client, _cfg(), "sys", "user",
        tools=[{"name": "portfolio", "description": "d",
                "parameters": {"type": "object", "properties": {}}}],
        tool_executor=_run_tool, usage_out=usage))
    assert text == "done" and len(client.messages.calls) == 2
    assert usage == {"in": 650, "out": 60, "calls": 2,
                     "cache_read": 20_000, "cache_write": 21_500}


# ── where an Anthropic response is booked ───────────────────────────────────


def test_the_analyzers_thesis_books_the_cache():
    a = Analyzer.__new__(Analyzer)
    a._cost = CostTracker()
    a._book_anthropic_usage(_anthropic(inp=500, out=200, read=150_000, write=20_000),
                            "claude-sonnet-5", "BTC/USDT", "thesis")
    snap = a._cost.snapshot()
    assert snap.cost_by_category["thesis"] == pytest.approx(
        _cost("claude-sonnet-5", 500, 200, 150_000, 20_000))
    assert snap.prompt_tokens == 500


def test_the_analyzers_thesis_books_nothing_for_a_response_with_no_usage():
    a = Analyzer.__new__(Analyzer)
    a._cost = CostTracker()
    a._book_anthropic_usage(SimpleNamespace(content=[]), "claude-sonnet-5", "X", "thesis")
    assert a._cost.snapshot().llm_calls == 0


def _calls(fn, attr):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def test_the_thesis_call_site_books_through_the_reading():
    """The seam is the one place the thesis books an Anthropic response: the
    function that sends the request calls it, and no other ``record_llm`` in
    the analyzer reads ``input_tokens``."""
    tree = ast.parse((ROOT / "bot/core/analyzer.py").read_text())
    fns = [fn for fn in ast.walk(tree)
           if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
    senders = [fn for fn in fns
               if any(isinstance(c.func.value, ast.Attribute)
                      and c.func.value.attr == "messages" for c in _calls(fn, "create"))]
    assert [fn.name for fn in senders] == ["_llm_thesis"]
    assert len(_calls(senders[0], "_book_anthropic_usage")) == 1
    readers = [fn.name for fn in fns if _calls(fn, "record_llm")
               and any(isinstance(n, ast.Constant) and n.value == "input_tokens"
                       for n in ast.walk(fn))]
    assert readers == ["_book_anthropic_usage"]


def _fallback_through_anthropic(monkeypatch, response):
    for env in ("ALIBABA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
                "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _Client(response)
    monkeypatch.setattr(analyzer_mod, "create_llm_client", lambda cfg: client)
    analyzer = Analyzer()
    analyzer._llm_calls_today = 0
    analyzer._cost = CostTracker()
    real = analyzer_mod.CONFIG.llm
    monkeypatch.setattr(analyzer_mod, "CONFIG", SimpleNamespace(llm=SimpleNamespace(
        temperature=real.temperature, max_tokens=real.max_tokens,
        timeout_seconds=real.timeout_seconds, fallback_cost_accounting_enabled=True)))
    signal = SimpleNamespace(symbol="BTC/USDT", volume_spike=False, change_pct_24h=0.0)
    result = asyncio.run(analyzer._try_llm_fallback(
        "prompt text", signal, use_full_model=True, failed_provider="openai",
        is_admin=True))
    [sent] = client.messages.calls
    return analyzer._cost.snapshot(), result, sent["model"]


_THESIS = '{"direction": "LONG", "confidence": 0.7, "reasoning": "ok"}'


def test_the_anthropic_fallback_books_what_was_measured_cache_included(monkeypatch):
    snap, result, model = _fallback_through_anthropic(
        monkeypatch, _anthropic(_THESIS, inp=700, out=90, read=40_000, write=6_000))
    assert result is not None and result["direction"] == "LONG"
    # Measured, not the char-length estimate it used to book.
    assert (snap.prompt_tokens, snap.completion_tokens) == (700, 90)
    assert snap.llm_cost_usd == pytest.approx(_cost(model, 700, 90, 40_000, 6_000))


def test_the_anthropic_fallback_estimates_only_what_was_not_reported(monkeypatch):
    bare = SimpleNamespace(content=[SimpleNamespace(type="text", text=_THESIS)],
                           stop_reason="end_turn")
    snap, _, _ = _fallback_through_anthropic(monkeypatch, bare)
    assert snap.llm_calls == 1
    assert snap.prompt_tokens > 0 and snap.llm_cost_usd > 0


@pytest.fixture
def _anthropic_chat(monkeypatch):
    BYOK.reset()
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY",
                "GROQ_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="k",
                                   model="claude-sonnet-5"))
    monkeypatch.setattr(th_mod, "CONFIG", replace(th_mod.CONFIG, llm=replace(
        th_mod.CONFIG.llm, api_key="", daily_call_limit=500, daily_budget_usd=50.0,
        chat_budget_share=1.0)))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    asked: list[str] = []

    async def _complete(client, cfg, system_prompt, user_prompt, **kw):
        asked.append(cfg.model)
        kw["usage_out"].update({"in": 400, "out": 120, "calls": 1,
                                "cache_read": 30_000, "cache_write": 8_000})
        return "a reply"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    yield asked
    BYOK.reset()


class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False,
                                   token_budget=None):
        return []


def test_a_chat_reply_books_the_cache(_anthropic_chat):
    cost = CostTracker()
    host = SimpleNamespace(
        engine=SimpleNamespace(cost=cost), conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="", surface="telegram": "sp",
        _is_admin=lambda update: True)
    answer = asyncio.run(H._llm_chat(host, "hello", is_admin=True))
    assert answer == "a reply"
    [model] = _anthropic_chat
    assert cost.snapshot().cost_by_category["chat"] == pytest.approx(
        _cost(model, 400, 120, 30_000, 8_000))


def test_a_history_fold_books_the_cache(_anthropic_chat):
    from bot.nlp.conversation_store import ConversationStore
    store = ConversationStore(max_messages_per_user=2)
    for role, text in (("user", "I trade small"), ("assistant", "Noted."),
                       ("user", "SOL?"), ("assistant", "Choppy.")):
        store.append("u", role, text)
    cost = CostTracker()
    host = SimpleNamespace(conversations=store, engine=SimpleNamespace(cost=cost),
                           _SUMMARY_SYSTEM_PROMPT=H._SUMMARY_SYSTEM_PROMPT)
    assert asyncio.run(H._summarize_if_due(host, "u"))
    [model] = _anthropic_chat
    assert cost.snapshot().cost_by_category["chat"] == pytest.approx(
        _cost(model, 400, 120, 30_000, 8_000))


# ── the lifetime the write price assumes ────────────────────────────────────


def test_every_cache_mark_in_the_bot_is_the_five_minute_lifetime():
    """`CACHE_WRITE_MULTIPLIER` is the five-minute write price. A one-hour
    mark (``"ttl": "1h"``) writes at twice the input price, which this table
    would under-book; one added means pricing it first."""
    marks = 0
    for path in sorted((ROOT / "bot").rglob("*.py")):
        code = code_only(path.read_text(encoding="utf-8"))
        marks += code.count('"cache_control"')
        assert '"ttl"' not in code and "'ttl'" not in code, path
    assert marks >= 2, "found no cache marks: the scan measures nothing"
