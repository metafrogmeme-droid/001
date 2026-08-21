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


def code_only(text: str) -> str:
    """Source with comments and docstrings stripped.

    Fourth time this trap has been hit: a comment that QUOTES the string it
    forbids is indistinguishable from the code doing it when you scan raw
    text. Scan code; read prose separately.
    """
    import io
    import tokenize
    out, prev_type = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev_type in (
                    None, tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                    tokenize.DEDENT):
                continue                      # a docstring, not a value
            out.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                prev_type = tok.type
            else:
                prev_type = tok.type
    except tokenize.TokenError:
        return text
    return " ".join(out)


CODE = code_only(SRC)

sys.path.insert(0, str(ROOT / "scripts"))
import preflight  # noqa: E402


def _ci_local_commands() -> list[str]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    out = []
    for job in wf["jobs"].values():
        if job.get("name") not in preflight.LOCAL_JOBS:
            continue
        for step in job.get("steps", []):
            run, name = step.get("run"), (step.get("name") or "").lower()
            if not run or any(s in name for s in preflight.ALWAYS_SKIP):
                continue
            out.append(" ".join(run.split()))
    return out


# ── it runs what CI runs ──────────────────────────────────────────────────

def test_every_ci_gate_is_in_the_plan():
    # Compare NORMALIZED on both sides. The plan carries commands verbatim —
    # a `run: |` block is a multi-line script whose newlines are semantic,
    # and flattening it for execution broke it (see _flatten's docstring) —
    # so parity is judged on whitespace-normalized text, not on the exact
    # bytes preflight must preserve to run correctly.
    planned = {" ".join(cmd.split()) for _, cmd, _ in preflight.steps(fast=False)}
    missing = [c for c in _ci_local_commands() if c not in planned]
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
        assert any(n.endswith("Brand new gate") and c == "echo invented"
                   for n, c, _ in preflight.steps(fast=False))
    finally:
        preflight.WORKFLOW = original
        tmp.unlink()


def test_guard_lint_is_included_even_though_it_lives_in_another_job():
    planned = {cmd for _, cmd, _ in preflight.steps(fast=False)}
    assert "python3 scripts/guard_lint.py" in planned


def test_every_npm_suite_is_covered_and_runs_in_its_own_directory():
    """The first version of this script omitted the web suite while printing
    "this is what CI will run" — the exact overclaim it exists to prevent, in
    itself."""
    # The step is now a script that re-prints failures within log-tail reach
    # (PR #993's flake failed with its NAME beyond the log API's window) —
    # so "covered" means the script still runs `npm test`, from its own package.
    #
    # THIS ASSERTED A LIST OF EXACTLY ONE until `site/` arrived, so adding a
    # second npm package failed it — for the right reason (a new suite showed
    # up) with the wrong message (it read as the app suite having moved). The
    # property worth pinning was never "there is one npm suite"; it is that
    # NONE of them runs from the repo root, where it would fail for a reason
    # that has nothing to do with the code. Both halves are checked now: every
    # npm suite has its own directory, and the app one is still among them.
    dirs = sorted(wd for _, c, wd in preflight.steps(fast=False) if "npm test" in c)
    assert dirs, "no npm suite is covered at all"
    assert "." not in dirs, (
        f"an npm suite runs from the repo root: {dirs} — there is no "
        "package.json there for it to find")
    assert "app" in dirs, "the web app suite must stay covered"


def test_the_summary_names_what_it_cannot_judge():
    """"All gates green — this is what CI will run" was false: cargo,
    solidity, gitleaks and token tooling are never touched here."""
    assert "this is what CI will run" not in CODE, "the overclaim must be gone"
    assert "All local gates green" in SRC
    assert "Still only CI can judge" in SRC
    missing = preflight.uncovered()
    for job in ("Staking program (cargo)", "Secret scan (gitleaks)",
                "Rune NFT (solidity)", "Token tooling (node)"):
        assert job in missing


def test_token_tooling_is_excluded_deliberately_not_accidentally():
    """It is a node job, so "we only do Python" does not explain it. One of
    its steps curl-pipes a Solana installer."""
    assert "Token tooling (node)" not in preflight.LOCAL_JOBS
    assert "curl-pipes a Solana validator installer" in preflight.__doc__ or \
        "curl-pipes a Solana validator installer" in SRC


def test_no_gate_needing_an_absent_toolchain_is_attempted():
    """Pretending to run these would be worse than naming them uncovered."""
    planned = " ".join(cmd for _, cmd, _ in preflight.steps(fast=False))
    for foreign in ("cargo ", "anchor ", "solana-test-validator"):
        assert foreign not in planned, (
            "these need toolchains a dev box will not have; pretending to run "
            "them would be worse than naming them as uncovered")


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


def test_fast_only_drops_dependency_advisory_gates():
    """The property is "--fast must not hide a gate an ordinary edit can break".

    This asserted `"pip-audit" in cmd`, which was that property back when
    pip-audit was the only SCA gate in the workflow. `FAST_SKIP` has always
    matched on the step NAME — including "SCA —" — so when M3 added the npm
    advisory ratchet for app/, preflight dropped it exactly as designed and this
    test called it a violation.

    The generalisation keeps the guard rather than widening it to nothing: a
    dropped gate must read a DEPENDENCY MANIFEST, which is the reason neither
    can go red from editing a .py or .js file. Anything else disappearing from
    the fast loop is still a failure.
    """
    full = {c for _, c, _ in preflight.steps(fast=False)}
    fast = {c for _, c, _ in preflight.steps(fast=True)}
    dropped = full - fast
    assert dropped, "fast mode must actually drop something"
    # Every known dependency-advisory gate, by the command that runs it.
    ADVISORY_GATES = ("pip-audit", "audit_gate.mjs", "npm audit", "cargo audit")
    for cmd in dropped:
        assert any(g in cmd for g in ADVISORY_GATES), (
            f"--fast dropped a gate an edit CAN break: {cmd}\n"
            "Only dependency-advisory gates may be skipped — they cannot fail "
            "from a source edit, which is the whole reason dropping them is safe.")


def test_fast_still_drops_something_real():
    """Guards the guard above: if FAST_SKIP stopped matching anything, the loop
    would pass vacuously while --fast silently became identical to a full run."""
    full = {c for _, c, _ in preflight.steps(fast=False)}
    fast = {c for _, c, _ in preflight.steps(fast=True)}
    assert len(full - fast) >= 2, (
        "--fast now drops fewer than two gates; it is meant to drop every "
        "dependency-advisory gate in the plan")


def test_one_failure_does_not_hide_the_rest():
    """Running the whole plan and reporting at the end beats bailing on the
    first failure: one run should tell you everything that is wrong."""
    assert "results.append" in SRC
    assert "for name, cmd, wd in plan:" in SRC
    assert "sys.exit" not in SRC.split("def main")[1].split("print(")[0]


# ── it actually runs ──────────────────────────────────────────────────────

@pytest.mark.timeout(120)
def test_the_script_lists_its_plan_without_running_anything():
    r = subprocess.run([sys.executable, "scripts/preflight.py", "--list"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ruff check" in r.stdout and "guard_lint" in r.stdout
