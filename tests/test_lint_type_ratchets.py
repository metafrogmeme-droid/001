"""The declared lint config was not the enforced one, and nobody could tell.

`pyproject.toml` says::

    [tool.ruff.lint]
    select = ["E", "F", "W", "I"]

CI ran `E9,F821,F811` over `bot/ tests/` and `F401,F541` over `bot/`. Both were
green. Plain `ruff check .` -- what the declared config actually means, and what
any contributor running ruff locally gets -- reported 1,361 findings on the same
tree. Same shape for types: `mypy` gated six modules while the other 272 carried
390 errors that nothing counted.

Neither was a hidden backlog, exactly; both were an UNMEASURED one, which is
worse, because a module could take on fifty new errors and no check would move.

`scripts/ruff_gate.py` and `scripts/mypy_gate.py` close that as ratchets rather
than sweeps -- for reasons recorded in each script's docstring, chiefly that
`I001` is an UNSAFE fix in a repo whose imports run `load_dotenv` and the
secrets-vault restore, and that CLAUDE.md rules out auto-fixing hot-path `F841`
in words.

WHAT THIS FILE GUARDS

Not the counts -- those are the baselines' job. This guards the things that
would make the baselines meaningless:

  * a gate that reports a failure and exits 0, which is this repo's signature
    defect wearing yet another hat -- a verdict nothing acts on;
  * a baseline that has drifted ABOVE reality, which is the stale
    `known_failures.txt` entry in another costume: it silently permits
    regressions back up to the recorded number;
  * a toolchain mismatch rendered as a regression -- the first CI run of these
    gates did exactly that, naming eleven "grown" classes that were entirely an
    artefact of mypy 1.19.1 vs the pinned 1.15.0;
  * the gates falling out of CI, or the strict floors being replaced by them,
    which is how this got here.

None of these runs the real analyser. An earlier version did, and it passed
alone and failed inside the full suite: the gates measure the LIVE WORKING
TREE, the suite writes to that tree, so the counts moved underneath the test
and it reported growth that had nothing to do with the logic under test. The
comparison is what matters here and it needs no ruff or mypy -- which also
takes four minutes back off the suite. Whether TODAY's counts match the
baseline is the gate's own job, on every CI push.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"

GATES = (
    ("ruff", ROOT / "scripts" / "ruff_gate.py", ROOT / "tests" / "ruff_baseline.json"),
    ("mypy", ROOT / "scripts" / "mypy_gate.py", ROOT / "tests" / "mypy_baseline.json"),
)


@pytest.mark.parametrize("name,script,baseline", GATES, ids=[g[0] for g in GATES])
def test_the_gate_and_its_baseline_both_exist(name, script, baseline):
    assert script.exists(), f"{script} is gone -- the {name} ratchet is unenforced"
    assert baseline.exists(), f"{baseline} is gone -- {name} has nothing to compare to"
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data.get("counts"), f"{baseline} records no per-rule counts"
    assert isinstance(data.get("total"), int), f"{baseline} has no integer total"
    assert data["total"] == sum(data["counts"].values()), (
        f"{baseline}: total {data['total']} != sum of counts "
        f"{sum(data['counts'].values())} -- it was hand-edited rather than "
        f"regenerated, so it no longer describes any real run")


def _load_gate(script: Path):
    """Import a gate module by path, without running it."""
    spec = importlib.util.spec_from_file_location(script.stem, script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name,script,baseline", GATES, ids=[g[0] for g in GATES])
def test_the_gate_exits_nonzero_when_it_reports_failure(name, script, baseline,
                                                        monkeypatch):
    """A gate that prints FAIL and exits 0 is not a gate.

    This repo has been bitten by the verdict-nobody-acts-on shape repeatedly --
    the healthcheck that could not fail, the deploy check whose exit code was
    read as a bot death.

    THE LOGIC IS DRIVEN; THE ANALYSER IS NOT RUN, AND THAT IS THE POINT.

    The first version of this test shelled out to the real gate, which runs
    `mypy bot/` over the live working tree. It passed alone and failed inside
    the full suite -- because the suite WRITES to that tree, so the counts
    moved underneath it and the gate correctly reported growth that had nothing
    to do with the gate's logic. A whole-tree analyser invoked from within a
    suite that mutates the tree measures a moving target; that is the same
    shared-state coupling this PR fixes elsewhere, committed by the test
    written to guard against it.

    So: substitute the counts, keep the comparison. What is under test is
    "does growth produce a non-zero status", which needs no real analyser --
    and it now runs in milliseconds instead of adding four minutes to the
    suite.
    """
    src = script.read_text(encoding="utf-8")
    assert "raise SystemExit(main())" in src, (
        f"{script.name} does not propagate main()'s return value to the exit "
        f"status; CI would read every run as success")

    mod = _load_gate(script)
    base = json.loads(baseline.read_text(encoding="utf-8"))["counts"]
    grown = Counter({k: v + 5 for k, v in base.items()})

    monkeypatch.setattr(mod, "check_version", lambda _tool: None)
    if name == "mypy":
        monkeypatch.setattr(mod, "current_counts", lambda: (grown, 99))
    else:
        monkeypatch.setattr(mod, "current_counts", lambda: grown)
    monkeypatch.setattr(sys, "argv", [str(script)])

    assert mod.main() == 1, (
        f"{script.name} was handed counts strictly above its baseline and did "
        f"not return a failing status")


@pytest.mark.parametrize("name,script,baseline", GATES, ids=[g[0] for g in GATES])
def test_the_gate_reports_a_baseline_that_drifted_above_reality(
        name, script, baseline, monkeypatch):
    """A baseline ABOVE reality silently permits regressions back up to it.

    Same rule as known_failures.txt and unreachable_baseline.txt: an entry that
    stops being true must be removed in the commit that made it untrue, or the
    list stops meaning anything. Whether TODAY's baseline is current is the
    gate's own job in CI -- it runs on every push and fails on shrinkage. What
    this asserts is that it would: hand it counts below the baseline and it
    must not call that success.
    """
    mod = _load_gate(script)
    base = json.loads(baseline.read_text(encoding="utf-8"))["counts"]
    shrunk = Counter({k: max(0, v - 1) for k, v in base.items()})

    monkeypatch.setattr(mod, "check_version", lambda _tool: None)
    if name == "mypy":
        monkeypatch.setattr(mod, "current_counts", lambda: (shrunk, 99))
    else:
        monkeypatch.setattr(mod, "current_counts", lambda: shrunk)
    monkeypatch.setattr(sys, "argv", [str(script)])

    assert mod.main() != 0, (
        f"{script.name} was handed counts BELOW its baseline and called it "
        f"success. A baseline sitting above reality permits regressions back "
        f"up to the recorded number without anything noticing.")


@pytest.mark.parametrize("name,script,baseline", GATES, ids=[g[0] for g in GATES])
def test_a_toolchain_mismatch_is_not_reported_as_a_regression(
        name, script, baseline, monkeypatch):
    """The first CI run of these gates failed here, and the failure was a lie.

    The mypy baseline was recorded with 1.19.1 in an environment carrying extra
    packages; CI pins 1.15.0 and installs exactly requirements-ci.txt. It
    reported 654 errors against a baseline of 390 and named eleven classes as
    grown -- including a `list-item` class the other version does not emit at
    all. Not one was a code change.

    Counts are only comparable to counts from the same analyser, so a mismatch
    is the ABSENCE of a verdict rather than a bad one, and exit 2 says so
    distinctly from the 1 that means something really grew.
    """
    mod = _load_gate(script)
    monkeypatch.setattr(mod, "_running", lambda _tool: "0.0.0-not-the-pinned-one")
    with pytest.raises(SystemExit) as excinfo:
        mod.check_version(name)
    assert excinfo.value.code == 2, (
        f"{script.name} treated a toolchain mismatch as exit "
        f"{excinfo.value.code}; 1 would read as 'the code regressed', which is "
        f"a verdict it cannot support")


def test_both_gates_run_in_ci():
    """They exist to be enforced. Unwired, they are two more uncalled modules —
    which this repo has a whole ratchet about."""
    steps = []
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and step.get("run"):
                steps.append(step["run"])
    joined = "\n".join(steps)
    for _name, script, _baseline in GATES:
        rel = script.relative_to(ROOT)
        assert str(rel) in joined, (
            f"{rel} is not run by any CI job. The whole finding was that a "
            f"declared standard nothing executes is a standard that drifts.")


def test_the_strict_gates_are_still_strict():
    """The ratchets ADD coverage; they must not have replaced the floors.

    The narrow steps fail on a single error in the money modules. Swapping them
    for a tolerant whole-tree ratchet would look like more coverage and be less.
    """
    ci = CI.read_text(encoding="utf-8")
    assert "ruff check --select E9,F821,F811 bot/ tests/" in ci, (
        "the strict syntax/undefined-name gate is gone")
    assert "ruff check --select F401,F541 bot/" in ci, (
        "the strict unused-import gate is gone")
    assert "mypy bot/risk bot/compliance" in ci, (
        "the strict per-module mypy floor is gone")
