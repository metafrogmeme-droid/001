"""F-14's 24-hour session timeout expires the permissions it claims to.

THE SET WAS A FUNCTION-LOCAL LITERAL WITH ONE READER, and driven against the
real gate table it was wrong in both directions at once::

    _SENSITIVE_CMDS = {"trade", "halt", "reset", "mode",
                       "golive", "approve", "revoke"}

THREE OF THE SEVEN NAMED NO COMMAND. `/golive` -- arm live trading -- had been
re-gated onto the `admin` permission and `admin` was never added, so `/golive`,
`/liveclose`, `/autoconfirm`, `/forcescan` and `/calibration` silently stopped
expiring. The stale word was the TOMBSTONE rather than merely dead: a reader
asking "is arming live trading session-protected?" found `golive` in the set and
stopped. `/approve` and `/revoke` are gated by an in-body `_is_admin` that never
reaches `permission_denial` at all, so those two could not have done anything in
either direction. That is `guarded_commands_baseline.txt`'s own header sentence
-- "a guard that silently disappears is an auth regression nothing else
notices" -- happening in the one place that baseline cannot see.

AND `stake` WAS MISSING. `/stake` and `/unstake` move real funds on the caller's
own linked venue account, and `VOUCHED_ONLY_PERMISSIONS` sits 1,000 lines up in
the same file naming exactly that as its reason, while `trade` -- which opens a
real position on the same account, behind the same confirm card -- was expired.

The blast radius is not Telegram. `permission_denial` has ten non-test callers:
the web gateway (twice), the callback handler through `has_permission`, the
chat-tool catalogue (which tools the model may call), `/stake`'s own in-body
check and the earn button's owner check.

EVERY CLAIM HERE IS DRIVEN. The permission a command carries is read from the
live AST walk (`tests/command_guards.py`) rather than written down, so a
re-gate moves the assertion instead of going stale the way `golive` did; the
staleness itself is driven through a real `UserStore` with a planted
`last_seen`; and the one-reading claim is proved by patching the reading and
asking the consumer, because a byte-identical copy agrees with every fixture.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot.utils import user_store as us
from bot.utils.user_store import (
    DECLARED_SENSITIVE_PERMISSIONS,
    OPERATOR_CONTROL_PERMISSIONS,
    SESSION_MAX_AGE_SECONDS,
    VOUCHED_ONLY_PERMISSIONS,
    UserStore,
    is_admin_only_permission,
    is_sensitive_permission,
)
from tests.command_guards import command_guards


def _gated() -> dict[str, list[str]]:
    """permission -> the `_cmd_*` handlers gated on it, from the live walk."""
    out: dict[str, list[str]] = {}
    for handler, permission in command_guards().items():
        out.setdefault(permission, []).append(handler)
    return out


def _permission_of(handler: str) -> str:
    """The permission `handler` carries TODAY. Never a literal in this file:
    `golive` became `admin` once already, and a written-down answer is what
    turned that into a tombstone instead of a failure."""
    perm = command_guards().get(handler)
    assert perm, f"{handler} carries no permission the walk can read"
    return perm


@pytest.fixture()
def store(tmp_path):
    s = UserStore(tmp_path / "users.json")
    idle = (datetime.now(timezone.utc)
            - timedelta(seconds=SESSION_MAX_AGE_SECONDS + 3600)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    s._users["idle_admin"] = {"role": "admin", "authorized": True, "last_seen": idle}
    s._users["idle_trader"] = {"role": "trader", "authorized": True, "last_seen": idle}
    s._users["live_trader"] = {"role": "trader", "authorized": True, "last_seen": now}
    return s


class TestTheTombstone:
    """The regression the stale word hid."""

    def test_the_permission_that_arms_live_trading_expires(self, store):
        perm = _permission_of("_cmd_golive")
        assert store.permission_denial("idle_admin", perm) == "stale_session", (
            f"/golive is gated on {perm!r} and an admin idle "
            f"{SESSION_MAX_AGE_SECONDS // 3600}h was not asked to re-assert "
            f"presence. The old set named the literal 'golive', which gates no "
            f"command -- so it read as protected and was not.")

    def test_the_permission_that_closes_a_live_position_expires(self, store):
        perm = _permission_of("_cmd_liveclose")
        assert store.permission_denial("idle_admin", perm) == "stale_session"

    def test_the_three_dead_names_are_gone_from_the_gate_table(self):
        """Not an assertion that they are un-sensitive -- an unrecognised name
        reads sensitive by the fail-closed rule below. The claim is that they
        gate NOTHING, which is why naming them protected nothing."""
        gated = _gated()
        for dead in ("golive", "approve", "revoke"):
            assert dead not in gated, (
                f"{dead!r} gates {gated.get(dead)} now. The old set named it "
                f"and it named no command; if it gates one again, this file's "
                f"whole history needs re-reading rather than this line "
                f"deleting.")


class TestTheMissingRow:
    """The caller's own real money."""

    def test_moving_the_callers_own_money_expires(self, store):
        perm = _permission_of("_cmd_stake")
        assert store.permission_denial("idle_trader", perm) == "stale_session", (
            f"/stake is gated on {perm!r} and moves real funds on the caller's "
            f"own linked venue account. It was absent from the old set while "
            f"`trade` -- a real position on the same account, behind the same "
            f"confirm card -- was present.")

    def test_redeeming_expires_too(self, store):
        assert store.permission_denial(
            "idle_trader", _permission_of("_cmd_unstake")) == "stale_session"

    def test_opening_a_position_still_expires(self, store):
        """The one row the old set got right; `connect` joined it as the
        second declared row when the credential writes left `status`."""
        assert store.permission_denial(
            "idle_trader", _permission_of("_cmd_trade")) == "stale_session"


class TestWhatMustNotExpire:
    """A timeout on a read is a bot that stops answering questions."""

    @pytest.mark.parametrize("handler", ["_cmd_portfolio", "_cmd_scan",
                                         "_cmd_risk", "_cmd_dashboard"])
    def test_a_read_card_does_not_expire(self, store, handler):
        perm = _permission_of(handler)
        assert store.permission_denial("idle_trader", perm) is None, (
            f"{handler} is gated on {perm!r} and is a read. Expiring reads "
            f"turns a 24h-idle chat into a bot that cannot answer 'is it "
            f"running?', which is not what F-14 is for.")

    def test_a_fresh_session_is_refused_nothing(self, store):
        for handler in ("_cmd_stake", "_cmd_trade", "_cmd_halt", "_cmd_golive"):
            perm = _permission_of(handler)
            assert store.permission_denial("live_trader", perm) in (
                None, "role"), perm
        # and the admin, who holds every permission, is refused none of them
        for handler in ("_cmd_stake", "_cmd_trade", "_cmd_halt", "_cmd_golive"):
            store._users["live_admin"] = {
                "role": "admin", "authorized": True,
                "last_seen": datetime.now(timezone.utc).isoformat()}
            assert store.permission_denial(
                "live_admin", _permission_of(handler)) is None


class TestTheDerivation:
    """Three sources and one declared row, rather than a longer list."""

    def test_every_admin_only_permission_expires(self):
        """The rule that would have caught the tombstone WITHOUT anybody
        noticing /golive had moved: a permission no non-admin role carries is
        an operator authority, so it expires by derivation."""
        gated = _gated()
        admin_only = sorted(p for p in gated if is_admin_only_permission(p))
        assert admin_only, "the derivation found nothing -- it is not running"
        for perm in admin_only:
            assert is_sensitive_permission(perm), (
                f"{perm!r} gates {gated[perm]} and no role but admin carries "
                f"it, yet it does not expire")

    def test_every_operator_control_and_vouched_permission_expires(self):
        for perm in sorted(OPERATOR_CONTROL_PERMISSIONS | VOUCHED_ONLY_PERMISSIONS):
            assert is_sensitive_permission(perm), perm

    def test_the_declared_row_is_the_one_no_derivation_reaches(self):
        """A declared row that a derivation ALREADY reaches is a second answer.
        If one appears, the derivation is the thing to fix -- which is what the
        old set's seven hand-written names were instead of."""
        for perm in sorted(DECLARED_SENSITIVE_PERMISSIONS):
            assert not is_admin_only_permission(perm), (
                f"{perm!r} is declared AND admin-only: drop the declaration")
            assert perm not in OPERATOR_CONTROL_PERMISSIONS, perm
            assert perm not in VOUCHED_ONLY_PERMISSIONS, perm

    def test_no_declared_or_derived_name_gates_nothing(self):
        """THE TOMBSTONE RULE. A name in one of the written-down sets that
        gates no command is exactly what `golive` was: a word that reads as a
        protection and protects nothing."""
        gated = _gated()
        written = (DECLARED_SENSITIVE_PERMISSIONS
                   | OPERATOR_CONTROL_PERMISSIONS | VOUCHED_ONLY_PERMISSIONS)
        orphans = sorted(p for p in written if p not in gated)
        assert not orphans, (
            f"{orphans} are named as sensitive and gate no command. Either a "
            f"command was re-gated (find where its authority went and make "
            f"sure THAT expires) or the row is dead and goes.")

    def test_the_declared_set_is_small_enough_to_read(self):
        """Not a cap for its own sake: the declared set exists because two
        permissions escape both derivations (`trade`, and `connect` since the
        credential writes left `status`). A growing list is the shape this
        file replaced, so growth asks for a derivation rather than a row."""
        assert len(DECLARED_SENSITIVE_PERMISSIONS) <= 2, (
            f"{sorted(DECLARED_SENSITIVE_PERMISSIONS)} -- a hand-written list "
            f"is growing again. Ask what derivation reaches these instead.")


class TestTheAdminOnlyRule:
    """Driven on PLANTED tables: the real one has a single wildcard role, so a
    mutation of the wildcard rule changes no verdict against it -- which is a
    guard reporting coverage it does not have."""

    def test_a_permission_a_non_admin_role_holds_is_not_admin_only(self, monkeypatch):
        monkeypatch.setattr(us, "ROLE_PERMISSIONS",
                            {"admin": {"*"}, "viewer": {"peek"}})
        assert not is_admin_only_permission("peek")

    def test_the_wildcard_is_read_and_not_the_role_name(self, monkeypatch):
        """A role NAMED admin that holds a SPECIFIC list is an ordinary role.

        That is the input that separates the two readings, and finding it took
        a mutation round: my first fixture planted two wildcard roles under the
        claim that a name check would mishandle them, and it does not -- `perm
        in {"*"}` is False for every real permission, so a second `*` role
        reads identically either way. The difference is here: a role called
        "admin" carrying a real permission list is skipped by a name check and
        read by the wildcard one.
        """
        monkeypatch.setattr(us, "ROLE_PERMISSIONS",
                            {"owner": {"*"}, "admin": {"peek"}, "viewer": {"look"}})
        assert not is_admin_only_permission("peek"), (
            "a role named 'admin' that holds a specific list carries `peek`, "
            "so `peek` is not admin-only -- a check on the NAME would skip it")
        assert is_admin_only_permission("nuke")

    def test_a_second_wildcard_role_is_a_recorded_NON_difference(self, monkeypatch):
        """Stated rather than claimed. Both readings answer the same here, so
        this is not evidence for the wildcard rule -- it is the case the first
        draft of the test above mistook for evidence."""
        monkeypatch.setattr(us, "ROLE_PERMISSIONS",
                            {"owner": {"*"}, "root": {"*"}, "viewer": {"peek"}})
        assert not is_admin_only_permission("peek")
        assert is_admin_only_permission("nuke")

    def test_an_unrecognised_permission_fails_closed(self, monkeypatch):
        """A name invented at a @guard decorator and never added to the table
        -- which has happened four times in this repo -- reads sensitive. For
        every non-admin the role check has already refused it; for an admin it
        costs one /start."""
        monkeypatch.setattr(us, "ROLE_PERMISSIONS",
                            {"admin": {"*"}, "viewer": {"peek"}})
        assert is_admin_only_permission("typoed_permission")
        assert is_sensitive_permission("typoed_permission")


class TestOneReading:
    """A byte-identical second copy agrees with every fixture and diverges on
    the first edit to either, so the only proof is to patch the reading and
    ask the consumer what it says."""

    def test_permission_denial_asks_the_shared_reading(self, store, monkeypatch):
        seen: list[str] = []

        def _fake(permission: str) -> bool:
            seen.append(permission)
            return permission == "portfolio"   # a read, deliberately

        monkeypatch.setattr(us, "is_sensitive_permission", _fake)
        # The reading now says only `portfolio` is sensitive. If
        # permission_denial holds its own copy, these two answers do not move.
        assert store.permission_denial("idle_trader", "portfolio") == "stale_session"
        assert store.permission_denial("idle_trader", "trade") is None
        assert "portfolio" in seen and "trade" in seen

    def test_the_set_literal_is_gone_from_the_method(self):
        """A scan, and it says so: the DRIVE above proves the reading is
        asked, and this proves no second copy sits beside it waiting for a
        later edit to start consulting it."""
        from tests.source_scan import code_only
        src = code_only(Path("bot/utils/user_store.py").read_text(encoding="utf-8"))
        assert "_SENSITIVE_CMDS" not in src, (
            "the function-local set is back; it is the thing this file is about")


class TestWhatThePermissionUnitCouldNotExpress:
    """RECORDED with its measurement by the slice that wrote this file, and
    SPLIT by the next one: `status` gated /connect and /disconnect -- which
    write and erase exchange API credentials -- beside read cards, so expiring
    it would have expired "is the bot running" and not expiring it left the
    writes unexpired. The writes carry `connect` now; the full claim, driven
    on both ends, is `tests/test_the_credential_writes_have_their_own_permission.py`.
    What stays here is the half this file owns: `status` is still a read
    permission and still does not expire."""

    def test_the_credential_writes_left_status(self):
        gated = _gated()
        status = set(gated.get("status", []))
        writes = {"_cmd_connect", "_cmd_disconnect"}
        assert not (writes & status), (
            "a credential write is back on `status`, the permission that must "
            "not expire because it gates the read cards")
        assert len(status) > 2, "`status` no longer carries read cards"
        assert not is_sensitive_permission("status"), (
            "`status` expires now -- the read cards it gates expire with it, "
            "and that is the thing to check")

    def test_the_writes_permission_expires(self, store):
        for handler in writes_today():
            perm = _permission_of(handler)
            assert is_sensitive_permission(perm), (handler, perm)
            assert store.permission_denial("idle_trader", perm) == "stale_session"


def writes_today() -> tuple[str, ...]:
    """The two credential-write handlers, by name: the structural walk that
    DERIVES them lives in the sibling file, and this one asks only whether
    the permission those two carry expires."""
    return ("_cmd_connect", "_cmd_disconnect")
