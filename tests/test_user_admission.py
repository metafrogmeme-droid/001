"""/approve announced an access the gate did not recognise.

`_is_allowlisted` (audit F-2) read TELEGRAM_CHAT_ID / ADMIN_TELEGRAM_IDS /
LIVE_TRADER_TELEGRAM_IDS and nothing else. `UserStore.authorize` — the one
thing `/approve` calls — wrote `authorized: True` and a role. Nothing joined
them. So on any live bot (live mode REQUIRES TELEGRAM_CHAT_ID, so the allowlist
is never empty there):

    admin:  /approve 4242
    bot  → admin: "USER APPROVED ... Status: 🟢 authorized"
    bot  → user:  "Access Granted. Use /start to begin trading."
    user:  /scan
    bot  → user:  "This bot is locked to its configured operator."

Two surfaces asserting an access the third refuses. The only real way to add a
person was to edit an env var and redeploy, which no message anywhere said.

The fix splits `authorized` (register() sets it for everyone, so it can gate
nothing) from ADMISSION — `admitted_at` + `admitted_by`, written only by
`authorize(by=...)`, called only by the `_is_admin`-gated /approve and its
inline button. `_is_allowlisted` accepts either door.

**F-2 is what these tests are really guarding.** The hole it closed was
SELF-registration: any /start made a stranger an authorized trader who could
/halt, /reset, /mode and emergency-stop a live bot. That stays shut, because
register() cannot name an approving admin. The tests below assert the shut half
at least as hard as the open half — a security fix relaxed by a usability fix
is exactly how these get lost.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.utils.user_store import UserStore


def _store(tmp_path) -> UserStore:
    return UserStore(tmp_path / "users.json")


class TestAdmissionIsNotAuthorization:
    def test_self_registration_authorizes_but_does_not_admit(self, tmp_path):
        """THE F-2 line. register() is reachable by any stranger; it must not
        be able to produce an admitted record."""
        store = _store(tmp_path)
        record = store.register(4242, name="Stranger")
        assert record["authorized"] is True, "auto-approve behaviour is unchanged"
        assert store.is_admitted(4242) is False
        assert "admitted_by" not in record

    def test_an_admin_approval_admits(self, tmp_path):
        store = _store(tmp_path)
        store.register(4242, name="Ann")
        assert store.authorize(4242, role="trader", by="111") is True
        assert store.is_admitted(4242) is True
        assert store.get(4242)["admitted_by"] == "111"

    def test_authorize_without_an_admin_does_NOT_admit(self, tmp_path):
        """`by` is required, not decorative. A caller that cannot name the
        admin responsible is not performing an admin approval, and an
        unattributable admission is what F-2 was closed against."""
        store = _store(tmp_path)
        store.register(4242, name="Ann")
        store.authorize(4242, role="trader")          # legacy call shape
        assert store.is_admitted(4242) is False

    def test_admission_needs_both_stamps(self, tmp_path):
        """A hand-edited or half-migrated record must not open the gate."""
        path = tmp_path / "users.json"
        path.write_text(json.dumps({"7": {"telegram_id": "7", "role": "trader",
                                          "authorized": True,
                                          "admitted_at": "2026-01-01T00:00:00+00:00"}}),
                        encoding="utf-8")
        assert UserStore(path).is_admitted(7) is False

    def test_a_deauthorized_record_is_not_admitted(self, tmp_path):
        path = tmp_path / "users.json"
        path.write_text(json.dumps({"7": {"telegram_id": "7", "role": "pending",
                                          "authorized": False,
                                          "admitted_at": "2026-01-01T00:00:00+00:00",
                                          "admitted_by": "111"}}),
                        encoding="utf-8")
        assert UserStore(path).is_admitted(7) is False

    def test_revoke_closes_the_gate_it_opened(self, tmp_path):
        """revoke() sets role=pending. If it left the admission behind, the
        allowlist — which reads admitted_by, not role — would still let them in."""
        store = _store(tmp_path)
        store.register(4242, name="Ann")
        store.authorize(4242, by="111")
        assert store.is_admitted(4242) is True
        store.revoke(4242)
        assert store.is_admitted(4242) is False
        assert "admitted_by" not in store.get(4242)

    def test_revoking_is_not_undone_by_the_next_message(self, tmp_path):
        """register() runs for every user on every first contact — and /start
        calls it unconditionally. Its legacy-pending auto-upgrade could not
        tell "never had a decision" from "an admin removed them" (both are
        role=pending, authorized=False), so a revocation lasted until the
        person's next message."""
        store = _store(tmp_path)
        store.register(4242, name="Ann")
        store.authorize(4242, by="111")
        store.revoke(4242)
        store.register(4242, name="Ann")          # their next message
        assert store.is_authorized(4242) is False
        assert store.is_admitted(4242) is False

    def test_nor_by_a_restart(self, tmp_path):
        """migrate_pending_users() runs at startup over every pending record —
        the same collapse, but silent, with no message to prompt a look."""
        path = tmp_path / "users.json"
        s1 = UserStore(path)
        s1.register(4242, name="Ann")
        s1.revoke(4242)
        s2 = UserStore(path)                       # the restart
        s2.migrate_pending_users()
        assert s2.is_authorized(4242) is False

    def test_a_genuinely_legacy_pending_user_is_still_migrated(self, tmp_path):
        """Mutate the guard: the fix must not strand the users the migration
        exists for — registered before auto-approve, never decided about."""
        path = tmp_path / "users.json"
        path.write_text(json.dumps({"7": {"telegram_id": "7", "name": "Old",
                                          "role": "pending",
                                          "authorized": False}}),
                        encoding="utf-8")
        store = UserStore(path)
        assert store.migrate_pending_users() == 1
        assert store.is_authorized(7) is True

    def test_approving_again_clears_the_revocation(self, tmp_path):
        store = _store(tmp_path)
        store.register(4242, name="Ann")
        store.revoke(4242)
        store.authorize(4242, role="trader", by="111")
        assert store.is_admitted(4242) is True
        assert "revoked_at" not in store.get(4242), (
            "a record that reads as both revoked and approved is a coin flip "
            "for whatever consults it next")

    def test_admission_survives_a_reload(self, tmp_path):
        """It is worth nothing if a restart drops it — which is the complaint
        that started this: people did not stay registered."""
        path = tmp_path / "users.json"
        s1 = UserStore(path)
        s1.register(4242, name="Ann")
        s1.authorize(4242, by="111")
        assert UserStore(path).is_admitted(4242) is True


class TestTheOperatorPing:
    def test_the_first_refusal_asks_and_later_ones_do_not(self, tmp_path):
        """A stranger working through the menu must not page the operator once
        per command."""
        store = _store(tmp_path)
        store.register(4242, name="Ann")
        assert store.mark_access_requested(4242) is True
        assert store.mark_access_requested(4242) is False
        assert store.mark_access_requested(4242) is False

    def test_an_unknown_user_cannot_be_flagged(self, tmp_path):
        assert _store(tmp_path).mark_access_requested(9999) is False

    def test_revoking_lets_them_ask_again(self, tmp_path):
        """The one-shot flag exists to stop spam, not to ban anyone."""
        store = _store(tmp_path)
        store.register(4242, name="Ann")
        store.mark_access_requested(4242)
        store.authorize(4242, by="111")
        store.revoke(4242)
        assert store.mark_access_requested(4242) is True


class TestTheGateAcceptsBothDoorsAndNoThird:
    """`_is_allowlisted` is the security boundary; exercise it, don't read it."""

    def _handler(self, tmp_path):
        from bot.skills.telegram_handler import TelegramHandler
        h = TelegramHandler.__new__(TelegramHandler)
        h.users = _store(tmp_path)
        return h

    def _update(self, uid):
        return SimpleNamespace(effective_user=SimpleNamespace(id=uid),
                               effective_chat=SimpleNamespace(id=uid))

    def test_a_self_registered_stranger_is_still_refused(self, tmp_path):
        """The F-2 regression test. If this ever passes as True, any /start
        grants a live bot's /halt again."""
        h = self._handler(tmp_path)
        h.users.register(999, name="Stranger")
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            assert h._is_allowlisted(self._update(999)) is False

    def test_an_admin_approved_user_gets_in(self, tmp_path):
        h = self._handler(tmp_path)
        h.users.register(999, name="Ann")
        h.users.authorize(999, role="trader", by="111")
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            assert h._is_allowlisted(self._update(999)) is True

    def test_the_env_allowlist_still_works_on_its_own(self, tmp_path):
        h = self._handler(tmp_path)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            assert h._is_allowlisted(self._update(111)) is True

    def test_an_unconfigured_bot_stays_open(self, tmp_path):
        h = self._handler(tmp_path)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = ""
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            assert h._is_allowlisted(self._update(999)) is True

    def test_revoking_shuts_the_gate_at_the_handler(self, tmp_path):
        h = self._handler(tmp_path)
        h.users.register(999, name="Ann")
        h.users.authorize(999, by="111")
        h.users.revoke(999)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            assert h._is_allowlisted(self._update(999)) is False


class TestAdmissionGrantsBotAccessAndNothingElse:
    """The whole reason admission is safe to add. If any of these flips, an
    approve-button tap starts handing out real money instead of a paper account.
    """

    def _handler(self, tmp_path):
        from bot.skills.telegram_handler import TelegramHandler
        h = TelegramHandler.__new__(TelegramHandler)
        h.users = _store(tmp_path)
        h.users.register(999, name="Ann")
        h.users.authorize(999, role="trader", by="111")
        return h

    def test_it_does_not_grant_live_trading(self, tmp_path):
        h = self._handler(tmp_path)
        h.users.set_live_trading(999, True)   # even with the store flag ON
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            assert h._can_trade_live("999") is False, (
                "_can_trade_live must keep reading the ENV allowlist alone — "
                "admission is bot access, not money")

    def test_it_does_not_make_an_operator(self, tmp_path):
        from bot.core.engine import RuneClawEngine
        store = _store(tmp_path)
        store.register(999, name="Ann")
        store.authorize(999, role="trader", by="111")
        eng = RuneClawEngine.__new__(RuneClawEngine)
        eng._user_store = store
        with patch("bot.core.engine.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            assert eng._is_operator_user("999") is False, (
                "an operator routes to the operator's account and skips "
                "per-user risk isolation")

    def test_it_does_not_make_an_admin(self, tmp_path):
        h = self._handler(tmp_path)
        update = SimpleNamespace(effective_user=SimpleNamespace(id=999),
                                 effective_chat=SimpleNamespace(id=999))
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = "111"
            assert h._is_admin(update) is False


class TestTheWebGatewayAgreesWithTelegram:
    """Same claim, two surfaces. An approved user working in Telegram and
    refused on the web is the divergence this repo keeps paying for."""

    def test_the_gateway_admits_the_same_people(self, tmp_path):
        from bot.skills.telegram_handler import TelegramHandler
        from bot.web import user_gateway
        h = TelegramHandler.__new__(TelegramHandler)
        h.users = _store(tmp_path)
        h.users.register(999, name="Ann")
        h.users.authorize(999, role="trader", by="111")
        h._limiter = SimpleNamespace(allow=lambda _k: True)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            assert user_gateway._guard_user(h, "999", "scan") is None

    def test_and_refuses_the_same_people(self, tmp_path):
        from bot.skills.telegram_handler import TelegramHandler
        from bot.web import user_gateway
        h = TelegramHandler.__new__(TelegramHandler)
        h.users = _store(tmp_path)
        h.users.register(999, name="Stranger")     # self-registered only
        h._limiter = SimpleNamespace(allow=lambda _k: True)
        with patch("bot.skills.telegram_handler.CONFIG") as mc:
            mc.telegram.chat_id = "111"
            mc.telegram.admin_ids = ""
            mc.telegram.live_trader_ids = ""
            resp = user_gateway._guard_user(h, "999", "scan")
        assert resp is not None and resp.status == 403


def test_every_fake_user_store_implements_what_the_gateway_calls():
    """Eight hand-written doubles of one class, and they drift.

    Adding `permission_denial` to the gateway broke five suites at once — each
    had its own FakeUsers/`_Users` with just `get`/`has_permission`/
    `can_trade_live`, and the miss surfaced as a 500 from an aiohttp handler
    rather than as an AttributeError anyone could read. Two were fixed from the
    failure list, then a grep for `def has_permission` found six more; the
    fifth and sixth were in suites the first run had not even reached.

    So this scans for the shape instead of trusting a list: any test class that
    implements `has_permission` is standing in for UserStore, and must carry
    every method bot/web/user_gateway.py actually calls on the store. A ninth
    double fails here, by name, instead of as a 500 three suites away.
    """
    import ast
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent
    gateway = (root.parent / "bot/web/user_gateway.py").read_text(encoding="utf-8")
    # Derive the requirement from the CALLER, so a newly-called method is
    # covered without anyone remembering to update this list.
    required = set(re.findall(r"\.users\.([a-z_]+)\(", gateway))
    required -= {"get"}          # every double has it; keep the message short
    assert "permission_denial" in required and "is_admitted" in required, (
        "the scan lost its grip on the gateway — check the .users.<method>( "
        "pattern still matches how it calls the store")

    problems = []
    for path in sorted(root.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if "has_permission" not in methods:
                continue
            missing = required - methods
            if missing:
                problems.append(f"{path.name}::{node.name} lacks "
                                f"{sorted(missing)}")
    assert not problems, (
        "these stand in for UserStore but do not implement everything the web "
        "gateway calls on it, so the gap surfaces as a 500:\n  "
        + "\n  ".join(problems))


# Functions that read the env allowlist and MUST NOT honour admission. These
# are the money and identity decisions: admission is bot access, granted from a
# chat button, and it may never widen either of these.
_ENV_ONLY_BY_DESIGN = {
    "_allowlist_ids",       # builds the set; consults nothing
    "_can_trade_live",      # live orders stay env-gated
}


@pytest.mark.parametrize("path", ["bot/skills/telegram_handler.py",
                                  "bot/web/user_gateway.py"])
def test_every_bot_access_gate_consults_admission(path):
    """Ask which OTHER surface makes the same claim.

    Two places decide "may this id use the bot", written months apart, and a
    third could be added the same way. A source scan on purpose: the behaviour
    is covered by real calls above; what those cannot see is a NEW gate that
    reads `_allowlist_ids()` and forgets `is_admitted`, silently reinstating
    the split where /approve announces an access the gate refuses.

    Resolved through the AST rather than a line window — the first draft used
    ±8 lines and flagged `_can_trade_live`, whose `def` sat just outside it.
    The enclosing function is the unit the rule is actually about.
    """
    import ast
    import pathlib

    from tests.source_scan import segment_reader
    src = pathlib.Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    # Split once. `ast.get_source_segment` re-splits the whole file per call,
    # which on telegram_handler.py (13,575 lines, 260 functions) was 29.6s and
    # tipped past the 60s pytest-timeout under load. Byte-identical output.
    seg_of = segment_reader(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = seg_of(node) or ""
        if "_allowlist_ids()" not in body:
            continue
        if node.name in _ENV_ONLY_BY_DESIGN:
            assert "is_admitted" not in body, (
                f"{path}::{node.name} is env-only by design — admission must "
                "not widen it")
            continue
        if "is_admitted" not in body:
            offenders.append(f"{path}::{node.name} (line {node.lineno})")
    assert not offenders, (
        "these gate bot access on the env allowlist alone, so an admin's "
        "/approve announces access they then refuse:\n  " + "\n  ".join(offenders))


class TestTheAdminCheckHasExactlyOneDefinition:
    """`_is_admin(update)` was split so `_viewer_board_handle` could ask the
    same question about a bare telegram id. A split is where a rule quietly
    becomes two rules, so this pins that the Update-taking entry point still
    answers with the id-taking one — and that both still say yes.

    Nothing pinned `_is_admin` before: stubbing it to `return False` passes all
    34 auth/guard suites. It fails closed, so it is not a hole; it would just
    silently lock every admin out of every admin command.
    """

    def _handler(self, role):
        import types
        from bot.skills.telegram_handler import TelegramHandler
        stub = types.SimpleNamespace(users={"7": {"role": role}})
        stub._get_tg_id = lambda _u: "7"
        stub._is_admin_id = types.MethodType(TelegramHandler._is_admin_id, stub)
        stub._is_admin = types.MethodType(TelegramHandler._is_admin, stub)
        return stub

    def test_an_admin_is_recognised_by_id(self):
        assert self._handler("admin")._is_admin_id("7") is True

    def test_an_admin_is_recognised_through_the_update_path(self):
        """The delegation itself — a `return False` here breaks every admin
        command and no existing suite notices."""
        assert self._handler("admin")._is_admin(object()) is True

    def test_a_trader_is_not_an_admin_either_way(self):
        h = self._handler("trader")
        assert h._is_admin_id("7") is False
        assert h._is_admin(object()) is False

    def test_both_entry_points_agree(self):
        for role in ("admin", "trader", "viewer"):
            h = self._handler(role)
            assert h._is_admin(object()) == h._is_admin_id("7"), (
                f"the two definitions disagree for {role!r}")
