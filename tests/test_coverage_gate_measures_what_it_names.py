"""The coverage gate named three targets and measured two of them.

``scripts/ci_test_gate.py`` declares::

    COV_TARGETS = ["bot/risk", "bot/core/live_executor.py", "bot/compliance"]

The first and third are DIRECTORIES, which coverage resolves. The middle one
is a file path, which it does not. Every full run printed

    CoverageWarning: Module bot/core/live_executor.py was never imported.

immediately above ``TOTAL ... 86%`` — and that 86% was ``bot/risk`` plus
``bot/compliance`` over 2,721 statements. ``live_executor.py`` has 4,262 on
its own, more than both measured targets combined, and is the only module in
the list that places real orders. It contributed nothing.

The failure message at ``_coverage_below_floor`` names all three targets, so
even the gate's refusal overstated what it had read.

WHY A TEST AND NOT JUST A FIX. The warning was printed on every run for as
long as the target list has existed, in the middle of a 10,000-test log, and a
number that is too high looks exactly like good news. Nothing could fail on
it. So this asserts the property the list is FOR: that running the gate's own
flags actually produces a measurement of every module it claims to cover.

It runs coverage rather than reading the list, because "the string does not
end in .py" is a spelling check and the defect was a resolution failure. A
future entry that is spelled like a module and still resolves to nothing —
a typo, a moved file, a package without ``__init__`` — fails here.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from ci_test_gate import COV_FAIL_UNDER, COV_FLAGS, COV_TARGETS  # noqa: E402

#: One cheap test per target that is guaranteed to import it. Coverage reports
#: a module as measured only if it was imported while the tracer was running,
#: so the subset has to actually touch each one.
_TOUCHES = [
    "tests/test_live_executor.py",              # bot.core.live_executor
    "tests/test_check2_position_size_authority.py",   # bot.risk
    "tests/test_exchange_and_compliance.py",    # bot.compliance
]


@pytest.fixture(scope="module")
def measured(tmp_path_factory) -> str:
    """`coverage report` after running the gate's own flags over a subset."""
    data = tmp_path_factory.mktemp("cov") / ".coverage"
    env = {"COVERAGE_FILE": str(data)}
    import os
    env = {**os.environ, **env}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--no-header",
         *_TOUCHES, *COV_FLAGS],
        cwd=REPO, capture_output=True, text=True, timeout=900, env=env)
    r = subprocess.run([sys.executable, "-m", "coverage", "report"],
                       cwd=REPO, capture_output=True, text=True, timeout=300, env=env)
    return r.stdout + r.stderr


def test_every_declared_target_is_actually_measured(measured):
    """The assertion the gate needed. `bot/core/live_executor.py` was named
    and produced no rows at all."""
    missing = []
    for target in COV_TARGETS:
        as_path = target.replace(".", "/")
        if as_path not in measured:
            missing.append(target)
    assert not missing, (
        f"these COV_TARGETS produced no coverage rows, so the percentage the "
        f"gate prints excludes them entirely: {missing}\n{measured}")


def test_the_biggest_money_module_is_in_the_measurement(measured):
    """Named explicitly, because it is the one that was missing and the one
    whose absence mattered: it places the orders."""
    assert "bot/core/live_executor.py" in measured, measured
    assert "never imported" not in measured, (
        "coverage could not resolve a target — the percentage below is a "
        "subset reported as the whole:\n" + measured)


def test_no_target_is_written_as_a_file_path(measured):
    """The specific spelling that failed. Kept alongside the behavioural
    check rather than instead of it: this one names the mistake so the
    failure message can teach, the one above catches the ones it cannot
    predict."""
    bad = [t for t in COV_TARGETS if t.endswith(".py") or "/" in t]
    assert not bad, (
        f"coverage resolves dotted module paths, not file paths: {bad} — "
        "use bot.core.live_executor, not bot/core/live_executor.py")


def test_the_floor_is_a_real_number_the_gate_enforces():
    """A floor of 0 would make the whole apparatus decorative."""
    assert isinstance(COV_FAIL_UNDER, int) and COV_FAIL_UNDER > 0
