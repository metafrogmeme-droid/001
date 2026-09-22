"""A workflow that declares no `permissions:` inherits whatever a web UI says.

`.github/workflows/ci.yml` declared no workflow-level `permissions:`, so the
GITHUB_TOKEN handed to every job was whatever the repository's *Workflow
permissions* setting happened to be — a control that lives in a web form,
outside this repository, changeable by anyone with admin rights, and readable
by no test here. Eight jobs, and the scope of the token each one runs with was
decided somewhere `git log` cannot see.

WHAT THIS IS NOT, and the distinction is the whole honesty of the slice. It is
NOT the claim that the token is over-privileged today. The `secrets` job's own
comment records the opposite, from a real CI run: gitleaks-action called
`GET /repos/{o}/{r}/pulls/{n}/commits` and died on **403 Resource not accessible
by integration**, the response naming `pull_requests=read` as what it wanted. A
token that is refused `pull-requests: read` is not a token carrying write-all.
So the default in force on 2026-09-22 was already restricted, and that is
measured rather than assumed.

What the declaration buys is that the grant stops being decided elsewhere:

  * an admin flipping that setting to "Read and write permissions" can no
    longer silently widen all eight jobs, because a job-or-workflow `permissions`
    block WINS over the repository default;
  * a job added tomorrow inherits a stated read-only floor rather than whatever
    the setting reads at that moment;
  * the grant becomes reviewable in the diff, which is where every other gate
    in this repo lives.

THE RULE IS ABOUT THE EFFECTIVE SCOPE, NOT ABOUT WHERE IT IS WRITTEN. GitHub
resolves a job's permissions as: the job's own block if it has one, otherwise
the workflow's, otherwise the repository default. A job-level block REPLACES the
workflow-level one outright — it does not merge — which is why `secrets` has to
restate `contents: read` beside the `pull-requests: read` it actually came for.
So this guard computes what each job ends up with and reads THAT, rather than
grepping for a key.

Write is not forbidden outright; it is forbidden SILENTLY. A scope that grants
write must be named in `WRITE_SCOPES_ALLOWED` with its reason, and the rule is
two-way as `known_failures.txt` is: an entry that stops applying is a hard
failure, so a stale exemption cannot quietly cover the next one. The tree is at
zero write scopes today, so this holds from here with nothing to forgive — the
ruling `test_a_comment_names_the_default_the_flag_has` already set.

Driven against PLANTED workflows as well as the real file, because the real
file passes and a mutation of the RULE would otherwise change no verdict
against it — and a rule no input can reach is a claim that there is a check.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: Scopes that may be granted at `write` level, each with the reason. Empty is
#: the honest state today: no job in this workflow writes anything back to
#: GitHub — no artifact upload, no release, no PR comment, no push. A row added
#: here without a reason fails, and a row whose scope no longer appears in the
#: workflow fails too.
WRITE_SCOPES_ALLOWED: dict[str, str] = {}

#: The two blanket forms. `write-all` is the one that matters; `read-all` grants
#: every read scope, which is wider than anything here needs and is still a
#: decision somebody should have to write down.
BLANKET = ("write-all", "read-all")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _jobs() -> dict[str, Any]:
    return _workflow().get("jobs") or {}


def effective_permissions(wf: dict[str, Any], job: dict[str, Any]) -> Any:
    """What this job's GITHUB_TOKEN ends up with.

    A job-level block REPLACES the workflow-level one rather than merging with
    it, which is GitHub's documented resolution and the reason `secrets` has to
    restate `contents: read`. `None` means neither level declared anything, so
    the answer is the repository setting — which is exactly the state this file
    exists to remove.
    """
    if "permissions" in job:
        return job["permissions"]
    return wf.get("permissions")


def _write_scopes(perms: Any) -> list[str]:
    """Every scope in `perms` granted at write level."""
    if isinstance(perms, str):
        return [perms] if perms == "write-all" else []
    if not isinstance(perms, dict):
        return []
    return [k for k, v in perms.items() if str(v).strip() == "write"]


# ── the real workflow ───────────────────────────────────────────────────

def test_there_is_a_workflow_to_check():
    """A rule that measured zero files would pass forever and mean nothing."""
    assert WORKFLOW.exists(), f"{WORKFLOW} is gone; this guard now watches nothing"
    assert _jobs(), "ci.yml declares no jobs; this guard now watches nothing"


def test_the_workflow_declares_a_token_scope():
    """The finding itself. Without this key the grant is a web-UI setting."""
    wf = _workflow()
    assert "permissions" in wf, (
        "ci.yml declares no workflow-level `permissions:`, so every job's "
        "GITHUB_TOKEN is whatever the repository's Workflow permissions "
        "setting says — a control outside this repo that no test here can "
        "read.\n\nAdd, above `jobs:`:\n  permissions:\n    contents: read"
    )


@pytest.mark.parametrize("job_name", sorted(_jobs()))
def test_every_job_ends_up_with_a_declared_scope(job_name):
    """Inheriting the workflow's block counts; inheriting the repo's does not."""
    wf = _workflow()
    eff = effective_permissions(wf, wf["jobs"][job_name])
    assert eff is not None, (
        f"job `{job_name}` declares no permissions and neither does the "
        f"workflow, so its token scope comes from the repository setting"
    )


@pytest.mark.parametrize("job_name", sorted(_jobs()))
def test_no_job_is_granted_write_without_a_reason(job_name):
    wf = _workflow()
    eff = effective_permissions(wf, wf["jobs"][job_name])
    undeclared = [s for s in _write_scopes(eff) if s not in WRITE_SCOPES_ALLOWED]
    assert not undeclared, (
        f"job `{job_name}` is granted write on {undeclared} with no recorded "
        f"reason. If a job genuinely needs to write back to GitHub, add the "
        f"scope to WRITE_SCOPES_ALLOWED in this file with why."
    )


def _checks_out(job: dict[str, Any]) -> bool:
    """Does this job run `actions/checkout`?"""
    return any("actions/checkout" in str(s.get("uses", ""))
               for s in (job.get("steps") or []))


def grants_contents(eff: Any) -> bool:
    """Can a token with these permissions clone the repo?

    Extracted so the RULE's own verdict is driveable on planted input. The
    real workflow grants contents everywhere, so a mutation of what this
    accepts would otherwise change no verdict anywhere — the assertion would
    be a claim that there is a check rather than a check.
    """
    if eff == "write-all":
        return True
    if not isinstance(eff, dict):
        return False
    return str(eff.get("contents", "")).strip() in ("read", "write")


@pytest.mark.parametrize("job_name", sorted(_jobs()))
def test_every_job_that_checks_out_can_read_contents(job_name):
    """THE TRAP THE REPLACE-NOT-MERGE RULE SETS, and the reason this is derived
    rather than hand-written.

    A job-level block does not merge with the workflow's — it REPLACES it. So a
    job that declares `permissions: {pull-requests: read}` to get one extra
    scope has silently *dropped* `contents: read`, and `actions/checkout` loses
    the scope it clones with. The result is still read-only and still declared,
    so every other rule in this file passes it: only asking what the job
    actually RUNS can see it.

    Derived from the steps, so a job added tomorrow is covered without anybody
    editing a list — the `/setllm` ten-of-eleven shape this repo keeps finding.
    """
    wf = _workflow()
    job = wf["jobs"][job_name]
    if not _checks_out(job):
        pytest.skip(f"`{job_name}` runs no checkout")
    eff = effective_permissions(wf, job)
    assert grants_contents(eff), (
        f"job `{job_name}` runs actions/checkout but its effective permissions "
        f"{eff!r} grant no `contents` scope. A job-level block REPLACES the "
        f"workflow's rather than merging, so `contents: read` has to be "
        f"restated in the job's own block."
    )


def test_no_blanket_grant_anywhere():
    """`permissions: write-all` is the shape this whole guard exists to refuse,
    and `read-all` is wider than any job here needs."""
    wf = _workflow()
    found = []
    if isinstance(wf.get("permissions"), str) and wf["permissions"] in BLANKET:
        found.append(f"workflow: {wf['permissions']}")
    for name, job in (wf.get("jobs") or {}).items():
        p = job.get("permissions")
        if isinstance(p, str) and p in BLANKET:
            found.append(f"{name}: {p}")
    assert not found, f"blanket token grant(s): {found}"


def stale_rows(allowed: dict[str, str], live: set[str]) -> list[str]:
    """Allowlist entries no job is granted any more.

    Extracted because WRITE_SCOPES_ALLOWED is EMPTY today, so this rule computes
    over `{}` against the real tree and can never fire — vacuous, and a mutation
    of it would change no verdict. Planted input is the only thing that can
    measure it, which is the ruling `tier_gate_noun_baseline` already records.
    """
    return sorted(set(allowed) - live)


def reasonless_rows(allowed: dict[str, str]) -> list[str]:
    """Allowlist entries with no reason written down. Vacuous today for the same
    reason, and driven on planted input for the same reason."""
    return sorted(k for k, v in allowed.items() if not str(v).strip())


@pytest.mark.parametrize("allowed,live,expected", [
    ({}, set(), []),
    ({"contents": "why"}, {"contents"}, []),
    ({"contents": "why"}, set(), ["contents"]),
    ({"contents": "why", "packages": "why"}, {"contents"}, ["packages"]),
])
def test_the_stale_rule_is_driven(allowed, live, expected):
    assert stale_rows(allowed, live) == expected


@pytest.mark.parametrize("allowed,expected", [
    ({}, []),
    ({"contents": "a real reason"}, []),
    ({"contents": ""}, ["contents"]),
    ({"contents": "   "}, ["contents"]),
])
def test_the_reason_rule_is_driven(allowed, expected):
    assert reasonless_rows(allowed) == expected


def test_the_write_allowlist_has_no_stale_row():
    """Two-way, the `known_failures.txt` rule: an exemption that stopped
    applying must be deleted in the same commit, or it sits there ready to
    acquit the next write nobody argued for."""
    wf = _workflow()
    live = set()
    for job in (wf.get("jobs") or {}).values():
        live.update(_write_scopes(effective_permissions(wf, job)))
    stale = stale_rows(WRITE_SCOPES_ALLOWED, live)
    assert not stale, (
        f"WRITE_SCOPES_ALLOWED names {stale}, which no job is granted any "
        f"more. Delete the row."
    )


def test_every_allowed_write_scope_carries_a_reason():
    empty = reasonless_rows(WRITE_SCOPES_ALLOWED)
    assert not empty, f"write scope(s) allowed with no reason written down: {empty}"


# ── driven against planted workflows, because the real one passes ───────

def _planted(text: str) -> dict[str, Any]:
    return yaml.safe_load(text)


PLANTED_NO_TOP = """
name: X
jobs:
  a:
    steps: [{run: "true"}]
"""

PLANTED_TOP_READ = """
name: X
permissions:
  contents: read
jobs:
  a:
    steps: [{run: "true"}]
"""

PLANTED_JOB_OVERRIDES = """
name: X
permissions:
  contents: read
jobs:
  a:
    permissions:
      contents: read
      pull-requests: read
    steps: [{run: "true"}]
"""

PLANTED_JOB_WRITE = """
name: X
permissions:
  contents: read
jobs:
  a:
    permissions:
      contents: write
    steps: [{run: "true"}]
"""

PLANTED_WRITE_ALL = """
name: X
permissions: write-all
jobs:
  a:
    steps: [{run: "true"}]
"""


def test_the_effective_reading_is_driven():
    """A job with no block inherits the workflow's; a job with one REPLACES it.
    The replace half is the one a reader gets wrong, and it is why `secrets`
    restates `contents: read` beside the scope it came for."""
    wf = _planted(PLANTED_NO_TOP)
    assert effective_permissions(wf, wf["jobs"]["a"]) is None

    wf = _planted(PLANTED_TOP_READ)
    assert effective_permissions(wf, wf["jobs"]["a"]) == {"contents": "read"}

    wf = _planted(PLANTED_JOB_OVERRIDES)
    eff = effective_permissions(wf, wf["jobs"]["a"])
    assert eff == {"contents": "read", "pull-requests": "read"}, (
        "a job-level block must REPLACE the workflow's, not merge with it")


@pytest.mark.parametrize("text,expected", [
    (PLANTED_TOP_READ, []),
    (PLANTED_JOB_OVERRIDES, []),
    (PLANTED_JOB_WRITE, ["contents"]),
    (PLANTED_WRITE_ALL, ["write-all"]),
])
def test_the_write_detector_is_driven(text, expected):
    """The real file grants no write, so only planted input can measure this."""
    wf = _planted(text)
    assert _write_scopes(effective_permissions(wf, wf["jobs"]["a"])) == expected


def test_a_planted_missing_declaration_is_caught():
    """The rule must actually fire on the shape it forbids — otherwise it is a
    claim that there is a check."""
    wf = _planted(PLANTED_NO_TOP)
    assert "permissions" not in wf
    assert effective_permissions(wf, wf["jobs"]["a"]) is None

PLANTED_JOB_DROPS_CONTENTS = """
name: X
permissions:
  contents: read
jobs:
  a:
    permissions:
      pull-requests: read
    steps:
      - uses: actions/checkout@abc
"""


def test_the_dropped_contents_case_is_driven():
    """The real workflow restates `contents: read` in its one overriding job, so
    only planted input can measure this rule."""
    wf = _planted(PLANTED_JOB_DROPS_CONTENTS)
    job = wf["jobs"]["a"]
    assert _checks_out(job), "the planted job must actually run a checkout"
    eff = effective_permissions(wf, job)
    assert eff == {"pull-requests": "read"}, "job block must REPLACE, not merge"
    assert "contents" not in eff, (
        "this is the shape the rule exists for: declared, read-only, and "
        "missing the scope its own checkout needs")


@pytest.mark.parametrize("eff,ok,why", [
    ({"contents": "read"}, True, "the ordinary grant"),
    ({"contents": "write"}, True, "write implies read"),
    ("write-all", True, "blanket grants it (refused elsewhere, not here)"),
    ({"pull-requests": "read"}, False, "THE DEFECT: declared, read-only, no contents"),
    ({}, False, "an empty block grants nothing"),
    (None, False, "nothing declared at either level"),
])
def test_the_contents_predicate_is_driven(eff, ok, why):
    """Every job in the real workflow grants contents, so only these inputs can
    measure what the rule accepts."""
    assert grants_contents(eff) is ok, f"{eff!r}: {why}"
