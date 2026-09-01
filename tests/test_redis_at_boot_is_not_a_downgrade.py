"""A Redis that is down at boot must not disarm the revocation guards.

From the audit's confirmed-not-remediated tier:
"py-api-authz: Redis unreachable at boot silently downgrades JWT revocation
(HIGH)".

`_maybe_connect_redis` returned a bare `None` for TWO different situations:

    not configured          -> in-process IS the backend. Nothing is promised.
    configured, ping failed -> durability was promised and is not delivered.

Every guard in the module keys off `self._redis is not None`, so the second
silently became the first. `bump_epoch` and `try_consume_jti` stopped raising
`RevocationNotDurable` — the entire M16 write-path posture, which exists
because `/auth/logout` once returned `{"ok": True}` while other workers kept
honouring the killed token — was disarmed for the life of the process by one
warning line at startup.

The same Redis dying one second AFTER boot failed loud, exactly as designed.
Boot was an accidental exception, not a decision, and that is the tell: two
code paths for one situation, disagreeing.
"""
from __future__ import annotations

import collections
import sys
import types

import pytest

from bot.api.token_store import RevocationNotDurable, TokenStore


class _UnreachableRedis:
    """Constructs fine, answers nothing — a Redis that is down."""

    def ping(self):
        raise RuntimeError("Error 111 connecting to redis-prod:6379")

    def get(self, k):
        raise RuntimeError("Error 111 connecting to redis-prod:6379")

    def incr(self, k):
        raise RuntimeError("Error 111 connecting to redis-prod:6379")

    def set(self, k, v, nx=False, ex=None):
        raise RuntimeError("Error 111 connecting to redis-prod:6379")


@pytest.fixture
def down_at_boot(monkeypatch):
    """A configured Redis whose every call fails, installed as the `redis` module."""
    fake_mod = types.ModuleType("redis")
    fake_mod.Redis = type("Redis", (), {
        "from_url": staticmethod(lambda *a, **k: _UnreachableRedis()),
        "__new__": lambda cls, *a, **k: _UnreachableRedis(),
    })
    monkeypatch.setitem(sys.modules, "redis", fake_mod)
    monkeypatch.setenv("REDIS_URL", "redis://redis-prod:6379/0")
    return fake_mod


def test_a_redis_down_at_boot_is_not_reported_as_no_redis(down_at_boot):
    """`backend` says "redis", and that is the honest answer.

    The first draft of this test asserted "redis-unavailable" and failed. It
    was the test that was wrong: the client is deliberately KEPT so the store
    self-heals, and reachability is a per-call fact, not a property of the
    process. Freezing one boot-time ping into a lasting "unavailable" label
    would be a stale claim five minutes later when Redis is back — the exact
    shape of defect this codebase keeps removing. Each call reports its own
    reachability by raising; that is what the tests below assert.

    "redis-unavailable" is kept for the one case that really is permanent:
    configured, but no client can exist at all.
    """
    store = TokenStore()
    assert store.backend != "in-process", (
        "a configured-but-unreachable Redis reported the same backend as no "
        "Redis at all, which is what disarmed every write guard"
    )
    assert store.backend == "redis"


def test_a_revocation_still_fails_loud_after_a_boot_time_outage(down_at_boot):
    """The M16 guard, which the boot path had been switching off."""
    store = TokenStore()
    with pytest.raises(RevocationNotDurable):
        store.bump_epoch(7)


def test_the_bump_is_still_recorded_locally(down_at_boot):
    """Partial enforcement beats none — raising is about what the caller is told."""
    store = TokenStore()
    with pytest.raises(RevocationNotDurable):
        store.bump_epoch(7)
    assert store.get_epoch(7) == 1


def test_refresh_replay_protection_still_fails_closed_after_a_boot_outage(down_at_boot):
    store = TokenStore()
    with pytest.raises(RevocationNotDurable):
        store.try_consume_jti("jti-boot", 60)


def test_the_read_path_keeps_its_availability_posture(down_at_boot):
    """Unchanged and deliberate: a blip must not log every user out."""
    store = TokenStore()
    assert store.get_epoch(99) == 0


def test_the_client_is_kept_so_the_store_can_self_heal(down_at_boot):
    """redis-py reconnects per command; dropping the client made a boot-time
    blip permanent until somebody restarted the process."""
    store = TokenStore()
    assert store._redis is not None, (
        "the client was discarded, so a Redis that comes back a minute later "
        "is never noticed"
    )


# ── the other half: no Redis configured must stay exactly as it was ───────

def test_no_redis_configured_still_promises_nothing(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    store = TokenStore()
    assert store.backend == "in-process"
    assert store.bump_epoch(1) == 1          # must NOT raise
    assert store.try_consume_jti("a", 60) is True
    assert store.try_consume_jti("a", 60) is False


def test_configured_but_package_missing_also_fails_loud(monkeypatch):
    """No client can exist at all. Durability was still promised."""
    monkeypatch.setenv("REDIS_URL", "redis://redis-prod:6379/0")
    monkeypatch.setitem(sys.modules, "redis", None)   # import redis -> raises
    store = TokenStore()
    assert store.backend == "redis-unavailable"
    with pytest.raises(RevocationNotDurable):
        store.bump_epoch(3)
    with pytest.raises(RevocationNotDurable):
        store.try_consume_jti("b", 60)


def test_a_store_built_without_the_flag_defaults_to_nothing_promised():
    """The class default keeps the repo's existing __new__-based test helpers
    working — and defaults to the safe reading: nothing was promised."""
    store = TokenStore.__new__(TokenStore)
    store._epoch = collections.defaultdict(int)
    store._consumed_jti = set()
    store._redis = None
    assert store.backend == "in-process"
    assert store.bump_epoch(5) == 1          # must not raise
