"""An allocation trace for the bot process, off unless the process asks for it.

WHY THIS EXISTS. The bot (`python -m bot.main --mode telegram`) runs for days
and its resident memory climbs; on 22 November 2026 it sat around 4.4-4.7 GB
under a unit capped at MemoryMax=5500M. A climb like that names no line by
itself. With the flag on, this logs every 30 minutes the 25 source lines whose
live Python allocations grew most since the previous look, so the next climb
says where it comes from.

THE FLAG. ``RUNECLAW_TRACEMALLOC`` (``1``/``true``/``yes``/``on``), default
off. It is honoured from the PROCESS environment only: `bot/main.py` hands in
`bot.config._PRE_DOTENV_ENV`, the copy of `os.environ` taken before `.env` is
loaded, so a line in `.env` does not turn it on. This module reads no
environment and no file of its own; every reader takes the mapping it is
handed. Off means off: `tracemalloc` is never started and no task is created.

WHAT IT LOGS. Frames and byte counts, nothing else: ``path:line``, the size and
block-count change, the line's current size, and tracemalloc's own totals. No
object is formatted, so no message text, request body, key or env value can
reach the line.

WHAT IT COSTS, AND WHAT IT CANNOT SEE.
- tracemalloc keeps a record per live allocation, so its own memory grows with
  the heap (`trace_overhead` in every line says how much). Under a 5.5 GB cap,
  turn it on right after a restart, not on a process already at 4.5 GB.
- A snapshot copies one record per live allocation and holds the GIL while it
  does; it runs in a worker thread, but the event loop still pauses briefly
  every 30 minutes.
- It sees the Python allocators only. Memory a C library takes for itself
  (OpenSSL contexts, numpy buffers, an SDK's native pool) is invisible here. If
  `traced_now` stays far below the process's RSS, the growth is native.
"""
from __future__ import annotations

import asyncio
import sysconfig
import tracemalloc
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

from bot.utils.logger import audit, system_log

#: The environment variable that turns the trace on.
FLAG = "RUNECLAW_TRACEMALLOC"

#: Seconds between two looks. Thirty minutes: often enough to see a climb that
#: takes days, rare enough that the snapshot's pause is noise.
INTERVAL_S = 30 * 60

#: How many growing lines each look reports.
TOP_N = 25

#: Frames recorded per allocation. One frame (the allocating line) is the
#: smallest overhead tracemalloc offers, and file:line is what is reported.
FRAMES = 1

_ON = frozenset({"1", "true", "yes", "on"})

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STDLIB = Path(sysconfig.get_paths()["stdlib"]).resolve()

#: Frames that are the trace's own machinery, not the bot's.
_IGNORED = (
    tracemalloc.__file__,
    "<frozen importlib._bootstrap>",
    "<frozen importlib._bootstrap_external>",
    "<unknown>",
)

#: One look: ``{"path:line": (size_bytes, block_count)}``.
Reading = dict[str, tuple[int, int]]


def trace_requested(env: Mapping[str, str]) -> bool:
    """True when ``env`` turns the trace on. ``env`` is required: an empty
    mapping means nothing is set, never "go and read the process"."""
    return str(env.get(FLAG, "")).strip().lower() in _ON


def start_if_enabled(env: Mapping[str, str]) -> bool:
    """Start tracemalloc when ``env`` asks for it; return whether the trace is
    on. Off touches nothing: tracemalloc is not started, not even asked."""
    if not trace_requested(env):
        return False
    if not tracemalloc.is_tracing():
        tracemalloc.start(FRAMES)
    return True


def _short_path(filename: str) -> str:
    """A frame's file, without the machine's directory layout."""
    try:
        p = Path(filename).resolve()
    except (OSError, ValueError):
        return filename
    for root, label in ((_REPO_ROOT, ""), (_STDLIB, "stdlib/")):
        try:
            return label + p.relative_to(root).as_posix()
        except ValueError:
            continue
    parts = p.as_posix().split("site-packages/", 1)
    if len(parts) == 2:
        return "site-packages/" + parts[1]
    return filename if not p.is_absolute() else "/".join(p.parts[-2:])


def take_reading() -> Reading:
    """Live Python allocations now, grouped by allocating line."""
    snap = tracemalloc.take_snapshot().filter_traces(
        tuple(tracemalloc.Filter(False, pattern) for pattern in _IGNORED))
    out: Reading = {}
    for stat in snap.statistics("lineno"):
        frame = stat.traceback[0]
        at = f"{_short_path(frame.filename)}:{frame.lineno}"
        size, count = out.get(at, (0, 0))
        out[at] = (size + stat.size, count + stat.count)
    return out


def top_growers(prev: Reading, cur: Reading, n: int = TOP_N) -> list[dict[str, Any]]:
    """The ``n`` lines whose live size grew most from ``prev`` to ``cur``.

    A line absent from ``prev`` grew from nothing. A line that shrank or held
    is not a grower and is left out, so fewer than ``n`` come back when fewer
    grew."""
    rows: list[dict[str, Any]] = []
    for at, (size, count) in cur.items():
        was_size, was_count = prev.get(at, (0, 0))
        delta = size - was_size
        if delta > 0:
            rows.append({"at": at, "size_delta": delta,
                         "count_delta": count - was_count, "size": size})
    rows.sort(key=lambda r: (-r["size_delta"], r["at"]))
    return rows[:n]


def _log(result: str, data: dict) -> None:
    audit(system_log, f"tracemalloc: {result.lower().replace('_', ' ')}",
          action="mem_trace", result=result, data=data)


class GrowthTrace:
    """Each `tick` compares this look with the previous one.

    Only the grouped reading is kept between ticks, never the Snapshot, so
    the trace does not hold a second copy of every allocation record."""

    def __init__(self, *, top_n: int = TOP_N, interval_s: int = INTERVAL_S,
                 read: Callable[[], Reading] = take_reading,
                 log: Callable[[str, dict], None] = _log) -> None:
        self._top_n = top_n
        self._interval_s = interval_s
        self._read = read
        self._log = log
        self._prev: Optional[Reading] = None

    @property
    def interval_s(self) -> int:
        """Seconds between two looks; `run_periodically` sleeps this long."""
        return self._interval_s

    def note_failure(self, exc: BaseException) -> None:
        """A look that raised, by class name only."""
        self._log("FAILED", {"interval_s": self._interval_s,
                             "error": type(exc).__name__})

    def _totals(self) -> dict:
        now, peak = tracemalloc.get_traced_memory()
        return {"interval_s": self._interval_s, "traced_now": now,
                "traced_peak": peak,
                "trace_overhead": tracemalloc.get_tracemalloc_memory()}

    def tick(self) -> Optional[list[dict]]:
        """Take a look. The first is the baseline and returns None; each later
        one logs and returns the top growers since the one before."""
        cur = self._read()
        prev, self._prev = self._prev, cur
        data = self._totals()
        if prev is None:
            data["lines"] = len(cur)
            self._log("BASELINE", data)
            return None
        growers = top_growers(prev, cur, self._top_n)
        data["growers"] = growers
        self._log("TOP_GROWERS", data)
        return growers


async def run_periodically(
        trace: GrowthTrace, *,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        to_thread: Callable[..., Awaitable[object]] = asyncio.to_thread) -> None:
    """Baseline now, then a look every ``trace.interval_s`` until cancelled.

    A look that fails is logged by class name and the loop goes on: a broken
    diagnostic must not end the process it is diagnosing."""
    while True:
        try:
            await to_thread(trace.tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            trace.note_failure(exc)
        await sleep(trace.interval_s)
