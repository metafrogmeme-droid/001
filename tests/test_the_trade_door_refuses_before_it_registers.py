""""/trade" gates in the BODY, and its only pin asserted that a string exists.

`_cmd_trade` opens a real position. It does not carry `@guard("trade")`; it
calls the gate inline:

    if not await self._guard(update, "trade"):
        return

and its own comment records what the gate's absence cost (audit F-12): "the
prior inline `authorized`-only check skipped all three, letting any authorized
user (incl. a viewer role) queue trades". Driven here: `viewer` holds `status`
and does NOT hold `trade`, so that sentence is about a real refusal.

The only thing pinning it was
`test_audit_v7_fixes.py::test_trade_routes_through_guard`, which asserts
`'self._guard(update, "trade")' in src`. Driven on 2026-09-17, three ways to
break the gate:

    guard DELETED                          -> 1 failed (that scan). caught.
    `if False and not await self._guard(`  -> 7 passed. NOT CAUGHT.
    guard moved BELOW register_manual_idea -> 7 passed. NOT CAUGHT.

The literal survives both, because the assertion asks whether a STRING EXISTS,
not whether the gate RUNS or runs FIRST. CLAUDE.md records that exact pair --
"the mutation kept the literal and inverted the branch ... Neither could see
reachability, which is the one thing they were being asked about" -- and, for
the third, "a refusal that has already done the read it exists to refuse is
invisible from the response".

So this drives it. The scan stays where it is: it is not wrong, it is
narrower than the claim read off it, and it names the deletion case loudly.
"Do not convert wholesale" -- a scan that locks wiring is fine once the
behaviour is covered, which is what this file is.

BOTH ARMS ARE THE POINT. A refusal assertion alone cannot tell "the gate
refused" from "the method does nothing" -- a `return` on line one passes it.
The allow arm is what makes the refusal arm mean something, which is the
asymmetric-fixture rule this repo records about the operator/caller books.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.skills.telegram_handler import TelegramHandler

ROOT = Path(__file__).resolve().parent.parent

#: A line the parser takes. `tests/test_the_trade_help_example_is_one_the_parser_takes.py`
#: exists because the help's own example did not; this one is driven below.
GOOD = "/trade buy SOL 71.42 sl 70.05 tp 76.42"


def _host(monkeypatch, *, allow: bool, text: str = GOOD):
    """A stand-in `self` carrying only what `_cmd_trade` reaches for.

    Not the halt suite's `bot` fixture: that one replaces `_send` with a stub
    AND `CONFIG` with a MagicMock, under which every boolean flag reads truthy
    -- CLAUDE.md records both traps.
    """
    sent: list[str] = []
    asked: list[str] = []
    registered: list[object] = []

    async def _guard(update, command="", ctx=None):
        asked.append(command)
        return allow

    async def _send(update, text_, **kw):
        sent.append(text_)

    import bot.skills.manual_trade as mt

    def _register(engine, idea, margin_usd=None):
        registered.append(idea)
    monkeypatch.setattr(mt, "register_manual_idea", _register)

    msg = SimpleNamespace(text=text)
    update = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=4242))

    host = SimpleNamespace(
        engine=SimpleNamespace(), sent=sent, asked=asked, registered=registered,
        update=update,
        _guard=_guard, _send=_send,
        _get_tg_id=lambda u: "4242", _lang=lambda u: "en",
    )
    for name in ("_cmd_trade", "_parse_manual_trade"):
        setattr(host, name, getattr(TelegramHandler, name).__get__(host))
    return host


@pytest.mark.asyncio
async def test_a_refused_caller_registers_nothing_and_is_not_shown_a_card(monkeypatch):
    """The gate runs, and it runs BEFORE anything is registered.

    `registered == []` is what the `if False:` mutation breaks and what the
    moved-below mutation breaks; neither is visible from the source literal.
    """
    h = _host(monkeypatch, allow=False)
    await h._cmd_trade(h.update, None)

    assert h.asked == ["trade"], "the gate was asked, and asked for `trade`"
    assert h.registered == [], (
        "a refused caller must not reach register_manual_idea -- this is the "
        "assertion the literal scan cannot make, and the one the guard moved "
        "below registration would fail")
    assert h.sent == [], "and is shown no trade card"


@pytest.mark.asyncio
async def test_an_allowed_caller_does_register_so_the_refusal_means_something(monkeypatch):
    """Without this arm the test above passes against a `_cmd_trade` that does
    nothing at all, which is a guard that proves nothing."""
    h = _host(monkeypatch, allow=True)
    await h._cmd_trade(h.update, None)

    assert h.asked == ["trade"]
    assert len(h.registered) == 1, "an allowed caller reaches registration"
    assert h.sent and "71.42" in h.sent[0], "and is shown the card for their own line"


@pytest.mark.asyncio
async def test_the_gate_is_asked_before_the_message_is_even_parsed(monkeypatch):
    """A refusal on junk input must still be a refusal, not a parse error.

    If the gate moved below the parse, a refused caller would be told what is
    wrong with their trade line -- which is a read the refusal exists to
    prevent, answered anyway.
    """
    h = _host(monkeypatch, allow=False, text="/trade nonsense that cannot parse")
    await h._cmd_trade(h.update, None)

    assert h.asked == ["trade"]
    assert h.sent == [], "no parse complaint reaches a caller who was refused"


def test_the_guard_is_the_first_statement_in_the_body():
    """Driven above; this says WHERE, which a drive cannot.

    The AST, not a literal: a comment mentioning `_guard` satisfies a string
    search, and `code_only()` blanking comments is not enough on its own to
    say the call is the first thing the method does.
    """
    src = (ROOT / "bot" / "skills" / "trading_commands.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "_cmd_trade")
    body = [s for s in fn.body if not (isinstance(s, ast.Expr)
                                       and isinstance(s.value, ast.Constant))]
    # The `if not update.message: return` bail is not a gate and may precede it.
    gate = next(i for i, s in enumerate(body) if "self._guard(" in ast.unparse(s))
    before = [ast.unparse(s) for s in body[:gate]]
    assert all("update.message" in b for b in before), (
        f"only the no-message bail may precede the gate; found {before}")
