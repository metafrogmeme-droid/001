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

  * a baseline that has drifted ABOVE reality, which is the stale
    `known_failures.txt` entry in another costume: it silently permits
    regressions back up to the recorded number;
  * a gate that reports a failure and exits 0, which is this repo's signature
    defect wearing yet another hat -- a verdict nothing acts on;
  * the gates falling out of CI, which is how they got into this state.
"""
from __future__ import annotations

import json
import subprocess
import sys
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


@pytest.mark.parametrize("name,script,baseline", GATES, ids=[g[0] for g in GATES])
def test_the_gate_exits_nonzero_when_it_reports_failure(name, script, baseline):
    """A gate that prints FAIL and exits 0 is not a gate.

    This repo has been bitten by the verdict-nobody-acts-on shape repeatedly --
    the healthcheck that could not fail, the deploy check whose exit code was
    read as a bot death. Driven rather than read, because a `return 1` is easy
    to see and easy to be wrong about: what matters is the process's status,
    and a pipeline that swallows it (`gate | tail`) reports the wrong one.
    """
    src = script.read_text(encoding="utf-8")
    assert "raise SystemExit(main())" in src, (
        f"{script.name} does not propagate main()'s return value to the exit "
        f"status; CI would read every run as success")

    # A baseline claiming FEWER findings than reality is the growth case, and
    # is what the gate must reject. Build one in a temp copy so the real
    # baseline is never touched.
    data = json.loads(baseline.read_text(encoding="utf-8"))
    lowered = dict(data)
    lowered["counts"] = {k: max(0, v - 1) for k, v in data["counts"].items()}
    lowered["total"] = sum(lowered["counts"].values())

    original = baseline.read_text(encoding="utf-8")
    baseline.write_text(json.dumps(lowered, indent=2) + "\n", encoding="utf-8")
    try:
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, cwd=ROOT, timeout=900)
        assert proc.returncode != 0, (
            f"{script.name} reported against a deliberately-lowered baseline and "
            f"still exited 0. Output:\n{proc.stdout[-1500:]}")
    finally:
        baseline.write_text(original, encoding="utf-8")


@pytest.mark.parametrize("name,script,baseline", GATES, ids=[g[0] for g in GATES])
def test_the_baseline_is_not_stale(name, script, baseline):
    """A baseline ABOVE reality silently permits regressions back up to it.

    Same rule as known_failures.txt and unreachable_baseline.txt: an entry that
    stops being true must be removed in the commit that made it untrue, or the
    list stops meaning anything. Both gates already report this and exit 1; this
    asserts they are being obeyed.
    """
    proc = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, cwd=ROOT, timeout=900)
    assert proc.returncode == 0, (
        f"{script.name} is not green.\n{proc.stdout[-2000:]}{proc.stderr[-1000:]}")
    assert "improved; re-record" not in proc.stdout, (
        f"the {name} baseline is now above reality -- something was fixed "
        f"without re-recording. Run `python3 {script.relative_to(ROOT)} "
        f"--update` in the same commit.\n{proc.stdout[-1500:]}")


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
