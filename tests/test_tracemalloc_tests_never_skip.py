"""A tracemalloc measurement runs, or the test fails. It never skips.

Four tests used to `pytest.skip` when a trace was already on in the test
process (PYTHONTRACEMALLOC, or a tracer another test left running). A skip is
silent in a green run, so the memory claims they check could stop being
checked with nothing red to say so. They now run their measured block in a
fresh interpreter (`tests/_fresh_trace.py`), where no earlier tracer can
exist, and a tracer that is somehow on there is a hard failure.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._fresh_trace import run_traced
from tests.source_scan import code_only

TESTS = Path(__file__).resolve().parent


def test_no_test_that_touches_tracemalloc_can_skip():
    offenders = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        code = code_only(path.read_text(encoding="utf-8"))
        if "tracemalloc" not in code:
            continue
        for word in ("pytest.skip", "pytest.mark.skip", "skipif", "importorskip"):
            if word in code:
                offenders.append(f"{path.name}: {word}")
    assert not offenders, offenders


def test_the_measured_block_runs_in_a_fresh_interpreter_with_no_trace_on():
    out = run_traced("""
import os
print(_tm.is_tracing(), os.environ.get("PYTHONTRACEMALLOC"))
""")
    assert out == "False None"


def test_a_trace_already_on_in_the_fresh_interpreter_is_a_hard_failure():
    with pytest.raises(AssertionError, match="already tracing"):
        run_traced("print('the block ran')", env={"PYTHONTRACEMALLOC": "1"})
