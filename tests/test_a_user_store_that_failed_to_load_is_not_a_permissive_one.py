"""A user store that failed to load answers no live permission from its empty map.

`UserStore._load` was cured of writing an unreadable `users.json` back as an
empty one: it sets `_load_failed`, refuses every write, and logs CRITICAL.
Its READERS went on answering from the empty map -- `live_trading_revoked`
False for everyone, `max_margin` None for everyone -- and under
`LIVE_OPEN_TO_KEY_HOLDERS` (the shipped default) "not revoked" plus linked
keys is a live order. Driven on the unfixed tree with per-user live on:

    users.json: "{not json"   ->  _load_failed True, _users {}
    per_user_live_eligibility ->  (True, "linked keys (live open to key holders)")
    _per_user_margin_cap      ->  None
    /trade LONG SOL $500      ->  "LIVE LONG SOL/USDT opened", size 500.0

for a user whose revoke and $20 cap were in the file that did not read. The
two permission readers RAISE on a failed load now (the store's own erasure
rule), the cap reader stops swallowing, the eligibility check refuses by
class, and the Telegram gate refuses a revoke it cannot read.
"""
from __future__ import annotations

import inspect
import types

import pytest

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.utils.user_store import UserStore
from tests.source_scan import code_only
from tests.test_a_seal_failure_does_not_unplace_a_trade import _no_website_sync as _seal_no_website_sync
from tests.test_the_envelope_is_asked_at_every_door import (
    FILLED,
    UID,
    _confirm,
    _engine,
    _own,
)
from tests.test_the_envelope_is_asked_at_every_door import audits as _audits
from tests.test_the_envelope_is_asked_at_every_door import wired as _wired

_no_website_sync = _seal_no_website_sync
wired = _wired
audits = _audits   # the system channel does not propagate, so caplog cannot see it

BAD = "{not json"


@pytest.fixture
def corrupt_store(tmp_path):
    p = tmp_path / "users.json"
    p.write_text(BAD)
    store = UserStore(str(p))
    assert store._load_failed is True and store._users == {}
    return store


@pytest.fixture
def healthy_store(tmp_path):
    store = UserStore(str(tmp_path / "users_ok.json"))
    store.register(UID, "Trader")
    return store


@pytest.fixture
def open_policy():
    was = CONFIG.live_open_to_key_holders
    object.__setattr__(CONFIG, "live_open_to_key_holders", True)
    try:
        yield
    finally:
        object.__setattr__(CONFIG, "live_open_to_key_holders", was)


@pytest.fixture
def staged_policy():
    was = CONFIG.live_open_to_key_holders
    object.__setattr__(CONFIG, "live_open_to_key_holders", False)
    try:
        yield
    finally:
        object.__setattr__(CONFIG, "live_open_to_key_holders", was)


# ── the store ────────────────────────────────────────────────────────────────


class TestTheStoreRefusesAPermissionReadItCannotMake:

    def test_the_revoke_and_the_cap_raise_and_name_the_question(self, corrupt_store, tmp_path):
        with pytest.raises(RuntimeError) as e1:
            corrupt_store.live_trading_revoked(UID)
        with pytest.raises(RuntimeError) as e2:
            corrupt_store.max_margin(UID)
        assert "live_trading_revoked" in str(e1.value) and "max_margin" in str(e2.value)
        assert "users.json failed to load" in str(e1.value)
        assert str(tmp_path) not in str(e1.value), "the path never reaches a sentence"

    def test_can_trade_live_still_answers_false_which_is_the_closed_direction(self, corrupt_store):
        assert corrupt_store.can_trade_live(UID) is False

    def test_a_healthy_store_still_answers_both(self, healthy_store):
        assert healthy_store.live_trading_revoked(UID) is False
        assert healthy_store.max_margin(UID) is None
        healthy_store.set_live_trading(UID, False)
        healthy_store.set_max_margin(UID, 20.0)
        assert healthy_store.live_trading_revoked(UID) is True
        assert healthy_store.max_margin(UID) == 20.0

    def test_the_open_policy_is_the_shipped_default(self):
        """The reason this is reachable on a stock deploy."""
        import re
        src = open("bot/config.py", encoding="utf-8").read()
        m = re.search(r'_env_bool\("LIVE_OPEN_TO_KEY_HOLDERS",\s*(True|False)\)', src)
        assert m and m.group(1) == "True"


# ── the engine ───────────────────────────────────────────────────────────────


class TestTheEngineRefusesRatherThanReadingPermission:

    def test_eligibility_under_the_open_policy_refuses_by_class(self, wired, corrupt_store, open_policy, tmp_path):
        engine, idea = _engine(tmp_path, _own(), symbol="SOL", margin=500.0)
        engine._user_store = corrupt_store
        ok, why = engine.per_user_live_eligibility(UID)
        assert ok is False
        assert "could not be read (RuntimeError)" in why and "refusing" in why
        assert str(tmp_path) not in why

    def test_the_drive_places_nothing(self, wired, corrupt_store, open_policy, tmp_path):
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="SOL", margin=500.0)
        engine._user_store = corrupt_store
        result = _confirm(engine, idea)
        assert result.startswith("Trade REJECTED"), result
        own.execute.assert_not_awaited()
        assert idea.id in engine._pending_ideas

    def test_the_cap_reader_raises_rather_than_answering_no_cap(self, wired, corrupt_store, open_policy, tmp_path):
        engine, idea = _engine(tmp_path, _own(), symbol="SOL", margin=500.0)
        engine._user_store = corrupt_store
        with pytest.raises(RuntimeError):
            engine._per_user_margin_cap(UID)

    def test_a_cap_that_cannot_be_read_refuses_at_the_recheck(self, wired, open_policy, tmp_path):
        """The revoke read answers, the cap read alone raises: the confirm-time
        re-check that asks first refuses with it, so the cap the operator set
        is never read as no cap set."""
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="SOL", margin=500.0)

        def _boom(uid):
            raise RuntimeError("cap unreadable")

        engine._user_store = types.SimpleNamespace(
            can_trade_live=lambda u: True, live_trading_revoked=lambda u: False, max_margin=_boom)
        result = _confirm(engine, idea)
        assert result.startswith("Trade REJECTED") and "cap unreadable" in result, result
        own.execute.assert_not_awaited()

    def test_the_staged_policy_was_already_closed(self, wired, corrupt_store, staged_policy, tmp_path):
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="SOL", margin=500.0)
        engine._user_store = corrupt_store
        ok, why = engine.per_user_live_eligibility(UID)
        assert ok is False and "allowlist" in why
        result = _confirm(engine, idea)
        assert result.startswith("Trade REJECTED"), result
        own.execute.assert_not_awaited()

    def test_a_healthy_store_still_gates_by_revoke_and_cap(self, wired, healthy_store, open_policy, tmp_path):
        """Controls: not revoked and uncapped places at the ticket; revoked
        refuses; a $20 cap caps the typed $500.

        register() leaves the self-admission role, and that confirm opens a
        practice row and does not call the executor. This control is the live
        door, so the user is vouched as a trader first.
        """
        assert healthy_store.authorize(UID, "trader", by="1") is True
        own = _own()
        engine, idea = _engine(tmp_path, own, symbol="SOL", margin=500.0)
        engine._user_store = healthy_store
        assert _confirm(engine, idea).startswith(FILLED)
        assert own.execute.await_args.args[1] == 500.0

        own2 = _own()
        engine2, idea2 = _engine(tmp_path, own2, symbol="SOL", margin=500.0)
        healthy_store.set_max_margin(UID, 20.0)
        engine2._user_store = healthy_store
        assert _confirm(engine2, idea2).startswith(FILLED)
        assert own2.execute.await_args.args[1] == 20.0

        own3 = _own()
        engine3, idea3 = _engine(tmp_path, own3, symbol="SOL", margin=500.0)
        healthy_store.set_live_trading(UID, False)
        engine3._user_store = healthy_store
        result = _confirm(engine3, idea3)
        assert "revoked" in result
        own3.execute.assert_not_awaited()


# ── the Telegram gate ────────────────────────────────────────────────────────


def _gate(users, *, own_keys=True):
    from bot.skills.telegram_handler import TelegramHandler
    me = types.SimpleNamespace(
        users=users,
        engine=types.SimpleNamespace(_is_operator_user=lambda uid: False),
        _allowlist_ids=lambda: set(),
        _has_own_exchange_keys=lambda uid: own_keys)
    return TelegramHandler._can_trade_live(me, UID)


class TestTheTelegramGate:

    def test_a_revoke_it_cannot_read_is_refused_and_named_by_class(self, wired, corrupt_store, open_policy, audits):
        assert _gate(corrupt_store) is False
        said = [r.getMessage() for r in audits if "revoke record" in r.getMessage()]
        assert said and "RuntimeError" in said[0] and "users.json failed" not in said[0]

    def test_a_healthy_store_still_opens_to_a_key_holder(self, wired, healthy_store, open_policy):
        assert _gate(healthy_store) is True
        assert _gate(healthy_store, own_keys=False) is False
        healthy_store.set_live_trading(UID, False)
        assert _gate(healthy_store) is False


# ── the shape ────────────────────────────────────────────────────────────────


def test_the_cap_reader_keeps_no_except_of_its_own():
    """The swallow that turned a store fault into "no cap" is gone, and the
    re-check's own `except` is what refuses."""
    src = code_only(inspect.getsource(RuneClawEngine._per_user_margin_cap))
    assert "except" not in src and "store.max_margin(user_id)" in src


def test_both_permission_readers_ask_the_one_refusal():
    for name in ("live_trading_revoked", "max_margin"):
        src = code_only(inspect.getsource(getattr(UserStore, name)))
        assert f'self._refuse_permission_read("{name}")' in src, name
