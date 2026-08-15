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

EXCEPT WHERE THE ABSENCE IS EVIDENCE. "Cannot tell" describes what is known,
not a category of record, and for a `web:<n>` id more is known: no admission
surface accepts one, so nothing could have written the stamp and its absence
proves the gateway provisioned the account. Those ARE downgraded — the proof
is pinned in `tests/test_a_web_id_cannot_be_vouched_for.py`, which drives the
refusal rather than trusting it. For a numeric id the ambiguity is real and the
caution stands.

The two now share a bucket, so each row carries the reason it is in it. "The
door admitted them" and "no door but the automatic one exists for them" are
different findings, and a count printed over both would present them as one.
"""
from __future__ import annotations

import importlib.util
import json
import os
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
        "web:4": {**_u("trader"), "telegram_id": "web:4"},
    })
    p = mig.plan(s)
    ids = [r["id"] for k in ("migrate", "vouched", "unattributable") for r in p[k]]
    assert sorted(ids) == ["1", "2", "3", "web:4"]
    assert len(ids) == len(set(ids))


# ── where the absence is evidence ────────────────────────────────────

def test_a_web_account_with_no_attribution_is_planned_for_downgrade(tmp_path):
    """Not "cannot tell". No admission surface accepts a web id, so nothing
    could have written the stamp and its absence is proof the gateway
    provisioned the account — pinned by test_a_web_id_cannot_be_vouched_for."""
    s = _store(tmp_path, {"web:90001": {**_u("trader"), "telegram_id": "web:90001"}})
    p = mig.plan(s)
    assert [r["id"] for r in p["migrate"]] == ["web:90001"]
    assert p["unattributable"] == []


def test_a_numeric_account_with_no_attribution_is_still_left_alone(tmp_path):
    """The caution survives exactly where it is warranted. /approve was
    reachable for this id, so the same absence means something different."""
    s = _store(tmp_path, {"1": {**_u("trader"), "telegram_id": "1"}})
    assert [r["id"] for r in mig.plan(s)["unattributable"]] == ["1"]


def test_the_two_reasons_for_downgrading_are_not_merged(tmp_path):
    """One bucket, two findings. A count over both presents them as one, and
    an operator reading "3 would be downgraded" deserves to know which of the
    two arguments applies to each."""
    s = _store(tmp_path, {
        "1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"},
        "web:2": {**_u("trader"), "telegram_id": "web:2"},
    })
    reasons = {r["id"]: r["reason"] for r in mig.plan(s)["migrate"]}
    assert len(set(reasons.values())) == 2
    assert all(reasons.values()), "a planned row with no stated reason"
    assert SELF_ADMISSION_BY in reasons["1"]


def test_a_stamped_web_account_is_still_believed(tmp_path):
    """Defensive, and deliberately not clever. If some path we have not found
    did write an admitting admin, that record says a human decided — and this
    script does not overrule a decision on the strength of its own reasoning
    about which paths exist."""
    s = _store(tmp_path, {"web:2": {**_u("trader", "77777"), "telegram_id": "web:2"}})
    p = mig.plan(s)
    assert p["migrate"] == []
    assert [r["id"] for r in p["vouched"]] == ["web:2"]


def test_a_web_admin_is_not_downgraded_either(tmp_path):
    """The `admin` exemption is about the role, not about how they arrived."""
    s = _store(tmp_path, {"web:2": {**_u("admin"), "telegram_id": "web:2"}})
    assert mig.plan(s)["migrate"] == []


# ── what must never be touched ───────────────────────────────────────

def test_an_admin_is_never_downgraded(tmp_path):
    """Even one the door originally admitted. If an operator later made them an
    admin, that supersedes how they arrived — and demoting an admin would be
    the worst possible outcome of a tidying script."""
    s = _store(tmp_path, {"1": {**_u("admin", SELF_ADMISSION_BY), "telegram_id": "1"}})
    p = mig.plan(s)
    assert p["migrate"] == []
    assert mig.apply(s, p["migrate"]) == []
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
    assert mig.apply(s, p["migrate"]) == ["1"]
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
    assert mig.apply(s, p["migrate"]) == []
    assert s.get("1")["role"] == "admin"


def test_what_is_returned_is_what_actually_changed(tmp_path):
    """Not the plan. Reporting the intention as the outcome is the defect this
    repository is built around — and the ids are returned rather than a count
    so the caller can re-read those exact records from disk afterwards."""
    s = _store(tmp_path, {
        "1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"},
        "2": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "2"},
    })
    p = mig.plan(s)
    s._users["2"]["role"] = "admin"
    assert len(p["migrate"]) == 2
    assert mig.apply(s, p["migrate"]) == ["1"]


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


# ── the bot holds this file in memory ────────────────────────────────

def test_a_live_bot_blocks_the_write(tmp_path, monkeypatch, capsys):
    """UserStore loads once at construction and saves the whole map back on
    every register(), which fires on any user's next message. Writing under a
    running bot gets reverted within minutes — with this script having printed
    success, which is worse than not running it."""
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}}),
        encoding="utf-8")
    monkeypatch.setattr(mig, "running_bot_pids", lambda: [4242])
    monkeypatch.setattr("sys.argv", ["m", "--apply", "--path", str(path)])
    assert mig.main() == 2
    assert UserStore(path).get("1")["role"] == "trader", "it wrote anyway"
    assert "4242" in capsys.readouterr().out, "the pid was not named"


def test_being_unable_to_check_also_blocks_the_write(tmp_path, monkeypatch):
    """The one that matters. "No bot is running" and "we could not tell" are
    different answers, and putting the reassuring one on the failure path is
    exactly the shape this repository exists to prevent."""
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}}),
        encoding="utf-8")
    monkeypatch.setattr(mig, "running_bot_pids", lambda: None)
    monkeypatch.setattr("sys.argv", ["m", "--apply", "--path", str(path)])
    assert mig.main() == 2
    assert UserStore(path).get("1")["role"] == "trader"


def test_no_bot_running_lets_the_write_through(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}}),
        encoding="utf-8")
    monkeypatch.setattr(mig, "running_bot_pids", lambda: [])
    monkeypatch.setattr("sys.argv", ["m", "--apply", "--path", str(path)])
    assert mig.main() == 0
    assert UserStore(path).get("1")["role"] == SELF_ADMISSION_ROLE


def test_the_override_exists_but_is_not_the_default(tmp_path, monkeypatch):
    """An operator who has genuinely stopped the bot on a box with no readable
    /proc needs a way through. It must be typed, not inferred."""
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}}),
        encoding="utf-8")
    monkeypatch.setattr(mig, "running_bot_pids", lambda: None)
    monkeypatch.setattr("sys.argv",
                        ["m", "--apply", "--allow-running-bot", "--path", str(path)])
    assert mig.main() == 0
    assert UserStore(path).get("1")["role"] == SELF_ADMISSION_ROLE


def test_a_dry_run_is_not_gated(tmp_path, monkeypatch, capsys):
    """Reading is always safe, and an operator diagnosing this needs the report
    without stopping the bot first."""
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}}),
        encoding="utf-8")
    monkeypatch.setattr(mig, "running_bot_pids", lambda: [4242])
    monkeypatch.setattr("sys.argv", ["m", "--path", str(path)])
    assert mig.main() == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_the_detector_does_not_find_this_process(tmp_path):
    """`pgrep -f` matching a pattern also matches the checking script's own
    command line — CLAUDE.md records the first draft of verify_bot_alive.sh
    reporting OK for a process that never existed. This scan excludes its own
    pid and its own script name."""
    got = mig.running_bot_pids()
    assert got is None or os.getpid() not in got


# ── the write is confirmed from disk ─────────────────────────────────

def test_a_write_that_did_not_stick_is_not_reported_as_success(
        tmp_path, monkeypatch, capsys):
    """`_save()` returns nothing and declines silently when the store failed to
    load. "We called apply()" is not evidence of a file that changed — the same
    distinction as a launcher printing DEPLOY_DONE because it started a process
    rather than because one is alive."""
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}}),
        encoding="utf-8")
    monkeypatch.setattr(mig, "running_bot_pids", lambda: [])
    monkeypatch.setattr(mig, "apply", lambda store, rows: ["1"])   # claims, writes nothing
    monkeypatch.setattr("sys.argv", ["m", "--apply", "--path", str(path)])
    assert mig.main() == 1
    out = capsys.readouterr().out
    assert "DID NOT STICK" in out
    assert "Applied and verified" not in out


def test_a_successful_write_says_it_was_verified(tmp_path, monkeypatch, capsys):
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {"1": {**_u("trader", SELF_ADMISSION_BY), "telegram_id": "1"}}),
        encoding="utf-8")
    monkeypatch.setattr(mig, "running_bot_pids", lambda: [])
    monkeypatch.setattr("sys.argv", ["m", "--apply", "--path", str(path)])
    assert mig.main() == 0
    assert "verified on disk" in capsys.readouterr().out


def test_an_empty_store_is_a_no_op(tmp_path):
    s = _store(tmp_path, {})
    p = mig.plan(s)
    assert all(p[k] == [] for k in ("migrate", "vouched", "unattributable", "already"))
