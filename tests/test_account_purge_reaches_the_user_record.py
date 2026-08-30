"""The purge must delete the bot's user record, not just name the store.

`tests/test_account_purge.py` proves `UserStore.forget` works and that every
per-user store is NAMED by the purge handler. Both passed while the record was
never deleted, because neither drives the handler with the object production
actually hands it.

`handle_account_purge` reached for the store as::

    store = getattr(tg_handler, "user_store", None)

and `TelegramHandler` has no `user_store`. It binds the store as `self.users`
(`bot/skills/telegram_handler.py:846`) — which is what every other call site in
`bot/web/user_gateway.py` uses (`:94`, `:148`, `:156`, `:162`, `:193`). The
probe therefore resolved to `None` on every real request, `user_record` was set
to `"error"`, `ok` went False, and the endpoint answered 409 `{"purged": false}`
for a deletion it had not attempted.

This is the shape CLAUDE.md records twice over: an attribute probe naming a
field that does not exist, rendering as a confident negative; and a subsystem
whose only caller was a test. On a GDPR erasure path, where the user has asked
to be forgotten and the operator is told the request partly failed with no
indication that one line of it never ran at all.

So this test asserts the OUTCOME through the handler — that the record is gone
from the store afterwards — rather than that the handler mentions a store.
"""

from __future__ import annotations

import json
import types

import pytest

from bot.utils.user_store import UserStore


class _FakeRequest:
    """Just enough aiohttp Request for the handler."""

    def __init__(self, app, payload):
        self.app = app
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


def _handler_like_production(tmp_path, tg_id="111"):
    """A stand-in shaped like `TelegramHandler`: the store lives on `.users`.

    Deliberately does NOT define `user_store`. A fake that carries both names
    would pass whichever one the code reaches for, which is exactly how the
    original defect stayed invisible.
    """
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {tg_id: {"name": "u", "role": "trader", "tier": "basic",
                 "authorized": True, "language": "en"}}),
        encoding="utf-8")
    store = UserStore(str(path))
    return types.SimpleNamespace(users=store), store


@pytest.mark.asyncio
async def test_purge_deletes_the_bot_user_record(tmp_path, monkeypatch):
    from bot.web import user_gateway

    tg_handler, store = _handler_like_production(tmp_path)
    assert store.get("111") is not None, "fixture did not seed the record"

    # Neutralise the authorization guard: this test is about the purge reaching
    # the record, not about who may ask for it (covered elsewhere).
    monkeypatch.setattr(user_gateway, "_guard_user", lambda *a, **k: None)

    req = _FakeRequest({"tg_handler": tg_handler}, {"telegram_id": "111"})
    resp = await user_gateway.handle_account_purge(req)

    body = json.loads(resp.body.decode())
    assert body["stores"].get("user_record") != "error", (
        "the purge could not reach the user store — it is probing an attribute "
        f"TelegramHandler does not define. stores={body['stores']}"
    )
    assert store.get("111") is None, (
        "handle_account_purge reported on the user record without deleting it"
    )


@pytest.mark.asyncio
async def test_purge_reports_error_when_the_store_really_is_absent(tmp_path, monkeypatch):
    """The `error` branch must stay reachable.

    Renaming the probe to whatever the object happens to expose would make the
    check vacuous. A handler with no store at all must still report `error`
    rather than a quiet `none` — "we could not look" is not "there was nothing
    to delete".
    """
    from bot.web import user_gateway

    monkeypatch.setattr(user_gateway, "_guard_user", lambda *a, **k: None)
    tg_handler = types.SimpleNamespace()          # no store under any name

    req = _FakeRequest({"tg_handler": tg_handler}, {"telegram_id": "111"})
    resp = await user_gateway.handle_account_purge(req)

    body = json.loads(resp.body.decode())
    assert body["stores"].get("user_record") == "error"
    assert body["purged"] is False
    assert resp.status == 409
