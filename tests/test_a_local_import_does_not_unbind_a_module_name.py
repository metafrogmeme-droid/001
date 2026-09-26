"""A function-local import must not unbind a name the module already binds.

Python decides a name's scope once per function: an import ANYWHERE in the
body makes that name local to the WHOLE function. `_cmd_portfolio` imported
`from datetime import datetime, timezone` inside the loop over resting limit
orders, so `datetime` was local to the command, and the stats picture a
hundred lines below read `datetime.now(UTC)` from a local that had been bound
only when a limit order was resting. With none, the picture raised
`UnboundLocalError`, the `except` around it logged at debug, and `/portfolio`
fell back to text: the picture the code was written to send reached the
reader only while an order rested. Ruff's F823 does not see it, because the
use sits BELOW the import in source order.

THE RULE. In `bot/` and `scripts/`, a function that imports a name the module
already binds (an import, a def or a class at module level) must do it in the
function's own body above every use, or keep every use inside the same
top-level statement as the import. Zero today, so no baseline: a new instance
fails by name.

What it does not see, stated: it compares top-level statements, so an import
in one branch of an `if` and a use in the other branch of the same `if` is
not flagged; and a name the module binds by plain assignment is not in its
vocabulary.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
    return names


def unbinding_imports(src: str) -> list[str]:
    tree = ast.parse(src)
    module_names = _module_names(tree)
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parent: dict[ast.AST, ast.AST] = {}
        order: list[ast.AST] = []
        stack: list[ast.AST] = [fn]
        while stack:
            n = stack.pop()
            for c in ast.iter_child_nodes(n):
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.Lambda)):
                    continue      # its own scope
                parent[c] = n
                order.append(c)
                stack.append(c)

        def top(n: ast.AST) -> ast.AST:
            while parent[n] is not fn:
                n = parent[n]
            return n

        imports: dict[str, list[ast.AST]] = {}
        for n in order:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    name = (a.asname or a.name).split(".")[0]
                    if name in module_names:
                        imports.setdefault(name, []).append(n)
        for name, imps in imports.items():
            loads = [n for n in order if isinstance(n, ast.Name) and n.id == name
                     and isinstance(n.ctx, ast.Load)]
            direct = [i for i in imps if parent[i] is fn]
            if direct:
                first = min(i.lineno for i in direct)
                bad = [n for n in loads if n.lineno < first]
            else:
                blocks = {top(i) for i in imps}
                bad = [n for n in loads if top(n) not in blocks]
            if bad:
                found.append(f"{fn.name}:{name}")
    return found


def _sources():
    for d in ("bot", "scripts"):
        for p in sorted((ROOT / d).rglob("*.py")):
            yield p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8")


def test_no_function_unbinds_a_module_name_with_a_local_import():
    hits = [f"{rel}::{h}" for rel, src in _sources() for h in unbinding_imports(src)]
    assert not hits, (
        "A local import makes the name local to the WHOLE function; a use "
        "outside the import's branch raises UnboundLocalError:\n  " + "\n  ".join(hits))


PORTFOLIO_SHAPE = '''
from datetime import datetime
def card(limits):
    for lp in limits:
        if lp:
            from datetime import datetime
            age = datetime.now()
    return datetime.now()
'''


@pytest.mark.parametrize("src, expect", [
    (PORTFOLIO_SHAPE, ["card:datetime"]),
    # An import at the top of the body, above every use, binds before them.
    ("import os\ndef f():\n    import os\n    return os.sep\n", []),
    # An import in the body BELOW a use leaves that use unbound.
    ("import os\ndef f():\n    x = os.sep\n    import os\n    return x\n", ["f:os"]),
    # Every use inside the import's own statement is bound when it runs.
    ("import os\ndef f(a):\n    if a:\n        import os\n        return os.sep\n", []),
    # A name the module does not bind is an ordinary local import.
    ("def f(a):\n    if a:\n        import json\n    return 1\n", []),
    # A nested def is its own scope, charged to itself.
    ("import os\ndef f():\n    def g():\n        import os\n        return os\n"
     "    return os.sep\n", []),
    # An aliased import binds its alias.
    ("import os as o\ndef f(a):\n    if a:\n        import os as o\n    return o.sep\n",
     ["f:o"]),
])
def test_the_rule_on_planted_source(src, expect):
    assert unbinding_imports(src) == expect


def test_the_committed_portfolio_shape_raises_as_the_rule_says():
    ns: dict = {}
    exec(compile(PORTFOLIO_SHAPE, "<shape>", "exec"), ns)
    with pytest.raises(UnboundLocalError):
        ns["card"]([])
    ns["card"]([1])     # bound when a limit order was listed
