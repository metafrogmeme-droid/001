"""A push to main used to cancel the run that was still going.

The concurrency group was one per ref, and cancel-in-progress was on. The
next merge cancelled the previous main run, including a run that had only
queued, so that commit never got a verdict. Main now takes the SHA into the
group, and cancel-in-progress stays on for every other ref.

A merge queue would serialize the pre-merge check. GitHub offers it for a
public repository owned by an organization, and this repository's owner is a
user account, so it is not on the plan. The workflow comment records how an
admin would turn it on after a move to an organization. This test pins the
part that is in the file: the group, the cancel expression, and that the
parallel shadow is not the required gate.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

_GROUP = (
    "ci-${{ github.ref }}${{ github.ref == 'refs/heads/main' "
    "&& format('-{0}', github.sha) || '' }}"
)
_CANCEL = "${{ github.ref != 'refs/heads/main' }}"
_SHADOW = "Tests (parallel shadow, non-blocking)"
_SERIAL = "Lint + tests (baseline gate)"
#: The file the shadow keeps off its workers and runs by itself.
_ALONE = "tests/test_no_new_unreachable_functions.py"
_PARALLEL_STEP = "Tests — parallel shadow of the baseline gate"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job(name: str) -> dict:
    for job in _workflow()["jobs"].values():
        if job.get("name") == name:
            return job
    raise AssertionError(f"no job named {name!r}")


def _gate():
    spec = importlib.util.spec_from_file_location(
        "_gate_for_the_ci_shape", ROOT / "scripts" / "ci_test_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_main_does_not_share_a_concurrency_group_across_commits():
    """Pending runs in one group are cancelled even when cancel-in-progress is off.

    The SHA has to be in the group. A group of only the ref still drops the
    commits that land between the run in progress and the latest push.
    """
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"] == _GROUP, concurrency["group"]
    assert "github.sha" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == _CANCEL


def test_the_serial_gate_is_unchanged_and_blocking():
    job = _job(_SERIAL)
    assert job.get("continue-on-error") is not True
    runs = [step.get("run") for step in job["steps"] if step.get("run")]
    assert "python3 scripts/ci_test_gate.py" in runs
    assert all("--parallel" not in str(run) for run in runs)


def test_the_parallel_shadow_is_the_same_gate_and_does_not_block():
    job = _job(_SHADOW)
    assert job.get("continue-on-error") is True
    runs = [str(step.get("run") or "") for step in job["steps"]]
    assert any("python3 scripts/ci_test_gate.py --parallel" in run for run in runs)
    # xdist arrives from a hashed file of its own since 2026-11-19, so the
    # pin is read where it lives rather than from the run text. Both halves:
    # the shadow installs that file under --require-hashes, and the file
    # pins 3.8.0.
    assert any("pip install --require-hashes -r requirements-xdist.txt" in run for run in runs)
    xdist = (ROOT / "requirements-xdist.txt").read_text(encoding="utf-8")
    assert re.search(r"^pytest-xdist==3\.8\.0 ", xdist, re.M), "requirements-xdist.txt must pin 3.8.0"
    # The serial environment is the one the baseline was recorded against.
    serial_runs = " ".join(str(step.get("run") or "") for step in _job(_SERIAL)["steps"])
    assert "pytest-xdist" not in serial_runs
    assert "requirements-xdist" not in serial_runs
    assert not re.search(r"^pytest-xdist==", (ROOT / "requirements-ci.txt").read_text(encoding="utf-8"), re.M), (
        "requirements-ci.txt is the serial environment and must not install xdist")
    # The required gate is not wrapped. A hang-stop belongs only to the shadow.
    serial_test = [
        str(step.get("run") or "")
        for step in _job(_SERIAL)["steps"]
        if step.get("run") == "python3 scripts/ci_test_gate.py"
    ]
    assert serial_test == ["python3 scripts/ci_test_gate.py"]


def test_a_shadow_hang_fails_the_step_instead_of_cancelling_the_workflow():
    """The job timeout cancels the run, continue-on-error included.

    The step stops its own process group first, so the workers die with it
    and the job fails rather than the workflow being cancelled.
    """
    job = _job(_SHADOW)
    step = next(
        s for s in job["steps"]
        if "ci_test_gate.py --parallel" in str(s.get("run") or "")
    )
    run = str(step["run"])
    assert "timeout --kill-after=30s 40m" in run
    assert job["timeout-minutes"] > 40


def test_the_shadow_keeps_the_tree_walk_file_off_its_workers_and_runs_it_alone():
    """Run 44768167620 (5de013a) ended with pytest exit 3, and the gate could
    only say the suite had not run to completion.

    Worker gw0 crashed while reporting a test in the file that walks the whole
    tree. Alone, that file's slowest tests take about half the 60 s limit. The
    limit is a signal, raised wherever the test happens to be, and pytest 9.0.3
    cannot format a traceback entry with no line number, so a timeout that
    lands on one takes the worker down instead of failing one test. A forced
    timeout in that test reproduces the crash; the run's log names no cause.
    The file now stays off the workers and runs in the next step with nothing
    beside it.
    """
    job = _job(_SHADOW)
    steps = job["steps"]
    parallel = next(s for s in steps if "ci_test_gate.py --parallel" in str(s.get("run") or ""))
    assert parallel["name"] == _PARALLEL_STEP
    assert parallel.get("env") == {"PYTEST_ADDOPTS": f"--ignore={_ALONE}"}
    assert (ROOT / _ALONE).is_file(), "the step leaves out a file that is not there"

    # A path named on the command line is collected whatever is ignored (driven
    # below), so the exclusion holds only while the gate's first pass names none.
    gate = _gate()
    first_pass = gate.apply_parallel(gate.PYTEST_CMD + gate.COV_FLAGS, parallel=True)
    assert not [a for a in first_pass[1:] if a.startswith("tests") or a.endswith(".py")], first_pass

    # Then the file, in the very next step: the gate's own flags, no workers.
    alone = steps[steps.index(parallel) + 1]
    assert alone is steps[-1]
    assert str(alone.get("run")) == (
        "timeout --kill-after=30s 15m python3 " + " ".join(gate.PYTEST_CMD[1:]) + " " + _ALONE)
    words = str(alone["run"]).split()
    assert "-n" not in words and "--dist" not in words
    assert "PYTEST_ADDOPTS" not in (alone.get("env") or {})
    # A red parallel pass must not leave the file unrun.
    assert alone.get("if") == "${{ !cancelled() }}"
    # Both stops fit under the job's, which would cancel the workflow.
    assert job["timeout-minutes"] > 40 + 15


def test_the_exclusion_is_the_shadows_alone():
    """The required gate runs every file. Nothing but the shadow's parallel
    step hands pytest an exclusion, by environment or on a command line."""
    workflow = _workflow()
    assert "PYTEST_ADDOPTS" not in (workflow.get("env") or {})
    holders = []
    for job in workflow["jobs"].values():
        if "PYTEST_ADDOPTS" in (job.get("env") or {}):
            holders.append((job.get("name"), None))
        for step in job["steps"]:
            run = str(step.get("run") or "")
            if ("PYTEST_ADDOPTS" in (step.get("env") or {}) or "PYTEST_ADDOPTS" in run
                    or re.search(r"--ignore(-glob)?[= ]tests|--deselect", run)):
                holders.append((job.get("name"), step.get("name")))
    assert holders == [(_SHADOW, _PARALLEL_STEP)], holders


def test_an_ignored_file_is_left_out_only_while_no_path_is_named(tmp_path):
    """What the shadow's exclusion rests on, driven on pytest itself: an
    `--ignore` from PYTEST_ADDOPTS leaves a file uncollected when pytest is
    given no path, and a path named on the command line is collected anyway."""
    (tmp_path / "tests").mkdir()
    for name in ("test_kept.py", "test_left_out.py"):
        (tmp_path / "tests" / name).write_text("def test_it():\n    pass\n", encoding="utf-8")
    env = {**os.environ, "PYTEST_ADDOPTS": "--ignore=tests/test_left_out.py"}

    def collected(*paths):
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only",
             "-q", "--no-header", *paths],
            cwd=tmp_path, capture_output=True, text=True, env=env, timeout=120)
        return sorted(line.split("::")[0] for line in r.stdout.splitlines() if "::" in line)

    assert collected() == ["tests/test_kept.py"]
    assert collected("tests/test_left_out.py") == ["tests/test_left_out.py"]


def test_a_contract_job_stays_on_ci_and_off_the_local_plan(name, workdir):
    """A solidity job of its own, not a step of the test gate.

    The per-SHA group is workflow-wide, so the job gets the same main
    verdict as the rest of the workflow. It is not folded into the shadow
    and not into local preflight: a solidity suite in the local plan would
    change the gate count, and continue-on-error would make a red contract
    run look like the comparison job.
    """
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight

    job = _job(name)
    assert "concurrency" not in job
    assert job.get("continue-on-error") is not True
    runs = [
        (step.get("working-directory"), str(step.get("run") or ""))
        for step in job["steps"]
    ]
    assert (workdir, "npm test") in runs
    assert name not in preflight.LOCAL_JOBS
    assert name in preflight.uncovered()
    assert _SHADOW not in preflight.LOCAL_JOBS


test_a_contract_job_stays_on_ci_and_off_the_local_plan = pytest.mark.parametrize(
    "name,workdir",
    [
        ("CGC contracts (solidity)", "contracts/cgc"),
        ("Kink Cap contracts (solidity)", "contracts/kinkcap"),
        ("FVB contracts (solidity)", "contracts/fvb"),
    ],
)(test_a_contract_job_stays_on_ci_and_off_the_local_plan)


def test_the_rxb_contract_job_is_a_local_gate():
    """RXB is hermetic npm, and main added it to the local plan.

    The log counts those two steps in the twenty-four gates. Leaving the job
    uncovered would make that sentence a lie, and marking it continue-on-error
    would hide a red contract run behind the shadow.
    """
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight

    name = "RXB contracts (solidity)"
    job = _job(name)
    assert "concurrency" not in job
    assert job.get("continue-on-error") is not True
    assert name in preflight.LOCAL_JOBS
    assert name not in preflight.uncovered()
    planned = [(n, c, wd) for n, c, wd in preflight.steps(fast=False)]
    assert any(wd == "contracts/rxb" and c.strip() == "npm test" for _, c, wd in planned)


def test_the_fvb_solana_consumer_stays_on_the_staking_job():
    """The host cargo test is a step of the staking job, which local
    preflight does not run. Putting it in the local plan would require a
    Solana toolchain on every preflight, and running it from the repo root
    rewrites a lockfile the Solana toolchain cannot read.
    """
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight

    job = _job("Staking program (cargo)")
    assert "Staking program (cargo)" not in preflight.LOCAL_JOBS
    assert "Staking program (cargo)" in preflight.uncovered()
    match = [
        step for step in job["steps"]
        if step.get("name") == "Tests — fair-value bridge solana consumer (host)"
    ]
    assert len(match) == 1
    assert match[0].get("working-directory") == "contracts/fvb/solana"
    assert match[0].get("run") == "cargo test"
    dirs = [wd for _, _cmd, wd in preflight.steps(fast=False)]
    assert "contracts/fvb/solana" not in dirs


def test_the_shadow_is_not_a_local_preflight_gate():
    """Two full gates in every local preflight is not the comparison."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight

    assert _SHADOW not in preflight.LOCAL_JOBS
    assert _SHADOW in preflight.uncovered()
    planned = " ".join(cmd for _, cmd, _ in preflight.steps(fast=False))
    assert "--parallel" not in planned
    assert "python3 scripts/ci_test_gate.py" in planned
