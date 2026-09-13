"""Every string this product sends a person goes through one scrub.

TWO GAPS, one on each transport.

`_send`'s own comment has called itself "the single chokepoint for all
outbound text" since the F-15 audit, and the DEFAULT path stopped going
through it. `chat_streaming_enabled` defaults True, so the model's answer is
delivered by `TelegramStream.finish()` -> `message.edit_text(...)` and every
provisional delta by `_maybe_edit()`; `finish()` returning True RETURNS above
`_send`. Only skill-result sends and the non-streaming fallback kept the
scrub — so the one reply most likely to echo something back out of the user's
own message was the one reply nobody scrubbed.

And the web never had a chokepoint at all: `_chat_turn` puts the answer
straight into `reply_html`, aiohttp serialises it, `gateway.js::relay` passes
the JSON through byte-for-byte, and the browser's `sanitizeBotHtml` is a
MARKUP allowlist that does nothing to a credential.

WHAT THIS IS NOT. No live credential leak is reachable through the web today
— every web-reachable path carrying driver text scrubs at its own site. This
pins defence in depth, and the argument for it is that "every producer
remembers" is a property no test can check. A chokepoint new code inherits
cannot be forgotten.

The scrub is deliberately the EXISTING vocabulary (`_redact_string`'s named
`key=value` shapes) plus the bot-token pattern `_safe_exc_text` already knew
and the outbound path did not. Widening the vocabulary would be its own
slice: a second vocabulary is a second answer, which is the rule
`honesty_vocabulary.json` exists to state.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

from bot.utils.outbound import reply_safe

#: A key the shared redactor knows, and a bot token it does not.
KEYED = "auth failed: api_key=sk-abcdefghijklmnopqrs"
TOKEN = "call https://api.telegram.org/bot1234567890:AAFvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv/x"


# ── 1. the seam ─────────────────────────────────────────────────────────────

def test_the_named_key_shapes_are_scrubbed():
    out = reply_safe(KEYED)
    assert "sk-abcdefghijklmnopqrs" not in out
    assert "api_key=***REDACTED***" in out


def test_it_knows_the_bot_token_shape_the_outbound_path_did_not():
    """`_redact_string` matches `key=value`; a bot token carries no `=` at
    all. `_safe_exc_text` has scrubbed it since it was written and says so in
    its own docstring — so the EXCEPTION path knew about the worst single
    secret in the process and the general outbound path did not."""
    from bot.utils.logger import _redact_string

    assert "1234567890:AAF" in _redact_string(TOKEN), "the old scrub misses it"
    assert "1234567890:AAF" not in reply_safe(TOKEN)


def test_it_does_not_escape_because_the_text_is_already_the_card():
    """RED HERRING: `_safe_exc_text` escapes and this must not. It runs over
    text that is already markup the surface is supposed to render; escaping
    would print the tags instead of applying them."""
    card = "<b>BTC/USDT</b> · entry <code>63000</code> · 3 &amp; rising"
    assert reply_safe(card) == card


def test_a_clean_reply_is_returned_byte_identical():
    for clean in ("No open positions right now.", "🔎 MARKET SCANNER",
                  "Engine: ACTIVE | PAPER", ""):
        assert reply_safe(clean) == clean


def test_a_fault_returns_the_text_rather_than_breaking_a_chat(monkeypatch):
    import bot.utils.outbound as ob

    def _boom(_s):
        raise RuntimeError("redactor down")

    monkeypatch.setattr(ob, "_redact_string", _boom)
    assert ob.reply_safe("hello") == "hello"


def test_none_and_non_strings_do_not_raise():
    assert reply_safe("") == ""
    assert reply_safe(None) is None


# ── 2. Telegram: the default streamed path ──────────────────────────────────

class _Msg:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text, **kw):
        self.edits.append(text)


def _stream(msg):
    from bot.skills.chat_runtime import TelegramStream
    return TelegramStream(msg)


def test_the_streamed_FINAL_answer_is_scrubbed():
    """The default path. `finish()` returns True above `_send`, so for as
    long as streaming has been on this was the one reply with no scrub."""
    msg = _Msg()
    assert asyncio.run(_stream(msg).finish(f"<b>Done.</b> {KEYED}")) is True
    assert msg.edits, "nothing was sent"
    assert "sk-abcdefghijklmnopqrs" not in msg.edits[-1]
    assert "<b>Done.</b>" in msg.edits[-1], "the markup still renders"


def test_the_tag_stripped_RETRY_is_scrubbed_too():
    """A fallback that skipped the seam would be the same hole one branch
    deeper — and this branch runs precisely when the first edit failed."""
    class _Fussy(_Msg):
        def __init__(self):
            super().__init__()
            self.first = True

        async def edit_text(self, text, **kw):
            if self.first:
                self.first = False
                raise RuntimeError("bad HTML")
            self.edits.append(text)

    msg = _Fussy()
    assert asyncio.run(_stream(msg).finish(f"<b>x</b> {KEYED}")) is True
    assert "sk-abcdefghijklmnopqrs" not in msg.edits[-1]


def test_the_provisional_deltas_are_scrubbed():
    """`_maybe_edit` paints the answer as it arrives; a secret is on screen
    the moment it streams, not only when the turn ends."""
    msg = _Msg()
    st = _stream(msg)
    st._clock = lambda: 10_000.0
    st.text = f"thinking... {KEYED}"
    asyncio.run(st._maybe_edit())
    assert msg.edits, "no provisional edit happened"
    assert "sk-abcdefghijklmnopqrs" not in msg.edits[-1]


def test_the_non_streaming_send_still_scrubs():
    """Driven on the REAL method, not through a fixture.

    Every suite that asks "what did the bot say" uses the halt fixture, and
    that fixture REPLACES `_send` with a stub that appends to a list — so no
    test using it can see the chokepoint at all, and a scrub deleted from
    `_send` would leave every one of them green. The unbound method is called
    here with the smallest update that reaches a send.
    """
    from bot.skills.telegram_handler import TelegramHandler

    sent: list[str] = []

    class _Message:
        async def reply_text(self, text, **kw):
            sent.append(text)
            return NS(message_id=1)

    update = NS(callback_query=None, message=_Message(),
                effective_chat=NS(id=1), effective_user=NS(id=1))
    # `_split_message` is the only other attribute `_send` reaches for; a
    # stand-in `self` carrying just that is enough to drive the real body.
    me = NS(_split_message=lambda text, limit: [text])
    asyncio.run(TelegramHandler._send(me, update, f"<b>hi</b> {KEYED}"))
    assert sent, "nothing was sent"
    assert "sk-abcdefghijklmnopqrs" not in sent[-1]
    assert "<b>hi</b>" in sent[-1]


# ── 3. the web: one middleware, every JSON route ────────────────────────────

def _app_with(handler):
    from aiohttp import web

    from bot.web.user_gateway import outbound_redaction_middleware
    app = web.Application(middlewares=[outbound_redaction_middleware])
    app.router.add_post("/x", handler)
    return app


async def _post(app, path="/x"):
    from aiohttp.test_utils import TestClient, TestServer
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(path)
        return resp.status, await resp.text()


def test_a_json_reply_is_scrubbed_without_the_route_asking():
    """Seventeen `reply_html` returns in `_chat_turn` alone, and a new route
    is the eighteenth. A chokepoint every call site must remember is not one
    — this is the `_fmt_price(None)` rule, at the boundary."""
    from aiohttp import web

    async def handler(_req):
        return web.json_response({"reply_html": f"<b>hi</b> {KEYED}"})

    status, body = asyncio.run(_post(_app_with(handler)))
    assert status == 200
    assert "sk-abcdefghijklmnopqrs" not in body
    assert "<b>hi</b>" in body, "the markup survives"
    assert json.loads(body)["reply_html"], "still valid JSON"


def test_the_scrub_cannot_corrupt_the_json():
    """The replacement carries no quote, backslash or brace, so a body that
    was valid JSON still is. Driven rather than argued."""
    from aiohttp import web

    async def handler(_req):
        return web.json_response({"a": KEYED, "b": [TOKEN], "c": {"d": KEYED}})

    _status, body = asyncio.run(_post(_app_with(handler)))
    parsed = json.loads(body)
    assert "sk-abcdefghijklmnopqrs" not in body
    assert "1234567890:AAF" not in body
    assert set(parsed) == {"a", "b", "c"}


def test_a_clean_body_is_not_rewritten_at_all():
    """RED HERRING: a middleware that re-encodes every response is a cost on
    every request. It touches the body only when the scrub changed it."""
    from aiohttp import web

    async def handler(_req):
        return web.json_response({"reply_html": "No open positions."})

    _status, body = asyncio.run(_post(_app_with(handler)))
    assert json.loads(body) == {"reply_html": "No open positions."}


def test_a_non_json_response_is_left_exactly_as_it_was():
    from aiohttp import web

    async def handler(_req):
        return web.Response(text=KEYED, content_type="text/plain")

    _status, body = asyncio.run(_post(_app_with(handler)))
    assert body == KEYED, "only JSON bodies are rewritten"


def test_a_stream_response_does_not_raise():
    """Its frames are scrubbed at `_sse_frame`, which is where they are
    built. The middleware must not try to read a body that is not there."""
    from aiohttp import web

    async def handler(req):
        resp = web.StreamResponse()
        await resp.prepare(req)
        await resp.write(b"chunk")
        return resp

    status, _body = asyncio.run(_post(_app_with(handler)))
    assert status == 200


def test_the_redactor_wraps_the_auth_gate_it_sits_in_front_of():
    """aiohttp runs middlewares outermost-first, so the order in the app is
    the claim: `secret_middleware`'s own refusal names an env var, and a
    refusal is outbound text like any other."""
    import inspect

    from bot.web import user_gateway as ug
    # `build_gateway`, not `create_app` — the first draft of this assertion
    # named a function that does not exist and failed with AttributeError,
    # which is the loud direction of the mistake this repo keeps recording.
    src = inspect.getsource(ug.build_gateway)
    i_red = src.index("outbound_redaction_middleware")
    i_sec = src.index("secret_middleware")
    assert i_red < i_sec, "the redactor must wrap the gate, not the reverse"


# ── 4. the web's streamed frames ────────────────────────────────────────────

def test_every_sse_frame_is_scrubbed():
    from bot.web.user_gateway import _sse_frame

    for event in ("delta", "tool", "final", "error"):
        raw = _sse_frame(event, {"text": KEYED}).decode()
        assert "sk-abcdefghijklmnopqrs" not in raw, event
        assert raw.startswith(f"event: {event}\n"), raw


def test_an_sse_frame_stays_parseable():
    from bot.web.user_gateway import _sse_frame

    raw = _sse_frame("final", {"reply_html": f"<b>x</b> {KEYED}"}).decode()
    data = raw.split("data: ", 1)[1].rsplit("\n\n", 1)[0]
    assert json.loads(data)["reply_html"].startswith("<b>x</b>")


# ── 5. the copy that had lost its scrubbing ─────────────────────────────────

def test_the_quant_reason_does_what_its_docstring_promised():
    """It said "never a key, never a URL with a token" over a trim and a
    truncation. `_safe_exc_text` does exactly that and always has."""
    from bot.skills.quant_skill import _safe_reason

    out = _safe_reason(RuntimeError(
        "auth failed api_key=sk-abcdefghijklmnop at https://api.x.com/v1?token=zz"))
    assert "sk-abcdefghijklmnop" not in out
    assert "token=zz" not in out
    assert "auth failed" in out, "the operator still learns what happened"


def test_an_empty_exception_still_names_its_class():
    from bot.skills.quant_skill import _safe_reason
    assert _safe_reason(ValueError("")) == "ValueError"


# ── 6. no chat-shaped path bypasses the seam ────────────────────────────────

def test_the_stream_has_no_edit_that_skips_the_seam():
    """A reachability ratchet on the class that WAS the bypass: every
    `edit_text` in `TelegramStream` must be handed scrubbed text."""
    import ast
    import inspect

    from bot.skills.chat_runtime import TelegramStream
    from tests.source_scan import code_only

    src = code_only(inspect.getsource(TelegramStream))
    tree = ast.parse(src)
    edits = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "edit_text"]
    assert len(edits) >= 3, f"only found {len(edits)} edit_text calls"
    # Either the argument is scrubbed at the call, or the name it uses was
    # scrubbed on the way in. Both spellings are honest; a raw literal is not.
    scrubbed_names = {"final_html", "plain"}
    for call in edits:
        arg = call.args[0] if call.args else None
        txt = ast.unparse(arg) if arg is not None else ""
        assert ("reply_safe" in txt
                or any(n in txt for n in scrubbed_names)), txt
    assert "final_html = reply_safe(final_html)" in src
    assert "reply_safe(plain)" in src


