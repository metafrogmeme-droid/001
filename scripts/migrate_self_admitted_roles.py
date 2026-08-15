#!/usr/bin/env python3
"""Downgrade accounts the DOOR admitted, not the ones a human vouched for.

H4 made self-admission grant `paper` instead of `trader`. That clamp lives in
`authorize()` and fires at ADMISSION TIME, so it only ever applied to people who
arrived after it shipped. Everyone already in `data/users.json` kept whatever
they were given — on this deployment, 13 accounts holding `trader`, all of them
let in by `PAPER_AUTO_ACCEPT` before the fix existed.

WHAT THAT IS AND IS NOT

It is NOT an open hole. Every shared-engine control is gated on engine
ownership rather than role since H4's second half — `/reset`, `/resume` and
`/pause` route through `_control_scope`, `/halt`, `/emergency_stop` and `/mode`
through `_is_operator`, and free text through `DANGEROUS_SKILLS`. A legacy
trader can reach none of them.

What it costs is the two things the role separation was FOR:

  * `/users` cannot distinguish a stranger who let themselves in from a
    teammate the operator vouched for — every row says `trader`;
  * the role gate is no longer a second layer for these accounts. If a command
    added next year is role-gated and not operator-gated, all thirteen inherit
    it — which is precisely the shape H4 was.

THREE GROUPS, NOT TWO

The discriminator is `admitted_by`, and it has three states that must not be
collapsed:

    "auto-accept"   the door admitted them            → migrate
    an admin id     a human vouched for them          → leave alone
    absent          we cannot tell how they got in    → REPORT, never guess

The third is the one that matters. A record with no `admitted_by` predates the
attribution entirely, and treating its absence as "probably auto-accept" would
downgrade somebody an operator deliberately promoted — absence is not a
measurement, and a migration is exactly where that rule gets skipped because
the alternative is a slightly untidy report.

EXCEPT WHERE THE ABSENCE IS PROVABLE

"Cannot tell" is a statement about the evidence, and for one shape of id there
is more evidence than `admitted_by`. A `web:<n>` identity exists only because
`bot/web/user_gateway._guard_user` called `register()` on a first request —
and `register()` cannot stamp an admission. The two surfaces that CAN stamp one
(`/approve` and the `admit:` callback) both refuse a non-numeric id, so no
`web:` account has ever reached an admin's decision. Its role is whatever the
door handed out at the time, which before H4 was `trader`.

That is a proof, not a guess, so those records are planned for downgrade —
and each carries the REASON it was planned, because "the door admitted them"
and "no door but the automatic one exists for them" are different findings and
a single bucket printed without them would read as one.

The proof rests on those two refusals, so it is not left as an inference: both
call `user_store.is_vouchable`, and `tests/test_a_web_id_cannot_be_vouched_for`
drives them. A numeric id with no `admitted_by` stays unattributable, because
for one of those the ambiguity is real.

DRY RUN BY DEFAULT. This edits production identity records; nothing happens
without `--apply`.

    python3 scripts/migrate_self_admitted_roles.py                 # show
    python3 scripts/migrate_self_admitted_roles.py --apply         # do it
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.utils.user_store import (SELF_ADMISSION_BY, SELF_ADMISSION_ROLE,  # noqa: E402
                                  UserStore, is_vouchable)

#: Roles a self-admitted account may be holding that are stronger than the one
#: the door would grant today. `admin` is deliberately absent: if an operator
#: made somebody an admin, the fact that the door first let them in does not
#: undo that decision, and a migration that demoted an admin would be the
#: worst possible outcome of a tidying script.
_DOWNGRADABLE = ("trader", "viewer")


def plan(store: UserStore) -> dict:
    """Classify every user without changing anything::

        {migrate:[...], vouched:[...], unattributable:[...], already:[...]}
    """
    out: dict[str, list] = {"migrate": [], "vouched": [],
                            "unattributable": [], "already": []}
    for u in store.list_users():
        role = u.get("role", "pending")
        by = u.get("admitted_by")
        row = {"id": u.get("telegram_id"), "name": (u.get("name") or "")[:20],
               "role": role, "admitted_by": by, "reason": ""}
        if role == SELF_ADMISSION_ROLE:
            out["already"].append(row)
        elif role not in _DOWNGRADABLE:
            # admin, pending, or something unrecognised — not this script's
            # business either way.
            continue
        elif by == SELF_ADMISSION_BY:
            row["reason"] = f"admitted_by={SELF_ADMISSION_BY!r} — the door let them in"
            out["migrate"].append(row)
        elif by:
            out["vouched"].append(row)
        elif not is_vouchable(u.get("telegram_id")):
            # No stamp AND no surface that could have written one. Both admin
            # admission paths refuse a non-numeric id, so this account reached
            # its role through the gateway's register() and nothing else.
            row["reason"] = ("no admin surface accepts this id shape — "
                             "auto-provisioned by the web gateway")
            out["migrate"].append(row)
        else:
            out["unattributable"].append(row)
    return out


def apply(store: UserStore, rows: list) -> int:
    """Set the role on each planned row. Returns the count actually changed."""
    changed = 0
    with store._lock:                                   # noqa: SLF001
        for row in rows:
            rec = store._users.get(str(row["id"]))      # noqa: SLF001
            if not rec or rec.get("role") != row["role"]:
                # Re-read and re-check: the plan was computed earlier and a
                # record that moved since then is not one this run decided about.
                continue
            rec["role"] = SELF_ADMISSION_ROLE
            changed += 1
        if changed:
            store._save()                               # noqa: SLF001
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it, nothing changes.")
    ap.add_argument("--path", default="data/users.json")
    args = ap.parse_args()

    store = UserStore(args.path)
    p = plan(store)

    def show(title: str, rows: list, note: str = "") -> None:
        print(f"\n{title} ({len(rows)})" + (f" — {note}" if note else ""))
        for r in rows:
            print(f"  {str(r['id']):>12}  {r['name']:<20} {r['role']:<8} "
                  f"admitted_by={r['admitted_by']!r}")
            # Two different findings share this bucket. Printing the count
            # alone would present them as one.
            if r.get("reason"):
                print(f"  {'':>12}  └ {r['reason']}")

    show("WOULD DOWNGRADE", p["migrate"],
         f"these reached their role with no human deciding; "
         f"they would become {SELF_ADMISSION_ROLE!r}")
    show("LEFT ALONE — vouched for", p["vouched"],
         "an admin approved these; their role is a decision, not a default")
    show("LEFT ALONE — cannot tell", p["unattributable"],
         "no admitted_by, and an admin COULD have approved them — "
         "NOT guessed either way; decide these by hand")
    show("already correct", p["already"])

    if p["unattributable"]:
        print(f"\n  {len(p['unattributable'])} account(s) have no attribution and a "
              "numeric id, so /approve was reachable for them. They predate the "
              "admitted_by stamp and nothing here can say whether a human let "
              "them in; downgrading on that guess would demote somebody an "
              "operator promoted on purpose.")

    if not args.apply:
        print(f"\nDRY RUN — nothing written. Re-run with --apply to change "
              f"{len(p['migrate'])} record(s).")
        return 0

    changed = apply(store, p["migrate"])
    print(f"\nApplied: {changed} record(s) set to {SELF_ADMISSION_ROLE!r}.")
    if changed != len(p["migrate"]):
        # Never report the plan as the outcome. A record that moved between
        # planning and writing is a real difference and gets said out loud.
        print(f"  NOTE: {len(p['migrate']) - changed} planned record(s) were "
              "not changed — they no longer matched the plan when written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
