"""The kill-switch answers ``revoked`` only when the revoke is on disk.

`UserAuthorityStore._save` swallowed the OSError from its write and every
writer above it returned True regardless. Driven with the write failing:
`revoke()` returned True, the running process read the envelope as revoked,
and a restart read the file and came back ``is_enforcing = True, revoked =
False, mode = enforce`` — while the web answered ``{"ok": true, "revoked":
true}``. A revoke that disappears on restart is the one failure a kill-switch
must never report as success.

The writers return False when the write did not land, KEEP the change in
memory (this process stays revoked), and every caller that reads the boolean
says "in memory only — not persisted" instead of "done".
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.guardian import authority as auth
from bot.guardian import user_authority_store as us

TG = "42"


def _enforcing_env():
    return auth.compile_envelope({"mode": "enforce", "allowed_venues": ["bitget"],
                                  "max_notional_per_trade_usd": 100})


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "ua.json")


@pytest.fixture
def failing_writes(monkeypatch):
    """The write the store imports by name refuses, the way a full disk does."""
    state = {"fail": False}
    real = us.atomic_write_json

    def maybe(*a, **k):
        if state["fail"]:
            raise OSError("disk full")
        return real(*a, **k)

    monkeypatch.setattr(us, "atomic_write_json", maybe)
    return state


# ── the store ───────────────────────────────────────────────────────────

def test_a_revoke_that_did_not_land_returns_false_and_stays_revoked_here(path, failing_writes):
    s1 = us.UserAuthorityStore(path)
    assert s1.bind(TG, _enforcing_env()) is True
    failing_writes["fail"] = True
    assert s1.revoke(TG) is False
    # THIS process honours the revoke it could not persist …
    assert s1.get(TG)["revoked"] is True and s1.is_enforcing(TG) is False
    # … and a restart shows exactly what False warned about.
    s2 = us.UserAuthorityStore(path)
    assert s2.is_enforcing(TG) is True and s2.get(TG)["revoked"] is False


def test_a_revoke_that_landed_survives_the_restart(path, failing_writes):
    s1 = us.UserAuthorityStore(path)
    s1.bind(TG, _enforcing_env())
    assert s1.revoke(TG) is True
    s2 = us.UserAuthorityStore(path)
    assert s2.is_enforcing(TG) is False and s2.get(TG)["revoked"] is True


def test_every_writer_reports_a_write_that_did_not_land(path, failing_writes):
    s = us.UserAuthorityStore(path)
    s.bind(TG, _enforcing_env())
    failing_writes["fail"] = True
    other = auth.compile_envelope({"mode": "shadow", "allowed_venues": ["bybit"]})
    assert s.bind(TG, other) is False and s.get(TG)["envelope_id"] == other["envelope_id"]
    assert s.set_mode(TG, "off") is False and s.mode(TG) == "off"
    assert s.clear(TG) is False and s.get(TG) is None
    # Nothing landed: the original enforcing binding is what a restart reads.
    assert us.UserAuthorityStore(path).is_enforcing(TG) is True


def test_nothing_to_change_is_still_false_without_a_write(path):
    s = us.UserAuthorityStore(path)
    assert s.revoke("nobody") is False
    assert s.set_mode("nobody", "off") is False
    assert s.clear("nobody") is False


# ── the handlers ────────────────────────────────────────────────────────

class _Users:
    def get(self, tg):
        return {"authorized": True, "role": "admin"} if str(tg) == TG else None

    def is_admitted(self, tg):
        return True


def _handler():
    return SimpleNamespace(users=_Users(), _allowlist_ids=lambda: set(),
                           _limiter=SimpleNamespace(allow=lambda uid: True))


@pytest.fixture
def gateway(monkeypatch, path):
    from bot.web import user_gateway as ug
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", "s" * 32)
    store = us.UserAuthorityStore(path)
    store.bind(TG, _enforcing_env())
    monkeypatch.setattr(us, "_STORE", store)
    return ug.build_gateway(SimpleNamespace(_pending_ideas={}), _handler()), store


async def _post(app, route, body):
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        r = await c.post(route, json=body, headers={"X-Gateway-Secret": "s" * 32})
        return r.status, await r.json()
    finally:
        await c.close()


async def test_the_revoke_route_says_in_memory_only(gateway, failing_writes, path):
    app, store = gateway
    failing_writes["fail"] = True
    status, d = await _post(app, "/authority/revoke", {"telegram_id": TG})
    assert status == 500
    assert d["ok"] is False and d["persisted"] is False and d["revoked"] is True
    assert d["detail"].startswith("Revoked in memory only — not persisted; it will "
                                  "come back on restart.")
    assert store.is_enforcing(TG) is False                        # this process
    assert us.UserAuthorityStore(path).is_enforcing(TG) is True   # the restart


async def test_the_revoke_route_answers_revoked_only_when_it_landed(gateway, path):
    app, _store = gateway
    status, d = await _post(app, "/authority/revoke", {"telegram_id": TG})
    assert status == 200 and d == {"ok": True, "revoked": True, "persisted": True}
    assert us.UserAuthorityStore(path).is_enforcing(TG) is False


async def test_nothing_bound_is_not_a_revoke_and_not_a_failure(gateway):
    app, store = gateway
    store.clear(TG)
    status, d = await _post(app, "/authority/revoke", {"telegram_id": TG})
    assert status == 200 and d["revoked"] is False


async def test_the_mode_route_says_in_memory_only(gateway, failing_writes, path):
    app, store = gateway
    failing_writes["fail"] = True
    status, d = await _post(app, "/authority/mode", {"telegram_id": TG, "mode": "off"})
    assert status == 500 and d["error"] == "not_persisted"
    assert "in memory only — not persisted; it reverts on restart" in d["detail"]
    assert us.UserAuthorityStore(path).mode(TG) == "enforce"
    failing_writes["fail"] = False
    status, d = await _post(app, "/authority/mode", {"telegram_id": TG, "mode": "shadow"})
    assert status == 200 and d == {"ok": True, "mode": "shadow"}


async def test_the_apply_route_says_in_memory_only(gateway, failing_writes, path):
    app, _store = gateway
    failing_writes["fail"] = True
    status, d = await _post(app, "/authority/apply",
                            {"telegram_id": TG, "text": "max $50 per trade", "mode": "shadow"})
    assert status == 500 and d["error"] == "not_persisted"
    assert "the previous authority comes back on restart" in d["detail"]


async def test_the_tighten_route_marks_nothing_reviewed_on_a_failed_write(gateway, failing_writes,
                                                                          monkeypatch):
    app, _store = gateway
    marked = []
    import bot.guardian.review_queue as rq
    monkeypatch.setattr(rq, "get_review_queue",
                        lambda: SimpleNamespace(mark_reviewed=lambda *a, **k: marked.append(a) or 1))
    failing_writes["fail"] = True
    status, d = await _post(app, "/guardian/review/tighten",
                            {"telegram_id": TG, "target_user": TG,
                             "tighten": {"max_notional_per_trade_usd": 10}})
    assert status == 500 and d["error"] == "not_persisted"
    assert marked == []


def test_the_purge_reports_an_unlanded_clear_as_an_error(tmp_path, monkeypatch):
    """"none" says nothing was bound; "deleted" says it is gone. A binding
    cleared in memory whose write failed is neither — it comes back."""
    from bot.web import user_gateway as ug
    from tests.test_chat_guards_say_what_ran import (_conversations, _gateway_handler,
                                                     _purge, _stub_the_other_stores)
    # That suite's own autouse fixture waves the caller through; this one
    # borrows its helpers, so it does the same, explicitly.
    monkeypatch.setattr(ug, "_guard_user", lambda *a, **k: None)
    store = _stub_the_other_stores(monkeypatch, tmp_path)
    monkeypatch.setattr(us, "atomic_write_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    h = _gateway_handler(tmp_path)
    h.conversations = _conversations(tmp_path)
    h.users = SimpleNamespace(forget=lambda uid: False)
    _status, body = _purge(h, "web:7")
    assert body["stores"]["live_authority"] == "error"
    assert store.get("web:7") is None       # gone from memory, not from disk
