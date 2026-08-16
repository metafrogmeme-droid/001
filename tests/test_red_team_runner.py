"""The red-team runner, and the one property that makes it worth having.

`bot/core/red_team.py` was 691 lines of adversarial scenarios against the live
risk engine, reachable only from a unit test asserting the report had the right
FIELDS. So the gate the whole product's safety claim rests on had never actually
been attacked — the tests said the report was well-shaped, not that anything was
caught.

`scripts/red_team.py` runs it and CI gates on it. Which moves the risk: a gate
that cannot fail is worse than no gate, because it converts "nobody checked"
into "we check every build" while checking nothing. So the load-bearing test
here is not "it exits 0 today" — it is that it exits NON-ZERO when a scenario
slips through, and non-zero when the harness itself cannot run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "red_team.py"


def _run(*args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(ROOT),
                          env=env, timeout=300)


# ── it runs, and it actually attacks something ────────────────────────────

def test_the_engine_refuses_every_adversarial_scenario():
    r = _run()
    assert r.returncode == 0, (
        "a scenario got past the risk engine:\n" + r.stdout + r.stderr)
    assert "adversarial scenarios were refused" in r.stdout


def test_it_reports_a_real_number_of_scenarios(tmp_path):
    import json
    r = _run("--json")
    assert r.returncode == 0
    rep = json.loads(r.stdout)
    assert rep["total_scenarios"] >= 25, (
        "the scenario count collapsed — the generators stopped producing")
    assert rep["passed"] + rep["failed"] == rep["total_scenarios"]
    assert rep["failed"] == 0
    # More than one category, or a "pass" means one guard was exercised.
    cats = {s["category"] for s in rep["scenarios"]}
    assert len(cats) >= 8, f"only {len(cats)} categories attacked: {sorted(cats)}"


# ── THE GATE MUST BE ABLE TO FAIL ─────────────────────────────────────────

def test_a_slipped_scenario_exits_non_zero(monkeypatch):
    """The property that makes the CI gate mean anything.

    Verified by forcing a failed scenario into the report rather than by
    trusting the exit-code arithmetic: `1 if report.failed else 0` is one
    inverted comparison away from a gate that always passes, and a gate that
    always passes reads as "checked every build" while checking nothing.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import red_team as runner

    real = runner.run

    def _one_failure(verbose=False):
        rep = real(verbose=verbose)
        rep.scenarios[0].passed = False
        rep.scenarios[0].actual_verdict = "APPROVED"
        rep.scenarios[0].expected_verdict = "REJECTED"
        rep.failed = 1
        rep.passed = rep.total_scenarios - 1
        return rep

    monkeypatch.setattr(runner, "run", _one_failure)
    monkeypatch.setattr(sys, "argv", ["red_team.py"])
    assert runner.main() == 1, "a slipped scenario must fail the build"


def test_a_harness_that_cannot_run_is_not_a_pass(monkeypatch):
    """A red team that errored has NOT cleared the engine.

    Reporting "0 failures" because the run never happened is the
    `integrity_veto.assess({}) == "clear"` trap one level up: a confident
    all-clear manufactured from no data.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import red_team as runner

    def _boom(verbose=False):
        raise RuntimeError("engine import exploded")

    monkeypatch.setattr(runner, "run", _boom)
    monkeypatch.setattr(sys, "argv", ["red_team.py"])
    assert runner.main() == 1

    monkeypatch.setattr(sys, "argv", ["red_team.py", "--json"])
    assert runner.main() == 1


# ── it does not touch the operator's state ────────────────────────────────

def test_it_never_writes_the_real_state_file():
    """A stress test's synthetic losses and tripped breakers must not land in
    production state. The engine is handed its own temp file, deleted after."""
    src = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))
    assert "tempfile" in code and "state_file=state" in code
    assert "risk_state" not in code, "no reference to the production state file"


def test_running_it_twice_leaves_no_temp_directories():
    import glob
    import tempfile
    before = set(glob.glob(str(Path(tempfile.gettempdir()) / "redteam-*")))
    _run()
    _run()
    after = set(glob.glob(str(Path(tempfile.gettempdir()) / "redteam-*")))
    assert after - before == set(), f"leaked temp dirs: {after - before}"


# ── the wiring ────────────────────────────────────────────────────────────

def test_ci_gates_on_it_in_the_python_job():
    """It needs pydantic and the risk engine, so it must sit in the job that
    installs them — not the cargo job, where an earlier draft of this put it
    and where it would have failed on import."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    owner = None
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if "red_team.py" in str(step.get("run", "")):
                owner = job.get("name")
    assert owner == "Lint + tests (baseline gate)", (
        f"the red-team gate is in {owner!r}; it needs the Python deps job")


def test_preflight_plans_it_without_being_told():
    """preflight parses ci.yml, so the gate should appear for free."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight
    assert any("red_team.py" in cmd for _n, cmd, _d in preflight.steps(fast=False)), (
        "preflight did not pick up the red-team step — is it in a job "
        "preflight does not run locally?")
