"""A coverage floor that could not be measured is not a coverage floor that passed.

`scripts/ci_test_gate.py`'s coverage check was `-> bool`, and a boolean cannot
carry three facts. It returned **False** — the same value as "coverage is fine" —
for a ``coverage report`` that came back with no usable data:

    if r.returncode == 0: return False   # genuinely above the floor
    if r.returncode == 2: return True    # genuinely below  -> FAIL
    return False                          # no data         -> read as PASS

CI prints that step as *"Tests — baseline-diff regression gate (+ coverage
floor)"*, so a run where the floor went unenforced was indistinguishable, from
the gate's own summary line, from a run where it held. That is the defect
`ruff_gate.check_version` exists to prevent, one gate over, and CLAUDE.md
devotes a chapter to it:

    "A gate that COULD NOT CHECK is not a gate that failed, and the launcher was
    collapsing the two."

The remedy was already written in this repo twice — `Outcome.ok` became
True/False/**None** because "there is no honest boolean for 'nothing was
measured'", and `toolchain.check_version` raises SystemExit(2) so "a launcher
reading truthiness still fails closed while a human reading the message learns
which of the two happened". It had simply never reached this function.

WHAT COV_UNMEASURED IS NOT: "pytest-cov is not installed". `main()` only calls
the verdict once ``import pytest_cov`` has succeeded, so the minimal-local-run
case the old docstring was written for is handled one level up and is untouched
by this change. COV_UNMEASURED means the report was asked for, in a run equipped
to produce one, and answered with nothing to read.

Driven, not scanned: every case below runs the real ``main()`` and reads the
exit code it hands the launcher. A scan for the literal "CANNOT CHECK" would
pass against a branch that computes the sentence and never returns 2 — which is
the mutation this file's own round is about.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ci_test_gate  # noqa: E402
import preflight  # noqa: E402


class _Done:
    """A `subprocess.run` result stand-in."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _healthy_suite(monkeypatch, cov_rc: int | None, *, raise_launch: bool = False):
    """Drive `main()` over a suite that passes, varying only what the coverage
    reader answers.

    `main()` shells out TWICE through the same `subprocess.run` — once for
    pytest and once for `coverage report` — so the stand-in dispatches on the
    command rather than answering both the same way. A stub that answered one
    code for both would hand pytest the coverage verdict, and the first draft of
    this helper did exactly that: it patched a `_run_pytest` seam that does not
    exist, with `raising=False`, so it silently patched nothing at all. A
    fixture that cannot produce the state it names measures nothing.

    The pytest run itself is replaced deliberately: this file is about the
    coverage verdict, and standing up 17,744 real tests to read one exit code
    would make the guard unrunnable — which is how a property stops being
    checked at all.
    """
    def fake_run(cmd, **kw):
        is_coverage = "coverage" in cmd and "report" in cmd
        if is_coverage:
            if raise_launch:
                raise OSError("coverage binary is not executable")
            return _Done(cov_rc, stdout="TOTAL 82%\n")
        # the pytest leg: ran to completion, nothing failed
        return _Done(0, stdout="1 passed in 0.01s\n")

    monkeypatch.setattr(ci_test_gate.subprocess, "run", fake_run)


# ── the three states, read off the real return value ────────────────────

def test_coverage_above_the_floor_is_a_pass():
    assert ci_test_gate.COV_OK == "ok"
    assert ci_test_gate.COV_BELOW != ci_test_gate.COV_UNMEASURED


def test_a_report_with_no_data_is_unmeasured_not_ok(monkeypatch):
    """rc 1 is coverage's "no data / other" — the case that used to read as a
    pass. It is the absence of a measurement."""
    _healthy_suite(monkeypatch, cov_rc=1)
    assert ci_test_gate._coverage_verdict() == ci_test_gate.COV_UNMEASURED


def test_a_reader_that_cannot_launch_is_unmeasured_not_ok(monkeypatch):
    """The old `except Exception: return False` was the same defect by a second
    door: a reader that never ran reported the floor as held."""
    _healthy_suite(monkeypatch, cov_rc=None, raise_launch=True)
    assert ci_test_gate._coverage_verdict() == ci_test_gate.COV_UNMEASURED


def test_below_the_floor_is_still_a_failure(monkeypatch):
    """The regression direction must survive the fix. Trading a false pass for
    a lost failure would be the quiet direction."""
    _healthy_suite(monkeypatch, cov_rc=2)
    assert ci_test_gate._coverage_verdict() == ci_test_gate.COV_BELOW


def test_a_clean_report_is_ok(monkeypatch):
    _healthy_suite(monkeypatch, cov_rc=0)
    assert ci_test_gate._coverage_verdict() == ci_test_gate.COV_OK


# ── and the exit code the launcher actually reads ───────────────────────

@pytest.mark.parametrize("cov_rc,expected,why", [
    (0, 0, "clean suite, coverage above the floor"),
    (2, 1, "clean suite, coverage genuinely below the floor"),
    (1, ci_test_gate.CANNOT_CHECK_EXIT, "clean suite, coverage not measured"),
])
def test_the_exit_code_separates_all_three(monkeypatch, capsys, cov_rc, expected, why):
    """The whole point: a launcher reading truthiness still fails closed on 2,
    and a human reading the summary learns which of the two happened."""
    _healthy_suite(monkeypatch, cov_rc=cov_rc)
    monkeypatch.setattr(ci_test_gate, "_coverage_verdict",
                        lambda: {0: ci_test_gate.COV_OK,
                                 2: ci_test_gate.COV_BELOW,
                                 1: ci_test_gate.COV_UNMEASURED}[cov_rc])
    rc = ci_test_gate.main()
    assert rc == expected, f"{why}: got {rc}\n{capsys.readouterr().out}"


def test_the_unmeasured_summary_does_not_say_pass(monkeypatch, capsys):
    """`[gate] PASS — no new failures beyond the known baseline.` over an
    unenforced floor is the sentence this whole file exists to delete."""
    _healthy_suite(monkeypatch, cov_rc=1)
    monkeypatch.setattr(ci_test_gate, "_coverage_verdict",
                        lambda: ci_test_gate.COV_UNMEASURED)
    ci_test_gate.main()
    out = capsys.readouterr().out
    assert "CANNOT CHECK" in out
    # Anchored to the gate's own verdict line rather than the bare word, because
    # "PASS" appears in ordinary pytest chatter — asserting a short string is
    # absent is the assertion this repo records as the one that keeps misfiring.
    assert "[gate] PASS" not in out


# ── the launcher's vocabulary is a reading, not a guess ─────────────────

def test_preflight_reads_this_gates_exit_two():
    """`preflight` only trusts rc==2 from scripts that document it that way.
    This gate now does, so it is named — and the constants must agree, or the
    launcher files a cannot-check as a failure."""
    assert "ci_test_gate.py" in preflight.CANNOT_CHECK_GATES
    assert ci_test_gate.CANNOT_CHECK_EXIT == preflight.CANNOT_CHECK_EXIT


def test_the_gate_still_runs_as_a_script():
    """A gate that exists and raises on import is the same defect one layer
    down — the lesson `test_the_reach_baseline_can_be_re_measured` records."""
    proc = subprocess.run(  # nosec B603 — fixed argv, no shell
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); import ci_test_gate; "
         "print(ci_test_gate.CANNOT_CHECK_EXIT)"],
        capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "2"


# ── the fourth word: nobody asked ───────────────────────────────────────
#
# CI FAILED THE FIRST DRAFT OF THIS SLICE HERE, and the guard above could not
# see it. `main()` decided whether to consult the coverage reader from
# `cov_available` — `import pytest_cov` succeeding — which answers "is the
# plugin installed" and NOT "did this run instrument anything". Those are the
# same answer in CI and different answers in the suite:
# `tests/test_gate_checks_exit_code.py` drives the baseline logic against a
# fake pytest and sets `COV_FLAGS = []` to do it, leaving the plugin installed.
# So the gate shelled out to `coverage report`, read `No data to report.`,
# filed rc 1 as COV_UNMEASURED and returned 2 — and two tests whose whole
# subject is the exit code went red on a coverage question neither one asks.
#
# Every fixture in the block above hands `main()` a command carrying
# `--cov=`, so all of them agree with the broken predicate and none could
# measure it. These are the inputs that tell the two apart.


def _requested(*cmd):
    return ci_test_gate._coverage_was_requested(list(cmd))


@pytest.mark.parametrize("cmd,asked,why", [
    ([], False, "no leg at all — what test_gate_checks_exit_code.py plants"),
    (["pytest", "-q"], False, "a plain run instruments nothing"),
    (["pytest", "--cov-report="], False,
     "says how to RENDER a measurement and names no target"),
    (["pytest", "--cov=bot.risk", "--cov-report="], True, "a real target"),
    (["pytest", "--cov=bot.risk"], True, "a target with no report flag"),
])
def test_the_request_is_read_off_the_command_that_ran(cmd, asked, why):
    assert _requested(*cmd) is asked, f"{cmd!r}: {why}"


def test_a_run_that_never_asked_is_not_an_unmeasured_one(monkeypatch, capsys):
    """The regression CI caught. `COV_FLAGS = []` is a run with no coverage leg,
    not a run whose floor went unenforced — and it must not spend the reader's
    attention, or a cannot-check, on a question nobody asked."""
    _healthy_suite(monkeypatch, cov_rc=1)   # the reader WOULD answer "no data"
    monkeypatch.setattr(ci_test_gate, "COV_FLAGS", [])
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py"])
    rc = ci_test_gate.main()
    out = capsys.readouterr().out
    assert rc == 0, f"a clean suite with no coverage leg must pass:\n{out}"
    assert "CANNOT CHECK" not in out


def test_the_reader_is_not_even_asked_when_nothing_was_instrumented(monkeypatch):
    """Not merely "the verdict is ignored" — the subprocess must not run.
    Reporting on whatever `.coverage` an earlier run left in the tree would be
    a memory presented as a measurement, which is the failure one door over."""
    _healthy_suite(monkeypatch, cov_rc=1)
    monkeypatch.setattr(ci_test_gate, "COV_FLAGS", [])
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py"])
    asked = []
    monkeypatch.setattr(ci_test_gate, "_coverage_verdict",
                        lambda: asked.append(1) or ci_test_gate.COV_UNMEASURED)
    ci_test_gate.main()
    assert asked == [], "the coverage reader ran for a run that measured nothing"


def test_the_omission_is_named_rather_than_silent(monkeypatch, capsys):
    """Omit is a sanctioned strategy; omitting SILENTLY is not. CI prints this
    step as "+ coverage floor", so a run that carried no coverage leg and said
    nothing about it lets the step's own name overstate what was checked."""
    _healthy_suite(monkeypatch, cov_rc=0)
    monkeypatch.setattr(ci_test_gate, "COV_FLAGS", [])
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py"])
    ci_test_gate.main()
    out = capsys.readouterr().out
    assert "NOT part of this run" in out
    assert str(ci_test_gate.COV_FAIL_UNDER) in out


def test_an_instrumented_run_still_reaches_the_reader(monkeypatch):
    """The direction that must survive the fix: with a real `--cov=` in the
    command, the floor is still enforced. Trading a false cannot-check for a
    lost measurement would be the quiet direction."""
    _healthy_suite(monkeypatch, cov_rc=1)
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py"])
    assert ci_test_gate.main() == ci_test_gate.CANNOT_CHECK_EXIT


def test_the_four_words_are_distinct():
    """A fourth state that collides with a third is a third state with two
    spellings, and `cov == COV_UNMEASURED` decides the exit code."""
    words = {ci_test_gate.COV_OK, ci_test_gate.COV_BELOW,
             ci_test_gate.COV_UNMEASURED, ci_test_gate.COV_NOT_REQUESTED}
    assert len(words) == 4
