"""Run a tracemalloc measurement in a fresh interpreter.

tracemalloc is process-wide. A test that starts it in the test process can be
defeated by a tracer already running there (PYTHONTRACEMALLOC, or one another
test left on): `start()` then keeps the earlier settings, `stop()` ends the
other tracer, and a peak reading includes whatever else that process holds.
The tests here used to skip in that case, which is silent in a green run.

So the measured block runs in its own interpreter, through
`tests/_env_subprocess.py::run_py` (state dir and `.env` neutralised), with
PYTHONTRACEMALLOC removed from its environment. Nothing earlier can be tracing
there. The block is prefixed with a precondition that FAILS, never skips, if a
trace is on all the same: then the measurement would not mean what it says.

The block sees `_tm` (the tracemalloc module). It runs with the repository
root as its working directory, so `bot.*` and `tests.*` import as usual.
"""
from __future__ import annotations

import textwrap
from typing import Optional

from tests._env_subprocess import run_py

_PRECONDITION = """\
import tracemalloc as _tm
if _tm.is_tracing():
    raise SystemExit(
        "FAIL: tracemalloc was already tracing when the measured block began, in a "
        "fresh interpreter started without PYTHONTRACEMALLOC. The measurement would "
        "not mean what it says, so the test fails rather than skipping."
    )
"""


def run_traced(body: str, env: Optional[dict[str, str]] = None) -> str:
    """Run ``body`` after the precondition in a fresh interpreter; return its
    stripped stdout. A nonzero exit (a failed assert in ``body``, or the
    precondition) raises AssertionError carrying the child's stderr."""
    return run_py(_PRECONDITION + textwrap.dedent(body),
                  env_overrides=env, env_removals=("PYTHONTRACEMALLOC",))
