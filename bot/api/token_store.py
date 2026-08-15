"""RC-AUD-020: durable JWT-revocation store with optional Redis backing.

The auth layer keeps two pieces of revocation state:
  - a per-user token *epoch* (bumped on logout → invalidates every prior token);
  - the set of *consumed refresh-token jtis* (single-use refresh / replay guard).

In-process dicts are correct for a single process but do NOT span multiple
uvicorn workers / replicas and do NOT survive a restart. This module backs that
state with Redis (already provisioned in docker-compose) **when a Redis endpoint
is configured**, and otherwise — and on any Redis error — falls back to the
in-process dicts.

Design notes:
  * Synchronous on purpose: the auth helpers (`_verify`, `create_jwt`,
    `_revoke_user_tokens`, `_check_and_record_refresh`) are sync, so this uses the
    sync `redis.Redis` client with short socket timeouts. The auth path is
    low-frequency, so the brief blocking call is acceptable.
  * **The read path and the write path have DIFFERENT postures**, and conflating
    them was audit M16. Reading (`get_epoch`, on every verify) falls back to the
    in-process value on a Redis error: auth must not hard-break on a blip. But a
    REVOCATION that could not be persisted where the verify path will read it is
    not a revocation, and reporting it as one is the defect this repository is
    built around — `/auth/logout` returned `{"ok": True}` while another worker,
    reading Redis, kept honouring the token the user had just killed. Worse, the
    in-process bump was invisible after recovery too, because `get_epoch` read
    Redis FIRST and returned the stale pre-bump number.

    So the write path now fails LOUD (`RevocationNotDurable`) instead of quietly,
    while still recording the bump locally so this worker at least enforces it —
    and `get_epoch` returns the HIGHER of the two, so a local bump is never
    forgotten once Redis comes back.
  * `try_consume_jti` fails CLOSED for the same reason: an in-process set is not
    shared with the worker that will see the replay. A rejected refresh costs a
    re-login; an accepted replay costs the account.
  * With no Redis configured (the default, and in tests), the in-process dicts are
    the sole backend and behave exactly as before this change — including never
    raising, since there is no durability being promised to fail.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

_EPOCH_KEY = "rc:jwt:epoch:"   # + user_id  → integer epoch (INCR/GET)
_JTI_KEY = "rc:jwt:jti:"       # + jti      → "1" with TTL (SET NX EX)


class RevocationNotDurable(RuntimeError):
    """A revocation could not be written where the verify path will read it.

    Raised ONLY when Redis is configured and unreachable — that is, when
    durability was promised and not delivered. With no Redis configured the
    in-process store IS the backend, nothing is promised, and nothing raises.

    Callers must surface this rather than swallow it. The whole point is that
    "we tried to log you out" and "you are logged out" stop being the same
    answer.
    """


class TokenStore:
    """Per-user token epoch + consumed-jti store, Redis-backed when configured."""

    def __init__(self) -> None:
        # In-process backend — the default, the test backend, and the fallback.
        self._epoch: dict[int, int] = defaultdict(int)
        self._consumed_jti: set[str] = set()
        self._redis = self._maybe_connect_redis()

    @staticmethod
    def _maybe_connect_redis():
        """Return a connected sync Redis client, or None to use in-process only."""
        url = os.getenv("REDIS_URL", "").strip()
        host = os.getenv("REDIS_HOST", "").strip()
        if not url and not host:
            return None  # Redis not configured → in-process only (default).
        try:
            import redis  # sync client; optional dependency
        except Exception as exc:  # pragma: no cover - import guard
            logger.warning(
                "redis package not installed — JWT revocation stays in-process: %s", exc
            )
            return None
        try:
            if url:
                client = redis.Redis.from_url(
                    url, socket_timeout=2, socket_connect_timeout=2,
                    decode_responses=True,
                )
            else:
                client = redis.Redis(
                    host=host or "localhost",
                    port=int(os.getenv("REDIS_PORT", "6379") or 6379),
                    password=os.getenv("REDIS_PASSWORD") or None,
                    socket_timeout=2, socket_connect_timeout=2,
                    decode_responses=True,
                )
            client.ping()
            logger.info("JWT revocation store: Redis backend active")
            return client
        except Exception as exc:
            logger.warning(
                "Redis unavailable — JWT revocation falls back to in-process: %s", exc
            )
            return None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "in-process"

    def get_epoch(self, user_id: int) -> int:
        """Current token epoch for a user (0 if never revoked).

        Returns the HIGHER of Redis and in-process. Reading Redis alone was the
        second half of M16: a bump that fell back to the local dict during an
        outage became invisible the moment Redis answered again, so tokens the
        operator had revoked started verifying once the incident was over. A
        revocation may be late to Redis; it must never be undone by Redis.
        """
        local = self._epoch.get(user_id, 0)
        if self._redis is not None:
            try:
                v = self._redis.get(f"{_EPOCH_KEY}{user_id}")
                return max(int(v) if v is not None else 0, local)
            except Exception as exc:
                # Read path keeps its availability posture: a blip must not log
                # every user out. Falling back here loses no revocation that this
                # worker knows about, because `local` is what we return.
                logger.warning("Redis get_epoch failed, using in-process: %s", exc)
        return local

    def bump_epoch(self, user_id: int) -> int:
        """Increment the user's epoch (revokes all prior tokens). Returns new epoch.

        Raises `RevocationNotDurable` if Redis is configured and the write fails.
        The local bump still happens first, so this worker enforces it — but the
        caller is told the revocation is not durable rather than being handed a
        number that looks like success.
        """
        if self._redis is not None:
            try:
                return int(self._redis.incr(f"{_EPOCH_KEY}{user_id}"))
            except Exception as exc:
                # Record locally ANYWAY — partial enforcement beats none, and
                # get_epoch's max() keeps it alive after recovery — then refuse
                # to call it done.
                self._epoch[user_id] += 1
                logger.error(
                    "Redis bump_epoch failed for user %s — revocation is NOT "
                    "durable across workers: %s", user_id, exc)
                raise RevocationNotDurable(
                    "token revocation could not be persisted") from exc
        self._epoch[user_id] += 1
        return self._epoch[user_id]

    def try_consume_jti(self, jti: str, ttl_seconds: int) -> bool:
        """Record a refresh jti as consumed. Returns True if newly consumed,
        False if it was already consumed (a replay).

        Raises `RevocationNotDurable` if Redis is configured and unreachable.
        Falling back to the in-process set would be worse than useless here: the
        replay this exists to catch arrives at whichever worker load-balancing
        picks, and that worker's set is empty. Rejecting the refresh costs a
        re-login; accepting a replay costs the account.
        """
        ttl = max(1, int(ttl_seconds))
        if self._redis is not None:
            try:
                # SET key 1 NX EX ttl → truthy if newly set, None if it existed.
                ok = self._redis.set(f"{_JTI_KEY}{jti}", "1", nx=True, ex=ttl)
                return bool(ok)
            except Exception as exc:
                logger.error(
                    "Redis try_consume_jti failed — refusing the refresh rather "
                    "than losing replay detection: %s", exc)
                raise RevocationNotDurable(
                    "refresh replay protection unavailable") from exc
        if jti in self._consumed_jti:
            return False
        self._consumed_jti.add(jti)
        return True


_store: Optional[TokenStore] = None


def get_token_store() -> TokenStore:
    """Process-wide singleton token store."""
    global _store
    if _store is None:
        _store = TokenStore()
    return _store


def ttl_from_exp(payload: dict) -> int:
    """Seconds until a token's `exp`, floored at 1 (used to expire consumed jtis)."""
    return max(1, int(payload.get("exp", 0) - time.time()))
