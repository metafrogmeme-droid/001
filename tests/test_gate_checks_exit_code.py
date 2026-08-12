"""The gate announced PASS having run nothing.

`ci_test_gate.py` decided the verdict entirely by parsing pytest's stdout for
`FAILED `/`ERROR ` lines. A run that aborted before printing any — an unknown
flag, a conftest that raises, a collection error, an empty selection — parsed
as zero failures, so the gate printed

    [gate] total failing: 0 | known-baseline: 0
    [gate] PASS — no new failures beyond the known baseline.

and exited 0. Driven, not argued: pytest with a bad flag exits 4, collects
nothing, and the gate went green.

This is the defect the whole repository is organised around — a subset, here
the empty set, reported as the whole — living inside the gate built to prevent
it, and standing behind every "preflight green" anyone has quoted, including
several in the session that found this.

The fix reads pytest's own exit code. This file exists because the fix itself
had no test: a mutation pass that deleted the check sailed straight through,
since the only thing exercising it was a throwaway probe. A guard nothing
pins is a guard that lasts until the next edit.

Exit codes: 0 all passed, 1 tests failed, 2 interrupted, 3 internal error,
4 usage/conftest error, 5 no tests collected. Only 0 and 1 mean "the suite ran
and stdout describes it".
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

GATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "ci_test_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("_gate_under_test", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_with_exit(monkeypatch, code: int, stdout: str = ""):
    """Run the gate against a fake pytest that prints `stdout` and exits `code`."""
    gate = _load_gate()
    script = f"import sys; sys.stdout.write({stdout!r}); sys.exit({code})"
    monkeypatch.setattr(gate, "PYTEST_CMD", [sys.executable, "-c", script])
    monkeypatch.setattr(gate, "COV_FLAGS", [])
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py"])
    return gate.main()


@pytest.mark.parametrize("code,label", [
    (2, "interrupted"),
    (3, "internal error"),
    (4, "usage / conftest error"),
    (5, "no tests collected"),
])
def test_an_aborted_run_is_never_green(monkeypatch, code, label):
    """The headline case. Nothing ran, so the empty failure list is ignorance,
    not evidence."""
    assert _run_with_exit(monkeypatch, code) == 1, (
        f"pytest exited {code} ({label}) and the gate called it a pass")


def test_exit_five_is_not_an_empty_success(monkeypatch):
    """The subtlest one: a selection that matches nothing looks exactly like a
    suite where everything passed."""
    assert _run_with_exit(monkeypatch, 5, "no tests ran in 0.01s\n") == 1


def test_failures_pytest_reports_but_the_parser_cannot_see_are_not_green(monkeypatch):
    """rc=1 means pytest found failures. If none parse, the parser has drifted
    from pytest's output format and its silence means nothing."""
    assert _run_with_exit(monkeypatch, 1, "1 failed in 0.2s\n") == 1


def test_a_clean_run_still_passes(monkeypatch):
    """The check must not make every run red — that would be its own way of
    telling the truth badly."""
    assert _run_with_exit(monkeypatch, 0, "42 passed in 1.2s\n") == 0


def test_a_real_failure_still_fails_through_the_normal_path(monkeypatch):
    """rc=1 WITH a parseable failure goes to the baseline logic, not the new
    early return — the existing behaviour has to survive the fix."""
    out = "FAILED tests/test_nonexistent_thing.py::test_x - AssertionError\n1 failed\n"
    assert _run_with_exit(monkeypatch, 1, out) == 1


def test_the_check_reads_the_process_result_not_the_text(monkeypatch):
    """Guards against a 'fix' that greps stdout for the exit code instead of
    reading it: stdout here claims success while the process reports abort."""
    assert _run_with_exit(monkeypatch, 4, "42 passed in 1.2s\n") == 1


def test_a_baseline_known_failure_still_passes(monkeypatch):
    """The discriminating case for `rc not in (0, 1)` versus `rc != 0`.

    Both spellings turn a failing run red, so no test above can tell them
    apart. This one can: pytest exits 1, the failure IS in the baseline, and
    the gate must reach the baseline comparison and pass. Under `rc != 0` the
    run is reported as "did not run to completion" instead — which would make
    every build red the moment `known_failures.txt` stops being empty, and
    would quietly disable the ratchet this gate exists to enforce.
    """
    gate = _load_gate()
    node = "tests/test_known_thing.py::test_drifted"
    script = (f"import sys; sys.stdout.write('FAILED {node} - AssertionError\\n"
              f"1 failed in 0.2s\\n'); sys.exit(1)")
    monkeypatch.setattr(gate, "PYTEST_CMD", [sys.executable, "-c", script])
    monkeypatch.setattr(gate, "COV_FLAGS", [])
    monkeypatch.setattr(gate, "_load_baseline", lambda: {node})
    # The flake re-run would try to execute the node; it does not exist, so
    # short-circuit it — the behaviour under test is the baseline comparison.
    monkeypatch.setattr(gate, "_confirm_failures", lambda nodes, *a, **k: (set(nodes), set()),
                        raising=False)
    monkeypatch.setattr(sys, "argv", ["ci_test_gate.py"])
    assert gate.main() == 0, (
        "a failure already in the baseline must pass the gate, not be reported "
        "as an aborted run")
