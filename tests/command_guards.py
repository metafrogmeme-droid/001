"""Which permission each Telegram command gates on, by WHATEVER spelling.

`tests/guarded_commands_baseline.txt` exists, in its own header, because "a
guard that silently disappears ... is an auth regression nothing else
notices". Its reader walked `node.decorator_list` for a decorator named
`guard` — and there are TWO spellings in this tree:

    @guard("trade")                                     # 96 commands
    async def _cmd_x(...):

    async def _cmd_trade(...):                          # 7 commands
        if not await self._guard(update, "trade"):
            return

A reader that knows one spelling ACQUITS the other. Driven on 2026-09-17, all
seven in-body ones were absent from the baseline, `_cmd_trade` — the door that
opens a real position — among them, so deleting that line was an auth
regression the ratchet written for exactly that could not see. It is the
`_SLASH_COMMAND` stopping at the underscore, pointed at the auth surface:
COVERAGE OF A SPELLING IS NOT COVERAGE OF THE GUARD.

The PERMISSION travels with the name, because a name-only baseline makes the
weaker claim. "It has some guard" stays true when `trade` is quietly re-spelled
`status`, and `status` is held by `viewer` where `trade` is not — which is the
role `_cmd_trade`'s own F-12 comment says the guard was added to refuse.

Not in scope, deliberately: the 42 commands that gate with an in-body
`_is_admin(...)`. That is a different gate with a different shape (no
permission string to record), and `tests/test_the_income_map_says_who_may_run_a_command.py`
already derives who may run those. Widening this walk to cover them would give
two answers about one question.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.source_scan import handler_sources

BASELINE = Path(__file__).resolve().parent / "guarded_commands_baseline.txt"


def _own_body(node: ast.FunctionDef | ast.AsyncFunctionDef):
    """Every node in `node`'s body, NOT descending into a nested def or lambda.

    `ast.walk` has no such bound; this is the bound spelled out.
    """
    stack = list(node.body)
    while stack:
        cur = stack.pop()
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # Not this function's body, and nothing inside it is either. The
            # first draft skipped such a node as a CHILD and still yielded it
            # from the body, so it descended anyway -- driven, unchanged.
            continue
        yield cur
        stack.extend(ast.iter_child_nodes(cur))


def _decorator_permission(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The permission named by an `@guard("x")` decorator, or None."""
    for d in node.decorator_list:
        call = d if isinstance(d, ast.Call) else None
        if call is None:
            continue
        name = call.func.id if isinstance(call.func, ast.Name) else None
        if name != "guard" or not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        # A computed permission is not a permission this walk can record, and
        # recording the expression's TEXT would put a string that is not a
        # permission into the baseline. Say so rather than guess.
        raise AssertionError(
            f"{node.name}: @guard(...) takes a computed argument "
            f"({ast.unparse(first)!r}); this walk records literals only")
    return None


def _inbody_permission(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The permission named by an in-body `self._guard(update, "x")`, or None.

    Bounded to THIS function's own body. `ast.walk` DESCENDS into a nested
    def, and a guard on an inner helper is not a guard on the command -- the
    first draft of this function said that in the docstring and did not do it,
    which the mutation round caught: driven on a planted `_cmd_demo` whose
    inner helper gates on "admin", the unbounded walk answered "admin". A
    docstring claiming a check the code does not make is the whole of
    `quant_skill._safe_reason`, arriving inside a slice about exactly that.
    """
    found: list[str] = []
    for sub in _own_body(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if not (isinstance(f, ast.Attribute) and f.attr == "_guard"):
            continue
        if not (isinstance(f.value, ast.Name) and f.value.id == "self"):
            continue
        if len(sub.args) < 2:
            continue
        perm = sub.args[1]
        if isinstance(perm, ast.Constant) and isinstance(perm.value, str):
            found.append(perm.value)
    if not found:
        return None
    # One command gating on two different permissions is a question this walk
    # cannot answer, not a value to pick from.
    distinct = sorted(set(found))
    assert len(distinct) == 1, f"{node.name}: gates on {distinct}"
    return distinct[0]


def command_guards() -> dict[str, str]:
    """{`_cmd_name`: the permission it gates on}, both spellings, one reading.

    A command carrying BOTH spellings is an error rather than a precedence
    rule: two gates on one command is a thing to look at, and silently
    preferring either is how the quieter one stops being read.
    """
    out: dict[str, str] = {}
    for path in handler_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("_cmd_"):
                continue
            deco = _decorator_permission(node)
            body = _inbody_permission(node)
            assert not (deco and body), (
                f"{node.name} carries BOTH @guard({deco!r}) and an in-body "
                f"self._guard(..., {body!r}) — decide which gate is the gate")
            perm = deco or body
            if perm is not None:
                out[node.name] = perm
    return out


def baseline() -> dict[str, str]:
    """The recorded `{command: permission}`, read from the baseline file."""
    rows: dict[str, str] = {}
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) == 2, f"expected '<_cmd_name> <permission>', got {line!r}"
        rows[parts[0]] = parts[1]
    return rows
