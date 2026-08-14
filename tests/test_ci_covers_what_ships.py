"""The module the production image RUNS by default was never scanned.

`bandit -r bot/` was the only SAST in this repository, and `Dockerfile`'s CMD is
`uvicorn api_bridge:app`. So the FastAPI bridge holding the bearer-token auth,
the rate limiter, `/confirm`, `/portfolio/close` and `/risk/halt` sat outside the
scan, together with `dashboard_api.py` and every script in `scripts/` — including
the gate scripts that decide whether anything else ships.

Separately, `app/` — 228 Express routes, the largest HTTP surface here — had
tests in CI and nothing else: no SAST, no SCA, no parse check.

WHAT THESE TESTS PIN, AND WHY EACH IS DERIVED RATHER THAN LISTED

A test that hardcoded "bandit must mention api_bridge.py" would be a second
copy of the fact, and would not notice the day the image's entrypoint changes.
So the entrypoint is read out of the Dockerfile and required to be in scope: the
rule is "whatever the container runs by default is statically analysed", which
stays true when the answer changes.

WHAT THEY DELIBERATELY DO NOT DO

They do not assert the gates are CLEAN — `ci_test_gate.py` and the gates
themselves do that, and re-asserting it here would pass or fail for reasons
these tests cannot explain. They assert the gates EXIST and point at the right
things, which is precisely what was missing: the coverage gap was never a failing
check, it was the absence of one.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML parses the workflow")

REPO = pathlib.Path(__file__).resolve().parent.parent
CI = REPO / ".github" / "workflows" / "ci.yml"
DOCKERFILE = REPO / "Dockerfile"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _steps(job_name: str) -> list[dict]:
    for job in _workflow().get("jobs", {}).values():
        if job.get("name") == job_name:
            return job.get("steps", [])
    raise AssertionError(f"no job named {job_name!r} — has ci.yml changed shape?")


def _run_of(job_name: str, step_substring: str) -> str:
    for step in _steps(job_name):
        if step_substring.lower() in str(step.get("name", "")).lower():
            return str(step.get("run", ""))
    raise AssertionError(
        f"{job_name!r} has no step matching {step_substring!r}: "
        f"{[s.get('name') for s in _steps(job_name)]}")


# ── M1: the entrypoint is scanned ────────────────────────────────────

def test_the_container_entrypoint_is_in_bandits_scope():
    """Derived from the Dockerfile, so it survives the entrypoint changing."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    cmd = re.search(r'^CMD\s+\[(.+?)\]', dockerfile, re.M | re.S)
    assert cmd, "no CMD in the Dockerfile — re-point this check"
    # 'uvicorn', 'api_bridge:app', ... → the module is the part before ':'
    modules = re.findall(r'"([A-Za-z_][A-Za-z0-9_]*):app"', cmd.group(1))
    assert modules, f"could not read a module out of CMD: {cmd.group(1)[:120]}"

    scope = _run_of("Lint + tests (baseline gate)", "bandit")
    for mod in modules:
        assert f"{mod}.py" in scope, (
            f"the image runs {mod}:app by default and bandit does not scan "
            f"{mod}.py — the production entrypoint gets no SAST.\nscope: {scope}")


def test_the_gate_scripts_are_scanned_too():
    """`scripts/` decides whether anything ships. It was outside the scan, and
    the one high/high finding at the wider scope came from there."""
    scope = _run_of("Lint + tests (baseline gate)", "bandit")
    assert "scripts/" in scope


def test_bandit_still_scans_the_bot():
    """The original scope, so widening never silently replaces it."""
    assert "bot/" in _run_of("Lint + tests (baseline gate)", "bandit")


def test_the_shell_true_is_annotated_not_globally_silenced():
    """The one finding the wider scope surfaced is preflight's deliberate
    `shell=True`. It is recorded with a SPECIFIC code at the line, so the next
    `shell=True` in this tree still has to justify itself — a blanket `-s B602`
    on the command line would have silenced the whole class."""
    src = (REPO / "scripts" / "preflight.py").read_text(encoding="utf-8")
    # Anchored to the CODE line, not to the file. The first draft asserted
    # `"# nosec B602" in src` and a mutation deleting the real annotation still
    # passed — because the comment block above the call QUOTES it, and a comment
    # that quotes the thing it describes is indistinguishable from the thing.
    # This repo documents that failure mode; the test written to prevent it had
    # it.
    call_lines = [ln for ln in src.split("\n") if "subprocess.call(" in ln]
    assert call_lines, "subprocess.call moved — re-point this check"
    assert any("# nosec B602" in ln for ln in call_lines), (
        "the nosec is not ON the subprocess.call line, so bandit will fail the "
        f"widened scan. Lines found: {call_lines}")
    assert "-s B602" not in _run_of("Lint + tests (baseline gate)", "bandit"), (
        "B602 is skipped for the whole scan — every future shell=True is now "
        "invisible, which is not what the annotation was for")


# ── M3: app/ has more than tests ─────────────────────────────────────

def test_app_has_a_parse_gate():
    """A syntax error in a route file does not fail the suite: the suite loads
    only the modules its tests require, and 224 files here are not all reachable
    that way. It fails at runtime, on the first request to whatever was never
    imported."""
    run = _run_of("Web app (express)", "parse")
    assert "node --check" in run
    for tree in ("routes/", "lib/"):
        assert tree in run, f"the parse gate does not cover app/{tree}"


def test_app_has_a_dependency_ratchet():
    run = _run_of("Web app (express)", "npm advisory ratchet")
    assert "audit_gate.mjs" in run


def test_the_ratchet_is_the_same_script_token_uses():
    """One ratchet, two trees. A second copy would drift, and the argument for
    a ratchet over `npm audit --audit-level=high` is the part that would be lost
    in the copy."""
    app_run = _run_of("Web app (express)", "npm advisory ratchet")
    token_run = _run_of("Token tooling (node)", "npm advisory ratchet")
    assert "audit_gate.mjs" in app_run and "audit_gate.mjs" in token_run
    assert (REPO / "token" / "scripts" / "audit_gate.mjs").exists()
    assert not (REPO / "app" / "scripts" / "audit_gate.mjs").exists(), (
        "app/ grew its own copy of the ratchet — one of the two will rot")


def test_the_ratchet_root_is_a_positional_arg_not_an_env_var():
    """preflight.py runs CI steps by parsing the `run:` string and ignores each
    step's `env:` block. An env-var root would be set in CI and unset locally,
    so preflight would audit token/ while reporting the app/ step as passing —
    a gate checking the wrong tree and saying nothing."""
    for step in _steps("Web app (express)"):
        if "ratchet" in str(step.get("name", "")).lower():
            assert not step.get("env"), (
                "the ratchet step carries an env block; preflight will not see it")
            assert re.search(r"audit_gate\.mjs\s+\S", str(step.get("run", ""))), (
                "no root argument passed — this would audit token/ from app/")
            return
    raise AssertionError("ratchet step not found")


def test_the_app_baseline_exists_and_is_a_floor_not_an_approval():
    baseline = REPO / "app" / ".audit-baseline.json"
    assert baseline.exists(), (
        "no app/.audit-baseline.json — the ratchet fails closed and the job is "
        "red on its first run")
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert "advisoryIds" in data and "counts" in data
    assert "RATCHET" in data.get("_comment", ""), (
        "the baseline does not say what it is; a future reader reads it as a "
        "list of accepted vulnerabilities")


# ── the gates reach preflight ────────────────────────────────────────

def test_the_new_gates_run_locally_too():
    """The property CLAUDE.md advertises: a new CI step becomes a preflight step
    for free, because preflight PARSES ci.yml rather than restating it. Asserted
    because a step added to a job preflight does not run would be a gate nobody
    sees until CI."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_preflight", REPO / "scripts" / "preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = [name for name, _cmd, _wd in mod.steps(fast=False)]
    for wanted in ("Parse", "npm advisory ratchet", "bandit"):
        assert any(wanted.lower() in n.lower() for n in names), (
            f"{wanted!r} is in ci.yml but not in the preflight plan: {names}")
