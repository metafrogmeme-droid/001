"""The H4 clamp fired at admission time, so it never touched anyone already in.

`authorize()` grants `paper` to a self-admitted user — but only from the moment
it shipped. Production's `/users` afterwards showed **13 traders and zero paper
accounts**: every one of them let in by `PAPER_AUTO_ACCEPT` before the fix
existed, all still holding the role the door used to hand out.

NOT AN OPEN HOLE, and the distinction matters for how hard to pull the lever.
Every shared-engine control is gated on engine OWNERSHIP rather than role since
H4's second half, so a legacy trader can reach none of them. What the drift
costs is the two things the separation was for: `/users` cannot tell a stranger
from a vouched-for teammate, and if a command added later is role-gated but not
operator-gated, all thirteen inherit it — which is exactly the shape H4 was.

THREE GROUPS, NOT TWO, and the third is the whole reason this file is careful:

    admitted_by == "auto-accept"   the door let them in       → migrate
    admitted_by == an admin id     a human vouched            → leave
    admitted_by absent             we cannot tell             → REPORT, not guess

A record with no attribution predates the stamp. Treating that absence as
"probably auto-accept" would demote somebody an operator promoted on purpose —
absence is not a measurement, and a migration is precisely where that rule gets
skipped, because the alternative is an untidy report instead of a clean one.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "_migrate", REPO / "scripts" / "migrate_self_admitted_roles.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def _store(tmp_path, users: dict) -> UserStore:
    path = tmp_path / "users.json"
    path.write_text(json.dumps(users), encoding="utf-8")
    return UserStore(path)


def _u(role: str, by=None, name="x") -> dict:
    rec = {"telegram_id": "0", "name": name, "role": role, "tier": "basic",
           "authorized": True}
    if by is not None:
        rec["admitted_by"] = by
    return rec


# ── the three groups ─────────────────────────────────────────────────

def test_a_door_admitted_trader_is_planned_for_downgrade(tmp_path):
    s = _store(tmp_path, {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}})
    assert [r["id"] for r in mig.plan(s)["migrate"]] == ["1"]


def test_a_vouched_for_trader_is_left_alone(tmp_path):
    """An admin's /approve is a decision. A tidying script must not undo it."""
    s = _store(tmp_path, {"1": {**_u("trader", "77777"), "telegram_id": "1"}})
    p = mig.plan(s)
    assert p["migrate"] == []
    assert [r["id"] for r in p["vouched"]] == ["1"]


def test_an_unattributable_record_is_reported_not_guessed(tmp_path):
    """No admitted_by means nobody can say how they got in. Guessing 'door'
    demotes an operator's deliberate promotion; guessing 'human' leaves a
    stranger elevated. The honest third option is to say so."""
    s = _store(tmp_path, {"1": {**_u("trader"), "telegram_id": "1"}})
    p = mig.plan(s)
    assert p["migrate"] == []
    assert p["vouched"] == []
    assert [r["id"] for r in p["unattributable"]] == ["1"]


def test_the_three_groups_never_overlap(tmp_path):
    s = _store(tmp_path, {
        "1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"},
        "2": {**_u("trader", "77777"), "telegram_id": "2"},
        "3": {**_u("trader"), "telegram_id": "3"},
    })
    p = mig.plan(s)
    ids = [r["id"] for k in ("migrate", "vouched", "unattributable") for r in p[k]]
    assert sorted(ids) == ["1", "2", "3"]
    assert len(ids) == len(set(ids))


# ── what must never be touched ───────────────────────────────────────

def test_an_admin_is_never_downgraded(tmp_path):
    """Even one the door originally admitted. If an operator later made them an
    admin, that supersedes how they arrived — and demoting an admin would be
    the worst possible outcome of a tidying script."""
    s = _store(tmp_path, {"1": {**_u("admin", SELF_ADMISSION_BY), "telegram_id": "1"}})
    p = mig.plan(s)
    assert p["migrate"] == []
    assert mig.apply(s, p["migrate"]) == 0
    assert s.get("1")["role"] == "admin"


def test_a_pending_user_is_not_promoted(tmp_path):
    """This script only ever moves DOWN. A revoked account must not be handed
    `paper` as a side effect of tidying."""
    s = _store(tmp_path, {"1": {**_u("pending", SELF_ADMISSION_BY), "telegram_id": "1"}})
    assert mig.plan(s)["migrate"] == []


def test_an_already_correct_account_is_not_rewritten(tmp_path):
    s = _store(tmp_path, {"1": {**_u(SELF_ADMISSION_ROLE, SELF_ADMISSION_BY),
                                "telegram_id": "1"}})
    p = mig.plan(s)
    assert p["migrate"] == []
    assert [r["id"] for r in p["already"]] == ["1"]


# ── applying ─────────────────────────────────────────────────────────

def test_apply_changes_only_the_planned_records(tmp_path):
    s = _store(tmp_path, {
        "1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"},
        "2": {**_u("trader", "77777"), "telegram_id": "2"},
        "3": {**_u("trader"), "telegram_id": "3"},
    })
    p = mig.plan(s)
    assert mig.apply(s, p["migrate"]) == 1
    assert s.get("1")["role"] == SELF_ADMISSION_ROLE
    assert s.get("2")["role"] == "trader"
    assert s.get("3")["role"] == "trader"


def test_the_change_survives_a_reload(tmp_path):
    """It has to reach disk. A migration that only edited memory would report
    success and revert on the next restart."""
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}}),
        encoding="utf-8")
    s = UserStore(path)
    mig.apply(s, mig.plan(s)["migrate"])
    assert UserStore(path).get("1")["role"] == SELF_ADMISSION_ROLE


def test_a_record_that_moved_since_planning_is_not_written(tmp_path):
    """The plan is computed before the write. A record whose role changed in
    between is not one this run decided about, and overwriting it anyway would
    clobber a concurrent /approve."""
    s = _store(tmp_path, {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}})
    p = mig.plan(s)
    s._users["1"]["role"] = "admin"          # somebody promoted them meanwhile
    assert mig.apply(s, p["migrate"]) == 0
    assert s.get("1")["role"] == "admin"


def test_the_count_returned_is_what_actually_changed(tmp_path):
    """Not the length of the plan. Reporting the intention as the outcome is
    the defect this repository is built around."""
    s = _store(tmp_path, {
        "1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"},
        "2": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "2"},
    })
    p = mig.plan(s)
    s._users["2"]["role"] = "admin"
    assert len(p["migrate"]) == 2
    assert mig.apply(s, p["migrate"]) == 1


# ── the safety of the default ────────────────────────────────────────

def test_planning_writes_nothing(tmp_path):
    """Dry run means dry. `plan` is called by the default invocation and must
    not touch the file — this edits production identity records."""
    path = tmp_path / "users.json"
    original = json.dumps({"1": {**_u("trader", SELF_ADMISSION_BY),
                                 "telegram_id": "1"}})
    path.write_text(original, encoding="utf-8")
    mig.plan(UserStore(path))
    assert json.loads(path.read_text())["1"]["role"] == "trader"


def test_the_script_requires_apply_to_write():
    src = (REPO / "scripts" / "migrate_self_admitted_roles.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))
    assert 'add_argument("--apply"' in code
    assert "if not args.apply:" in code, (
        "nothing guards the write; the default invocation would edit "
        "production identity records")


def test_an_empty_store_is_a_no_op(tmp_path):
    s = _store(tmp_path, {})
    p = mig.plan(s)
    assert all(p[k] == [] for k in ("migrate", "vouched", "unattributable", "already"))
