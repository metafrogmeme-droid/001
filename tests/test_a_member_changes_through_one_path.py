"""Every operator change to a member goes through one write path, from either door.

The owner asked for an admin section on the website that manages the bot.
Settings went through `bot/core/settings_registry.py`; members had four
Telegram commands, each with its own rules inline and nothing recording who
changed whom. A web page written against the store would have been a second
copy of four validations. So `bot/core/member_admin.py` holds them, the four
commands and the admin page both call it, and every change is one audit line.

What the website may NOT do, refused by door: grant the admin role, set the
admin tier (the website reads `plan == 'admin'` as the operator), or grant live
trading. What it may do: approve paper/trader/viewer, revoke, set a lower tier,
restrict to paper.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.core import member_admin as ma
from bot.utils.user_store import StoreWriteHeld, UserStore

ROOT = Path(__file__).resolve().parents[1]
OPERATOR = "1001"
# Telegram's HTML, which the page would print as text.
_TAG = re.compile(r"</?(?:b|i|u|s|code|pre|a)(?:\s[^>]*)?>")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def audit_file(tmp_path, monkeypatch):
    path = tmp_path / "member_admin_audit.jsonl"
    monkeypatch.setattr(ma, "AUDIT_PATH", path)
    return path


@pytest.fixture
def store(tmp_path, audit_file):
    st = UserStore(tmp_path / "users.json")
    st.seed_admin(OPERATOR)
    st.register("55", name="Bo")
    st.register("web:7", name="w7")
    return st


@pytest.fixture(autouse=True)
def _no_mirror(monkeypatch):
    from bot.utils import website_sync
    pushes: list = []
    monkeypatch.setattr(website_sync, "sync_tiers_in_background", lambda m, **kw: pushes.append(dict(m)))
    monkeypatch.setattr(website_sync, "notify_tier_change_in_background", lambda *a, **kw: None)
    return pushes


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


# ── the rules, as one function per change ───────────────────────────────────

class TestApprove:
    def test_a_member_is_let_in_and_it_is_recorded(self, store, audit_file):
        res = ma.approve(store, "55", "trader", by=OPERATOR, via=ma.WEBSITE)
        assert res.ok and store.is_admitted("55")
        row = _rows(audit_file)[-1]
        assert (row["action"], row["target"], row["actor"], row["via"]) == ("approve", "55", OPERATOR, "website")
        assert row["after"]["role"] == "trader" and row["after"]["authorized"] is True

    def test_the_website_cannot_grant_the_admin_role_and_telegram_can(self, store, audit_file):
        web = ma.approve(store, "55", "admin", by=OPERATOR, via=ma.WEBSITE)
        assert not web.ok and web.error == ma.ROLE_TELEGRAM_ONLY
        assert store.get("55").get("role") != "admin" and _rows(audit_file) == []
        tg = ma.approve(store, "55", "admin", by=OPERATOR, via=ma.TELEGRAM)
        assert tg.ok and store.get("55")["role"] == "admin"

    def test_a_web_only_account_cannot_be_vouched_for(self, store, audit_file):
        res = ma.approve(store, "web:7", "paper", by=OPERATOR, via=ma.WEBSITE)
        assert not res.ok and res.error == "invalid_tg_id_numeric"
        assert _rows(audit_file) == []

    def test_an_unknown_role_is_refused(self, store):
        res = ma.approve(store, "55", "god", by=OPERATOR, via=ma.TELEGRAM)
        assert not res.ok and res.error == "invalid_role" and res.params == {"role": "god"}


class TestRevoke:
    def test_a_member_is_revoked_and_it_is_recorded(self, store, audit_file):
        ma.approve(store, "55", "trader", by=OPERATOR, via=ma.TELEGRAM)
        res = ma.revoke(store, "55", by=OPERATOR, via=ma.WEBSITE)
        assert res.ok and not store.is_admitted("55")
        row = _rows(audit_file)[-1]
        assert row["action"] == "revoke" and row["before"]["role"] == "trader"

    def test_never_the_callers_own(self, store, audit_file):
        res = ma.revoke(store, OPERATOR, by=OPERATOR, via=ma.WEBSITE)
        assert not res.ok and res.error == "cannot_revoke_self"
        assert store.get(OPERATOR)["role"] == "admin" and store.get(OPERATOR)["authorized"] is True

    def test_an_unknown_member(self, store):
        res = ma.revoke(store, "999", by=OPERATOR, via=ma.TELEGRAM)
        assert not res.ok and res.error == "user_not_found_id"


class TestSetTier:
    def test_a_tier_is_set_mirrored_and_recorded(self, store, audit_file, _no_mirror):
        res = ma.set_tier(store, "55", "pro", by=OPERATOR, via=ma.WEBSITE)
        assert res.ok and store.get_tier("55") == "pro"
        assert _no_mirror and _no_mirror[-1]["55"] == "pro", "the website's plan mirror was not started"
        assert _rows(audit_file)[-1]["after"]["tier"] == "pro"

    def test_the_website_cannot_set_the_operator_tier_and_telegram_can(self, store, audit_file):
        web = ma.set_tier(store, "55", "admin", by=OPERATOR, via=ma.WEBSITE)
        assert not web.ok and web.error == ma.TIER_TELEGRAM_ONLY
        assert store.get_tier("55") != "admin" and _rows(audit_file) == []
        tg = ma.set_tier(store, "55", "admin", by=OPERATOR, via=ma.TELEGRAM)
        assert tg.ok and store.get_tier("55") == "admin"

    def test_a_website_member_never_holds_the_operator_tier_from_either_door(self, store):
        for via in ma.DOORS:
            res = ma.set_tier(store, "web:7", "admin", by=OPERATOR, via=via)
            assert not res.ok and res.error == "set_tier_web_admin"
        assert ma.set_tier(store, "web:7", "pro", by=OPERATOR, via=ma.WEBSITE).ok

    @pytest.mark.parametrize("bad", ["١٢", "１２", "abc", ""])
    def test_a_lookalike_id_is_refused(self, store, bad):
        assert ma.set_tier(store, bad, "pro", by=OPERATOR, via=ma.TELEGRAM).error == "invalid_tg_id"

    def test_an_unknown_tier_and_an_unknown_member(self, store):
        assert ma.set_tier(store, "55", "platinum", by=OPERATOR, via=ma.TELEGRAM).error == "invalid_tier"
        assert ma.set_tier(store, "999", "pro", by=OPERATOR, via=ma.TELEGRAM).error == "user_not_found_id_period"


class TestRevokeLive:
    def test_a_member_is_restricted_to_paper(self, store, audit_file):
        store.set_live_trading("55", True)
        res = ma.revoke_live(store, "55", by=OPERATOR, via=ma.WEBSITE)
        assert res.ok and store.get("55")["can_trade_live"] is False
        row = _rows(audit_file)[-1]
        assert row["before"]["live"] is True and row["after"]["live"] is False

    def test_a_held_write_is_in_force_not_saved_and_says_so(self, store, audit_file, monkeypatch):
        def held(target, enabled):
            raise StoreWriteHeld("live", "OSError")
        monkeypatch.setattr(store, "set_live_trading", held)
        res = ma.revoke_live(store, "55", by=OPERATOR, via=ma.WEBSITE)
        assert not res.ok and res.held and res.error == "revoke_live_held"
        assert _rows(audit_file)[-1]["held"] is True, "a held change was not recorded"


class TestAnAdminAccountIsTelegrams:
    """Taking admin away is as much a change of who runs the bot as giving it."""

    @pytest.fixture
    def second_admin(self, store):
        ma.approve(store, "66", "admin", by=OPERATOR, via=ma.TELEGRAM)
        return "66"

    @pytest.mark.parametrize("change", [
        lambda st, t: ma.approve(st, t, "viewer", by=OPERATOR, via=ma.WEBSITE),
        lambda st, t: ma.revoke(st, t, by=OPERATOR, via=ma.WEBSITE),
        lambda st, t: ma.set_tier(st, t, "basic", by=OPERATOR, via=ma.WEBSITE),
        lambda st, t: ma.revoke_live(st, t, by=OPERATOR, via=ma.WEBSITE),
    ], ids=["approve", "revoke", "set_tier", "revoke_live"])
    def test_the_website_changes_no_admin_account(self, store, audit_file, second_admin, change):
        store.set_live_trading(second_admin, True)
        before = dict(store.get(second_admin))
        n = len(_rows(audit_file))
        res = change(store, second_admin)
        assert not res.ok and res.error == ma.ADMIN_TELEGRAM_ONLY
        assert store.get(second_admin) == before
        assert len(_rows(audit_file)) == n

    def test_nor_its_own_operator_row(self, store):
        for change in (lambda: ma.approve(store, OPERATOR, "trader", by=OPERATOR, via=ma.WEBSITE),
                       lambda: ma.set_tier(store, OPERATOR, "pro", by=OPERATOR, via=ma.WEBSITE),
                       lambda: ma.revoke_live(store, OPERATOR, by=OPERATOR, via=ma.WEBSITE)):
            assert change().error == ma.ADMIN_TELEGRAM_ONLY
        assert store.get(OPERATOR)["role"] == "admin" and store.get_tier(OPERATOR) == "admin"
        assert store.get(OPERATOR)["can_trade_live"] is True

    def test_an_admin_tier_alone_is_an_admin_account(self, store):
        store.set_tier("55", "admin")
        assert ma.set_tier(store, "55", "pro", by=OPERATOR, via=ma.WEBSITE).error == ma.ADMIN_TELEGRAM_ONLY

    def test_telegram_still_changes_an_admin_account(self, store, second_admin):
        assert ma.set_tier(store, second_admin, "pro", by=OPERATOR, via=ma.TELEGRAM).ok
        assert ma.approve(store, second_admin, "trader", by=OPERATOR, via=ma.TELEGRAM).ok
        assert store.get(second_admin)["role"] == "trader"
        assert ma.revoke(store, second_admin, by=OPERATOR, via=ma.TELEGRAM).ok

    def test_and_a_member_who_is_not_an_admin_is_the_websites(self, store):
        assert ma.set_tier(store, "55", "pro", by=OPERATOR, via=ma.WEBSITE).ok


def test_an_unknown_door_is_a_programming_error(store):
    with pytest.raises(ValueError):
        ma.revoke(store, "55", by=OPERATOR, via="carrier-pigeon")


def test_recent_changes_newest_first_and_an_unreadable_file_named(store, audit_file, tmp_path, monkeypatch):
    ma.approve(store, "55", "paper", by=OPERATOR, via=ma.TELEGRAM)
    ma.set_tier(store, "55", "pro", by=OPERATOR, via=ma.WEBSITE)
    rows, err = ma.recent_changes()
    assert err is None and [r["action"] for r in rows] == ["set_tier", "approve"]
    monkeypatch.setattr(ma, "AUDIT_PATH", tmp_path / "missing.jsonl")
    assert ma.recent_changes() == ([], None)
    d = tmp_path / "a_dir.jsonl"
    d.mkdir()
    monkeypatch.setattr(ma, "AUDIT_PATH", d)
    rows, err = ma.recent_changes()
    assert rows is None and err


def test_the_roster_shows_absent_as_absent(store):
    rows = {r["id"]: r for r in ma.roster(store, lambda tid: False)}
    assert rows["55"]["name"] == "Bo"
    raw = store.get("55")
    raw.pop("tier", None)
    rows = {r["id"]: r for r in ma.roster(store, lambda tid: False)}
    assert rows["55"]["tier"] is None, "an absent tier rendered as a real one"
    boom = {r["id"]: r for r in ma.roster(store, lambda tid: 1 / 0)}
    assert boom["55"]["live"] is None, "an unreadable live flag rendered as paper"


# ── both doors call it ──────────────────────────────────────────────────────

_WRITES = {"authorize", "revoke", "set_tier", "set_live_trading"}
_COMMANDS = {"_cmd_approve": "approve", "_cmd_revoke": "revoke",
             "_cmd_set_tier": "set_tier", "_cmd_revoke_live": "revoke_live"}


def _fn(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(found) == 1, (path, name)
    return found[0]


def test_the_four_commands_call_the_one_path_and_write_nothing_themselves():
    path = ROOT / "bot" / "skills" / "access_commands.py"
    for cmd, fn in _COMMANDS.items():
        node = _fn(path, cmd)
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
        assert any(c.func.attr == fn and isinstance(c.func.value, ast.Name)
                   and c.func.value.id == "member_admin" for c in calls), f"{cmd} does not call member_admin.{fn}"
        direct = [c.func.attr for c in calls if c.func.attr in _WRITES
                  and isinstance(c.func.value, ast.Attribute) and c.func.value.attr == "users"]
        assert direct == [], f"{cmd} writes the store itself: {direct}"


def test_the_gateway_door_calls_the_one_path_as_the_website():
    node = _fn(ROOT / "bot" / "web" / "user_gateway.py", "handle_admin_members_act")
    called = {c.func.attr for c in ast.walk(node)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
              and isinstance(c.func.value, ast.Name) and c.func.value.id == "member_admin"}
    assert {"approve", "revoke", "set_tier", "revoke_live"} <= called
    src = ast.unparse(node)
    assert "member_admin.WEBSITE" in src and "member_admin.TELEGRAM" not in src


# ── the gateway's member endpoints ──────────────────────────────────────────

class _Req:
    def __init__(self, app, body):
        self.app = app
        self._body = body

    async def json(self):
        return self._body


def _gw_app(store):
    bot = SimpleNamespace(send_message=AsyncMock())
    tg = SimpleNamespace(users=store, _can_trade_live=lambda tid: False,
                         app=SimpleNamespace(bot=bot))
    return {"engine": SimpleNamespace(), "tg_handler": tg}, bot


class TestGateway:
    def test_a_non_admin_reads_and_changes_nothing(self, store, audit_file):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        assert _run(ug.handle_admin_members(_Req(app, {"telegram_id": "55"}))).status == 403
        r = _run(ug.handle_admin_members_act(_Req(app, {
            "telegram_id": "55", "action": "revoke", "target": OPERATOR})))
        assert r.status == 403 and store.get(OPERATOR)["authorized"] is True
        assert _rows(audit_file) == []

    def test_the_operator_reads_the_roster_and_the_record(self, store):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        ma.approve(store, "55", "paper", by=OPERATOR, via=ma.TELEGRAM)
        r = _run(ug.handle_admin_members(_Req(app, {"telegram_id": OPERATOR})))
        assert r.status == 200
        d = json.loads(r.body)
        assert {m["id"] for m in d["members"]} >= {"55", OPERATOR, "web:7"}
        assert d["changes"][0]["action"] == "approve"
        assert "admin" not in d["web_roles"] and "admin" not in d["web_tiers"]
        assert d["you"] == OPERATOR
        assert d["members_total"] == len(d["members"]) == 3

    def test_a_cut_roster_says_how_many_there_are(self, store, monkeypatch):
        from bot.web import user_gateway as ug
        monkeypatch.setattr(ug, "_ROSTER_SHOWN", 2)
        app, _bot = _gw_app(store)
        d = json.loads(_run(ug.handle_admin_members(_Req(app, {"telegram_id": OPERATOR}))).body)
        assert len(d["members"]) == 2 and d["members_total"] == 3

    def test_an_empty_start_is_said_as_users_says_it(self, store, monkeypatch):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        d = json.loads(_run(ug.handle_admin_members(_Req(app, {"telegram_id": OPERATOR}))).body)
        assert d["store_started_empty"] is True, "the fixture's store started with no file"
        users2 = UserStore(Path(store._path))
        assert users2.started_empty is False
        app2, _ = _gw_app(users2)
        d2 = json.loads(_run(ug.handle_admin_members(_Req(app2, {"telegram_id": OPERATOR}))).body)
        assert d2["store_started_empty"] is False

    def test_an_approval_lands_and_the_member_is_told(self, store, audit_file):
        from bot.web import user_gateway as ug
        app, bot = _gw_app(store)
        r = _run(ug.handle_admin_members_act(_Req(app, {
            "telegram_id": OPERATOR, "action": "approve", "target": "55", "role": "trader"})))
        assert r.status == 200 and json.loads(r.body)["ok"] is True
        assert store.is_admitted("55")
        assert bot.send_message.await_count == 1
        assert _rows(audit_file)[-1]["via"] == "website"

    @pytest.mark.parametrize("ask", [
        {"action": "grant_live", "target": "55"},
        {"action": "grant_admin", "target": "55"},
    ])
    def test_live_and_admin_grants_are_telegram_only(self, store, audit_file, ask):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        r = _run(ug.handle_admin_members_act(_Req(app, {"telegram_id": OPERATOR, **ask})))
        assert r.status == 403 and json.loads(r.body)["error"] == "telegram_only"
        assert store.get("55").get("can_trade_live") is not True
        assert _rows(audit_file) == []

    def test_the_admin_role_and_tier_are_refused_with_a_sentence(self, store, audit_file):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        for ask in ({"action": "approve", "role": "admin"}, {"action": "set_tier", "tier": "admin"}):
            r = _run(ug.handle_admin_members_act(_Req(app, {
                "telegram_id": OPERATOR, "target": "55", **ask})))
            d = json.loads(r.body)
            assert r.status == 400 and d["ok"] is False and "Telegram only" in d["detail"]
            assert not _TAG.search(d["detail"]), "a refusal reached the page with HTML in it"
        assert store.get("55").get("role") != "admin" and store.get_tier("55") != "admin"
        assert _rows(audit_file) == []

    def test_a_refusal_in_telegrams_words_arrives_as_plain_text(self, store):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        r = _run(ug.handle_admin_members_act(_Req(app, {
            "telegram_id": OPERATOR, "action": "revoke", "target": "999"})))
        d = json.loads(r.body)
        assert r.status == 400 and d["detail"] == "User 999 not found"

    def test_an_unknown_role_names_what_the_website_grants(self, store):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        r = _run(ug.handle_admin_members_act(_Req(app, {
            "telegram_id": OPERATOR, "action": "approve", "target": "55", "role": "god"})))
        d = json.loads(r.body)
        assert r.status == 400 and "trader" in d["detail"] and "admin" not in d["detail"]

    def test_an_admin_account_is_refused_with_a_sentence(self, store):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        r = _run(ug.handle_admin_members_act(_Req(app, {
            "telegram_id": OPERATOR, "action": "set_tier", "target": OPERATOR, "tier": "basic"})))
        d = json.loads(r.body)
        assert r.status == 400 and "Telegram only" in d["detail"]
        assert store.get_tier(OPERATOR) == "admin"

    def test_an_unknown_action(self, store):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)
        r = _run(ug.handle_admin_members_act(_Req(app, {
            "telegram_id": OPERATOR, "action": "delete_everyone", "target": "55"})))
        assert r.status == 400

    def test_a_held_write_is_not_reported_as_refused(self, store, monkeypatch):
        from bot.web import user_gateway as ug
        app, _bot = _gw_app(store)

        def held(target, enabled):
            raise StoreWriteHeld("live", "OSError")
        monkeypatch.setattr(store, "set_live_trading", held)
        r = _run(ug.handle_admin_members_act(_Req(app, {
            "telegram_id": OPERATOR, "action": "revoke_live", "target": "55"})))
        d = json.loads(r.body)
        assert r.status == 200 and d["held"] is True and d["ok"] is False
