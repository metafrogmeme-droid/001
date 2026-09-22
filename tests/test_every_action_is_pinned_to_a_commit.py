"""A `uses:` tag is mutable, and a mutable tag is arbitrary code in CI.

Every third-party action in `.github/workflows/ci.yml` was referenced by tag:

    actions/checkout@v4            gitleaks/gitleaks-action@v2
    actions/setup-node@v4          Swatinem/rust-cache@v2
    actions/setup-python@v5        dtolnay/rust-toolchain@stable   <- a BRANCH

A git tag is a movable pointer. Whoever can move `v4` — the maintainer, or
anyone who compromises the account — runs code inside this repository's CI, in
a job that has checked the source out and holds whatever token the job was
granted. `dtolnay/rust-toolchain@stable` is not even a tag: it is a branch, so
it moves by design on every push. This is the `tj-actions/changed-files` class
of attack, where a tag was repointed and every workflow referencing it began
leaking secrets on the next run.

The fix is the one GitHub itself documents: reference the immutable commit SHA
and keep the human-readable version in a trailing comment. The SHAs were
RESOLVED, not recalled — `git ls-remote` against each upstream at the commit
this landed on — because a 40-hex string typed from memory is a string that
points at nothing, and the failure mode of getting one wrong is a CI job that
cannot start rather than one that silently does the wrong thing.

WHAT THIS RULE DOES NOT CLAIM. Pinning is not upgrading: a pinned SHA goes
stale, and nothing here tells you when a pinned action has a published security
fix. That is Dependabot's job (`.github/dependabot.yml`, which this repo does
not yet have) and it is a separate slice — stating it here rather than letting
a green test imply a freshness guarantee it does not provide, because a gate
whose coverage is overstated is the failure this repository is built around.

The rule is structural and two-way: any NEW `uses:` on a mutable ref fails, by
name, on every run. It is driven against PLANTED workflow text as well as the
real file, because the real file passes today and a mutation of the RULE would
otherwise change no verdict — a rule no input can reach is a claim that there
is a check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

#: `uses: owner/repo@ref` — the ref is what this rule is about. Local actions
#: (`uses: ./.github/actions/x`) and docker refs carry no git ref and are not
#: matched, which is correct: there is no tag to move.
USES = re.compile(r"^\s*-?\s*uses:\s*([A-Za-z0-9._-]+/[A-Za-z0-9._/-]+)@(\S+)",
                  re.M)

#: A 40-character lowercase hex string is a git commit. Nothing else is
#: immutable: `v4`, `v4.1.0`, `main` and `stable` are all movable pointers.
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _mutable_refs(text: str) -> list[str]:
    """Every `uses:` in `text` whose ref is not a commit SHA."""
    return [f"{repo}@{ref}" for repo, ref in USES.findall(text)
            if not SHA40.match(ref)]


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


# ── the real tree ───────────────────────────────────────────────────────

def test_there_are_workflows_to_check():
    """A rule that measured zero files would pass forever and mean nothing —
    the shape `_coverage_verdict` was just cured of, one gate over."""
    assert _workflow_files(), f"no workflow files found under {WORKFLOWS}"


@pytest.mark.parametrize("wf", _workflow_files(), ids=lambda p: p.name)
def test_no_action_is_referenced_by_a_mutable_ref(wf):
    mutable = _mutable_refs(wf.read_text())
    assert not mutable, (
        f"{wf.name} references {len(mutable)} action(s) by a MUTABLE ref:\n  "
        + "\n  ".join(mutable)
        + "\n\nPin each to its commit SHA, keeping the version in a trailing "
          "comment:\n  uses: owner/repo@<40-hex>  # v4\n"
          "Resolve the SHA rather than recalling it:\n"
          "  git ls-remote --tags https://github.com/owner/repo 'refs/tags/v4^{}'"
    )


def test_every_pin_carries_its_version_in_a_comment():
    """A bare 40-hex string tells the next reader nothing about what it is or
    whether it is current. The comment is what makes the pin reviewable."""
    bare = []
    for wf in _workflow_files():
        for line in wf.read_text().splitlines():
            m = re.match(r"^\s*-?\s*uses:\s*\S+@([0-9a-f]{40})\s*(.*)$", line)
            if m and not m.group(2).lstrip().startswith("#"):
                bare.append(f"{wf.name}: {line.strip()}")
    assert not bare, (
        "pinned by SHA with no version comment:\n  " + "\n  ".join(bare))


# ── driven against planted text, because the real tree passes ───────────

@pytest.mark.parametrize("planted,should_flag", [
    ("      - uses: actions/checkout@v4", True),
    ("      - uses: actions/checkout@v4.1.7", True),
    ("      - uses: dtolnay/rust-toolchain@stable", True),
    ("      - uses: some/action@main", True),
    ("      - uses: some/action@" + "a" * 39, True),   # 39 hex is not a SHA
    ("      - uses: some/action@" + "A" * 40, True),   # uppercase is not one either
    ("      - uses: actions/checkout@" + "1" * 40 + "  # v4", False),
    ("      - uses: ./.github/actions/local-thing", False),  # no ref to move
])
def test_the_rule_itself_is_driven(planted, should_flag):
    """The real workflow passes, so a mutation of the RULE would change no
    verdict against it. These inputs are the only thing that can measure it."""
    flagged = bool(_mutable_refs(planted))
    assert flagged is should_flag, f"{planted!r} -> flagged={flagged}"


def test_a_planted_mutable_ref_names_itself_in_the_failure():
    """A ratchet that fails without naming the offender costs a bisect."""
    mutable = _mutable_refs("      - uses: evil/action@v9\n"
                            "      - uses: ok/action@" + "b" * 40 + "  # v1\n")
    assert mutable == ["evil/action@v9"]
