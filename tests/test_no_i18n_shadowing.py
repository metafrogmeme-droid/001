"""`/portfolio` died with UnboundLocalError, and the culprit was a loop variable.

`bot/skills/telegram_handler.py` imports the i18n function as `t` at module
level. `_cmd_portfolio` also wrote

    for t in recent:              # L8359
    for t in history[-5:]:        # L8427

Python decides a name is local at COMPILE time, for the whole function, the
moment it sees any binding of it anywhere in that function. So every
`t('some_key', lang)` call ABOVE those loops became a local reference to a
name not yet bound — and raised UnboundLocalError before the loop was ever
reached. The command was dead for every caller.

The failure is data-dependent in a way that hides it. If the loop runs, `t`
ends up bound to the last trade and later calls raise TypeError instead; if
the list is empty, the name is never bound at all. So it presents as
UnboundLocalError, TypeError, or nothing, depending on how many trades exist
— which is why it surfaced right after a restore changed the trade count.

Two notes for whoever extends this:

  * Comprehensions and generator expressions have their OWN scope in Python 3.
    `{s: float(t.get("last")) for s, t in tickers.items()}` does NOT shadow
    anything. A first version of this scan used ast.walk, which ignores that,
    and reported three offenders where there was one — nearly sending someone
    to "fix" two non-bugs. A guard that cries wolf gets ignored, so the walker
    below deliberately refuses to descend into comprehension and nested-function
    scopes.
  * It checks CALLS specifically. Merely reusing a name is fine; the defect is
    a function that both calls a module-level callable and binds that name.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# Module-level callables whose names are short enough to be grabbed as loop
# variables. `t` is the one that has actually happened.
SHADOWABLE = {"t"}

_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _function_scope_nodes(fn):
    """Nodes evaluated in `fn`'s OWN scope.

    Skips comprehension bodies and nested defs, which get their own scopes in
    Python 3 and therefore cannot shadow anything here. A comprehension's
    ITERABLE is still evaluated in the enclosing scope, so it is kept.
    """
    # The seed must be filtered too. `list(fn.body)` pushes a nested `def`
    # straight onto the stack, and only its CHILDREN were being skipped — so a
    # loop inside a nested function counted as a binding in the parent. Caught
    # by test_a_nested_function_is_NOT_flagged below, which is the entire
    # reason those "the scan does not cry wolf" cases exist.
    stack = [n for n in fn.body if not isinstance(n, _NESTED_SCOPES)]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _COMPREHENSIONS):
                for gen in child.generators:
                    stack.append(gen.iter)
                continue
            if isinstance(child, _NESTED_SCOPES):
                continue
            stack.append(child)


def _module_level_names(tree) -> set:
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
    return out & SHADOWABLE


def _offenders(path: pathlib.Path) -> list:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    imported = _module_level_names(tree)
    if not imported:
        return []
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls, binds = [], []
        for node in _function_scope_nodes(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in imported):
                calls.append(node.lineno)
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                    and node.id in imported):
                binds.append(node.lineno)
        if calls and binds:
            found.append((fn.name, min(calls), sorted(binds)))
    return found


PY_FILES = sorted(REPO.joinpath("bot").rglob("*.py"))


class TestNoFunctionShadowsAModuleLevelCallableItCalls:
    def test_no_offenders_anywhere_under_bot(self):
        problems = []
        for path in PY_FILES:
            for fn, call_line, bind_lines in _offenders(path):
                problems.append(
                    f"{path.relative_to(REPO)}::{fn}() calls a shadowable "
                    f"name at L{call_line} and binds it at {bind_lines}")
        assert not problems, (
            "a statement-level `for t in ...` makes t local for the WHOLE "
            "function, so every t(...) call raises UnboundLocalError:\n  "
            + "\n  ".join(problems))

    def test_cmd_portfolio_specifically_is_clean(self):
        """The one that actually broke, pinned by name so a revert is loud."""
        # Every file the handler class is made of: /portfolio lives in the
        # portfolio mixin since the handler split, and a scan of one file
        # would pass here for the wrong reason — the name absent because the
        # function is, not because it is clean.
        from tests.source_scan import handler_sources
        names = [fn for path in handler_sources() for fn, _, _ in _offenders(path)]
        assert "_cmd_portfolio" not in names
        assert any("def _cmd_portfolio(" in path.read_text(encoding="utf-8")
                   for path in handler_sources()), "the pinned command is gone"


class TestTheScanCanSeeTheRealShape:
    """A scan that matches nothing passes for the wrong reason."""

    def _write(self, tmp_path, src):
        p = tmp_path / "mod.py"
        p.write_text(src, encoding="utf-8")
        return p

    def test_it_catches_a_statement_level_for_loop(self, tmp_path):
        p = self._write(tmp_path, '''
from bot.utils.i18n import t

def render(lang, rows):
    out = [t("header", lang)]
    for t in rows:
        out.append(t.name)
    return out
''')
        assert _offenders(p), "the exact defect went undetected"

    def test_it_catches_a_plain_assignment_too(self, tmp_path):
        p = self._write(tmp_path, '''
from bot.utils.i18n import t

def render(lang):
    label = t("header", lang)
    t = 5
    return label, t
''')
        assert _offenders(p)

    def test_a_dict_comprehension_is_NOT_flagged(self, tmp_path):
        """The false positive that made version one report 3-for-1. Python 3
        scopes comprehension variables to the comprehension."""
        p = self._write(tmp_path, '''
from bot.utils.i18n import t

def render(lang, tickers):
    prices = {s: float(t.get("last", 0)) for s, t in tickers.items()}
    return t("header", lang), prices
''')
        assert not _offenders(p), "comprehension variables do not shadow"

    def test_a_generator_expression_is_NOT_flagged(self, tmp_path):
        p = self._write(tmp_path, '''
from bot.utils.i18n import t

def render(lang, tiers):
    joined = " / ".join(f"<code>{t}</code>" for t in tiers)
    return t("usage", lang, tiers=joined)
''')
        assert not _offenders(p)

    def test_a_nested_function_is_NOT_flagged(self, tmp_path):
        p = self._write(tmp_path, '''
from bot.utils.i18n import t

def render(lang, rows):
    def inner(rows):
        for t in rows:
            pass
    inner(rows)
    return t("header", lang)
''')
        assert not _offenders(p)

    def test_a_file_that_does_not_import_t_is_skipped(self, tmp_path):
        p = self._write(tmp_path, '''
def render(rows):
    for t in rows:
        pass
    return t
''')
        assert not _offenders(p)


class TestTheMechanismItself:
    """Python's rule, demonstrated — so the reason survives the fix."""

    def test_a_later_binding_makes_the_earlier_call_unbound(self):
        ns: dict = {}
        exec(  # noqa: S102 — demonstrating a language rule, fixed source
            "def f(rows):\n"
            "    first = t('k')\n"
            "    for t in rows:\n"
            "        pass\n"
            "    return first\n",
            {"t": lambda k: "translated"}, ns)
        with pytest.raises(UnboundLocalError):
            ns["f"]([])

    def test_and_a_nonempty_loop_turns_it_into_a_TypeError(self):
        """Same bug, different exception — which is why it looked
        data-dependent and survived so long."""
        ns: dict = {}
        exec(  # noqa: S102
            "def f(rows):\n"
            "    for t in rows:\n"
            "        pass\n"
            "    return t('k')\n",
            {"t": lambda k: "translated"}, ns)
        with pytest.raises(TypeError):
            ns["f"]([1])
