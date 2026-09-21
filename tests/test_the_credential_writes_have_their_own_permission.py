"""The two commands that WRITE the caller's exchange credentials carry a
permission of their own, and F-14's timeout reaches it.

`status` used to gate /connect (stores the caller's API keys), /disconnect
(erases every linked venue) and /exchange (a read) beside nine read cards, so
the 24-hour session timeout could not expire the writes without expiring "is
the bot running" -- and did not: a hijacked-but-idle chat, the case F-14
exists for, could replace or erase the account's credentials with no /start.
`is_sensitive_permission` said so in its own docstring and
`tests/test_the_session_timeout_covers_what_it_claims.py` pinned the state as
it stood, so this file is where that pin was pointing.

The split is by CONSEQUENCE, not by subject: the two writes carry `connect`
(held by exactly the roles that hold `status`, so no derivation reaches it and
it is the second declared row), and /exchange stays on `status` because a read
that expires is a bot that stops answering questions. The declaration is
CHECKED rather than merely declared: every registered command whose body
writes the credential store must carry a sensitive permission or an admin
gate, read off the handler sources -- so the next command that grows a
`store.set_venue(...)` under a read permission fails here, by name.

Driven on both ends. The permission each handler carries is the live AST walk
(`tests/command_guards.py`); the refusal is the real `_cmd_disconnect` with a
planted `_guard`, because a scan cannot tell a guard that is PRESENT from one
that RUNS FIRST (`test_the_trade_gate_is_driven` is the precedent).
"""
from __future__ import annotations

import ast
import asyncio
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

from bot.core import exchange_credentials as ec
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.user_store import (
    ROLE_PERMISSIONS,
    SESSION_MAX_AGE_SECONDS,
    UserStore,
    is_sensitive_permission,
)
from tests.command_gates import _definitions, command_gates, registered_commands
from tests.command_guards import _own_body, command_guards

# ── The reading: which handlers WRITE the credential store ───────────────────
#
# A write is a call of one of the store's mutating methods on a receiver that
# IS the store -- `get_credential_store().delete(...)` or a local bound from
# that factory in this function's own body. The receiver is checked because
# `/connect` also calls `update.message.delete()` (the secret-bearing message
# goes first, before any gate), and a walk keyed on the method name alone
# would read that as a credential erase: a checker with a blind spot
# manufactures the accusation it exists to prevent, which is the recorded
# shape of every probe in this tree's auth guards.
STORE_FACTORY = "get_credential_store"
STORE_WRITES = frozenset({"set", "set_venue", "set_active", "delete_venue", "delete"})


def _is_factory_call(expr: ast.AST) -> bool:
    return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
            and expr.func.id == STORE_FACTORY)


def credential_writes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """The store's WRITE methods this handler's own body calls, sorted."""
    names: set[str] = set()
    for sub in _own_body(node):
        if isinstance(sub, ast.Assign) and _is_factory_call(sub.value):
            names.update(t.id for t in sub.targets if isinstance(t, ast.Name))
    out: set[str] = set()
    for sub in _own_body(node):
        if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
            continue
        if sub.func.attr not in STORE_WRITES:
            continue
        recv = sub.func.value
        if _is_factory_call(recv) or (isinstance(recv, ast.Name) and recv.id in names):
            out.add(sub.func.attr)
    return sorted(out)


def writers_in(source: str) -> dict[str, list[str]]:
    """{`_cmd_name`: writes} for every command handler in one source text."""
    out: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("_cmd_"):
            writes = credential_writes(node)
            if writes:
                out[node.name] = writes
    return out


def credential_writers() -> dict[str, list[str]]:
    """{`_cmd_name`: writes} over the live handler sources, first definition wins."""
    out: dict[str, list[str]] = {}
    for name, (node, _file) in _definitions().items():
        writes = credential_writes(node)
        if writes:
            out[name] = writes
    return out


def offenders(writers: dict[str, list[str]], guards: dict[str, str],
              gates: dict[str, str], by_handler: dict[str, str]) -> list[tuple]:
    """Every writer whose gate does not expire: (command, permission, writes).

    A writer carries a permission `is_sensitive_permission` answers True for,
    or an admin gate (`command_gates` reads the in-body `_is_admin` spelling
    `command_guards` cannot), or it is an offender. An UNREGISTERED writer is
    an offender too rather than acquitted by omission: no command reaches it
    today, and the day one does it arrives with whatever gate it had.
    """
    out: list[tuple] = []
    for handler, writes in writers.items():
        cmd = by_handler.get(handler)
        perm = guards.get(handler)
        admin = "is_admin" in (gates.get(cmd, "") if cmd else "").split("+")
        if not ((perm and is_sensitive_permission(perm)) or admin):
            out.append((cmd or handler, perm, writes))
    return out


# ── The rule, on the real tree ──────────────────────────────────────────────

class TestTheWritersCarryAPermissionThatExpires:

    def test_the_two_writers_are_the_two_this_slice_is_about(self):
        writers = credential_writers()
        assert set(writers) == {"_cmd_connect", "_cmd_disconnect"}, writers
        assert writers["_cmd_connect"] == ["set_venue"]
        assert writers["_cmd_disconnect"] == ["delete"]

    def test_every_writer_carries_a_sensitive_permission_or_an_admin_gate(self):
        """THE STRUCTURAL RULE. The declared row is checked, not merely
        declared: a command that grows a credential write under a read
        permission fails here by name, whatever the role table says."""
        by_handler = {h: c for c, h in registered_commands().items()}
        bad = offenders(credential_writers(), command_guards(), command_gates(), by_handler)
        assert not bad, (
            f"{bad}: each writes the credential store under a permission that "
            f"does not expire -- a hijacked-but-idle chat could replace or erase "
            f"the account's keys with no /start")

    def test_the_two_writers_share_one_permission_and_it_is_not_status(self):
        guards = command_guards()
        assert guards["_cmd_connect"] == guards["_cmd_disconnect"]
        assert guards["_cmd_connect"] != "status"
        assert is_sensitive_permission(guards["_cmd_connect"])

    def test_the_read_beside_them_stays_on_a_permission_that_does_not_expire(self):
        """/exchange reads the link's state (never a key). A read that expires
        is a bot that stops answering questions; the split is by CONSEQUENCE."""
        guards = command_guards()
        perm = guards["_cmd_exchange"]
        assert perm != guards["_cmd_connect"]
        assert not is_sensitive_permission(perm), perm
        assert "_cmd_exchange" not in credential_writers()

    def test_the_new_permission_is_held_by_exactly_the_roles_that_hold_status(self):
        """No role change: the slice moves WHICH permission the writes carry,
        not WHO may link an account. `pending` holds neither."""
        perm = command_guards()["_cmd_connect"]
        for role, perms in ROLE_PERMISSIONS.items():
            if "*" in perms:
                continue
            assert (perm in perms) == ("status" in perms), (role, perm)
        assert perm not in ROLE_PERMISSIONS["pending"]


# ── F-14 reaches it, driven through a real store ────────────────────────────

@pytest.fixture()
def store(tmp_path):
    s = UserStore(tmp_path / "users.json")
    idle = (datetime.now(timezone.utc)
            - timedelta(seconds=SESSION_MAX_AGE_SECONDS + 3600)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    for role in ("trader", "paper", "viewer"):
        s._users[f"idle_{role}"] = {"role": role, "authorized": True, "last_seen": idle}
        s._users[f"live_{role}"] = {"role": role, "authorized": True, "last_seen": now}
    return s


class TestTheTimeoutReachesTheWrites:

    @pytest.mark.parametrize("handler", ["_cmd_connect", "_cmd_disconnect"])
    @pytest.mark.parametrize("role", ["trader", "paper", "viewer"])
    def test_an_idle_chat_is_asked_to_reassert_presence_first(self, store, handler, role):
        perm = command_guards()[handler]
        assert store.permission_denial(f"idle_{role}", perm) == "stale_session"
        assert store.permission_denial(f"live_{role}", perm) is None

    @pytest.mark.parametrize("role", ["trader", "paper", "viewer"])
    def test_the_link_status_read_is_still_answered_to_an_idle_chat(self, store, role):
        perm = command_guards()["_cmd_exchange"]
        assert store.permission_denial(f"idle_{role}", perm) is None


# ── The rule's own blind spots, driven on planted sources ───────────────────

PLANTED = textwrap.dedent('''
    class H:
        async def _cmd_reads_and_deletes_a_message(self, update, ctx):
            await update.message.delete()
            store = get_credential_store()
            return store.credential_state(1)

        async def _cmd_writes_through_a_bound_name(self, update, ctx):
            s = get_credential_store()
            s.set_venue(1, "bitget", {})

        async def _cmd_writes_through_the_call(self, update, ctx):
            get_credential_store().delete_venue(1, "bybit")

        async def _cmd_writes_only_in_a_nested_helper(self, update, ctx):
            def inner():
                get_credential_store().delete(1)
            return inner

        async def _cmd_writes_some_other_store(self, update, ctx):
            other = get_other_store()
            other.delete(1)
''')


class TestTheWalkReadsTheReceiver:

    def test_a_message_delete_beside_a_store_read_is_not_a_credential_erase(self):
        assert "_cmd_reads_and_deletes_a_message" not in writers_in(PLANTED)

    def test_a_write_through_a_bound_name_is_seen(self):
        assert writers_in(PLANTED)["_cmd_writes_through_a_bound_name"] == ["set_venue"]

    def test_a_write_through_the_factory_call_is_seen(self):
        assert writers_in(PLANTED)["_cmd_writes_through_the_call"] == ["delete_venue"]

    def test_a_write_inside_a_nested_helper_is_not_the_commands(self):
        """The same bound `command_guards` states for its own walk: a nested
        def is not this function's body. Recorded rather than guessed: the
        real tree has no such shape, so this is the only input that can
        measure the bound."""
        assert "_cmd_writes_only_in_a_nested_helper" not in writers_in(PLANTED)

    def test_another_stores_delete_is_not_this_stores(self):
        assert "_cmd_writes_some_other_store" not in writers_in(PLANTED)



class TestTheRuleIsDrivenOnPlantedTables:
    """On the real tree the rule passes, so a mutation of the RULE changes no
    verdict there -- a rule no input can reach is a claim that there is a
    check. These are the inputs that reach it."""

    WRITERS = {"_cmd_w": ["delete"]}
    BY = {"_cmd_w": "/w"}

    def test_a_writer_under_a_read_permission_is_an_offender(self):
        bad = offenders(self.WRITERS, {"_cmd_w": "status"}, {"/w": "guard"}, self.BY)
        assert bad == [("/w", "status", ["delete"])]

    def test_a_writer_under_an_expiring_permission_is_not(self):
        assert offenders(self.WRITERS, {"_cmd_w": "trade"}, {"/w": "guard"}, self.BY) == []

    def test_an_admin_gated_writer_is_not(self):
        assert offenders(self.WRITERS, {}, {"/w": "is_admin+rate_limit"}, self.BY) == []

    def test_a_writer_with_no_gate_at_all_is_an_offender(self):
        assert offenders(self.WRITERS, {}, {"/w": "none"}, self.BY) == [("/w", None, ["delete"])]

    def test_an_unregistered_writer_is_an_offender_not_an_acquittal(self):
        assert offenders(self.WRITERS, {"_cmd_w": "status"}, {}, {}) == [("_cmd_w", "status", ["delete"])]


# ── The handlers, driven: a refused guard writes nothing ────────────────────

class _Engine:
    def invalidate_user_executor(self, tg_id): pass


class _Stub:
    """The real methods under test are taken off the class; this is `self`."""

    def __init__(self, allow: bool):
        self.allow = allow
        self.asked: list[str] = []
        self.sent: list[str] = []
        self.engine = _Engine()

    async def _guard(self, update, command="", ctx=None):
        self.asked.append(command)
        return self.allow

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    def _get_tg_id(self, update): return 4242


class _Msg:
    def __init__(self): self.deleted = False
    async def delete(self): self.deleted = True


class _Chat:
    type = "private"


class _Update:
    def __init__(self):
        self.message = _Msg()
        self.effective_chat = _Chat()


class _Ctx:
    def __init__(self, *args): self.args = list(args)


class _LinkedStore:
    def __init__(self, venues):
        self.venues = list(venues)
        self.calls: list[str] = []

    def list_venues(self, tg_id):
        self.calls.append("list_venues")
        return list(self.venues)

    def delete(self, tg_id):
        self.calls.append("delete")
        existed = bool(self.venues)
        self.venues = []
        return existed

    def set_venue(self, *a, **k):
        raise AssertionError("set_venue reached through a refused guard")


class TestDisconnectIsDriven:

    def _run(self, monkeypatch, *, allow: bool, venues):
        store = _LinkedStore(venues)
        monkeypatch.setattr(ec, "get_credential_store", lambda: store)
        stub = _Stub(allow)
        asyncio.run(TelegramHandler._cmd_disconnect(stub, _Update(), _Ctx()))
        return stub, store

    def test_a_refused_guard_erases_nothing_and_sends_nothing(self, monkeypatch):
        stub, store = self._run(monkeypatch, allow=False, venues=["bitget"])
        assert store.calls == []
        assert stub.sent == []
        assert stub.asked == [command_guards()["_cmd_disconnect"]]

    def test_the_card_names_every_venue_it_erased(self, monkeypatch):
        stub, store = self._run(monkeypatch, allow=True, venues=["bitget", "bybit"])
        assert store.calls == ["list_venues", "delete"], (
            "the venues are read BEFORE the erase, or there is nothing to name")
        card = stub.sent[-1]
        assert "Bitget" in card and "Bybit" in card, card
        assert "unlinked" in card
        assert "Bitget account unlinked" not in card, (
            "the old card named one venue whatever was erased")

    def test_no_link_names_no_venue(self, monkeypatch):
        stub, store = self._run(monkeypatch, allow=True, venues=[])
        card = stub.sent[-1]
        assert "No exchange account is linked" in card, card
        assert "Bitget" not in card


class TestConnectIsDriven:

    def test_a_refused_guard_stores_nothing_and_still_deletes_the_message(self, monkeypatch):
        """The secret-bearing message goes FIRST, before any gate can return --
        the existing property, kept: a refusal must not leave the keys in the
        chat history."""
        store = _LinkedStore([])
        monkeypatch.setattr(ec, "get_credential_store", lambda: store)
        stub = _Stub(False)
        upd = _Update()
        asyncio.run(TelegramHandler._cmd_connect(
            stub, upd, _Ctx("bg_apikey_0123456789", "apisecret_0123456789", "passphrase1")))
        assert upd.message.deleted
        assert store.calls == []
        assert stub.sent == []
        assert stub.asked == [command_guards()["_cmd_connect"]]
