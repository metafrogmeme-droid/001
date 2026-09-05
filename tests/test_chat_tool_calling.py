"""The chat model can READ before it answers, and can reach nothing else.

The chat prompt said "You cannot run a tool from this chat", and it was true:
a typed sentence reached a skill only through a regex scoring 0.8, and
everything the regexes missed went to a model with no tools, which answered
account questions from memory. `bot/nlp/chat_tools.py` and
`llm_complete_with_tools` change that — and the point of this file is that
they change ONLY that.

Three properties, each asserted against the real tables rather than a copy:

1. EVERY tool is a skill the permission table already names, `halt` is never
   offered, and a role that lacks a permission is not shown the tool. The
   model is handed a subset of what a typed sentence could reach — never more.
2. The loop is honest about what ran. A name the model invents is refused
   without reaching the executor; a tool that raises or times out is reported
   as such, never as an empty result; every call is recorded in the
   conversation store in the shape `skill_memory` writes for regex-dispatched
   skills.
3. `_llm_chat` takes the tool path only when a registry and a user store are
   really there, so every existing suite that calls it with a SimpleNamespace
   stand-in still exercises the tool-less path it was written for.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm import provider as prov
from bot.llm.provider import BYOK, TOOL_RESULT_MAX_CHARS, LLMConfig, LLMProvider, llm_complete_with_tools
from bot.nlp import chat_tools
from bot.skills.skill_permissions import DANGEROUS_SKILLS, SKILL_PERMISSION, WEB_CHAT_SKILLS
from bot.skills.telegram_handler import _CHAT_NO_TOOLS_RULE, _CHAT_TOOLS_RULE
from bot.skills.telegram_handler import TelegramHandler as H


def _run(coro):
    return asyncio.run(coro)


# ── 1. what may be offered ──────────────────────────────────────────────────

def test_every_chat_tool_is_a_permissioned_skill():
    """Derived, not invented: a tool the permission table does not name would
    be reachable from chat by no other route, which is the defect the table
    exists to prevent."""
    for tool in chat_tools.CHAT_TOOLS:
        assert tool.name in SKILL_PERMISSION, tool.name
        assert tool.name not in DANGEROUS_SKILLS, tool.name


def test_halt_can_never_be_a_tool():
    assert "halt" in SKILL_PERMISSION, "the premise of this test moved"
    assert "halt" not in {t.name for t in chat_tools.CHAT_TOOLS}


def test_heavy_skills_are_not_offered():
    """Full analysis, backtests and deep scans have their own surfaces with a
    card, a keyboard and a tier gate; inside a chat turn they would time out."""
    offered = {t.name for t in chat_tools.CHAT_TOOLS}
    for heavy in ("analyze_asset", "run_backtest", "deepscan", "pro_scan",
                  "optimize", "run_strategy", "walk_forward"):
        assert heavy not in offered, heavy


class _Users:
    """A user store that holds a fixed set of permissions."""

    def __init__(self, held: set[str], stale: set[str] = frozenset()):
        self.held = set(held)
        self.stale = set(stale)

    def permission_denial(self, uid, perm):
        if perm in self.stale:
            return "stale_session"
        return None if perm in self.held else "role"

    def get(self, uid):
        return {"role": "viewer"}


def _no_tier_gate(monkeypatch):
    from bot.token import tier_gate
    monkeypatch.setattr(tier_gate, "check_user", lambda users, uid, f: (True, "ok"))


def test_a_role_without_the_permission_is_not_shown_the_tool(monkeypatch):
    _no_tier_gate(monkeypatch)
    users = _Users({"portfolio", "risk"})
    names = {t.name for t in chat_tools.tools_for(users, "u1")}
    assert "get_portfolio" in names
    assert "check_risk" in names
    assert "costs" not in names
    assert "scan_market" not in names


def test_a_stale_session_withholds_the_tool(monkeypatch):
    _no_tier_gate(monkeypatch)
    users = _Users({"portfolio"}, stale={"portfolio"})
    assert chat_tools.tools_for(users, "u1") == []


def test_web_surface_is_the_narrower_set(monkeypatch):
    """`trade_journal` is reachable from Telegram free text and not from the
    web (WEB_CHAT_SKILLS says so); the tool set follows the same table."""
    _no_tier_gate(monkeypatch)
    users = _Users(set(SKILL_PERMISSION.values()))
    tg = {t.name for t in chat_tools.tools_for(users, "u1", surface="telegram")}
    web = {t.name for t in chat_tools.tools_for(users, "u1", surface="web")}
    assert "trade_journal" in tg
    assert "trade_journal" not in web
    assert web <= WEB_CHAT_SKILLS
    assert "halt" not in tg and "halt" not in web


def test_an_unreadable_role_store_offers_nothing(monkeypatch):
    _no_tier_gate(monkeypatch)

    class _Broken:
        def permission_denial(self, uid, perm):
            raise RuntimeError("store unreadable")

    assert chat_tools.tools_for(_Broken(), "u1") == []
    assert chat_tools.tools_for(None, "u1") == []
    assert chat_tools.tools_for(_Users({"portfolio"}), "") == []


def test_the_tier_gate_verdict_is_honoured(monkeypatch):
    from bot.token import tier_gate
    monkeypatch.setattr(tier_gate, "check_user",
                        lambda users, uid, f: (f != "patterns", "insufficient"))
    users = _Users(set(SKILL_PERMISSION.values()))
    names = {t.name for t in chat_tools.tools_for(users, "u1")}
    assert "patterns" not in names
    assert "get_portfolio" in names


# ── 2. the loop: OpenAI-compatible shape ────────────────────────────────────

class _Usage:
    def __init__(self, i, o):
        self.prompt_tokens = i
        self.completion_tokens = o


def _oai_tool_call(cid, name, arguments):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=arguments))


def _oai_response(content=None, tool_calls=None, usage=(10, 5)):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                           usage=_Usage(*usage))


class _OaiClient:
    """Scripted responses; records every request it was sent."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.requests.append(kw)
        return self._responses.pop(0)


def _cfg(provider=LLMProvider.GROK, model="grok-4.3"):
    return LLMConfig(provider=provider, api_key="k", model=model, timeout_seconds=5.0)


TOOLS = [{"name": "get_portfolio", "description": "d", "parameters": {"type": "object", "properties": {}}},
         {"name": "whynot", "description": "d",
          "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}}}]


def test_openai_loop_runs_the_tool_and_feeds_the_result_back():
    client = _OaiClient([
        _oai_response(tool_calls=[_oai_tool_call("c1", "get_portfolio", "{}")]),
        _oai_response(content="You hold nothing open."),
    ])
    seen = []

    async def executor(name, args):
        seen.append((name, args))
        return "0 open positions, equity $10,000"

    usage, events = {}, []
    out = _run(llm_complete_with_tools(
        client, _cfg(), "sys", "what do I hold?", TOOLS, executor,
        usage_out=usage, events_out=events))
    assert out == "You hold nothing open."
    assert seen == [("get_portfolio", {})]
    # The second request carries the assistant tool call AND the tool result.
    msgs = client.requests[1]["messages"]
    assert msgs[-2]["role"] == "assistant" and msgs[-2]["tool_calls"][0]["id"] == "c1"
    assert msgs[-1] == {"role": "tool", "tool_call_id": "c1",
                        "content": "0 open positions, equity $10,000"}
    # Both rounds were offered tools; both rounds' usage is summed.
    assert "tools" in client.requests[0] and "tools" in client.requests[1]
    assert usage == {"in": 20, "out": 10, "calls": 2}
    assert events == [{"name": "get_portfolio", "args": {}, "ok": True, "ms": events[0]["ms"]}]


def test_openai_loop_parses_json_arguments():
    client = _OaiClient([
        _oai_response(tool_calls=[_oai_tool_call("c1", "whynot", '{"symbol": "BTC"}')]),
        _oai_response(content="done"),
    ])
    seen = []

    async def executor(name, args):
        seen.append((name, args))
        return "gate: liquidity"

    _run(llm_complete_with_tools(client, _cfg(), "s", "q", TOOLS, executor))
    assert seen == [("whynot", {"symbol": "BTC"})]


def test_an_invented_tool_name_never_reaches_the_executor():
    client = _OaiClient([
        _oai_response(tool_calls=[_oai_tool_call("c1", "transfer_funds", "{}")]),
        _oai_response(content="ok"),
    ])
    calls = []

    async def executor(name, args):
        calls.append(name)
        return "x"

    events = []
    _run(llm_complete_with_tools(client, _cfg(), "s", "q", TOOLS, executor,
                                 events_out=events))
    assert calls == []
    result = client.requests[1]["messages"][-1]["content"]
    assert result.startswith("UNAVAILABLE")
    assert events[0]["ok"] is False


def test_bad_json_arguments_do_not_run_the_tool():
    client = _OaiClient([
        _oai_response(tool_calls=[_oai_tool_call("c1", "whynot", "{not json")]),
        _oai_response(content="ok"),
    ])
    calls = []

    async def executor(name, args):
        calls.append(name)
        return "x"

    _run(llm_complete_with_tools(client, _cfg(), "s", "q", TOOLS, executor))
    assert calls == []
    assert client.requests[1]["messages"][-1]["content"].startswith("ERROR")


def test_an_executor_exception_is_reported_not_swallowed():
    client = _OaiClient([
        _oai_response(tool_calls=[_oai_tool_call("c1", "get_portfolio", "{}")]),
        _oai_response(content="I could not read it."),
    ])

    async def executor(name, args):
        raise RuntimeError("driver said: host=10.0.0.1")

    events = []
    out = _run(llm_complete_with_tools(client, _cfg(), "s", "q", TOOLS, executor,
                                       events_out=events))
    assert out == "I could not read it."
    fed = client.requests[1]["messages"][-1]["content"]
    assert fed.startswith("FAILED")
    assert "10.0.0.1" not in fed, "driver text must not reach the model"
    assert events[0]["ok"] is False


def test_an_empty_tool_result_is_named_not_blank():
    client = _OaiClient([
        _oai_response(tool_calls=[_oai_tool_call("c1", "get_portfolio", "{}")]),
        _oai_response(content="ok"),
    ])

    async def executor(name, args):
        return "   "

    _run(llm_complete_with_tools(client, _cfg(), "s", "q", TOOLS, executor))
    assert client.requests[1]["messages"][-1]["content"].startswith("NO OUTPUT")


def test_a_long_tool_result_is_cut_with_an_announced_marker():
    client = _OaiClient([
        _oai_response(tool_calls=[_oai_tool_call("c1", "get_portfolio", "{}")]),
        _oai_response(content="ok"),
    ])
    body = "r" * (TOOL_RESULT_MAX_CHARS + 500)

    async def executor(name, args):
        return body

    _run(llm_complete_with_tools(client, _cfg(), "s", "q", TOOLS, executor))
    fed = client.requests[1]["messages"][-1]["content"]
    assert fed.startswith("[TRUNCATED")
    assert str(TOOL_RESULT_MAX_CHARS) in fed and str(len(body)) in fed


def test_the_final_round_is_made_without_tools_on_offer():
    """A model that keeps asking is made to answer with what it has."""
    ask = _oai_response(tool_calls=[_oai_tool_call("c1", "get_portfolio", "{}")])
    client = _OaiClient([ask, ask, _oai_response(content="fine, here is the answer")])

    async def executor(name, args):
        return "x"

    out = _run(llm_complete_with_tools(client, _cfg(), "s", "q", TOOLS, executor,
                                       max_rounds=2))
    assert out == "fine, here is the answer"
    assert "tools" in client.requests[0] and "tools" in client.requests[1]
    assert "tools" not in client.requests[2], "the closing call must not offer tools"


def test_a_plain_answer_needs_no_second_call():
    client = _OaiClient([_oai_response(content="hi")])

    async def executor(name, args):
        raise AssertionError("must not run")

    assert _run(llm_complete_with_tools(client, _cfg(), "s", "q", TOOLS, executor)) == "hi"
    assert len(client.requests) == 1


# ── 2b. the loop: Anthropic shape ───────────────────────────────────────────

class _AUsage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(cid, name, inp):
    return SimpleNamespace(type="tool_use", id=cid, name=name, input=inp)


def _anth_response(content, stop_reason="end_turn", usage=(10, 5)):
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=_AUsage(*usage))


class _AnthClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kw):
        self.requests.append(kw)
        return self._responses.pop(0)


def test_anthropic_loop_runs_the_tool_and_feeds_the_result_back():
    client = _AnthClient([
        _anth_response([_text("let me look"), _tool_use("t1", "whynot", {"symbol": "ETH"})],
                       stop_reason="tool_use"),
        _anth_response([_text("The gate was liquidity.")]),
    ])
    seen = []

    async def executor(name, args):
        seen.append((name, args))
        return "gate: liquidity"

    usage, events = {}, []
    out = _run(llm_complete_with_tools(
        client, _cfg(LLMProvider.ANTHROPIC, "claude-sonnet-5"), "sys", "why no ETH?",
        TOOLS, executor, usage_out=usage, events_out=events))
    assert out == "The gate was liquidity."
    assert seen == [("whynot", {"symbol": "ETH"})]
    first = client.requests[0]
    assert [t["name"] for t in first["tools"]] == ["get_portfolio", "whynot"]
    assert first["tools"][1]["input_schema"] == TOOLS[1]["parameters"]
    msgs = client.requests[1]["messages"]
    assert msgs[-2]["role"] == "assistant"
    assert msgs[-1] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "gate: liquidity"}]}
    assert usage == {"in": 20, "out": 10, "calls": 2}
    assert events[0]["name"] == "whynot" and events[0]["ok"] is True


def test_anthropic_refusal_still_raises():
    client = _AnthClient([_anth_response([], stop_reason="refusal")])

    async def executor(name, args):
        return "x"

    with pytest.raises(RuntimeError):
        _run(llm_complete_with_tools(
            client, _cfg(LLMProvider.ANTHROPIC, "claude-sonnet-5"), "s", "q",
            TOOLS, executor))


# ── 3. run_tool: the executor the chat hands the loop ───────────────────────

class _Store:
    def __init__(self):
        self.rows = []

    def append(self, uid, role, content, metadata=None):
        self.rows.append((uid, role, content, metadata or {}))


class _Skill:
    def __init__(self, fn):
        self._fn = fn

    async def execute(self, engine, **kw):
        return await self._fn(**kw)


def _handler(skills: dict, engine=None):
    return SimpleNamespace(
        registry=SimpleNamespace(get=lambda n: skills.get(n)),
        conversations=_Store(),
        engine=engine or SimpleNamespace(),
    )


def test_run_tool_records_what_the_tool_said():
    async def portfolio(**kw):
        return "<b>Equity</b> $10,000 &amp; 0 open"

    h = _handler({"get_portfolio": _Skill(portfolio)})
    out = _run(chat_tools.run_tool(h, "u1", "get_portfolio", {}, {"get_portfolio"}))
    assert out == "Equity $10,000 & 0 open"
    uid, role, content, meta = h.conversations.rows[-1]
    assert (uid, role) == ("u1", "assistant")
    assert content.startswith("[get_portfolio] result:")
    assert meta["via"] == "tool_call" and meta["skill"] == "get_portfolio"


def test_run_tool_refuses_a_name_that_was_not_offered():
    ran = []

    async def costs(**kw):
        ran.append(1)
        return "x"

    h = _handler({"costs": _Skill(costs)})
    out = _run(chat_tools.run_tool(h, "u1", "costs", {}, offered={"get_portfolio"}))
    assert out.startswith("UNAVAILABLE")
    assert ran == [] and h.conversations.rows == []


def test_run_tool_normalises_and_validates_the_symbol():
    seen = []

    async def whynot(**kw):
        seen.append(kw)
        return "ok"

    h = _handler({"whynot": _Skill(whynot)})
    _run(chat_tools.run_tool(h, "u1", "whynot", {"symbol": "btc"}, {"whynot"}))
    assert seen[-1]["symbol"] == "BTC/USDT"
    out = _run(chat_tools.run_tool(h, "u1", "whynot", {"symbol": "../etc"}, {"whynot"}))
    assert out.startswith("NOT RUN")
    assert len(seen) == 1, "an invalid symbol must not reach the skill"


def test_run_tool_requires_a_symbol_where_the_skill_does():
    async def ev(**kw):
        raise AssertionError("must not run")

    h = _handler({"check_event_risk": _Skill(ev)})
    out = _run(chat_tools.run_tool(h, "u1", "check_event_risk", {}, {"check_event_risk"}))
    assert out.startswith("NOT RUN") and "symbol" in out


def test_run_tool_timeout_is_recorded_as_a_timeout():
    async def slow(**kw):
        await asyncio.sleep(5)
        return "late"

    h = _handler({"scan_market": _Skill(slow)})
    out = _run(chat_tools.run_tool(h, "u1", "scan_market", {}, {"scan_market"}, timeout=1.0))
    assert "TIMED OUT" in out
    assert "TIMED OUT" in h.conversations.rows[-1][2]
    assert h.conversations.rows[-1][3]["timed_out"] is True


def test_run_tool_failure_is_recorded_and_raised_without_the_driver_text():
    async def boom(**kw):
        raise RuntimeError("host=10.0.0.1 refused")

    h = _handler({"costs": _Skill(boom)})
    with pytest.raises(RuntimeError) as exc:
        _run(chat_tools.run_tool(h, "u1", "costs", {}, {"costs"}))
    assert "10.0.0.1" not in str(exc.value)
    assert h.conversations.rows[-1][2].startswith("[costs] FAILED")


def test_run_tool_ignores_arguments_the_tool_does_not_declare():
    seen = []

    async def portfolio(**kw):
        seen.append(kw)
        return "x"

    h = _handler({"get_portfolio": _Skill(portfolio)})
    _run(chat_tools.run_tool(h, "u1", "get_portfolio", {"symbol": "BTC", "count": 3},
                             {"get_portfolio"}))
    assert seen[-1] == {"user_id": "u1"}


# ── 4. _llm_chat takes the tool path only when it really can ────────────────

class _Conversations:
    def __init__(self):
        self.rows = []

    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False):
        return []

    def append(self, uid, role, content, metadata=None):
        self.rows.append((uid, role, content, metadata or {}))


def _stub(with_tools: bool):
    ns = SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="": (
            "system prompt\n" + _CHAT_NO_TOOLS_RULE + "PERSONALITY"),
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
    )
    if with_tools:
        ns.registry = SimpleNamespace(get=lambda n: object())
        ns.users = _Users(set(SKILL_PERMISSION.values()))
    return ns


@pytest.fixture(autouse=True)
def _reset_byok():
    BYOK.reset()
    yield
    BYOK.reset()


@pytest.fixture(autouse=True)
def _chat_tier(monkeypatch):
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.GROK, api_key="k",
                                   model="grok-4.3"))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="",
                                   chat_tools_enabled=True)))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "resolve_profile_note", lambda note, uid: "")
    _no_tier_gate(monkeypatch)


def test_a_stand_in_without_registry_takes_the_tool_less_path(monkeypatch):
    plain, tooled = [], []

    async def _complete(client, cfg, sys_p, q, **kw):
        plain.append(sys_p)
        return "plain"

    async def _with_tools(*a, **kw):
        tooled.append(1)
        return "tooled"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    monkeypatch.setattr(th_mod, "llm_complete_with_tools", _with_tools)
    out = _run(H._llm_chat(_stub(with_tools=False), "hello", user_id="u1"))
    assert out == "plain" and tooled == []
    assert _CHAT_NO_TOOLS_RULE in plain[0]


def test_a_real_handler_shape_takes_the_tool_path_with_the_tools_rule(monkeypatch):
    plain, tooled = [], []

    async def _complete(*a, **kw):
        plain.append(1)
        return "plain"

    async def _with_tools(client, cfg, sys_p, q, tools, tool_executor, **kw):
        tooled.append((sys_p, [t["name"] for t in tools]))
        kw["events_out"].append({"name": "get_portfolio", "args": {}, "ok": True, "ms": 3})
        kw["usage_out"].update({"in": 120, "out": 40, "calls": 2})
        return "tooled"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    monkeypatch.setattr(th_mod, "llm_complete_with_tools", _with_tools)
    stub = _stub(with_tools=True)
    out, meta = _run(H._llm_chat(stub, "what do I hold?", user_id="u1",
                                 return_meta=True))
    assert out == "tooled" and plain == []
    sys_p, names = tooled[0]
    assert _CHAT_TOOLS_RULE in sys_p
    assert _CHAT_NO_TOOLS_RULE not in sys_p, "one document, one rule about tools"
    assert "get_portfolio" in names and "halt" not in names
    assert meta["tools"] == [{"name": "get_portfolio", "ok": True, "ms": 3}]
    # MEASURED usage was booked, not the character estimate.
    snap = stub.engine.cost.snapshot()
    assert snap.prompt_tokens == 120 and snap.completion_tokens == 40


def test_the_flag_off_restores_the_tool_less_chat(monkeypatch):
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, chat_tools_enabled=False)))
    tooled = []

    async def _complete(client, cfg, sys_p, q, **kw):
        return "plain"

    async def _with_tools(*a, **kw):
        tooled.append(1)
        return "tooled"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    monkeypatch.setattr(th_mod, "llm_complete_with_tools", _with_tools)
    assert _run(H._llm_chat(_stub(with_tools=True), "hi", user_id="u1")) == "plain"
    assert tooled == []


def test_public_chat_never_gets_tools(monkeypatch):
    tooled = []

    async def _complete(client, cfg, sys_p, q, **kw):
        return "plain"

    async def _with_tools(*a, **kw):
        tooled.append(1)
        return "tooled"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    monkeypatch.setattr(th_mod, "llm_complete_with_tools", _with_tools)
    stub = _stub(with_tools=True)
    stub._PUBLIC_CHAT_SYSTEM_PROMPT = "public"
    assert _run(H._llm_chat(stub, "hi", user_id="u1", public=True)) == "plain"
    assert tooled == []


def test_a_tool_attempt_gets_more_time_but_never_past_the_deadline(monkeypatch):
    seen = []

    async def _with_tools(client, cfg, *a, **kw):
        seen.append(cfg.timeout_seconds)
        return "ok"

    monkeypatch.setattr(th_mod, "llm_complete_with_tools", _with_tools)
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="",
                                   chat_tools_enabled=True,
                                   chat_deadline_seconds=25.0)))
    _run(H._llm_chat(_stub(with_tools=True), "hi", user_id="u1"))
    assert len(seen) == 1
    # Raised toward CHAT_TOOL_ATTEMPT_SEC (30) and clamped to what is LEFT of
    # the 25s deadline — a few milliseconds less than 25, never more.
    assert 24.0 < seen[0] <= 25.0, seen


def test_measured_usage_is_booked_on_the_plain_path_too(monkeypatch):
    async def _complete(client, cfg, sys_p, q, **kw):
        kw["usage_out"].update({"in": 777, "out": 33, "calls": 1})
        return "plain"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    stub = _stub(with_tools=False)
    _run(H._llm_chat(stub, "hello", user_id="u1"))
    snap = stub.engine.cost.snapshot()
    assert (snap.prompt_tokens, snap.completion_tokens) == (777, 33)


def test_no_usage_reported_falls_back_to_the_estimate_not_zero(monkeypatch):
    async def _complete(client, cfg, sys_p, q, **kw):
        return "a" * 400

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    stub = _stub(with_tools=False)
    _run(H._llm_chat(stub, "hello", user_id="u1"))
    snap = stub.engine.cost.snapshot()
    assert snap.completion_tokens == 100 and snap.prompt_tokens > 0


def test_provider_hands_back_measured_usage_from_a_real_shaped_response():
    """The seam the tests above lean on: `llm_complete` fills `usage_out` from
    the response's own usage, and leaves it empty when there is none."""
    out = {}
    prov._hand_back_usage(out, _oai_response(content="x", usage=(11, 7)))
    assert out == {"in": 11, "out": 7, "calls": 1}
    prov._hand_back_usage(out, SimpleNamespace(usage=None))
    assert out == {"in": 11, "out": 7, "calls": 1}
    none = {}
    prov._hand_back_usage(none, SimpleNamespace())
    assert none == {}
