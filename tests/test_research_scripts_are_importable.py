"""Importing a research script must not run the research.

`tests/unreachable_baseline.txt` filed `scripts/research/rwa_funding.py` and
`rwa_session_gap.py` with the note that they have no ``__main__`` guard, "so
they cannot be run at all".

THAT WAS THE WRONG WAY ROUND, and checking it before acting on it is the point
of this file. They ran perfectly well as scripts — every statement was
top-level, so `python3 scripts/research/rwa_funding.py` executed the study
exactly as intended. What the missing guard actually meant was the opposite and
worse: **importing** either module ran the whole study. Every `curl` to Bitget,
every print, minutes of it — fired by anything that imports what it finds while
walking the tree, including the reachability checker that catalogued them.

So the defect was never "cannot run". It was "runs when nobody asked", and a
study is a thing you ask for rather than a side effect of reading a file.

These tests import the modules and assert that nothing happened. They make no
network calls themselves and must not: if the guard regresses, the import here
would try to, and the assertions below fail on the observable evidence (the
study's own output) rather than on a timeout.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import contextlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = [
    REPO / "scripts" / "research" / "rwa_funding.py",
    REPO / "scripts" / "research" / "rwa_session_gap.py",
]


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_the_module_body_does_no_work(path):
    """Structural: nothing at module level except definitions and constants.

    Checked by AST rather than by importing, because this is the property that
    makes the import safe — asserting it only by importing would mean the test
    that catches a regression is the test that triggers it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                                    # the module docstring
        if isinstance(node, ast.If):                    # the __main__ guard
            test = node.test
            if (isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                continue
        offenders.append(f"line {node.lineno}: {type(node).__name__}")
    assert offenders == [], (
        f"{path.name} does work at module level: {offenders}. Importing it "
        "runs the study — network calls and all.")


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_it_has_a_main_guard_that_calls_main(path):
    src = path.read_text(encoding="utf-8")
    assert "if __name__ ==" in src, f"{path.name} has no __main__ guard"
    assert "def main(" in src, f"{path.name} has no main()"
    # Reachability excludes entry points BY the guard, so a guard that calls
    # nothing would satisfy the checker while running nothing.
    assert "main()" in src.split("if __name__ ==")[1], (
        f"{path.name}'s __main__ guard does not call main()")


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_importing_it_prints_nothing_and_fetches_nothing(path):
    """Behavioural: import it for real and assert the study did not run.

    The old version printed a header row before its first fetch, so output at
    import time is the observable proof. Captured rather than asserted-absent
    from source, because a scan cannot tell a print that runs from one that is
    defined.
    """
    spec = importlib.util.spec_from_file_location(f"_probe_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        spec.loader.exec_module(mod)
    assert out.getvalue() == "", (
        f"importing {path.name} printed:\n{out.getvalue()[:400]}\n"
        "The study ran on import.")
    assert callable(getattr(mod, "main", None)), (
        f"{path.name} imported cleanly but exposes no main() to call")


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_an_empty_read_is_not_reported_as_a_result(path):
    """A study that measured nothing must say so, not divide by zero.

    Both scripts end in pooled statistics over everything collected. With an
    unreachable venue that collection is empty and the means below it raise —
    so the run dies with a traceback where it should report that it read
    nothing. Absent is not a measurement, and a traceback is not a result.
    """
    src = path.read_text(encoding="utf-8")
    assert "the study" in src and "return 1" in src, (
        f"{path.name} has no empty-read guard; an unreachable venue produces "
        "a ZeroDivisionError instead of an honest 'measured nothing'")
