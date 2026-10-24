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


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job(name: str) -> dict:
    for job in _workflow()["jobs"].values():
        if job.get("name") == name:
            return job
    raise AssertionError(f"no job named {name!r}")


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
    assert any("pytest-xdist==3.8.0" in run for run in runs)
    # The serial environment is the one the baseline was recorded against.
    serial_runs = " ".join(str(step.get("run") or "") for step in _job(_SERIAL)["steps"])
    assert "pytest-xdist" not in serial_runs


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
    ],
)(test_a_contract_job_stays_on_ci_and_off_the_local_plan)


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
