"""Two ways a session outlived what the user thought they had.

M15 — `GET /auth/me` was gated only by a valid access token, and answered with a
freshly minted access token AND a 7-day refresh token. So a stolen one-hour
token could be turned into a week-long one with a single GET, repeatedly, and
without passing the rotation and single-use replay detection that `/refresh`
applies to exactly this operation. Over a GET, which is the request most likely
to be logged, cached or sitting in a proxy history.

Nothing about "tell me who I am" requires issuing a credential — and the express
twin, `app/auth.js` GET /me, has always returned user fields only. This endpoint
was the one that diverged.

M16 — `/auth/logout` bumped the token epoch and returned `{"ok": True}` without
reading the result. When the bump could not be persisted it was recorded in a
per-process dict instead, so every OTHER worker went on honouring the token the
user had just killed. And because `get_epoch` read Redis first, the local bump
disappeared the moment Redis recovered: the revoked token started verifying
again once the incident was over.

THE RULE, ON A WRITE

Reading may fall back — a blip must not log everyone out. A security action that
did not take effect must not report that it did. "We tried to end your session"
and "your session is ended" are different answers, and only one of them is `ok`.
"""
from __future__ import annotations

import os

# auth_routes refuses to import without a real JWT_SECRET — the module-level
# guard that stops a server booting on the hardcoded default. Set it first.
os.environ.setdefault("JWT_SECRET", "0" * 64)

import pytest  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from bot.api import auth_routes as ar  # noqa: E402
from bot.api.token_store import RevocationNotDurable  # noqa: E402


def code_only(fn) -> str:
    """Source with comments AND docstrings removed.

    Written after this file's first draft asserted `"503" in inspect.getsource(
    ar.logout)` — which logout's own docstring satisfies ("a logout that did not
    take is a 503, not an ok"). Deleting the actual raise left the test green.
    Four false passes in this repo have come from a comment quoting the string
    it forbids; `ast.unparse` drops comments, and the docstring is popped.
    """
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            b = getattr(node, "body", None)
            if (b and isinstance(b[0], ast.Expr)
                    and isinstance(b[0].value, ast.Constant)
                    and isinstance(b[0].value.value, str)):
                b.pop(0)
    return ast.unparse(tree)


# ── M15: /me mints nothing ───────────────────────────────────────────

def test_the_user_payload_carries_no_credential():
    """`_user_info` is what /me answers with. A token in here is a token minted
    by a read."""
    info = ar._user_info.__doc__ or ""
    assert "no credentials" in info.lower()

    src = code_only(ar._user_info)
    assert "create_jwt" not in src
    for field in ("token", "refresh_token"):
        assert f'"{field}"' not in src, (
            f"_user_info returns a {field}; /me would be minting again")


def test_me_does_not_mint_anything():
    """The endpoint itself. Source-level because constructing the FastAPI
    dependency chain needs a DB and a real user; the property — no minting —
    is exactly what the source can show, since minting is a call."""
    src = code_only(ar.me)
    assert "create_jwt" not in src, (
        "/auth/me mints a credential again — a 1-hour token can be upgraded "
        "to a 7-day one with a GET")
    assert "_user_response" not in src, (
        "/auth/me returns the minting payload; use _user_info")


def test_only_the_entitled_endpoints_mint_refresh_tokens():
    """login, register and refresh — and refresh is the only one reachable with
    an existing credential, which is why it rotates and replay-checks."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ar))
    minters = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(inspect.getsource(ar), node) or ""
        if 'create_jwt' in body and 'token_type="refresh"' in body:
            minters.add(node.name)
    assert minters <= {"login", "register", "refresh"}, (
        f"unexpected refresh-token minter(s): {minters - {'login', 'register', 'refresh'}}")


def test_the_minting_helper_still_exists_for_those_endpoints():
    """The fix is to stop /me minting, not to break login."""
    src = code_only(ar._user_response)
    assert "'refresh_token'" in src
    assert "_user_info" in src, "the two payloads have diverged again"


# ── M16: a logout that did not take is not an ok ─────────────────────

@pytest.mark.asyncio
async def test_logout_reports_a_failed_revocation(monkeypatch):
    """DRIVEN, not read. It used to `return {"ok": True}` without so much as
    looking at the result."""
    def _boom(_uid):
        raise RevocationNotDurable("not persisted")

    monkeypatch.setattr(ar, "_revoke_user_tokens", _boom)
    with pytest.raises(HTTPException) as ei:
        await ar.logout(user_id=1)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_logout_still_says_ok_when_the_revocation_persisted():
    """The half that keeps the other half honest — a logout that always 503'd
    would pass the test above and break every sign-out."""
    calls = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ar, "_revoke_user_tokens", lambda uid: calls.append(uid) or 1)
        assert await ar.logout(user_id=1) == {"ok": True}
    assert calls == [1]


@pytest.mark.asyncio
async def test_the_failure_message_names_no_driver_detail(monkeypatch):
    """Same rule as /readyz: a coarse reason, never the driver's words. The
    exception carries a Redis message; the user must not receive it."""
    def _boom(_uid):
        raise RevocationNotDurable("Error 111 connecting to redis-prod:6379")

    monkeypatch.setattr(ar, "_revoke_user_tokens", _boom)
    with pytest.raises(HTTPException) as ei:
        await ar.logout(user_id=1)
    detail = str(ei.value.detail).lower()
    for leak in ("redis", "6379", "error 111", "connect"):
        assert leak not in detail, f"{leak!r} reached the caller"


@pytest.mark.asyncio
async def test_refresh_distinguishes_a_replay_from_an_unverifiable_one(monkeypatch):
    """A 401 'already used' tells a user their token was replayed. When the
    truth is 'we cannot check right now' that is both wrong and alarming."""
    monkeypatch.setattr(ar, "_verify", lambda t: {"type": "refresh", "sub": 1,
                                                  "jti": "j", "exp": 9e9})
    monkeypatch.setattr(ar, "get_user_by_id", lambda uid: object())

    def _boom(_payload):
        raise RevocationNotDurable("redis down")

    monkeypatch.setattr(ar, "_check_and_record_refresh", _boom)
    with pytest.raises(HTTPException) as ei:
        await ar.refresh(ar.RefreshIn(refresh_token="x"))
    assert ei.value.status_code == 503, "an unverifiable token reported as a replay"


@pytest.mark.asyncio
async def test_a_genuine_replay_is_still_a_401(monkeypatch):
    monkeypatch.setattr(ar, "_verify", lambda t: {"type": "refresh", "sub": 1,
                                                  "jti": "j", "exp": 9e9})
    monkeypatch.setattr(ar, "get_user_by_id", lambda uid: object())
    monkeypatch.setattr(ar, "_check_and_record_refresh", lambda p: False)
    with pytest.raises(HTTPException) as ei:
        await ar.refresh(ar.RefreshIn(refresh_token="x"))
    assert ei.value.status_code == 401


# ── the shape of the rule ────────────────────────────────────────────

def test_the_read_path_did_not_inherit_the_write_path_posture():
    """If verification started failing closed on a Redis blip, every user would
    be logged out by an infrastructure hiccup. The asymmetry is the design, not
    an oversight — pinned so a later tidy-up does not 'make it consistent'."""
    import inspect

    from bot.api import token_store as ts
    assert "RevocationNotDurable" not in code_only(ts.TokenStore.get_epoch)
    assert "RevocationNotDurable" in code_only(ts.TokenStore.bump_epoch)
    assert "RevocationNotDurable" in code_only(ts.TokenStore.try_consume_jti)


def test_the_exception_is_not_raised_without_redis_configured():
    """Behavioural. With no Redis there is no durability promise to break, and
    every existing single-process deployment must be untouched."""
    import collections

    from bot.api.token_store import TokenStore
    store = TokenStore.__new__(TokenStore)
    store._epoch = collections.defaultdict(int)
    store._consumed_jti = set()
    store._redis = None
    assert store.bump_epoch(1) == 1
    assert store.try_consume_jti("j", 60) is True


def test_a_broken_redis_raises_on_both_write_paths():
    import collections

    from bot.api.token_store import TokenStore

    class _Broken:
        def get(self, k):
            raise RuntimeError("down")

        def incr(self, k):
            raise RuntimeError("down")

        def set(self, k, v, nx=False, ex=None):
            raise RuntimeError("down")

    store = TokenStore.__new__(TokenStore)
    store._epoch = collections.defaultdict(int)
    store._consumed_jti = set()
    store._redis = _Broken()
    with pytest.raises(RevocationNotDurable):
        store.bump_epoch(1)
    with pytest.raises(RevocationNotDurable):
        store.try_consume_jti("j", 60)
    # ...and the read path still answers.
    assert store.get_epoch(1) == 1
