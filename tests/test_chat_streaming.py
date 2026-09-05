"""The reply reaches the reader as the model writes it — and is still checked.

The web drawer faked streaming: a complete answer paced out in word batches.
Telegram sent a thinking phrase and then, seconds later, a second message.
Both now receive the model's text as it is produced, and BOTH replace what
they streamed with the answer that went through `_chat_ret`'s checks —
a fragment is provisional by construction, and the honesty post-processors
(fabricated tool results, stated risk:reward) need the whole text.

Four layers, each pinned here:

1. provider: both SDK shapes stream, hand fragments to the listener, and
   return the same complete text and measured usage they always did;
2. `_llm_chat`: emits attempt / delta / tool events, and only when the flag
   is on;
3. the gateway: /chat/stream answers text/event-stream with the turn's
   events and ONE final frame carrying exactly the JSON the plain route
   returns — for a model answer, a refusal and a validation error alike;
4. Telegram: one message, edited under a rate limit, finished in place.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot.skills.telegram_handler as th_mod
from bot.core.cost import CostTracker
from bot.llm.provider import BYOK, LLMConfig, LLMProvider, llm_complete, llm_complete_with_tools
from bot.skills.telegram_handler import TelegramHandler as H
from bot.skills.telegram_handler import TelegramStream
from bot.web import user_gateway as ug


def _run(coro):
    return asyncio.run(coro)


# ── 1. provider ─────────────────────────────────────────────────────────────

class _Usage:
    def __init__(self, i, o):
        self.prompt_tokens = i
        self.completion_tokens = o


def _chunk(text=None, tool=None, finish=None, usage=None):
    delta = SimpleNamespace(content=text, tool_calls=[tool] if tool else None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
                           usage=usage)


def _tc(index, cid=None, name=None, args=None):
    return SimpleNamespace(index=index, id=cid,
                           function=SimpleNamespace(name=name, arguments=args))


class _OaiStreamClient:
    """`create(stream=True)` answers an async iterator of scripted chunks;
    records every request so the test can see what was asked."""

    def __init__(self, scripts, reject_stream_options=False):
        self._scripts = list(scripts)
        self.requests: list[dict] = []
        self.reject = reject_stream_options
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.requests.append(kw)
        if self.reject and "stream_options" in kw:
            raise RuntimeError("unrecognized request argument: stream_options")
        assert kw.get("stream") is True, "a listener means a streamed request"
        chunks = self._scripts.pop(0)

        async def _gen():
            for c in chunks:
                yield c
        return _gen()


def _cfg(provider=LLMProvider.GROK, model="grok-4.3"):
    return LLMConfig(provider=provider, api_key="k", model=model, timeout_seconds=5.0)


def test_openai_stream_hands_fragments_over_and_returns_the_whole_text():
    client = _OaiStreamClient([[
        _chunk("Hel"), _chunk("lo "), _chunk("there", finish="stop"),
        SimpleNamespace(choices=[], usage=_Usage(12, 3)),
    ]])
    seen = []
    usage = {}
    out = _run(llm_complete(client, _cfg(), "sys", "hi", on_delta=seen.append,
                            usage_out=usage))
    assert out == "Hello there"
    assert seen == ["Hel", "lo ", "there"]
    assert usage == {"in": 12, "out": 3, "calls": 1}
    assert client.requests[0]["stream_options"] == {"include_usage": True}


def test_openai_stream_drops_stream_options_on_a_provider_that_rejects_it():
    client = _OaiStreamClient([[_chunk("ok", finish="stop")]], reject_stream_options=True)
    seen = []
    out = _run(llm_complete(client, _cfg(), "sys", "hi", on_delta=seen.append))
    assert out == "ok" and seen == ["ok"]
    assert "stream_options" in client.requests[0]
    assert "stream_options" not in client.requests[1], "retried without the field"


def test_openai_stream_reassembles_a_tool_call_from_fragments_and_streams_the_answer():
    client = _OaiStreamClient([
        [_chunk(tool=_tc(0, cid="c1", name="whynot", args='{"sym')),
         _chunk(tool=_tc(0, args='bol": "BTC"}'), finish="tool_calls")],
        [_chunk("The gate "), _chunk("was liquidity.", finish="stop")],
    ])
    ran = []

    async def executor(name, args):
        ran.append((name, args))
        return "gate: liquidity"

    seen, tools = [], []
    tools_spec = [{"name": "whynot", "description": "d",
                   "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}}}]
    out = _run(llm_complete_with_tools(
        client, _cfg(), "sys", "why no BTC?", tools_spec, executor,
        on_delta=seen.append, on_tool=lambda n, p, ok: tools.append((n, p, ok))))
    assert out == "The gate was liquidity."
    assert ran == [("whynot", {"symbol": "BTC"})]
    assert seen == ["The gate ", "was liquidity."]
    assert tools == [("whynot", "start", None), ("whynot", "done", True)]
    # The reassembled call was fed back with its id and full arguments.
    msgs = client.requests[1]["messages"]
    assert msgs[-2]["tool_calls"][0] == {
        "id": "c1", "type": "function",
        "function": {"name": "whynot", "arguments": '{"symbol": "BTC"}'}}


def test_a_listener_that_raises_cannot_break_the_reply():
    client = _OaiStreamClient([[_chunk("fine", finish="stop")]])

    def boom(text):
        raise RuntimeError("listener bug")

    assert _run(llm_complete(client, _cfg(), "sys", "hi", on_delta=boom)) == "fine"


class _AnthStream:
    def __init__(self, texts, final):
        self._texts = texts
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def _gen():
            for t in self._texts:
                yield t
        return _gen()

    async def get_final_message(self):
        return self._final


class _AnthClient:
    def __init__(self, streams):
        self._streams = list(streams)
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream, create=self._create)

    def _stream(self, **kw):
        self.requests.append(kw)
        return self._streams.pop(0)

    async def _create(self, **kw):
        raise AssertionError("a listener means the streaming API, not create()")


def test_anthropic_stream_hands_fragments_over_and_returns_the_final_message_text():
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Hello there")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=9, output_tokens=2))
    client = _AnthClient([_AnthStream(["Hel", "lo there"], final)])
    seen, usage = [], {}
    out = _run(llm_complete(client, _cfg(LLMProvider.ANTHROPIC, "claude-sonnet-5"),
                            "sys", "hi", on_delta=seen.append, usage_out=usage))
    assert out == "Hello there"
    assert seen == ["Hel", "lo there"]
    assert usage == {"in": 9, "out": 2, "calls": 1}


def test_anthropic_stream_runs_tools_between_rounds():
    round1 = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name="get_portfolio", input={})],
        stop_reason="tool_use", usage=SimpleNamespace(input_tokens=5, output_tokens=1))
    round2 = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Nothing open.")],
        stop_reason="end_turn", usage=SimpleNamespace(input_tokens=6, output_tokens=2))
    client = _AnthClient([_AnthStream([], round1), _AnthStream(["Nothing ", "open."], round2)])

    async def executor(name, args):
        return "0 open"

    seen, tools = [], []
    out = _run(llm_complete_with_tools(
        client, _cfg(LLMProvider.ANTHROPIC, "claude-sonnet-5"), "sys", "what do I hold?",
        [{"name": "get_portfolio", "description": "d",
          "parameters": {"type": "object", "properties": {}}}], executor,
        on_delta=seen.append, on_tool=lambda n, p, ok: tools.append((n, p))))
    assert out == "Nothing open."
    assert seen == ["Nothing ", "open."]
    assert tools == [("get_portfolio", "start"), ("get_portfolio", "done")]


# ── 2. _llm_chat emits events ───────────────────────────────────────────────

class _Conversations:
    def get_recent_as_llm_messages(self, user_id, limit=8, drop_trailing_user=False):
        return []

    def append(self, *a, **kw):
        pass


def _stub():
    return SimpleNamespace(
        engine=SimpleNamespace(cost=CostTracker(), analyzer=None),
        conversations=_Conversations(),
        _build_chat_system_prompt=lambda user_id, user_name="": "system prompt",
        _is_admin=lambda update: False,
        _note_chat_llm_failure=lambda reason="": None,
    )


@pytest.fixture(autouse=True)
def _reset_byok():
    BYOK.reset()
    yield
    BYOK.reset()


@pytest.fixture
def chat_tier(monkeypatch):
    monkeypatch.setattr(
        th_mod, "resolve_tier_config",
        lambda *a, **kw: LLMConfig(provider=LLMProvider.GROK, api_key="k", model="grok-4.3"))
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ALIBABA_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, api_key="",
                                   chat_streaming_enabled=True)))
    monkeypatch.setattr(th_mod, "create_llm_client", lambda cfg: object())
    monkeypatch.setattr(th_mod, "resolve_profile_note", lambda note, uid: "")


def test_llm_chat_streams_through_the_listener(chat_tier, monkeypatch):
    async def _complete(client, cfg, sys_p, q, **kw):
        await kw["on_delta"]("Hel")
        await kw["on_delta"]("lo")
        return "Hello"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    events = []

    async def on_event(ev):
        events.append(ev)

    out = _run(H._llm_chat(_stub(), "hi", user_id="u1", on_event=on_event))
    assert out == "Hello"
    assert events[0]["type"] == "attempt" and events[0]["provider"] == "grok"
    assert [e for e in events if e["type"] == "delta"] == [
        {"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}]


def test_the_flag_off_means_no_deltas_but_the_turn_still_answers(chat_tier, monkeypatch):
    monkeypatch.setattr(th_mod, "CONFIG", replace(
        th_mod.CONFIG, llm=replace(th_mod.CONFIG.llm, chat_streaming_enabled=False)))
    got = {}

    async def _complete(client, cfg, sys_p, q, **kw):
        got["on_delta"] = kw.get("on_delta")
        return "Hello"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    events = []
    assert _run(H._llm_chat(_stub(), "hi", user_id="u1", on_event=events.append)) == "Hello"
    assert got["on_delta"] is None
    assert [e["type"] for e in events] == ["attempt"]


def test_no_listener_means_no_streaming_request(chat_tier, monkeypatch):
    got = {}

    async def _complete(client, cfg, sys_p, q, **kw):
        got["on_delta"] = kw.get("on_delta")
        return "Hello"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    _run(H._llm_chat(_stub(), "hi", user_id="u1"))
    assert got["on_delta"] is None


def test_a_sync_listener_is_fine_too(chat_tier, monkeypatch):
    async def _complete(client, cfg, sys_p, q, **kw):
        await kw["on_delta"]("x")
        return "x"

    monkeypatch.setattr(th_mod, "llm_complete", _complete)
    events = []
    assert _run(H._llm_chat(_stub(), "hi", user_id="u1", on_event=events.append)) == "x"
    assert {"type": "delta", "text": "x"} in events


# ── 3. the gateway streams the turn ─────────────────────────────────────────

class _GwHandler:
    """A handler whose _llm_chat streams two fragments then answers."""

    def __init__(self, fail=False):
        self.users = SimpleNamespace(get=lambda uid: {"role": "trader", "authorized": True},
                                     get_tier=lambda uid: "pro",
                                     permission_denial=lambda uid, perm: None,
                                     register=lambda *a, **kw: None,
                                     can_trade_live=lambda uid: False)
        self._limiter = SimpleNamespace(allow=lambda uid: True)
        self.intent_router = SimpleNamespace(classify_rules=lambda t: SimpleNamespace(
            matched=False, confidence=0.0, skill="", kwargs={}, is_social=False))
        self.registry = SimpleNamespace(get=lambda n: None)
        self.conversations = SimpleNamespace(append=lambda *a, **kw: None,
                                             get_recent=lambda *a, **kw: [])
        self.fail = fail

    def _allowlist_ids(self):
        return set()

    def _can_trade_live(self, tg):
        return False

    async def _llm_chat(self, q, on_event=None, return_meta=False, public=False, **kw):
        if on_event is not None:
            await on_event({"type": "attempt", "n": 1, "provider": "grok", "model": "grok-4.3"})
            await on_event({"type": "delta", "text": "Hel"})
            await on_event({"type": "delta", "text": "lo"})
        if return_meta:
            return "Hello", {"provider": "grok", "model": "grok-4.3"}
        return "Hello"


@contextlib.asynccontextmanager
async def _client(handler, monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", "s" * 32)
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
    monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
    from bot.web import chat_quota
    monkeypatch.setattr(chat_quota, "quota_enabled", lambda: False)
    engine = SimpleNamespace(_pending_ideas={}, firewall_scan=None)
    app = ug.build_gateway(engine, handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


HDRS = {"X-Gateway-Secret": "s" * 32}


def _frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for raw in text.split("\n\n"):
        if not raw.strip():
            continue
        event, data = "message", None
        for line in raw.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        out.append((event, data))
    return out


async def test_the_stream_route_emits_the_turn_and_one_final_frame(monkeypatch):
    async with _client(_GwHandler(), monkeypatch) as client:
        r = await client.post("/chat/stream", json={"telegram_id": "web:1", "text": "hi"},
                              headers=HDRS)
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/event-stream")
        fr = _frames(await r.text())
    assert [e for e, _ in fr] == ["attempt", "delta", "delta", "final"]
    assert fr[1][1] == {"type": "delta", "text": "Hel"}
    final = fr[-1][1]
    assert final["status"] == 200
    assert final["body"]["reply_html"] == "Hello"
    assert final["body"]["model"] == "grok-4.3"


async def test_a_validation_error_is_a_final_frame_with_its_status(monkeypatch):
    async with _client(_GwHandler(), monkeypatch) as client:
        r = await client.post("/chat/stream", json={"telegram_id": "web:1", "text": ""},
                              headers=HDRS)
        fr = _frames(await r.text())
    assert [e for e, _ in fr] == ["final"]
    assert fr[0][1]["status"] == 400


async def test_the_public_stream_route_carries_no_identity(monkeypatch):
    async with _client(_GwHandler(), monkeypatch) as client:
        r = await client.post("/chat/public/stream", json={"text": "what is runeclaw"},
                              headers=HDRS)
        fr = _frames(await r.text())
    assert [e for e, _ in fr] == ["attempt", "delta", "delta", "final"]
    assert fr[-1][1]["body"] == {"reply_html": "Hello", "intent": "chat"}


async def test_the_plain_route_is_unchanged(monkeypatch):
    async with _client(_GwHandler(), monkeypatch) as client:
        r = await client.post("/chat", json={"telegram_id": "web:1", "text": "hi"}, headers=HDRS)
        assert r.status == 200
        body = await r.json()
    assert body["reply_html"] == "Hello" and body["intent"] == "chat"


# ── 4. Telegram: one message, edited under a rate limit ─────────────────────

class _Msg:
    def __init__(self, fail_after=None):
        self.edits: list[tuple[str, object]] = []
        self.fail_after = fail_after

    async def edit_text(self, text, parse_mode=None, **kw):
        if self.fail_after is not None and len(self.edits) >= self.fail_after:
            raise RuntimeError("Flood control exceeded")
        self.edits.append((text, parse_mode))


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_edits_are_throttled_by_time_and_growth():
    msg, clock = _Msg(), _Clock()
    s = TelegramStream(msg, clock=clock)
    _run(s.on_event({"type": "delta", "text": "short"}))
    assert msg.edits == [], "too little text, too soon"
    clock.t += 2
    _run(s.on_event({"type": "delta", "text": " " + "x" * 60}))
    assert len(msg.edits) == 1 and msg.edits[0][0].endswith(TelegramStream.CARET)
    assert msg.edits[0][1] is None, "provisional text is plain"
    _run(s.on_event({"type": "delta", "text": " " + "y" * 60}))
    assert len(msg.edits) == 1, "inside the interval: no edit"
    clock.t += 2
    # Growth is measured since the LAST EDIT, so the held-back y-text now
    # qualifies as soon as the interval has passed.
    _run(s.on_event({"type": "delta", "text": " z"}))
    assert len(msg.edits) == 2
    _run(s.on_event({"type": "delta", "text": " " + "w" * 60}))
    assert len(msg.edits) == 2, "inside the interval again: no edit"
    clock.t += 2
    _run(s.on_event({"type": "delta", "text": "."}))
    assert len(msg.edits) == 3
    _run(s.on_event({"type": "delta", "text": "."}))
    clock.t += 2
    _run(s.on_event({"type": "delta", "text": "."}))
    assert len(msg.edits) == 3, "two characters since the last edit is not growth"


def test_the_provisional_text_strips_tags_that_may_be_half_written():
    msg, clock = _Msg(), _Clock()
    s = TelegramStream(msg, clock=clock)
    clock.t += 2
    _run(s.on_event({"type": "delta", "text": "<b>Bold</b> and " + "x" * 60 + " <i>ope"}))
    assert "<b>" not in msg.edits[0][0] and "<i>" not in msg.edits[0][0]


def test_a_new_attempt_clears_what_the_last_one_streamed():
    msg, clock = _Msg(), _Clock()
    s = TelegramStream(msg, clock=clock)
    clock.t += 2
    _run(s.on_event({"type": "delta", "text": "x" * 60}))
    _run(s.on_event({"type": "attempt", "n": 2}))
    assert s.text == ""


def test_finish_edits_the_message_in_place_with_html():
    msg = _Msg()
    s = TelegramStream(msg, clock=_Clock())
    assert _run(s.finish("<b>Done</b>")) is True
    assert msg.edits[-1] == ("<b>Done</b>", "HTML")


def test_finish_falls_back_to_plain_when_html_is_refused():
    class _Picky(_Msg):
        async def edit_text(self, text, parse_mode=None, **kw):
            if parse_mode == "HTML":
                raise RuntimeError("can't parse entities")
            self.edits.append((text, parse_mode))

    msg = _Picky()
    assert _run(TelegramStream(msg, clock=_Clock()).finish("<b>Done</b>")) is True
    assert msg.edits[-1] == ("Done", None)


def test_a_failed_edit_turns_streaming_off_and_finish_declines():
    msg, clock = _Msg(fail_after=0), _Clock()
    s = TelegramStream(msg, clock=clock)
    clock.t += 2
    _run(s.on_event({"type": "delta", "text": "x" * 60}))
    assert s.dead is True and msg.edits == []
    assert _run(s.finish("final")) is False, "the caller sends a fresh message"


def test_finish_declines_an_answer_telegram_cannot_hold_in_one_message():
    s = TelegramStream(_Msg(), clock=_Clock())
    assert _run(s.finish("x" * 4001)) is False


def test_no_message_means_nothing_to_edit():
    s = TelegramStream(None)
    _run(s.on_event({"type": "delta", "text": "x" * 100}))
    assert _run(s.finish("y")) is False
