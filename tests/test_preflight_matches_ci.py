"""Preflight must run what CI runs, and must not be able to drift from it.

Written after a push that passed pytest and guard_lint and then failed CI on
ruff F541. The stray f-prefix was trivial; the finding was that "my checks
pass" had been standing in for "CI will pass" while covering a fraction of it.
The baseline gate runs six commands and I was running none of them — a raw
pytest that approximates the last, plus guard_lint from a different job.

Same defect as most of what this codebase spent that week fixing: a check
whose coverage is narrower than the confidence placed in it.

The fix is structural rather than diligent. preflight.py does not restate the
commands — restating is how a copy drifts from its original, silently, exactly
once, at the least convenient moment. It PARSES ci.yml and runs what is
written there, so a new CI step is a new preflight step for free.

These tests pin the properties that make that true, and the honesty rules that
make its output worth trusting: a tool that is missing is not a gate that
passed, and an empty plan is a broken parse rather than a clean run.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SRC = (ROOT / "scripts" / "preflight.py").read_text(encoding="utf-8")

sys.path.insert(0, str(ROOT / "scripts"))
import preflight  # noqa: E402


def _ci_python_commands() -> list[str]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    out = []
    for job in wf["jobs"].values():
        if job.get("name") not in preflight.PYTHON_JOBS:
            continue
        for step in job.get("steps", []):
            run, name = step.get("run"), (step.get("name") or "").lower()
            if not run or any(s in name for s in preflight.ALWAYS_SKIP):
                continue
            out.append(" ".join(run.split()))
    return out


# ── it runs what CI runs ──────────────────────────────────────────────────

def test_every_ci_gate_is_in_the_plan():
    planned = {cmd for _, cmd in preflight.steps(fast=False)}
    missing = [c for c in _ci_python_commands() if c not in planned]
    assert missing == [], f"CI runs these and preflight does not:\n  " + "\n  ".join(missing)


def test_the_plan_is_not_a_second_copy_of_the_commands():
    """The whole point. If the commands were restated here, this file would
    pass while the two lists quietly diverged."""
    for fragment in ("--select E9,F821,F811", "--severity-level high",
                     "-r requirements.lock"):
        assert fragment not in SRC, (
            f"{fragment!r} is hardcoded in preflight — it must come from ci.yml")
    assert "yaml.safe_load(WORKFLOW.read_text" in SRC


def test_a_new_ci_step_needs_no_edit_here():
    """Exercised, not asserted: inject a step into a parsed copy and confirm
    the derivation picks it up."""
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = next(j for j in wf["jobs"].values() if j.get("name") in preflight.PYTHON_JOBS)
    job["steps"].append({"name": "Brand new gate", "run": "echo invented"})
    tmp = ROOT / ".github" / "workflows" / "_preflight_probe.yml"
    tmp.write_text(yaml.safe_dump(wf), encoding="utf-8")
    try:
        preflight.WORKFLOW, original = tmp, preflight.WORKFLOW
        assert ("Brand new gate", "echo invented") in preflight.steps(fast=False)
    finally:
        preflight.WORKFLOW = original
        tmp.unlink()


def test_guard_lint_is_included_even_though_it_lives_in_another_job():
    planned = {cmd for _, cmd in preflight.steps(fast=False)}
    assert "python3 scripts/guard_lint.py" in planned


def test_only_python_jobs_are_attempted():
    """cargo and node jobs need toolchains a Python box will not have.
    Pretending to run them would be worse than saying they are out of scope."""
    planned = " ".join(cmd for _, cmd in preflight.steps(fast=False))
    for foreign in ("cargo ", "npm ", "anchor "):
        assert foreign not in planned


# ── the honesty rules ─────────────────────────────────────────────────────

def test_a_missing_tool_is_not_a_pass():
    assert "Absent is not passing" in SRC
    assert "that is not a pass" in SRC
    # and it must not exit 0 in that case
    assert "if skipped:\n        return 2" in SRC


def test_an_empty_plan_is_an_error_not_a_clean_run():
    """A renamed job would otherwise produce zero steps, print nothing, and
    exit 0 — the most convincing possible false green."""
    assert "no steps found" in SRC
    i = SRC.index("if not plan:")
    assert "return 2" in SRC[i:i + 400]


def test_fast_mode_says_what_it_skipped():
    assert "--fast omitted the network gates" in SRC, (
        "a quick run that looks identical to a full one is how the full one "
        "stops being run")


def test_fast_only_drops_the_network_gates():
    full = {c for _, c in preflight.steps(fast=False)}
    fast = {c for _, c in preflight.steps(fast=True)}
    dropped = full - fast
    assert dropped, "fast mode must actually drop something"
    for cmd in dropped:
        assert "pip-audit" in cmd, f"--fast dropped a gate an edit CAN break: {cmd}"


def test_one_failure_does_not_hide_the_rest():
    """Running the whole plan and reporting at the end beats bailing on the
    first failure: one run should tell you everything that is wrong."""
    assert "results.append" in SRC
    assert "for name, cmd in plan:" in SRC
    assert "sys.exit" not in SRC.split("def main")[1].split("print(")[0]


# ── it actually runs ──────────────────────────────────────────────────────

@pytest.mark.timeout(120)
def test_the_script_lists_its_plan_without_running_anything():
    r = subprocess.run([sys.executable, "scripts/preflight.py", "--list"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ruff check" in r.stdout and "guard_lint" in r.stdout
