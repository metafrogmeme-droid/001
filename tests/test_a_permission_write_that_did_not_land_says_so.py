"""A live-permission write that failed at the disk is in force, and says so.

`set_live_trading`, `set_max_margin` and `set_sim_opt_in` change the map
and then write the whole file. Driven on the unfixed tree with the write
raising `OSError(28)`:

    /revoke_live 4242   memory: revoked / can_trade_live False   disk: live
    /setcap 4242 5      memory: cap $5                            disk: cap $20
    restart             the revoke and the cap are gone

The command reached `_on_error` -- "Something broke on my end ... OSError"
-- which reads as "nothing happened", and no audit was written (the audit
sits after the save). For a GRANT and for `/paper off` that is the loosening
direction: the operator believes the grant failed while the user can trade
live until the next restart; the person believes paper mode is still on
while their next confirm executes live.

The writers keep the change and raise `StoreWriteHeld` naming the class;
the commands say what is in force and what is not; the audit says HELD; the
control pull acks the row as not persisted, so the website keeps it pending
and the next pull applies it again -- which persists it once the disk is
writable.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import types

import pytest

from bot.skills import access_commands as ac
from bot.skills import trading_commands as tc
from bot.utils import control_pull as cp
from bot.utils import user_store as us
from bot.utils.i18n import SUPPORTED_LANGS, t
from bot.utils.user_store import StoreWriteHeld, UserStore
from tests.source_scan import code_only
from tests.test_the_envelope_is_asked_at_every_door import _results
from tests.test_the_envelope_is_asked_at_every_door import audits as _audits

audits = _audits  # the system channel does not propagate, so caplog cannot see it

UID = "4242"
SECRET_PATH = "/srv/secret-box/users.json"


class _FullDisk:
    """Every write refused, with a PATH in the exception's text."""

    calls = 0

    def __call__(self, *a, **k):
        _FullDisk.calls += 1
        raise OSError(28, "No space left on device", SECRET_PATH)


@pytest.fixture
def store(tmp_path):
    st = UserStore(str(tmp_path / "users.json"))
    st.register(int(UID), "alice")
    st.set_live_trading(UID, True)
    st.set_max_margin(UID, 20.0)
    st.set_sim_opt_in(UID, True)
    return st


@pytest.fixture
def full_disk(monkeypatch):
    monkeypatch.setattr(us, "atomic_write_json", _FullDisk())


@pytest.fixture
def paper_feature():
    """`PAPER_SIM_OPT_IN_ENABLED` on, for the one command that checks it. The
    config is frozen, so this is the write outside monkeypatch's bookkeeping
    this file records for the firewall fixture, restored in a finally."""
    from bot.config import CONFIG
    old = CONFIG.paper_sim_opt_in_enabled
    object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", True)
    try:
        yield
    finally:
        object.__setattr__(CONFIG, "paper_sim_opt_in_enabled", old)


def _disk(store):
    return json.load(open(store._path, encoding="utf-8"))[UID]


# ── the store ────────────────────────────────────────────────────────────────


class TestTheWriterKeepsTheChangeAndSaysItIsNotOnDisk:

    def test_a_revoke_is_in_force_in_memory_and_absent_on_disk(self, store, full_disk, audits):
        with pytest.raises(StoreWriteHeld) as ei:
            store.set_live_trading(UID, False)
        assert store.live_trading_revoked(UID) is True, "this process honours the revoke"
        assert store.can_trade_live(UID) is False
        assert _disk(store)["can_trade_live"] is True and not _disk(store).get("live_revoked_at")
        assert ei.value.detail == "OSError"
        assert SECRET_PATH not in str(ei.value) and "OSError" in str(ei.value)
        assert _results(audits, "live_trading_permission") == ["HELD"], "HELD, never OK"
        held = [r for r in audits if getattr(r, "result", "") == "HELD"]
        assert "OSError" in held[0].getMessage() and SECRET_PATH not in held[0].getMessage()

    def test_a_grant_is_in_force_in_memory_and_absent_on_disk(self, store, full_disk):
        store._users[UID]["can_trade_live"] = False
        store._users[UID]["live_revoked_at"] = "2026-09-01T00:00:00+00:00"
        with pytest.raises(StoreWriteHeld):
            store.set_live_trading(UID, True)
        assert store.can_trade_live(UID) is True and store.live_trading_revoked(UID) is False
        assert _disk(store)["can_trade_live"] is True, "the disk still holds what it held"

    def test_a_cap_is_in_force_in_memory_and_absent_on_disk(self, store, full_disk, audits):
        with pytest.raises(StoreWriteHeld):
            store.set_max_margin(UID, 5.0)
        assert store.max_margin(UID) == 5.0
        assert _disk(store)["max_margin_usd"] == 20.0
        assert _results(audits, "user_max_margin") == ["HELD"]

    def test_a_paper_switch_is_in_force_in_memory_and_absent_on_disk(self, store, full_disk, audits):
        with pytest.raises(StoreWriteHeld):
            store.set_sim_opt_in(UID, False)
        assert store.sim_opt_in(UID) is False, "OFF is LIVE, and it is in force"
        assert _disk(store)["sim_opt_in"] is True
        assert _results(audits, "sim_opt_in") == ["HELD"]

    def test_a_restart_forgets_it(self, store, full_disk):
        """The sentence the commands print says a restart forgets the change;
        this is that sentence, measured."""
        with pytest.raises(StoreWriteHeld):
            store.set_live_trading(UID, False)
        fresh = UserStore(store._path)
        assert fresh.live_trading_revoked(UID) is False and fresh.can_trade_live(UID) is True

    def test_the_next_successful_save_persists_the_held_change(self, store, monkeypatch):
        """The map is written whole, so a later write of ANY kind carries the
        held change to disk -- the reason the change is kept rather than
        undone."""
        real = us.atomic_write_json
        monkeypatch.setattr(us, "atomic_write_json", _FullDisk())
        with pytest.raises(StoreWriteHeld):
            store.set_live_trading(UID, False)
        monkeypatch.setattr(us, "atomic_write_json", real)
        store.set_sim_opt_in(UID, True)  # an unrelated, successful save
        assert _disk(store).get("live_revoked_at"), "the held revoke is on disk now"

    def test_a_writable_disk_still_answers_true_and_audits_ok(self, store, audits):
        assert store.set_live_trading(UID, False) is True
        assert store.set_max_margin(UID, 5.0) is True
        assert store.set_sim_opt_in(UID, False) is True
        assert _results(audits, "live_trading_permission") == ["OK"]
        assert _results(audits, "user_max_margin") == ["OK"]
        assert _results(audits, "sim_opt_in") == ["OK"]
        assert _disk(store)["max_margin_usd"] == 5.0

    def test_an_unknown_user_is_still_false_and_touches_no_disk(self, store, full_disk):
        assert store.set_live_trading("nobody", False) is False
        assert store.set_max_margin("nobody", 5.0) is False
        assert store.set_sim_opt_in("nobody", True) is False

    def test_only_a_disk_fault_is_held(self, store, monkeypatch):
        """A write that raises for any other reason is not a held change:
        that is a defect to surface, not a disk to fix."""
        def _bug(*a, **k):
            raise TypeError("not serialisable")
        monkeypatch.setattr(us, "atomic_write_json", _bug)
        with pytest.raises(TypeError):
            store.set_live_trading(UID, False)


# ── the three commands ───────────────────────────────────────────────────────


class _Cmd:
    """A stand-in host for the mixins: an admin whose sends are recorded."""

    def __init__(self, store, admin=True):
        self.users = store
        self.sent: list[str] = []
        self._admin = admin

    def _is_admin(self, update):
        return self._admin

    async def _guard(self, update, command, ctx=None):
        return True  # `/paper` is `@guard`-decorated; the gate is not this suite's subject

    def _lang(self, update):
        return "en"

    def _get_tg_id(self, update):
        return UID

    async def _send(self, update, text, **kw):
        self.sent.append(text)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(*args):
    return types.SimpleNamespace(args=list(args))


class TestTheCommandsSayWhatIsInForce:

    def test_grant_live_says_the_grant_is_in_force_and_not_saved(self, store, full_disk):
        store._users[UID]["can_trade_live"] = False
        store._users[UID]["authorized"] = True
        host = _Cmd(store)
        _run(ac.AccessCommands._cmd_grant_live(host, object(), _ctx(UID)))
        assert len(host.sent) == 1
        out = host.sent[0]
        assert "GRANTED" in out and "NOT SAVED" in out
        assert "in force for this bot process only" in out
        assert "(OSError)" in out and SECRET_PATH not in out
        assert f"/revoke_live {UID}" in out, "the undo is named beside the grant"
        assert store.can_trade_live(UID) is True, "and it IS in force"

    def test_revoke_live_says_the_revoke_is_in_force_and_not_saved(self, store, full_disk):
        host = _Cmd(store)
        _run(ac.AccessCommands._cmd_revoke_live(host, object(), _ctx(UID)))
        assert len(host.sent) == 1
        out = host.sent[0]
        assert "REVOKED" in out and "NOT SAVED" in out
        assert "restart" in out and "(OSError)" in out and SECRET_PATH not in out
        assert "not found" not in out.lower(), "a held revoke is not an unknown user"
        assert store.live_trading_revoked(UID) is True

    @pytest.mark.parametrize("raw,expect", [("5", "$5.00"), ("off", "cleared")])
    def test_setcap_says_the_cap_is_in_force_and_not_saved(self, store, full_disk, raw, expect):
        host = _Cmd(store)
        _run(ac.AccessCommands._cmd_setcap(host, object(), _ctx(UID, raw)))
        assert len(host.sent) == 1
        out = host.sent[0]
        assert "NOT SAVED" in out and expect in out
        assert "in force for this bot process only" in out
        assert "(OSError)" in out and SECRET_PATH not in out
        assert f"/setcap {UID} {raw}" in out
        assert store.max_margin(UID) == (5.0 if raw == "5" else None)

    @pytest.mark.parametrize("action,live", [("off", True), ("on", False)])
    def test_paper_says_which_way_the_next_confirm_goes(self, store, full_disk, paper_feature, action, live):
        store._users[UID]["sim_opt_in"] = not live  # the opposite of what the command sets
        host = _Cmd(store)
        _run(tc.TradingCommands._cmd_paper(host, object(), _ctx(action)))
        assert len(host.sent) == 1
        out = host.sent[0]
        assert "NOT SAVED" in out and "(OSError)" in out and SECRET_PATH not in out
        if live:
            assert "execute <b>LIVE</b>" in out and "for this bot process" in out
        else:
            assert "SIMULATED" in out and "execute <b>LIVE</b> again" in out
        assert store.sim_opt_in(UID) is (not live), "the switch is in force"

    def test_a_writable_disk_keeps_the_old_sentences(self, store):
        store._users[UID]["authorized"] = True
        host = _Cmd(store)
        _run(ac.AccessCommands._cmd_revoke_live(host, object(), _ctx(UID)))
        _run(ac.AccessCommands._cmd_setcap(host, object(), _ctx(UID, "5")))
        assert "NOT SAVED" not in " ".join(host.sent)
        assert "REVOKED" in host.sent[0] and "Margin cap set" in host.sent[1]

    def test_the_two_translated_sentences_carry_all_fourteen_languages(self):
        from bot.utils.i18n import _STRINGS
        for key in ("grant_live_held", "revoke_live_held"):
            for lang in SUPPORTED_LANGS:
                assert lang in _STRINGS[key], (key, lang)
                out = t(key, lang, id=UID, cls="OSError")
                assert UID in out and "OSError" in out and "{" not in out, (key, lang)
                assert "users.json" in out, (key, lang)


# ── the website's door ───────────────────────────────────────────────────────


class TestTheControlPullKeepsTheRowPending:

    def test_a_held_write_is_acked_as_not_persisted(self, store, full_disk, caplog):
        caplog.set_level(logging.WARNING, logger="bot.utils.control_pull")
        row = {"user_id": 9, "telegram_id": UID, "live_enabled": False}
        acks = cp.process_pending_controls([row], store)
        assert acks == [{"user_id": 9, "ok": False, "error": "not persisted"}]
        assert store.live_trading_revoked(UID) is True, "in force for this process"
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "stays pending" in msg and "OSError" in msg and SECRET_PATH not in msg

    def test_the_next_pull_persists_it_once_the_disk_is_writable(self, store, monkeypatch):
        real = us.atomic_write_json
        monkeypatch.setattr(us, "atomic_write_json", _FullDisk())
        row = {"user_id": 9, "telegram_id": UID, "live_enabled": False}
        assert cp.process_pending_controls([row], store)[0]["ok"] is False
        monkeypatch.setattr(us, "atomic_write_json", real)
        ack = cp.process_pending_controls([row], store)[0]
        assert ack["ok"] is True and ack["live_enabled"] is False
        assert _disk(store).get("live_revoked_at")

    def test_the_website_skips_an_ack_that_is_not_ok(self):
        """The receiving half, as a scan: `/controls/ack` deletes the pending
        row only for `ok` acks, which is what makes the re-pull the durable
        copy."""
        src = open("app/routes/sync.js", encoding="utf-8").read()
        i = src.index("router.post('/controls/ack'")
        body = src[i:i + 1200]
        assert "!a.ok) continue" in body
        assert "DELETE FROM pending_controls" in body


# ── the rule over the writers ────────────────────────────────────────────────


def test_the_three_permission_writers_save_through_the_holding_helper():
    """The writers of a live permission never call `_save` directly: a raise
    there is the generic error the whole slice replaces."""
    for name in ("set_live_trading", "set_max_margin", "set_sim_opt_in"):
        src = code_only(inspect.getsource(getattr(UserStore, name)))
        assert "_save_or_hold(" in src, name
        assert "self._save()" not in src, name


def test_the_helper_holds_only_a_disk_fault_and_audits_the_class():
    src = code_only(inspect.getsource(UserStore._save_or_hold))
    assert "except OSError" in src
    assert 'result="HELD"' in src
    assert "type(exc).__name__" in src and "{exc}" not in src
