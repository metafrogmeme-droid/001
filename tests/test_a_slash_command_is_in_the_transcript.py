"""A slash command's turn is in the transcript: the command typed, and what it
replied, captured where it was sent.

``bot/nlp/skill_memory.py`` exists because a turn the user can see and the
model cannot is a hole exactly where the answer was. The routed free-text path
records every command's card (``card_shown_memory``); the website records what
its own intercepts showed (``web_answer_memory``); and a SLASH COMMAND —
``/networth``, ``/help``, ``/scan``, 147 of them — wrote nothing at all, so
``/networth`` followed by "which is biggest?" reached the model with a history
in which nothing had been shown. Nothing in the tree blessed the gap; nobody
had asked.

One leaf, and two decisions worth driving:

- The reply is CAPTURED at the send chokepoint, not asked for. ``_send``
  returns nothing and a command sends zero, one or many messages, so the
  wrapper at the registration loop sets a context variable and the chokepoint
  appends each chunk it DELIVERED. What the user saw is what the model reads,
  a guard's refusal included; a chunk Telegram refused is not recorded; a
  command that replies by another route records that nothing was captured,
  and never that nothing was sent.
- The user turn is the command and the COUNT of its arguments, never the
  arguments: five commands take a secret as theirs, the store is a file on
  disk and the model's prompt, and a list of the safe ones would rot.

Plant the turn, drive the wrapper through the REAL chokepoint, read the
STORE. The halt suite's ``bot`` fixture replaces ``_send`` with a stub, which
is exactly the seam this file is about, so it builds its own handler.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import time
import types
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import CommandHandler

from bot.nlp.conversation_store import ConversationStore
from bot.nlp.fabricated_tool_calls import find_fabricated_marker
from bot.nlp.skill_memory import (
    MEMORY_CAP,
    card_shown_memory,
    command_reply_memory,
    command_turn_text,
    skill_failure_memory,
)
from bot.skills import telegram_handler as th
from bot.skills.command_guard import guard

OPERATOR = 424242


class _Users:
    def __init__(self, admitted: bool):
        self.admitted = admitted

    def get(self, tg_id):
        if not self.admitted:
            return None
        return {"authorized": True, "role": "admin", "name": "Op"}


def _handler(admitted: bool = True):
    """A handler with the one attribute each driven method reaches for — the
    real `_send`, `_send_photo`, `_remembering` and `_remember_command`."""
    h = th.TelegramHandler.__new__(th.TelegramHandler)
    h.conversations = ConversationStore(max_messages_per_user=50, max_users=200)
    h.users = _Users(admitted)
    h._is_allowlisted = lambda update: admitted
    return h


def _update(text: str = "/networth", uid: int = OPERATOR, reply_text=None,
            send_photo=None):
    msg = NS(text=text, reply_text=reply_text or AsyncMock(), chat=NS(id=uid))
    bot = NS(send_photo=send_photo or AsyncMock())
    return NS(effective_user=NS(id=uid, first_name="Op"),
              effective_chat=NS(id=uid), message=msg, callback_query=None,
              get_bot=lambda: bot)


def _ctx(*args):
    return NS(args=list(args), bot=NS(send_message=AsyncMock()))


def _turns(h, uid: int = OPERATOR):
    return [(m.role, m.content) for m in h.conversations.get_recent(str(uid), limit=20)]


# ── the records ──────────────────────────────────────────────────────────

def test_the_user_turn_is_the_command_and_the_count_of_its_arguments():
    assert command_turn_text("networth", 0) == "/networth"
    assert command_turn_text("/networth", 0) == "/networth"
    assert command_turn_text("research", 1) == "/research (1 argument not recorded)"
    assert command_turn_text("setexchange", 3) == "/setexchange (3 arguments not recorded)"


def test_a_captured_reply_is_recorded_as_the_user_saw_it_and_says_who_answered():
    rec = command_reply_memory("networth", ["<b>Net worth</b> $1,200", "row two"])
    assert rec.startswith("[networth] SHOWN by the /networth command"), rec
    assert "Net worth $1,200\nrow two" in rec, "chunks are one reply, tags are spaces"
    assert "no chat tool ran" in rec
    # Anchored to the RESULT marker: this is not a tool the model holds.
    assert "] result:" not in rec


def test_nothing_captured_says_so_and_claims_no_send():
    rec = command_reply_memory("help", [])
    assert rec.startswith("[help] SHOWN, CONTENTS NOT RECORDED"), rec
    assert "no reply from it was captured" in rec
    assert "may have sent nothing" in rec
    # RED HERRING: `card_shown_memory` is the same marker with a claim this
    # record cannot make — that the card "was sent to the user".
    assert "was sent to the user" in card_shown_memory("help")
    assert "was sent to the user" not in rec
    assert command_reply_memory("help", [None, "", "  \n "]) == rec


def test_a_long_reply_announces_the_truncation_with_both_lengths():
    body = "x" * (MEMORY_CAP + 700)
    rec = command_reply_memory("help", [body])
    assert "TRUNCATED" in rec and str(MEMORY_CAP) in rec and str(len(body)) in rec
    assert len(rec) < len(body) + 200


@pytest.mark.parametrize("rec", [
    command_reply_memory("networth", ["$1,200"]),
    command_reply_memory("networth", []),
    command_reply_memory("help", ["x" * (MEMORY_CAP + 1)]),
], ids=["reply", "nothing captured", "truncated"])
def test_the_fabrication_guard_polices_the_new_shape(rec):
    """A model copying "[networth] SHOWN by the /networth command" is claiming
    a command it never ran; the marker word is one the guard already knows."""
    assert find_fabricated_marker(rec) == 0
    assert find_fabricated_marker("Sure:\n" + rec) == 6


# ── the wrapper, through the real chokepoint ─────────────────────────────

@pytest.mark.asyncio
async def test_a_command_turn_is_recorded_with_the_reply_the_chokepoint_delivered():
    h = _handler()

    async def _cmd(update, ctx):
        await h._send(update, "<b>Net worth</b> $1,200")

    await h._remembering("networth", _cmd)(_update(), _ctx())
    turns = _turns(h)
    assert [r for r, _ in turns] == ["user", "assistant"], turns
    assert turns[0][1] == "/networth"
    assert turns[1][1] == command_reply_memory("networth", ["<b>Net worth</b> $1,200"])
    assert "Net worth $1,200" in turns[1][1]


@pytest.mark.asyncio
async def test_the_record_is_dated_like_a_tool_record_and_names_its_door():
    """Both surfaces' readers age a record by `metadata.skill` — the web's
    intercept record carries one, and so must this, or a three-day-old card
    reads as a reading of now."""
    h = _handler()

    async def _cmd(update, ctx):
        await h._send(update, "card")

    await h._remembering("networth", _cmd)(_update(), _ctx())
    msgs = h.conversations.get_recent(str(OPERATOR), limit=5)
    assert msgs[1].is_tool_record()
    assert msgs[1].metadata["skill"] == "networth"
    assert msgs[1].metadata["via"] == "command"
    assert msgs[1].metadata["surface"] == "telegram"
    later = h.conversations.get_recent_as_llm_messages(
        str(OPERATOR), limit=5, now=time.time() + 3 * 86400)
    assert any("3 d ago — as of then, not now" in m["content"] for m in later), later


@pytest.mark.asyncio
async def test_arguments_never_reach_the_store():
    """`/setexchange bitget KEY SECRET`: the store is a file on disk and the
    model's prompt, and the argument is the secret."""
    h = _handler()

    async def _cmd(update, ctx):
        await h._send(update, "✅ Bitget keys stored")

    await h._remembering("setexchange", _cmd)(
        _update(text="/setexchange bitget KEYabc123 SECRETxyz789"),
        _ctx("bitget", "KEYabc123", "SECRETxyz789"))
    turns = _turns(h)
    assert turns[0][1] == "/setexchange (3 arguments not recorded)"
    everything = "\n".join(c for _, c in turns)
    assert "KEYabc123" not in everything and "SECRETxyz789" not in everything
    assert "Bitget keys stored" in turns[1][1]


@pytest.mark.asyncio
async def test_only_delivered_chunks_are_captured():
    """A chunk Telegram refused (HTML and the plain retry both) is not in
    the transcript — the record holds what the user saw."""
    h = _handler()
    calls: list = []

    async def _reply_text(chunk, **kw):
        calls.append(chunk)
        if len(calls) > 1:
            raise RuntimeError("telegram down")

    text = "B" * 10 + "\n" + "A" * 3995   # two chunks: the split is at the newline

    async def _cmd(update, ctx):
        await h._send(update, text)

    await h._remembering("help", _cmd)(_update(reply_text=_reply_text), _ctx())
    rec = _turns(h)[1][1]
    assert "B" * 10 in rec
    assert "A" not in rec.split("):\n", 1)[1], "the refused chunk"


@pytest.mark.asyncio
async def test_a_chunk_delivered_by_the_plain_fallback_is_captured_as_delivered():
    """HTML refused, plain accepted: the transcript holds the plain text the
    user saw, not the markup Telegram rejected."""
    h = _handler()

    async def _reply_text(chunk, parse_mode=None, **kw):
        if parse_mode == "HTML":
            raise RuntimeError("can't parse entities")

    async def _cmd(update, ctx):
        await h._send(update, "<b>Net worth</b> $1,200")

    await h._remembering("networth", _cmd)(_update(reply_text=_reply_text), _ctx())
    assert "Net worth $1,200" in _turns(h)[1][1]


@pytest.mark.asyncio
async def test_a_reply_telegram_refused_entirely_is_nothing_captured_not_a_card():
    h = _handler()

    async def _reply_text(chunk, **kw):
        raise RuntimeError("telegram down")

    async def _cmd(update, ctx):
        await h._send(update, "the card")

    await h._remembering("help", _cmd)(_update(reply_text=_reply_text), _ctx())
    rec = _turns(h)[1][1]
    assert "no reply from it was captured" in rec
    assert "the card" not in rec


@pytest.mark.asyncio
async def test_a_command_that_replies_by_another_route_records_that_nothing_was_captured():
    """Twenty commands reply through the bot object directly; the record
    must not say the command sent nothing, and must not invent a card."""
    h = _handler()

    async def _cmd(update, ctx):
        await ctx.bot.send_message(chat_id=1, text="a report")

    await h._remembering("slippage", _cmd)(_update(text="/slippage"), _ctx())
    turns = _turns(h)
    assert turns[0][1] == "/slippage"
    assert turns[1][1] == command_reply_memory("slippage", [])
    assert "a report" not in turns[1][1]


@pytest.mark.asyncio
async def test_a_photo_card_records_its_caption():
    h = _handler()

    async def _cmd(update, ctx):
        await h._send_photo(update, b"png", "<b>Positions</b> 2 open")

    await h._remembering("positions", _cmd)(_update(text="/positions"), _ctx())
    assert "Positions 2 open" in _turns(h)[1][1]


@pytest.mark.asyncio
async def test_a_refusal_by_the_real_guard_decorator_is_the_reply_recorded():
    """The gate sends its refusal through the same chokepoint, so the model
    reads the refusal the user read — never the card the body would have
    sent, which never ran."""
    h = _handler()

    async def _refuse(update, command, ctx):
        await h._send(update, "⛔ the trader role is needed for /networth")
        return False

    h._guard = AsyncMock(side_effect=_refuse)

    async def _body(self, update, ctx):
        await self._send(update, "<b>Net worth</b> $1,200")

    bound = types.MethodType(guard("networth")(_body), h)
    await h._remembering("networth", bound)(_update(), _ctx())
    rec = _turns(h)[1][1]
    assert "trader role is needed" in rec
    assert "Net worth" not in rec
    assert rec.startswith("[networth] SHOWN by the /networth command")


def test_every_refusal_of_the_real_gate_leaves_through_the_chokepoint():
    """The test above plants a refusing `_guard`; this pins that the REAL one
    refuses the way the plant does — every `return False` in `_guard` sits
    directly under an `await self._send(...)`, so a refusal is always a
    captured reply and never a silent return the record would call
    "nothing captured"."""
    src = textwrap.dedent(inspect.getsource(th.TelegramHandler._guard))
    fn = ast.parse(src).body[0]
    refusals = []
    for node in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            for i, stmt in enumerate(stmts):
                if (isinstance(stmt, ast.Return)
                        and isinstance(stmt.value, ast.Constant)
                        and stmt.value.value is False):
                    refusals.append((stmt.lineno, stmts[i - 1] if i else None))
    assert len(refusals) >= 4, refusals
    for lineno, prev in refusals:
        assert prev is not None and "self._send(" in ast.unparse(prev), \
            f"a refusal at line {lineno} does not leave through _send"


@pytest.mark.asyncio
async def test_a_caller_the_bot_has_not_admitted_leaves_no_transcript():
    """Not admitted, no transcript — the free-text handler's own rule, and
    here it also keeps strangers from evicting admitted users' history."""
    h = _handler(admitted=False)

    async def _cmd(update, ctx):
        await h._send(update, "⛔ access denied")

    await h._remembering("networth", _cmd)(_update(uid=777), _ctx())
    assert h.conversations.get_recent("777", limit=5) == []


@pytest.mark.asyncio
async def test_a_command_that_raises_records_the_failure_and_still_raises():
    """The exception must reach `_on_error` (the user's apology) AND the
    store (the model's record), and the record carries no detail from it."""
    h = _handler()

    async def _cmd(update, ctx):
        await h._send(update, "half a card")
        raise RuntimeError("secret=abc host=10.0.0.1")

    with pytest.raises(RuntimeError):
        await h._remembering("networth", _cmd)(_update(), _ctx())
    turns = _turns(h)
    assert turns[0][1] == "/networth"
    assert turns[1][1] == skill_failure_memory("networth")
    assert "10.0.0.1" not in turns[1][1] and "half a card" not in turns[1][1]


@pytest.mark.asyncio
async def test_the_capture_does_not_leak_out_of_the_command():
    """Outside a command the variable is None: the free-text path, alerts
    and callbacks send through the same chokepoint and must not be recorded
    as a command's reply, and a raise must not leave the list behind."""
    h = _handler()
    assert th._REPLY_CAPTURE.get() is None
    th._capture_reply("stray")                      # a no-op, not an error
    assert th._REPLY_CAPTURE.get() is None

    async def _cmd(update, ctx):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await h._remembering("x", _cmd)(_update(), _ctx())
    assert th._REPLY_CAPTURE.get() is None
    await h._send(_update(), "a free-text reply")
    assert _turns(h)[-1][1] == skill_failure_memory("x"), "nothing recorded after the command"


# ── the wiring: the loop wraps every registered command ──────────────────

class _Builder:
    def __init__(self):
        self.app = _App()

    def __getattr__(self, name):
        def _chain(*a, **k):
            return self
        return _chain

    def build(self):
        return self.app


class _App:
    def __init__(self):
        self.handlers: list = []
        self.bot_data: dict = {}
        self.error_handlers: list = []

    def add_handler(self, handler):
        self.handlers.append(handler)

    def add_error_handler(self, fn):
        self.error_handlers.append(fn)


def _built_handler():
    h = _handler()
    h.engine = NS()
    h._known_commands = []

    async def _networth(update, ctx):
        await h._send(update, "<b>Net worth</b> $1,200")

    h._cmd_networth = _networth
    with patch.object(th, "Application", NS(builder=lambda: _Builder())):
        app = h.build_app()
    return h, app


def test_build_app_wraps_every_registered_command():
    """Driven, not scanned: the loop is faked through the builder and every
    `CommandHandler` it registered carries the wrapper for its own name."""
    h, app = _built_handler()
    cmds = [x for x in app.handlers if isinstance(x, CommandHandler)]
    assert len(cmds) >= 140, len(cmds)
    for x in cmds:
        tag = getattr(x.callback, "__runeclaw_command__", None)
        assert tag in x.commands, (x.commands, tag)
    assert sorted(h._known_commands) == sorted(c for x in cmds for c in x.commands)


@pytest.mark.asyncio
async def test_the_registered_networth_callback_records_its_turn():
    """The registered callback — the object PTB will call — is driven with a
    planted card, and the store holds the turn."""
    h, app = _built_handler()
    cb = next(x.callback for x in app.handlers
              if isinstance(x, CommandHandler) and "networth" in x.commands)
    await cb(_update(), _ctx())
    turns = _turns(h)
    assert turns[0][1] == "/networth"
    assert "Net worth $1,200" in turns[1][1]
    assert turns[1][1].startswith("[networth] SHOWN by the /networth command")


def test_the_routed_free_text_branches_still_record_the_card_marker():
    """The routed path calls the UNWRAPPED methods, outside any capture, so
    it records `card_shown_memory` as before — two doors, one record each,
    and a card is never recorded twice for one turn."""
    src = textwrap.dedent(inspect.getsource(th.TelegramHandler._handle_message))
    assert 'card_shown_memory("networth")' in src
    assert "_remembering(" not in src
