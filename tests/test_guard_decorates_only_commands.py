"""`@guard` on a helper breaks every call to it, and /news was broken for a month.

`guard(...)` wraps a handler as `(self, update, ctx, ...)` and runs the auth
gate before the body. Put on a COMMAND that is what it is for. Put on
`_news_digest_text(self)` -- a text helper shared by /news and the free-text
"news" intercept -- it turned every `self._news_digest_text()` into

    TypeError: _news_digest_text() missing 2 required positional arguments

which the global error handler answered with "Something broke on my end".
Two surfaces, one cause, since 2026-07-31. Found by driving every command
against a failing engine: /news was the one that raised a TypeError instead
of the engine's own error.

The decorator is gone from the helper (both callers already sit behind the
gate). This pins the shape: a guard may only decorate a method whose
signature can receive (update, ctx).
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.skills.telegram_handler import TelegramHandler

SRC = Path(__file__).resolve().parent.parent / "bot" / "skills" / "telegram_handler.py"


def _guarded_defs():
    """(name, params) for every method decorated with @guard(...) in the handler class."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                call = d if isinstance(d, ast.Call) else None
                name = (call.func.id if call and isinstance(call.func, ast.Name)
                        else d.id if isinstance(d, ast.Name) else None)
                if name == "guard":
                    out.append((node.name, [a.arg for a in node.args.args]))
    return out


def test_every_guard_decorates_a_method_that_can_take_update_and_ctx():
    bad = [(n, p) for n, p in _guarded_defs() if len(p) < 3]
    assert bad == [], ("@guard wraps a method as (self, update, ctx); these cannot receive them "
                       f"and every call to them raises TypeError: {bad}")


def test_the_news_command_carries_the_gate_the_helper_had():
    """Moved, not removed: /news had no gate of its own -- the author put it on
    the wrong def. Dropping the decorator without re-homing it would have
    made /news the one ungated command."""
    guarded = {n for n, _p in _guarded_defs()}
    assert "_cmd_news" in guarded, "/news must be gated -- the guard that sat on its helper belongs here"


def test_the_news_helper_takes_only_self_again():
    sig = inspect.signature(TelegramHandler._news_digest_text)
    assert list(sig.parameters) == ["self"], f"decorated again? signature is {sig}"


@pytest.mark.asyncio
async def test_news_command_answers_with_the_digest_not_a_type_error(monkeypatch):
    """Drive the real /news on a bare host whose helper reads are faked."""
    sent = []
    h = TelegramHandler.__new__(TelegramHandler)

    async def _send(update, text, *a, **k):
        sent.append(str(text))

    async def _guard(update, command="", ctx=None):
        return True
    h._send = _send
    h._guard = _guard
    h._lang = lambda u: "en"
    h.engine = SimpleNamespace(live_executor=None, _news_radar=None)
    monkeypatch.setenv("NEWS_RADAR_ENABLED", "false")
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1, first_name="op"),
                             effective_chat=SimpleNamespace(id=1), message=SimpleNamespace(text="/news"))
    await h._cmd_news(update, SimpleNamespace(args=[]))
    assert sent, "/news must answer"
    assert "Something broke" not in sent[-1]


# ── the guarded set is a ratchet ─────────────────────────────────────────────

BASELINE = Path(__file__).resolve().parent / "guarded_commands_baseline.txt"


def _baseline():
    return {ln.strip() for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def test_no_command_has_lost_its_guard():
    """The slip this catches: a helper inserted between `@guard(...)` and the
    `def` it belonged to. The decorator lands on the helper (the test above
    catches that half) and the COMMAND is left open (this half). Tonight it
    happened to /status on the way to fixing /news."""
    guarded = {n for n, _p in _guarded_defs() if n.startswith("_cmd_")}
    lost = sorted(_baseline() - guarded)
    assert lost == [], (f"commands that lost their @guard: {lost} -- an auth regression, "
                        "or edit the baseline in the same commit")


def test_a_newly_guarded_command_is_recorded():
    guarded = {n for n, _p in _guarded_defs() if n.startswith("_cmd_")}
    new = sorted(guarded - _baseline())
    assert new == [], (f"newly guarded commands not in tests/guarded_commands_baseline.txt: {new} "
                       "-- record them in the same commit")
