"""Every REGISTERED Telegram command and the gate it carries — including none.

`guarded_commands_baseline.txt` records what IS guarded, and
`test_the_income_map_says_who_may_run_a_command.py` derives who may run the
`_is_admin` ones. A command with NEITHER is absent from both, so the pair of
ratchets whose own header says "a guard that silently disappears is an auth
regression nothing else notices" cannot see a command that never had one. That
is a FALSE ACQUITTAL BY OMISSION: the quiet direction, which the methods
ratchet records as the one that just sits there.

Driven on 2026-09-18, four registered commands carried no gate of any kind —
`/duel`, `/alpha`, `/session`, `/funding` — each the lone ungated row in its
own catalogue group, all five documented `audience='user'` while `pending`
holds only {start, help, lang}. Two of them spend a live venue fetch per
invocation for a caller the bot has never admitted, with no rate limit either.
`bot/formatters/market_cards.py` had already written this finding down for a
different batch — "the only market commands with no `@guard` at all, so they
bypassed the F-2 allowlist entirely while three of them spend an exchange call
per invocation" — fixed those four, and left these standing. ASK WHICH OTHER
SURFACE MAKES THE SAME CLAIM, applied to a fix's own neighbourhood.

COVERAGE OF A SPELLING IS NOT COVERAGE OF THE GUARD is `command_guards.py`'s
lesson and it says "TWO SPELLINGS, one list". Driven, there are SIX, and
measuring them by hand took three re-runs of the search, each turning up one
more:

    @guard("x")                        the decorator          (baselined)
    self._guard(update, "x")           in-body                (baselined)
    self._is_admin(...)                in-body                (its own test)
    self.users.is_authorized(...)      /share, /mynotes
    self._limiter.allow(uid)           /version
    @require_registered                /me, /sync

So the vocabulary below is hand-written ON PURPOSE, and the direction it fails
in is why that is safe: a SEVENTH spelling reads here as `none`, and a `none`
row must carry a reason or this gate fails. An unknown gate is loud rather than
an acquittal.

THREE BLIND SPOTS, each of which manufactured a false accusation while this was
being measured by hand, and each written into the walk rather than remembered:

  - `...` TYPING STUBS. `callback_handler.py` declares six `_cmd_*` Protocol
    stubs under `TYPE_CHECKING`. Keying on the method NAME alone reported
    /positions, /orders, /performance, /risk, /strategy and /latest_signal as
    ungated while their real definitions carry `@guard`. A declaration is not
    a definition.
  - NOT-FOUND RENDERED AS NO-GATE. `/link`, `/unlink`, `/me` and `/sync` are
    module-level functions in `user_middleware.py`, which `handler_sources()`
    does not reach because it walks the handler's MRO. A probe that printed
    the same thing for "absent" and "found, gateless" accused four commands it
    had never read — this repository's opening rule, inside the instrument
    built to find it. `unresolved` is its own outcome here and it is not a
    pass.
  - A NEW SPELLING. Above.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.source_scan import handler_sources

BASELINE = Path(__file__).resolve().parent / "command_gate_baseline.txt"

# Module-level command functions, which `handler_sources()` cannot reach: it
# is derived from TelegramHandler's MRO, and these are registered as bare
# names rather than bound methods. `command_catalog.py` records the same gap
# on its own side ("four working commands, undocumented and outside 'exact,
# forever'"), so the omission is known and this is where it is repaired.
_EXTRA_SOURCES = (Path("bot/skills/user_middleware.py"),)

# The gate spellings this tree uses. A call or decorator NOT in here reads as
# no gate, which forces a reason into the baseline rather than passing.
_CALL_GATES = {
    "_guard": "guard",
    "_is_admin": "is_admin",
    "_is_admin_id": "is_admin",
    "is_authorized": "is_authorized",
    "_access_state": "access_state",
    "allow": "rate_limit",
}
_DECORATOR_GATES = {"guard": "guard", "require_registered": "require_registered"}


def _is_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A `...` declaration inside a TYPE_CHECKING Protocol, not a definition."""
    body = [s for s in node.body if not (isinstance(s, ast.Expr)
                                         and isinstance(s.value, ast.Constant)
                                         and isinstance(s.value.value, str))]
    return (len(body) == 1 and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and body[0].value.value is Ellipsis)


def registered_commands() -> dict[str, str]:
    """{'/name': '_cmd_name'} for every command `build_app` registers.

    Both registration spellings: `("x", self._cmd_x)` and `("x", _cmd_x)`.
    """
    src = Path("bot/skills/telegram_handler.py").read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Tuple) and len(node.elts) == 2):
            continue
        name, handler = node.elts
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
            continue
        attr = (handler.attr if isinstance(handler, ast.Attribute)
                else handler.id if isinstance(handler, ast.Name) else None)
        if attr and attr.startswith("_cmd_"):
            out[name.value] = attr
    return out


def _definitions() -> dict[str, tuple[ast.AST, str]]:
    """{method name: (node, file)} in MRO order — the FIRST wins, as Python does."""
    out: dict[str, tuple[ast.AST, str]] = {}
    for path in list(handler_sources()) + [p.resolve() for p in _EXTRA_SOURCES]:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except OSError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # `user_middleware` names them `cmd_link`; the registration
            # imports them AS `_cmd_link`.
            for key in (node.name, "_" + node.name):
                if not key.startswith("_cmd_"):
                    continue
                if _is_stub(node) or key in out:
                    continue
                out[key] = (node, Path(path).name)
    return out


def _gates_of(node) -> list[str]:
    kinds: set[str] = set()
    for d in node.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        nm = getattr(f, "id", None) or getattr(f, "attr", None)
        if nm in _DECORATOR_GATES:
            kinds.add(_DECORATOR_GATES[nm])
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            nm = getattr(sub.func, "attr", None)
            if nm in _CALL_GATES:
                kinds.add(_CALL_GATES[nm])
    return sorted(kinds)


def command_gates() -> dict[str, str]:
    """{'/name': 'guard+rate_limit' | 'none' | 'unresolved'}.

    `unresolved` is deliberately NOT folded into `none`: a handler this walk
    could not find is a gate nobody measured, and reporting it as "no gate"
    is the confident negative the whole file is about.
    """
    defs = _definitions()
    out: dict[str, str] = {}
    for cmd, meth in registered_commands().items():
        found = defs.get(meth)
        if found is None:
            out[cmd] = "unresolved"
            continue
        kinds = _gates_of(found[0])
        out[cmd] = "+".join(kinds) if kinds else "none"
    return out


def baseline() -> dict[str, str]:
    """{'/name': gates} as recorded.

    The REASON is not returned. It was, until the guard for it moved off this
    parser and onto the file — for its own good reason ("a parser that
    manufactures a reason defeats an assertion made through it", which the
    mutation round proved) — and a field a function returns that nobody reads
    is the fifth granularity. `reasonless_rows` is where the reason is read.
    """
    rows: dict[str, str] = {}
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, _reason = line.partition("#")
        parts = head.split()
        assert len(parts) == 2, f"expected '/name <gates>', got {line!r}"
        rows[parts[0]] = parts[1]
    return rows


def reasonless_rows(text: str) -> list[str]:
    """Every `none` row in `text` carrying no reason.

    A FUNCTION rather than a loop inside the test, so the rule can be driven
    on planted lines: the real baseline has no reasonless row, so a mutation
    of the rule changes no verdict against the file alone — which is a guard
    reporting coverage it does not have. Same argument as the planted tree the
    unresolved distinction needs.
    """
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, hashmark, why = line.partition("#")
        parts = head.split()
        if parts[1:2] == ["none"] and not (hashmark and why.strip()):
            out.append(parts[0])
    return out
