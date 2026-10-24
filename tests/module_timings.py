"""Per-module pytest durations for the CI test gate.

The gate sets ``RUNECLAW_MODULE_TIMINGS`` to a file path. ``tests/conftest.py``
registers :class:`ModuleTimings` only then, so an ordinary local run does not
write anything. Workers under pytest-xdist keep their own totals and do not
write the controller's file; the controller writes what it observed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_PHASES = ("setup", "call", "teardown")


def _number(value: object) -> float | None:
    """A real duration. ``bool`` is an ``int`` and is not a duration; ``None`` is not zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class ModuleTimings:
    """Sum setup, call and teardown per test file."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.by_file: dict[str, float] = {}
        self.phases: dict[str, float] = {}

    def pytest_runtest_logreport(self, report) -> None:
        # A worker's reports are also delivered to the controller. Recording
        # on the worker as well would write a second file the gate does not
        # read, so the worker keeps nothing.
        if os.environ.get("PYTEST_XDIST_WORKER"):
            return
        when = getattr(report, "when", None)
        if when not in _PHASES:
            return
        duration = _number(getattr(report, "duration", None))
        if duration is None:
            return
        nodeid = getattr(report, "nodeid", "") or ""
        rel = nodeid.split("::", 1)[0]
        if not rel:
            return
        self.by_file[rel] = self.by_file.get(rel, 0.0) + duration
        self.phases[when] = self.phases.get(when, 0.0) + duration

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        if os.environ.get("PYTEST_XDIST_WORKER"):
            return
        if getattr(getattr(session, "config", None), "workerinput", None) is not None:
            return
        try:
            Path(self.path).write_text(
                json.dumps({"by_file": self.by_file, "phases": self.phases}),
                encoding="utf-8",
            )
        except OSError:
            # The suite's verdict does not depend on this file. A failed write
            # is "not recorded" when the gate reads it, not a failed suite.
            return


def format_timing_report(payload: dict | None, *, limit: int = 10) -> list[str]:
    """The slowest modules, then the phase sums that were actually observed.

    A missing payload is not a row of zeros. A phase nobody reported is left
    out rather than printed as ``0.0s``.
    """
    if not isinstance(payload, dict):
        return ["[gate] slowest modules: not recorded"]
    by_file = payload.get("by_file")
    ranked: list[tuple[str, float]] = []
    if isinstance(by_file, dict):
        for name, raw in by_file.items():
            secs = _number(raw)
            if secs is None:
                continue
            ranked.append((str(name), secs))
        ranked.sort(key=lambda item: (-item[1], item[0]))
        ranked = ranked[:limit]
    if not ranked:
        lines = ["[gate] slowest modules: not recorded"]
    else:
        lines = ["[gate] slowest modules"]
        for index, (name, secs) in enumerate(ranked, 1):
            lines.append(f"  {index}. {name}  {secs:.1f}s")
    phases = payload.get("phases")
    if isinstance(phases, dict):
        observed = [(phase, _number(phases.get(phase))) for phase in _PHASES]
        observed = [(phase, secs) for phase, secs in observed if secs is not None]
        if observed:
            lines.append(
                "[gate] pytest phase sums (setup + call + teardown across tests; "
                "a parallel run's sum can exceed the wall clock)"
            )
            for phase, secs in observed:
                lines.append(f"  {phase}: {secs:.1f}s")
    return lines


def format_step_timings(
    *,
    suite_s: float,
    flake_s: float | None,
    cov_s: float | None,
    total_s: float,
) -> list[str]:
    """Wall-clock timings for the gate's own steps. ``None`` means that step did not run."""
    lines = ["[gate] step timings", f"  suite: {suite_s:.1f}s"]
    if flake_s is None:
        lines.append("  flake-filter: not run")
    else:
        lines.append(f"  flake-filter: {flake_s:.1f}s")
    if cov_s is None:
        lines.append("  coverage: not run")
    else:
        lines.append(f"  coverage: {cov_s:.1f}s")
    lines.append(f"  total: {total_s:.1f}s")
    return lines
