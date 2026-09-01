"""The secret scanner must judge THIS commit, not every branch on the runner.

RC-2026-024. `gitleaks git .` with no `--log-opts` scans every ref in the
checkout. `actions/checkout` with `fetch-depth: 0` fetches every branch, so the
subject of the scan was "the repository as the runner happened to see it" —
and one leak on any branch anybody pushed turned the check red on every open
PR, naming a commit the PR never touched, while the PR-scope step directly
above it reported no leaks.

MEASURED, not argued. Against this repo with CI's own pinned 8.28.0:

    no --log-opts    -> 506 commits, 84,635,643 bytes
    --log-opts=HEAD  -> 503 commits, 84,590,620 bytes

a delta of exactly the three commits sitting on two unrelated branches. In CI
the delta was 40 commits and 287 KB, and the identical check passed three hours
later on a branch that had GROWN by two commits — same pinned binary, same
config, same baseline, same runner image. Only the refs differed.

That is the hazard `ci.yml`'s own `pull_request` gating exists to prevent — "a
step that is guaranteed red on a whole class of events carries no signal and
trains people to click past the scanner" — reached by the other door.

`.github/workflows/` has no test harness, so this assertion lives with the
other CI-parity checks: it PARSES the workflow rather than restating it, so it
cannot drift from what CI actually runs.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _secrets_job() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = wf["jobs"].get("secrets")
    assert job, "the `secrets` job is gone; this guard now watches nothing"
    return job


def _full_history_step() -> dict:
    for step in _secrets_job()["steps"]:
        if "full history" in (step.get("name") or ""):
            return step
    pytest.fail("no 'gitleaks (full history)' step in the secrets job")


def _flags(run: str) -> set[str]:
    """The flags actually PASSED, one per continuation line.

    A second mutation survived `_code_only`: the step's own `echo` named
    `--redact` while explaining the output, so `"--redact" in run` matched a
    string literal in the code rather than an argument to the command. Comments
    were only half of it — any text that quotes a flag will do.

    The command writes one flag per backslash-continued line, so the argument
    itself is what this reads. A flag mentioned in prose, an echo, or a URL
    cannot satisfy it.
    """
    out: set[str] = set()
    for ln in _code_only(run).splitlines():
        tok = ln.strip().removesuffix("\\").strip()
        if tok.startswith("--"):
            out.add(tok.split("=", 1)[0].split()[0])
    return out


def _code_only(run: str) -> str:
    """The shell, without the comments explaining it.

    FOUND BY A MUTATION THAT SURVIVED: deleting `--log-opts="HEAD"` from the
    command left every assertion here passing, because the comment block above
    it QUOTES the flag while explaining why it must be there. The guard was
    matching its own rationale.

    CLAUDE.md records four false failures from a comment that quotes the string
    a test forbids. This is the same shape inverted — a comment that quotes the
    string a test REQUIRES — and it is worse, because it fails open: the test
    goes green on exactly the defect it exists to catch.
    """
    return "\n".join(
        ln for ln in run.splitlines() if not ln.lstrip().startswith("#")
    )


def test_the_full_history_scan_names_its_range() -> None:
    """The finding itself, as an assertion."""
    assert "--log-opts" in _flags(_full_history_step()["run"]), (
        "the full-history gitleaks scan has no --log-opts, so it scans EVERY "
        "REF the runner fetched rather than this commit's history. One leak on "
        "an unrelated branch then fails every open PR, naming a commit the PR "
        "never touched. See audit/verified_findings.md RC-2026-024."
    )


def test_the_range_is_this_commit_not_every_branch() -> None:
    run = _code_only(_full_history_step()["run"])
    assert '--log-opts="HEAD"' in run or "--log-opts=HEAD" in run, (
        "--log-opts is present but does not scope to HEAD; the step's question "
        "is 'is anything still reachable in THIS ref's history'"
    )


def test_a_failure_names_where() -> None:
    """An alarm nobody can attribute is an alarm people learn to click past.

    The step printed a redacted count and no location, so a reader could not
    tell a real incident from the false ones — which is what made them
    unfalsifiable rather than merely annoying.
    """
    run = _code_only(_full_history_step()["run"])
    assert "--report-path" in _flags(_full_history_step()["run"]), (
        "no report is written, so a failure cannot be acted on"
    )
    assert "cat " in run, (
        "a report is written but never surfaced; the log still shows only a count"
    )


def test_the_secret_itself_is_still_redacted() -> None:
    """Surfacing the location must not become printing the credential.

    The report is echoed into a CI log that is readable by anyone who can read
    the run.
    """
    assert "--redact" in _flags(_full_history_step()["run"]), (
        "the report is printed to the CI log without --redact — the finding's "
        "location is the diagnostic, the secret value is not"
    )


def test_the_binary_is_still_pinned_and_checksum_verified() -> None:
    """The control must not itself execute an unverified download.

    Unchanged by RC-2026-024 and asserted here so a future edit to this step
    cannot quietly drop it while adding a flag.
    """
    step = _full_history_step()
    env = step.get("env") or {}
    assert env.get("GITLEAKS_VERSION"), "the gitleaks version is no longer pinned"
    assert env.get("GITLEAKS_SHA256"), "the download is no longer checksum-verified"
    assert "sha256sum -c" in _code_only(step["run"])


def test_the_pr_scope_step_is_still_there_and_still_gated() -> None:
    """Scoping the full-history step must not become deleting the other one.

    They ask different questions: 'does this PR introduce a secret' and 'is
    anything still reachable in this ref's history'. Losing either is a
    narrowing, not a fix.
    """
    steps = _secrets_job()["steps"]
    pr = [s for s in steps if "gitleaks" in str(s.get("uses", "")).lower()]
    assert pr, "the PR-scoped gitleaks action step is gone"
    assert any("pull_request" in str(s.get("if", "")) for s in pr), (
        "the PR-scoped step is no longer gated on pull_request, where it "
        "falls back to a bare full-history scan WITHOUT the baseline and "
        "fails on every push"
    )
