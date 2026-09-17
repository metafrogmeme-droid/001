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
import textwrap
from types import SimpleNamespace

import pytest

from bot.skills.telegram_handler import TelegramHandler
from tests.command_guards import _decorator_permission, _inbody_permission, baseline, command_guards
from tests.source_scan import handler_sources


def _guarded_defs():
    """(name, params) for every method decorated with @guard(...) in the
    handler class — across every file that contributes methods to it, so a
    guard on a command that moved into a mixin is still counted and a guard
    that vanished there is still missed."""
    out = []
    for path in handler_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
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

# ── the walk's own rules, on PLANTED trees ──────────────────────────────────
# Both of these survived the first mutation round against the real tree, and
# for the same reason: no command in this repo has a computed `@guard(...)`
# argument or a nested def that gates. A rule the corpus cannot reach is a
# claim that there is a check, so the rule gets a tree where it is the only
# thing in play -- the shape this repo already uses for the methods ratchet.


def _fn(src: str):
    return ast.parse(textwrap.dedent(src)).body[0]


def test_a_guard_on_a_nested_def_is_not_the_commands_guard():
    """The first draft claimed this bound in a docstring and did not make it.

    `ast.walk` descends into a nested def, so driven, a `_cmd_demo` whose
    inner helper gates on "admin" was recorded as gating on "admin". The
    mutation round is what said so; a docstring claiming a check the code does
    not make is the whole of `quant_skill._safe_reason`.
    """
    nested_only = _fn("""
        async def _cmd_demo(self, update, ctx):
            async def _inner():
                if not await self._guard(update, "admin"):
                    return
            return await _inner()
    """)
    assert _inbody_permission(nested_only) is None

    own_and_nested = _fn("""
        async def _cmd_demo(self, update, ctx):
            if not await self._guard(update, "trade"):
                return
            async def _inner():
                if not await self._guard(update, "admin"):
                    return
    """)
    assert _inbody_permission(own_and_nested) == "trade", (
        "the command's own gate is the command's gate, and the inner one is "
        "not a second opinion about it")


def test_only_selfs_gate_counts_as_the_commands_gate():
    """`other._guard(update, "admin")` is somebody else's gate, not this one.

    Third of the three rules the real tree cannot reach -- no command here
    calls `_guard` on anything but `self` -- so it is driven on a planted tree
    for the same reason as the other two. Without the check, a command that
    delegates to another object's gate would be recorded under THAT object's
    permission, which is a row in the baseline that is about a different gate.
    """
    delegated = _fn("""
        async def _cmd_demo(self, update, ctx):
            if not await other._guard(update, "admin"):
                return
    """)
    assert _inbody_permission(delegated) is None

    mine = _fn("""
        async def _cmd_demo(self, update, ctx):
            if not await self._guard(update, "admin"):
                return
    """)
    assert _inbody_permission(mine) == "admin", (
        "and self's gate still reads, or the refusal above proves nothing")


def test_a_computed_guard_argument_is_refused_rather_than_recorded():
    """`@guard(SOME_CONST)` has no permission this walk can read.

    Recording the expression's TEXT would put a string that is not a
    permission into the baseline, where it would then be compared, printed and
    believed. Refusing is the only honest answer, and `raise` is how the reader
    finds out rather than the baseline quietly growing a row that means
    nothing.
    """
    computed = _fn("""
        @guard(SOME_CONST)
        async def _cmd_demo(self, update, ctx):
            pass
    """)
    with pytest.raises(AssertionError, match="computed argument"):
        _decorator_permission(computed)

    literal = _fn("""
        @guard("trade")
        async def _cmd_demo(self, update, ctx):
            pass
    """)
    assert _decorator_permission(literal) == "trade", (
        "and the ordinary case still reads, or the refusal above proves nothing")


def test_no_command_has_lost_its_guard():
    """The slip this catches: a helper inserted between `@guard(...)` and the
    `def` it belonged to. The decorator lands on the helper (the test above
    catches that half) and the COMMAND is left open (this half). Tonight it
    happened to /status on the way to fixing /news.

    It reads `command_guards()` rather than `_guarded_defs()`, and that is the
    whole of the 2026-09-17 fix: `_guarded_defs` walks `decorator_list`, so
    the SEVEN commands that gate with an in-body `self._guard(update, "...")`
    were acquitted by the ratchet written for exactly this -- /trade among
    them. A reader that knows one spelling acquits the other.
    """
    guarded = set(command_guards())
    lost = sorted(set(baseline()) - guarded)
    assert lost == [], (f"commands that lost their guard: {lost} -- an auth regression, "
                        "or edit the baseline in the same commit")


def test_a_newly_guarded_command_is_recorded():
    new = sorted(set(command_guards()) - set(baseline()))
    assert new == [], (f"newly guarded commands not in tests/guarded_commands_baseline.txt: {new} "
                       "-- record them in the same commit")


def test_no_command_quietly_changed_which_permission_it_gates_on():
    """A name-only baseline makes the weaker claim.

    "It has some guard" stays true when `trade` is re-spelled `status`, and
    driven, `viewer` HOLDS `status` and does not hold `trade` -- which is the
    role `_cmd_trade`'s own F-12 comment says its guard was added to refuse.
    So the permission is recorded beside the name and moves only on purpose.
    """
    now, was = command_guards(), baseline()
    moved = sorted((c, was[c], now[c]) for c in set(now) & set(was) if was[c] != now[c])
    assert moved == [], ("commands whose permission changed (command, recorded, now): "
                         f"{moved} -- weakening one is an auth regression; record it "
                         "in the same commit if it is deliberate")
