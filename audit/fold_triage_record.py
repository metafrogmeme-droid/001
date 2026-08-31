#!/usr/bin/env python3
"""Rewrite audit/verifier_surfaced_gaps.md's header from the workflow journals.

Committed so the numbers in that file can be re-derived rather than trusted.
It reads the per-agent journals under the session's workflow transcript dir,
which is NOT in the repository — so this will not re-run for anyone else, and
that limitation is the honest state of it rather than something to paper over.
What it documents is how each figure in the header was produced.

Every number is computed here. The alternative — carrying tiers and counts in
prose — is the defect this audit spent the day correcting in its own artifacts.
"""
import collections
import json
import pathlib

SP = pathlib.Path("/tmp/claude-0/-home-user-001/20105caf-8d17-5704-8f79-288c78cc68da/scratchpad")
BASE = pathlib.Path("/root/.claude/projects/-home-user-001/20105caf-8d17-5704-8f79-288c78cc68da/subagents/workflows")
RUNS = ["wf_5d946506-2db", "wf_bd8757c2-47a", "wf_d1e64367-bbf", "wf_509e4b8f-345"]
ORDER = ["BLOCKER", "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"]

# verification results, keyed by item
V = {}
for r in RUNS:
    for line in (BASE / r / "journal.jsonl").read_text().splitlines():
        x = json.loads(line)
        res = x.get("result")
        if x.get("type") == "result" and isinstance(res, dict) and "holds" in res:
            V[res["id"]] = res

# per-item adversarial classification, from the compact workflow returns
S = {}
for f in sorted(SP.glob("batch*_status.json")):
    for row in json.loads(f.read_text()):
        S[row["id"]] = row["status"]

hold = [i for i in sorted(V) if V[i]["holds"]]
notheld = [i for i in sorted(V) if not V[i]["holds"]]
counts = collections.Counter(S.values())
sev = collections.Counter(V[i]["severity"] for i in hold)
repro = [i for i in hold if V[i].get("reproduced")]
dups = {tuple(sorted((i, int(V[i]["duplicate_of"]))))
        for i in hold if V[i].get("duplicate_of") and int(V[i]["duplicate_of"]) in hold}

confirmed = sorted(i for i, s in S.items() if s == "CONFIRMED")
suspected = sorted(i for i, s in S.items() if s == "SUSPECTED")
refuted_both = sorted(i for i, s in S.items() if s == "REFUTED")
refuted_verify = sorted(i for i, s in S.items() if s == "REFUTED_BY_VERIFIER")
partial = sorted(i for i, s in S.items() if s in ("SINGLE_PASS", "REFUTED_SINGLE"))
unclassified = [i for i in range(1, 60) if i not in S]

assert len(V) == 59, f"verification covers {len(V)} of 59"
assert len(hold) + len(notheld) == 59

# Built as data, then joined. Written as literal f-string lines it added three
# E501s and the ratchet failed — the second time in one session, so the lesson
# is the shape, not the instance: a markdown table row cannot be wrapped, so it
# must not be a source line.
_ROWS = [
    ("**CONFIRMED** — held against both refuters", f"**{len(confirmed)}**", confirmed),
    ("**SUSPECTED** — one refuter dissented", str(len(suspected)), suspected),
    ("**REFUTED** — both refuters rejected it", str(len(refuted_both)), refuted_both),
    ("**rejected at verification** — did not survive first contact with the code",
     str(len(refuted_verify)), refuted_verify),
    ("**incomplete** — fewer than two refutation verdicts", str(len(partial)), partial),
]
if unclassified:
    _ROWS.append(("**unclassified**", str(len(unclassified)), unclassified))
OUTCOME_TABLE = "\n".join(
    f"| {label} | {n} | {', '.join(map(str, items)) or '—'} |" for label, n, items in _ROWS)

n_rejected = len(notheld) + len(refuted_both)
pct_all = round(100 * n_rejected / len(V))
pct_verify = round(100 * len(notheld) / len(V))
NOTHELD_LIST = ", ".join("#" + str(i) for i in notheld)
REFUTED_LIST = ", ".join("#" + str(i) for i in refuted_both)

head = f"""# Defects the VERIFIERS found that the finders missed

## Status: TRIAGE COMPLETE — {len(V)} of 59 examined, {len(S)} adversarially classified

Every claim in this file has now been read against the code. Each was then put
to **two independent adversarial refuters** with different lenses, both
instructed to default to `refuted`. A claim refuted by both is REFUTED; by one,
SUSPECTED; by neither, CONFIRMED. A claim the verification pass itself rejected
never reached the refuters.

| outcome | count | items |
|---|---|---|
{OUTCOME_TABLE}

**{n_rejected} of the {len(V)} claims were rejected somewhere** — {len(notheld)} did not
survive first contact with the code ({NOTHELD_LIST}), and a further
{len(refuted_both)} were held by the verification pass and then rejected by BOTH refuters
({REFUTED_LIST}). That is a **{pct_all}% overall rejection rate** on the
verifiers' own escalations.

Stating it as the verification-stage rate alone ({pct_verify}%) would understate
it,
and understating a rejection rate in the document that exists to say how much
these claims can be trusted is the wrong direction to be wrong in.

This is why they were held here as claims rather than promoted to findings: the
verifiers were right more often than not, and not reliably enough to be taken on
trust. **{len(confirmed) + len(suspected)} of {len(V)}** came through with something left standing.

**Every one of the {len(repro)} claims that survived verification was reproduced by
EXECUTION**, not by reading: the agents imported the modules, planted state,
drove the failure and pasted real output. That includes the {len(refuted_both)} the refuters
later rejected — reproducing a mechanism and establishing that it matters are
different questions, and the refuters answered the second one.

| severity of the {len(hold)} that hold | count |
|---|---|
{chr(10).join(f'| {k} | {sev[k]} |' for k in ORDER if sev[k])}

Duplicate pairs among the survivors: {', '.join(f'#{a}=#{b}' for a, b in sorted(dups))}.
So the {len(hold)} holding claims describe **{len(hold) - len(dups)} distinct defects**.

### What the triage caught that neither finder nor verifier did

**#30's claim is already false.** It says `users.telegram_id` carries no UNIQUE
index. It does — `app/db.js:2953`, added by the RC-2026-001 fix earlier in this
same audit. The verifier wrote the claim before the fix landed and nothing
re-read it afterwards. A finding register that is not re-checked against the
tree it describes goes stale in exactly this direction.

**#26 was refuted by both refuters after the verification pass held it HIGH.**
The disagreement is the mechanism working: one agent reading carefully is not a
finding, which is the whole argument of this file.

**#16 was wrong in the direction that matters.** It asserted that rebinding a
push endpoint lets an attacker *receive and decrypt* the victim's
notifications. Reproduced, it is the opposite: the delivery target is the
endpoint — the victim's browser — so the attacker receives nothing and the
victim gets ciphertext they cannot read. Integrity, not confidentiality. The
claim's own proposed alternative fix is strictly worse than the defect.

**#1 found a test that acquits vacuously.**
`tests/test_command_audience_matches_permission.py` scores a command with **no
guard at all** exactly as it scores one gated by a permission nobody holds:
`_permission_string` returns `None`, and the reachability comprehension then
evaluates `None in p`, which is empty. `/broadcast` and `/channel` both pass it
while carrying no `@guard`. A false acquittal, in the direction CLAUDE.md names
as the dangerous one.

### Status of these items

CONFIRMED and SUSPECTED items are **candidate findings, not findings**. They
have not been through the lead-auditor pass that the `RC-2026-NNN` register
requires — code re-read by hand, reachability established from outside the file,
a fix or a proposed patch written. They are not counted in the audit's finding
totals or in its release decision.

---
"""
p = pathlib.Path("audit/verifier_surfaced_gaps.md")
body = p.read_text().split("---", 1)[1]
p.write_text(head + body)
print(f"folded: {len(V)}/59 verified, {len(S)} classified — "
      f"CONFIRMED {len(confirmed)}, SUSPECTED {len(suspected)}, "
      f"REFUTED {len(refuted_both)}, rejected-at-verify {len(refuted_verify)}, "
      f"incomplete {len(partial)}, unclassified {len(unclassified)}")
