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
        self._redis, self._redis_required = self._maybe_connect_redis()

    @staticmethod
    def _maybe_connect_redis():
        """Return ``(client_or_None, redis_was_configured)``.

        TWO RETURNS, BECAUSE ONE `None` WAS ANSWERING TWO QUESTIONS. This
        returned a bare `None` both when Redis was not configured — nothing
        promised, in-process IS the backend — and when it was configured and
        the boot-time `ping()` failed. Every guard in this module keys off
        `self._redis is not None`, so the second case silently became the
        first: `bump_epoch` and `try_consume_jti` stopped raising
        `RevocationNotDurable`, and the M16 write-path posture this file is
        mostly about was disarmed for the life of the process by one warning
        line at startup.

        The same Redis dying one second AFTER boot failed loud, exactly as
        designed. Boot was an accidental exception, not a decision.

        So a configured-but-unreachable Redis now KEEPS its client:
        redis-py reconnects per command, so the store self-heals when Redis
        comes back instead of staying downgraded until someone restarts the
        process. `configured` covers the one case where no client can exist
        at all — the package is missing — so that stays loud too.
        """
        url = os.getenv("REDIS_URL", "").strip()
        host = os.getenv("REDIS_HOST", "").strip()
        if not url and not host:
            return None, False  # Redis not configured → in-process only (default).
        try:
            import redis  # sync client; optional dependency
        except Exception as exc:  # pragma: no cover - import guard
            logger.error(
                "Redis is CONFIGURED but the redis package is not installed — "
                "JWT revocation cannot be made durable. Revocations will FAIL "
                "rather than be reported as done: %s", exc
            )
            return None, True
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
        except Exception as exc:  # pragma: no cover - client construction
            logger.error(
                "Redis is CONFIGURED but its client could not be built — JWT "
                "revocation cannot be made durable: %s", exc
            )
            return None, True
        try:
            client.ping()
            logger.info("JWT revocation store: Redis backend active")
        except Exception as exc:
            # NOT a downgrade. The client is kept so every write still attempts
            # Redis and still raises RevocationNotDurable when it cannot reach
            # it — the same posture as an outage that starts after boot.
            logger.error(
                "Redis is CONFIGURED but unreachable at startup — JWT "
                "revocation is NOT durable. Revocations will fail loudly "
                "until it answers: %s", exc
            )
        return client, True

    #: Durability was PROMISED — Redis is configured — even if no usable client
    #: exists. A class default so stores built with __new__ (the test helpers)
    #: keep working and default to "nothing promised".
    _redis_required: bool = False

    @property
    def backend(self) -> str:
        """What is ACTUALLY backing revocation, not what was asked for."""
        if self._redis is not None:
            return "redis"
        return "redis-unavailable" if self._redis_required else "in-process"

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
        if self._redis_required:
            # Configured, but no client exists at all (the package is missing).
            # Durability was promised and cannot be delivered, so the caller is
            # told — the same answer as an unreachable Redis, because it is the
            # same situation.
            logger.error(
                "Redis is configured but unusable — revocation for user %s is "
                "local to this worker only", user_id)
            raise RevocationNotDurable(
                "token revocation could not be persisted")
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
        if self._redis_required:
            logger.error(
                "Redis is configured but unusable — refusing the refresh rather "
                "than losing replay detection")
            raise RevocationNotDurable(
                "refresh replay protection unavailable")
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
