"""The audit register is stored twice. Nothing was checking the two agree.

`audit/verified_findings.md` is the prose a human reads. `audit/generate_artifact.py`
carries the same statuses as Python literals and is what produces the
machine-readable `runeclaw-audit.json`. They are edited by hand, separately.

THIS IS NOT HYPOTHETICAL. RC-2026-012 was fixed on main in d0dd61c and BOTH
files went on saying OPEN until someone noticed by eye. That is the register
that exists to say what is outstanding, silently wrong about what is
outstanding — and the JSON is the half that gets published, so the drift
travels further than the file people actually read.

A status in two places with nothing comparing them is the same shape as a
count in prose with nothing pinning it, which is why `tests/ruff_baseline.json`
and `tests/test_claude_md_accuracy.py` exist. This is that guard for the
register.

WHAT IT DELIBERATELY DOES NOT DO: it does not check that a status is CORRECT —
no test can know whether a fix really landed. It checks the far cruder thing
that cannot be answered wrongly: do the two copies say the same word.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "audit" / "generate_artifact.py"
PROSE = ROOT / "audit" / "verified_findings.md"

FINDING_ID = re.compile(r"RC-\d{4}-\d{3}")


def _statuses_from_generator() -> dict[str, str]:
    """Read the `dict(id=..., status=...)` literals without importing.

    Parsed rather than imported: importing runs the generator's module body,
    which writes a file. A test that mutates the tree to read it is a test that
    can fail the next one.
    """
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "dict"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        fid, status = kw.get("id"), kw.get("status")
        if not (isinstance(fid, ast.Constant) and isinstance(status, ast.Constant)):
            continue
        if isinstance(fid.value, str) and FINDING_ID.fullmatch(fid.value):
            out[fid.value] = status.value
    return out


def _statuses_from_prose() -> dict[str, str]:
    """First `- **Status**: X` under each `## RC-....-...` heading.

    A finding can have more than one section (RC-2026-001 has a corroborating
    -evidence one). Only sections that actually declare a status are read, and
    two sections declaring DIFFERENT statuses for one id is itself a failure —
    tested separately below rather than silently resolved here.
    """
    out: dict[str, list[str]] = {}
    for fid, status in _prose_status_pairs():
        out.setdefault(fid, []).append(status)
    return {k: v[0] for k, v in out.items()}


def _prose_status_pairs() -> list[tuple[str, str]]:
    """(id, status) for every declared status, in document order.

    Two parsing details, both found by this file failing on its first run
    rather than by reading the markdown:

    - A heading can WRAP across two `## ` lines. RC-2026-001's does. Treating
      the continuation as a new section reset the id to None and the finding
      vanished from this side of the comparison entirely — a guard reporting a
      finding as missing when it is present is the accusation it exists to
      prevent, so consecutive `## ` lines are one heading.
    - The prose writes `PARTIALLY FIXED` with a space where the generator
      writes `PARTIALLY_FIXED`. That is a spelling difference, not a
      disagreement, and a test that called it one would be noise on every run.
    """
    text = PROSE.read_text(encoding="utf-8")
    pairs: list[tuple[str, str]] = []
    current: str | None = None
    prev_was_heading = False
    for line in text.splitlines():
        if line.startswith("## "):
            m = FINDING_ID.search(line)
            if m:
                current = m.group(0)
            elif not prev_was_heading:
                current = None
            prev_was_heading = True
            continue
        prev_was_heading = False
        if current and line.lstrip().startswith("- **Status**:"):
            m = re.search(r"\*\*Status\*\*:\s*\**([A-Z][A-Z_ ]*[A-Z])\**", line)
            if m:
                pairs.append((current, m.group(1).strip().replace(" ", "_")))
    return pairs


def test_the_generator_declares_some_findings():
    """Guard the guard: a parser that finds nothing passes everything."""
    gen = _statuses_from_generator()
    assert len(gen) >= 12, f"only parsed {len(gen)} findings — the parser has drifted"
    assert "RC-2026-001" in gen


def test_the_prose_declares_some_findings():
    prose = _statuses_from_prose()
    assert len(prose) >= 12, f"only parsed {len(prose)} findings — the parser has drifted"


# Findings the prose register documents and the published artifact does NOT.
#
# THIS FILE FOUND THEM ON ITS FIRST RUN. `runeclaw-audit.json` announces "12
# findings"; the register documents twenty-two. Ten are unpublished — and one
# of them, RC-2026-018, is an OPEN CRITICAL. The release decision was being
# computed against the twelve, so "no CRITICAL remains open" was a true
# statement about the artifact and a false one about the audit.
#
# 013-016 are the four HIGHs of Batch 3, written narratively (bold lead-ins,
# no `- **Status**:` block) with the other eighteen in
# `audit/workflow_raw_findings.md` as B3-01…B3-22. 017-022 carry full status
# blocks and are simply absent from the generator.
#
# A RATCHET IN BOTH DIRECTIONS, the `known_failures.txt` rule. A new entry
# means somebody documented a finding the artifact will not publish. An entry
# that gets published must be deleted here in the SAME commit, or this test
# fails on the stale line — so the list cannot quietly outlive the gap.
#
# It is a list and not a count because a count would let one finding be
# published while another went missing and still read as unchanged.
UNPUBLISHED_IN_ARTIFACT: set[str] = set()
# EMPTY, and emptied by publishing rather than by baselining. All ten were
# transcribed into generate_artifact.py in the same commit that cleared them —
# the rule this list states about itself. The note above records why they were
# deferred ("needs the auditor's severity and standard mappings, which are not
# mine to invent"); they are this auditor's own findings, so the mappings were
# mine to supply.
#
# The gap it was sizing is closed from BOTH ends now. These ten are published,
# and the artifact separately ingests all 162 verified findings by parsing
# workflow_raw_findings.md, so the register no longer has to be transcribed by
# hand to be represented. Keep the list — a future finding documented in prose
# and never published re-fills it, which is the whole point.


def _all_ids_in_prose() -> set[str]:
    """Every id the prose MENTIONS, not only those with a status block.

    The cruder question on purpose: 013-016 have no status block, and a parser
    that only saw formal blocks would report the gap as six when it is ten —
    under-reporting the very drift this is here to size.
    """
    return set(FINDING_ID.findall(PROSE.read_text(encoding="utf-8")))


def test_nothing_is_published_that_the_prose_does_not_document():
    """The direction that actually went wrong once: the artifact inventing one."""
    gen = _statuses_from_generator()
    orphans = set(gen) - _all_ids_in_prose()
    assert not orphans, (
        f"generate_artifact.py publishes {sorted(orphans)}, which "
        "verified_findings.md does not document at all"
    )


def test_the_unpublished_set_has_not_grown_or_gone_stale():
    gen = _statuses_from_generator()
    gap = _all_ids_in_prose() - set(gen)
    new = gap - UNPUBLISHED_IN_ARTIFACT
    assert not new, (
        f"{sorted(new)} are documented in verified_findings.md but missing from "
        "generate_artifact.py, so they will not appear in runeclaw-audit.json. "
        "Add them to the generator, or record them in UNPUBLISHED_IN_ARTIFACT "
        "with the reason."
    )
    stale = UNPUBLISHED_IN_ARTIFACT - gap
    assert not stale, (
        f"{sorted(stale)} are now published — delete them from "
        "UNPUBLISHED_IN_ARTIFACT in the same commit that published them"
    )


@pytest.mark.parametrize("fid", sorted(_statuses_from_generator()))
def test_the_two_halves_agree_on_status(fid):
    gen, prose = _statuses_from_generator(), _statuses_from_prose()
    assert gen[fid] == prose.get(fid), (
        f"{fid}: generate_artifact.py says {gen[fid]}, "
        f"verified_findings.md says {prose.get(fid)}"
    )


def test_a_finding_does_not_declare_two_different_statuses_in_the_prose():
    text = PROSE.read_text(encoding="utf-8")
    seen: dict[str, set[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            m = FINDING_ID.search(line)
            current = m.group(0) if m else None
            continue
        if current and line.lstrip().startswith("- **Status**:"):
            m = re.search(r"\*\*Status\*\*:\s*([A-Z_]+)", line)
            if m:
                seen.setdefault(current, set()).add(m.group(1))
    conflicts = {k: v for k, v in seen.items() if len(v) > 1}
    assert not conflicts, f"a finding declares conflicting statuses: {conflicts}"


def _release_decision() -> dict:
    """The decision as PUBLISHED, read from the artifact, not from the source.

    This used to grep the generator for `release_decision=dict(...)`. That
    worked while the decision was a literal and stopped working the moment it
    became derived — which was the fix for the drift this whole file is about.
    Its own assertion said "this test has drifted", and it was right: a source
    scan cannot see a computed value, so it could only ever check a decision
    written the one way it knew.

    Reading the artifact is also the better question. What ships to a reader is
    runeclaw-audit.json; how generate_artifact.py spells it is an implementation
    detail. Currency of that file is a separate concern and is asserted
    separately below, so this can read the committed bytes without a subprocess
    and without writing anything.
    """
    import json

    artifact = GENERATOR.parent / "runeclaw-audit.json"
    return json.loads(artifact.read_text(encoding="utf-8"))["release_decision"]


def test_the_blockers_list_does_not_name_a_fixed_finding():
    """The drift that is worst because it reads as fine.

    A NO-GO listing a blocker that has since been fixed is a decision nobody
    can re-derive: the stated reason is no longer true and the verdict standing
    unchanged looks like judgement rather than a stale string.

    Scoped to `blockers` and NOT to `basis`. `basis` is prose, and prose may
    legitimately name a fixed finding in order to say it is no longer the
    basis — asserting a short string is absent is the assertion this repository
    keeps having misfire. `blockers` is the machine-readable claim and can be
    held exactly.
    """
    gen = _statuses_from_generator()
    blockers = " ".join(_release_decision().get("blockers", []))
    fixed = {f for f, st in gen.items() if st == "FIXED"}
    stale = set(FINDING_ID.findall(blockers)) & fixed
    assert not stale, (
        f"the release decision still lists {sorted(stale)} as a blocker, "
        "but the register records them as FIXED"
    )


def test_the_decision_does_not_claim_no_critical_is_open():
    """The claim that was true of the artifact and false of the audit.

    With ten findings documented but unpublished — RC-2026-018 among them, an
    OPEN CRITICAL — a decision asserting every CRITICAL is resolved is counting
    only the ones it happens to carry. Whatever the decision says, it must not
    say that while a CRITICAL is open anywhere in the register.
    """
    prose = _statuses_from_prose()
    gen = _statuses_from_generator()
    open_criticals = sorted(
        fid for fid, st in prose.items()
        if st == "OPEN" and _prose_severity(fid) == "CRITICAL"
    ) or sorted(
        fid for fid, st in gen.items() if st == "OPEN"
        and 'severity="CRITICAL"' in _generator_entry(fid)
    )
    if not open_criticals:
        return
    text = str(_release_decision().get("basis", "")).lower().replace("\n", " ")
    for phrase in ("no critical remains open", "all three are fixed",
                   "no criticals remain", "every critical is fixed"):
        assert phrase not in text, (
            f"the decision claims {phrase!r} while {open_criticals} "
            "are OPEN CRITICAL in the register"
        )


def _prose_severity(fid: str) -> str:
    """Severity as the prose states it, ** emphasis and parentheticals removed."""
    text = PROSE.read_text(encoding="utf-8")
    for block in re.split(r"\n## ", text):
        if not FINDING_ID.search(block[:200]) or fid not in block[:200]:
            continue
        m = re.search(r"\*\*Severity\*\*:\s*\**([A-Z]+)", block)
        if m:
            return m.group(1)
    return ""


def _generator_entry(fid: str) -> str:
    src = GENERATOR.read_text(encoding="utf-8")
    i = src.find(f'id="{fid}"')
    return src[i:i + 600] if i >= 0 else ""


#: Fields the artifact CANNOT hold a current value for, and the reason is not a
#: shortcut. `commit` records the git HEAD the generator ran at, so the artifact
#: would have to contain the hash of the commit that contains it. A currency
#: check that ignored this would be unsatisfiable; one that ignored too much
#: would pass on a stale file. This is the exact set that is self-referential.
_SELF_REFERENTIAL = ("commit",)


def test_the_committed_artifact_is_the_generated_artifact():
    """Regenerating must change nothing that could have been current.

    `runeclaw-audit.json` is generated but committed, so it goes stale the
    moment a finding's status changes and nobody re-runs the generator — and a
    stale artifact is the drift the rest of this file is about, one level down.
    A reader takes the committed JSON at face value and nothing else checks it
    is what the generator would produce today.

    Same shape as CI's "The committed site is the built site" step. The
    regenerated file is written and then RESTORED, so running the suite never
    leaves the tree dirty.
    """
    import json
    import subprocess
    import sys

    root = GENERATOR.parent.parent
    artifact = GENERATOR.parent / "runeclaw-audit.json"
    committed = artifact.read_bytes()
    try:
        subprocess.run([sys.executable, str(GENERATOR)], check=True,
                       capture_output=True, cwd=str(root))
        regenerated = artifact.read_bytes()
    finally:
        artifact.write_bytes(committed)

    def _strip(raw: bytes) -> dict:
        d = json.loads(raw.decode("utf-8"))
        for k in _SELF_REFERENTIAL:
            d.get("audit", d).pop(k, None)
            d.pop(k, None)
        return d

    a, b = _strip(committed), _strip(regenerated)
    if a == b:
        return
    differing = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    raise AssertionError(
        "audit/runeclaw-audit.json is not what audit/generate_artifact.py "
        f"produces from the current register — {differing} differ. "
        "Regenerate it and commit the result:\n"
        "  python3 audit/generate_artifact.py"
    )
