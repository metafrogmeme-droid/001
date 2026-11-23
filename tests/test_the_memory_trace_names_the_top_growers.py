"""With the trace on, each look after the first names the 25 lines that grew.

The first look is a baseline (totals only). Every later look compares itself
with the one before and logs the lines whose live allocations grew most,
largest first, as ``path:line`` and byte counts: frames and sizes, never an
object, so nothing a user typed and no env value can reach the log line.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from bot.utils import mem_trace
from tests._fresh_trace import run_traced

KEYS = {"at", "size_delta", "count_delta", "size"}


class _Readings:
    """Hands out prepared readings in order, one per look."""

    def __init__(self, *readings):
        self._left = list(readings)

    def __call__(self):
        return self._left.pop(0)


def _recorder():
    lines: list[tuple[str, dict]] = []
    return lines, (lambda result, data: lines.append((result, data)))


def _baseline_and_second():
    """Forty lines that grow by different amounts, three that shrink, one
    that holds, and two that are new in the second look."""
    first = {f"bot/core/m{i}.py:{i}": (1000, 10) for i in range(40)}
    first.update({"bot/core/shrinks.py:1": (9000, 90), "bot/core/shrinks.py:2": (500, 5),
                  "bot/core/shrinks.py:3": (700, 7), "bot/core/holds.py:1": (400, 4)})
    second = {f"bot/core/m{i}.py:{i}": (1000 + 100 * (i + 1), 10 + i) for i in range(40)}
    second.update({"bot/core/shrinks.py:1": (10, 1), "bot/core/shrinks.py:2": (1, 1),
                   "bot/core/shrinks.py:3": (0, 0), "bot/core/holds.py:1": (400, 4),
                   "bot/web/new.py:7": (2500, 3), "bot/web/new.py:8": (50, 1)})
    return first, second


def test_the_second_look_logs_exactly_the_top_25_growers_largest_first():
    lines, log = _recorder()
    first, second = _baseline_and_second()
    trace = mem_trace.GrowthTrace(read=_Readings(first, second), log=log)

    assert trace.tick() is None                      # the baseline
    growers = trace.tick()

    assert [r for r, _ in lines] == ["BASELINE", "TOP_GROWERS"]
    assert "growers" not in lines[0][1]
    assert lines[1][1]["growers"] == growers
    assert len(growers) == 25
    deltas = [g["size_delta"] for g in growers]
    assert deltas == sorted(deltas, reverse=True)
    assert all(d > 0 for d in deltas)
    named = {g["at"] for g in growers}
    assert not any("shrinks" in at or "holds" in at for at in named)
    # the largest grower is m39 (+4000); a line new in this look grew from 0
    assert growers[0] == {"at": "bot/core/m39.py:39", "size_delta": 4000,
                          "count_delta": 39, "size": 5000}
    assert "bot/web/new.py:7" in named and "bot/web/new.py:8" not in named
    for g in growers:
        assert set(g) == KEYS


def test_fewer_growers_than_the_limit_are_all_reported_and_no_more():
    """Both arms of the limit: three growers come back as three."""
    lines, log = _recorder()
    first = {"a.py:1": (10, 1), "b.py:2": (10, 1), "c.py:3": (10, 1), "d.py:4": (10, 1)}
    second = {"a.py:1": (30, 2), "b.py:2": (20, 2), "c.py:3": (11, 1), "d.py:4": (5, 1)}
    trace = mem_trace.GrowthTrace(read=_Readings(first, second), log=log)
    trace.tick()
    assert [g["at"] for g in trace.tick()] == ["a.py:1", "b.py:2", "c.py:3"]


def test_the_loop_sleeps_thirty_minutes_between_looks():
    """A fake clock: the loop takes the baseline at once, sleeps 1800 s, takes
    the second look, and is cancelled on its next sleep."""
    lines, log = _recorder()
    first, second = _baseline_and_second()
    trace = mem_trace.GrowthTrace(read=_Readings(first, second), log=log)
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            raise asyncio.CancelledError

    async def inline(fn, *a):
        return fn(*a)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(mem_trace.run_periodically(trace, sleep=fake_sleep, to_thread=inline))

    assert slept == [1800, 1800] and mem_trace.INTERVAL_S == 1800
    assert [r for r, _ in lines] == ["BASELINE", "TOP_GROWERS"]
    assert len(lines[1][1]["growers"]) == 25


def test_a_failed_look_is_logged_by_class_and_the_loop_goes_on():
    lines, log = _recorder()

    def broken():
        raise OSError("/home/someone/secret path in the message")

    trace = mem_trace.GrowthTrace(read=broken, log=log)
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            raise asyncio.CancelledError

    async def inline(fn, *a):
        return fn(*a)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(mem_trace.run_periodically(trace, sleep=fake_sleep, to_thread=inline))
    assert [r for r, _ in lines] == ["FAILED", "FAILED"]
    assert lines[0][1] == {"interval_s": 1800, "error": "OSError"}


def _allocate_five_megabytes():
    return [bytearray(1024) for _ in range(5 * 1024)]   # the line the trace must name


def test_a_real_trace_names_the_line_that_grew_and_nothing_but_frames():
    """Run in a fresh interpreter (`tests/_fresh_trace.py`), so no tracer
    already on in the test process can change what the trace sees."""
    sentinel = "SENTINEL-VALUE-THAT-MUST-NOT-BE-LOGGED"
    out = run_traced("""
import json
from bot.utils import mem_trace
from tests.test_the_memory_trace_names_the_top_growers import _allocate_five_megabytes
lines = []
assert mem_trace.start_if_enabled({"RUNECLAW_TRACEMALLOC": "1"}) is True
try:
    trace = mem_trace.GrowthTrace(log=lambda result, data: lines.append([result, data]))
    trace.tick()
    kept = _allocate_five_megabytes()
    growers = trace.tick()
finally:
    _tm.stop()
assert len(kept) == 5 * 1024
print(json.dumps({"growers": growers, "lines": lines}))
""", env={"RUNECLAW_TEST_SENTINEL": sentinel})
    got = json.loads(out)
    growers, lines = got["growers"], got["lines"]
    assert sentinel not in out

    assert growers, "a 5 MB allocation between looks must show as growth"
    here = "tests/test_the_memory_trace_names_the_top_growers.py:"
    top = growers[0]
    assert top["at"].startswith(here), growers[:3]
    assert top["size_delta"] >= 5 * 1024 * 1024
    for g in growers:
        assert set(g) == KEYS
        assert re.fullmatch(r".+:\d+", g["at"]), g["at"]
        assert all(isinstance(g[k], int) for k in KEYS - {"at"})
    result, data = lines[-1]
    assert result == "TOP_GROWERS"
    assert set(data) == {"interval_s", "traced_now", "traced_peak",
                         "trace_overhead", "growers"}
    assert sentinel not in repr(lines)
    assert not any(g["at"].startswith("/") for g in growers), \
        "paths are shortened, not the machine's directory layout"
