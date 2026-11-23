"""The allocation trace is off unless the PROCESS environment turns it on.

`bot/utils/mem_trace.py` records every Python allocation once started, which
costs memory in proportion to the heap. The bot runs under a 5.5 GB cap, so
off has to mean off: tracemalloc never started, no snapshot, no task. And the
switch is the process environment only: `bot/main.py` hands in the copy of
`os.environ` taken before `.env` is loaded, so a line written into `.env`
cannot start a trace on the next restart.

The tests that read or start tracemalloc run in a fresh interpreter
(`tests/_fresh_trace.py`): a tracer already on in the test process cannot
make "off" look on, and a test here cannot stop someone else's tracer.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests._fresh_trace import run_traced
from tests.source_scan import code_only

REPO = Path(__file__).resolve().parent.parent


def _assert_off(mapping: dict, process_env: dict | None = None) -> None:
    """In a fresh interpreter, with any tracemalloc start or snapshot raising
    (so a passing run proves neither ran): ``mapping`` asks for no trace,
    starts none, and none is on afterwards."""
    out = run_traced(f"""
def _refuse(*_a, **_k):
    raise AssertionError("tracemalloc was touched with the trace off")
_tm.start = _refuse
_tm.take_snapshot = _refuse
from bot.utils import mem_trace
env = {mapping!r}
assert mem_trace.trace_requested(env) is False
assert mem_trace.start_if_enabled(env) is False
assert _tm.is_tracing() is False
print("off")
""", env=process_env)
    assert out == "off", "the block must run to its end"


@pytest.mark.parametrize("env", [
    {}, {"RUNECLAW_TRACEMALLOC": ""}, {"RUNECLAW_TRACEMALLOC": "0"},
    {"RUNECLAW_TRACEMALLOC": "false"}, {"RUNECLAW_TRACEMALLOC": "off"},
    {"RUNECLAW_TRACEMALLOC": "no"}, {"SOMETHING_ELSE": "1"},
], ids=["unset", "empty", "zero", "false", "off", "no", "other-key"])
def test_off_starts_nothing(env):
    _assert_off(env)


def test_the_process_environment_is_not_read_behind_the_mapping():
    """The flag in the process environment (where a `.env` load would put it)
    and absent from the mapping handed in: off. The module asks only what it
    is given."""
    _assert_off({}, process_env={"RUNECLAW_TRACEMALLOC": "1"})


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " on "])
def test_on_starts_tracing_with_one_frame(value):
    out = run_traced(f"""
from bot.utils import mem_trace
try:
    assert mem_trace.start_if_enabled({{"RUNECLAW_TRACEMALLOC": {value!r}}}) is True
    assert _tm.is_tracing() is True
    assert _tm.get_traceback_limit() == mem_trace.FRAMES == 1
finally:
    _tm.stop()
print("on")
""")
    assert out == "on", "the block must run to its end"


def _run_telegram_src() -> str:
    """`run_telegram`'s own lines, bounded by its AST node, comments blanked."""
    raw = (REPO / "bot" / "main.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(raw))
                if isinstance(n, ast.FunctionDef) and n.name == "run_telegram")
    lines = code_only(raw).splitlines()
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def test_the_bot_hands_in_the_environment_from_before_dotenv():
    """A shape no test can drive without booting the bot: the start is handed
    `_PRE_DOTENV_ENV` (not `os.environ`), and before the engine is built."""
    body = _run_telegram_src()
    calls = re.findall(r"mem_trace\.start_if_enabled\(([^)]*)\)", body)
    assert calls == ["_PRE_DOTENV_ENV"], calls
    assert body.index("mem_trace.start_if_enabled(") < body.index("RuneClawEngine(")


def test_the_periodic_task_exists_only_when_the_trace_is_on():
    body = _run_telegram_src()
    assert re.search(r"trace_task\s*=\s*\(asyncio\.create_task\(\s*"
                     r"mem_trace\.run_periodically\(mem_trace\.GrowthTrace\(\)\)\)\s*"
                     r"if\s+_trace_on\s+else\s+None\)", body), \
        "the trace task must be created only under _trace_on"
    assert "trace_task.cancel()" in body
