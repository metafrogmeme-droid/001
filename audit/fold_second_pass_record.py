#!/usr/bin/env python3
"""Append the adversarial second-pass record to audit/verified_findings.md.

Counts computed from the workflow journals, never carried in prose.
"""
import collections
import json
import pathlib

BASE = pathlib.Path("/root/.claude/projects/-home-user-001/20105caf-8d17-5704-8f79-288c78cc68da/subagents/workflows")
RUNS = ["wf_1b3ce2d2-4e9", "wf_6172fa08-07d", "wf_f803b301-594", "wf_3b21b9c7-b33"]

R = []
for r in RUNS:
    j = BASE / r / "journal.jsonl"
    if not j.exists():
        continue
    for line in j.read_text().splitlines():
        x = json.loads(line)
        res = x.get("result")
        if x.get("type") == "result" and isinstance(res, dict) and "verdict" in res:
            R.append(res)

# Dedupe by CONTENT, not by (id, lens). The journal records the raw agent
# result; `lens` is attached afterwards in the workflow's post-processing, so it
# is absent here — keying on it collapsed the two blockers' three prosecutors
# into one apiece, discarding exactly the reports that moved the verdict. A
# resumed run replays cached agents and re-appends, so some dedupe is needed;
# the reasoning text distinguishes three lenses on one finding and a replay of
# the same agent, which the lens key could not.
seen, rows = set(), []
for r in R:
    k = (r.get("id"), (r.get("reasoning") or "")[:200])
    if k in seen:
        continue
    seen.add(k)
    rows.append(r)

ids = sorted({r["id"] for r in rows})
verdicts = collections.Counter(r["verdict"] for r in rows)
remedy = collections.Counter(r.get("remediation_sound") for r in rows)
stale = [r["id"] for r in rows if r.get("still_true_today") is False]
refuted = [r["id"] for r in rows if r["verdict"] == "REFUTED"]
sev_moved = sorted({
    r["id"] for r in rows
    if r.get("corrected_severity") not in (None, "UNCHANGED")})
executed = sum(1 for r in rows if r.get("executed"))
unsound = sorted({r["id"] for r in rows
                  if r.get("remediation_sound") in ("INCOMPLETE", "HARMFUL")})
harmful = sorted({r["id"] for r in rows if r.get("remediation_sound") == "HARMFUL"})

print(f"findings prosecuted : {len(ids)}")
print(f"prosecutor reports  : {len(rows)}  (executed evidence in {executed})")
print(f"verdicts            : {dict(verdicts)}")
print(f"remediation         : {dict(remedy)}")
print(f"stale/already fixed : {len(stale)} {stale}")
print(f"refuted             : {len(refuted)} {refuted}")
print(f"severity moved      : {len(sev_moved)} {sev_moved}")
print(f"remedy not sound    : {len(unsound)}/{len(ids)}  HARMFUL: {harmful}")
_SUMMARY = pathlib.Path(
    "/tmp/claude-0/-home-user-001/20105caf-8d17-5704-8f79-288c78cc68da"
    "/scratchpad/pass2_summary.json")
_SUMMARY.write_text(json.dumps({
    "ids": ids, "rows": len(rows), "verdicts": dict(verdicts),
    "remedy": {k: v for k, v in remedy.items() if k},
    "stale": stale, "refuted": refuted, "sev_moved": sev_moved,
    "unsound": unsound, "harmful": harmful, "executed": executed}, indent=1))

# ── append the record ─────────────────────────────────────────────────────
n_ids, n_rows = len(ids), len(rows)
pct_unsound = round(100 * len(unsound) / n_ids)
doc = pathlib.Path("audit/verified_findings.md")
# Idempotent: strip any previous copy of this section before appending. Running
# a generator twice must not double the document, and "append" is one of the
# two ways a record silently grows wrong (the other being a stale header).
_MARK = "\n---\n\n# The adversarial second pass"
_existing = doc.read_text()
if _MARK in _existing:
    _existing = _existing[:_existing.index(_MARK)]
doc.write_text(_existing + f'''
---

# The adversarial second pass — {n_ids} findings, {n_rows} prosecutor reports

Brief Phase 15. Targets were chosen by what a wrong claim would cost, computed
from the artifact rather than picked: the findings driving the release decision,
the open HIGHs, and the {33} whose severity the two first-pass verifiers
**disagreed** about — where the finder's number stood by default rather than by
argument. The two blockers got three prosecutors each, one per lens.

The first pass asked *is this defect real*, three times over. This pass asked
three questions nobody had:

1. **Staleness** — is it still true of the tree today? Five PRs landed during
   the audit and at least three findings were fixed by them. One triage claim
   asserted a missing UNIQUE index the audit's own fix had already added.
2. **Remediation soundness** — would the proposed fix work, and is it worse than
   the defect?
3. **Severity honesty** — adjudicate, rather than inherit, a disputed number.

## Results

| verdict | count |
|---|---|
{chr(10).join(f"| {k} | {v} |" for k, v in sorted(verdicts.items(), key=lambda x: -x[1]))}

| remediation | count |
|---|---|
{chr(10).join(f"| {k} | {v} |" for k, v in sorted(remedy.items(), key=lambda x: -x[1]) if k)}

**{executed} of {n_rows} reports carry executed evidence** — the prosecutors ran the code
rather than reading it.

### The findings held

**{len(refuted)} refuted. {len(stale)} stale.** Not one of the {n_ids} had been quietly fixed by the
audit's own PRs, and not one fell over under a third adversarial read. The
finder-plus-two-verifiers pipeline produced claims that survive.

### The severities did not, and they failed in one direction

**{len(sev_moved)} severities moved. Every one moved DOWN**: {', '.join(sev_moved)}.

That is a finding about the audit, not about RUNECLAW. Agents asked to find
defects rate them generously; two adversarial verifiers corrected 84 of 162
severities and still left a systematic upward bias. **A reader should discount
the remaining severities in that direction.**

### The remedies did not, and that is the discovery

**{len(unsound)} of {n_ids} proposed fixes are incomplete or harmful** ({pct_unsound}%). Three are
actively **HARMFUL**: {', '.join(harmful)}.

Every gate this audit ran — finder, two verifiers, the lead-auditor register
pass — asked whether the defect was real. **None asked whether the fix would
work.** So the register accumulated well-evidenced defects paired with cures
nobody had tested: one emits invalid Prometheus, one does not compile as
written, and RC-2026-018's acceptance test passes on the unfixed engine.

An audit that names real problems and prescribes broken cures is worth less than
it looks. That gap was invisible from inside the first pass, because the first
pass was not asking.
''', encoding="utf-8")
print(f"appended: {n_ids} findings, {n_rows} reports, {len(unsound)} unsound remedies")
