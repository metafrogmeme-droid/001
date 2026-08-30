"""The learning docs may not claim an applier the code does not have.

`orchestrator.process_proposals()` said "Auto-apply", set `status = "applied"`,
counted into `auto_applied` and logged "Auto-applied docs proposal: <id>".
`docs/gitbook/ai-learning-system.md` said the same thing in three places —
an Allowed/Blocked table, a classification table, and the 10-step workflow.

Nothing in `bot/learning` applies a proposal. No file is written, no code is
edited. Every one of those was a claim about work that never happened.

WHY A TEST AND NOT JUST A CORRECTION

The docstring for `audit_proposal` was corrected the same way a day earlier and
nothing stopped it being re-introduced. Prose drifts from code precisely
because nothing checks it — the same reason `test_claude_md_accuracy.py`
exists. So this ties the two together: the docs may claim auto-application
exactly when `bot/learning` contains something that applies. Write an applier
and this test tells you to update the prose; delete one and it tells you the
same in reverse.

ASSERTING A SHORT STRING IS ABSENT IS THE ASSERTION THAT MISFIRES

CLAUDE.md records this trap three times in one sweep. It is live here:
`ai-learning-system.md:35` reads "may recommend, **never auto-apply**", which
is TRUE and must not be flagged, and the correction notes added alongside this
fix quote the old wording in order to explain it. A bare
`"auto-apply" not in text` fails on all three while the real claim could sit
untouched in a table. So the check runs over CLAIM-BEARING lines only —
table rows and the workflow block — with prose and block quotes excluded, and
the positive replacements are asserted directly.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "gitbook" / "ai-learning-system.md"
LEARNING = REPO / "bot" / "learning"

# A function whose name says it applies something. Deliberately broad: this
# test's job is to notice an applier arriving, not to police its name.
_APPLIER = re.compile(r"^\s*(?:async\s+)?def\s+(apply\w*|_apply\w*|\w*_apply)\s*\(",
                      re.MULTILINE)


def _has_applier() -> bool:
    for path in LEARNING.rglob("*.py"):
        if _APPLIER.search(path.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def _claim_lines() -> list[str]:
    """Lines that make a CAPABILITY claim — table rows and the workflow block.

    Block quotes (`>`) and ordinary prose are excluded: that is where the
    correction notes live, and where the true negative statement at line 35
    lives. Including them would make this test fail on sentences saying
    exactly what it wants said.
    """
    out, in_fence = [], False
    for raw in DOC.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if line.lstrip().startswith(">"):
            continue                      # a correction note, not a claim
        if in_fence or line.lstrip().startswith("|"):
            out.append(line)
    return out


def test_docs_do_not_claim_auto_application_while_no_applier_exists():
    if _has_applier():
        return   # an applier landed; the claim may be true now. Nothing to police.
    offenders = [ln for ln in _claim_lines()
                 if re.search(r"auto[-_ ]?appl", ln, re.IGNORECASE)]
    assert not offenders, (
        "bot/learning contains nothing that applies a proposal, but these "
        "claim-bearing lines in docs/gitbook/ai-learning-system.md say it "
        "auto-applies:\n  " + "\n  ".join(offenders)
        + "\n\nEither write the applier or correct the prose. A user reading "
          "this is told work happens that does not.")


def test_the_true_negative_statement_is_not_collateral():
    """Line 35 says "may recommend, never auto-apply" — TRUE, must survive.

    The first draft of the check above scanned the whole file and would have
    demanded the deletion of the one sentence stating the honest position.
    """
    text = DOC.read_text(encoding="utf-8")
    assert "never auto-apply" in text
    assert not [ln for ln in _claim_lines() if "never auto-apply" in ln], (
        "the honest negative belongs in prose, where the claim scan cannot "
        "reach it")


def test_the_correction_is_stated_where_a_reader_will_hit_it():
    text = DOC.read_text(encoding="utf-8")
    assert "Approved is not applied" in text
    assert "a human still applies it" in text


def test_the_orchestrator_agrees_with_the_docs():
    """Same claim, other surface — the corollary that finds these."""
    src = (LEARNING / "orchestrator.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert 'results = {"auto_approved": 0' in code
    assert 'proposal.status = "auto_approved"' in code
    assert 'proposal.status = "applied"' not in code
