"""A linked user's /link and /sync push nothing onto the agent's record.

Driven before the fix, on the in-memory website and the real route:

    operator sync  -> 200, the agent's record holds 3 closes, equity 250
    user 77 /link  -> 200, DELETE FROM trades WHERE user_id = 1,
                          DELETE FROM equity_snapshots WHERE user_id = 1,
                          INSERT equity_snapshots (1, 10000, ...)

`cmd_link` sent `sync_in_background(user_id, portfolio.get("equity", 800),
[], [])` after every successful link, and `cmd_sync` (``@require_registered``
only) sent `sync_portfolio(uc.user_id, ...)`. Both read `user_portfolio`,
which nothing in the tree writes, so the payload was always the column
default: equity 10,000, no positions, no closes. `app/routes/sync.js` applied
every push to the OPERATOR's rows whatever id it named, so any linked user
wiped the agent's published trade history and equity curve (the public track
record went to `trades: 0`) and stamped $10,000 as the agent's equity. The
user was told their dashboard had synced; their own account received nothing.

The contract this file drives:

* ``/link`` posts nothing to the website beyond the token validation.
* ``/sync`` posts nothing, and says so: no figure, no "synced".
* ``sync_portfolio`` is the AGENT's record and names no account; the website
  decides whose rows those are (`BOT_USER_ID`). The route's half of the fix,
  refusing a push that names another account, is
  `app/test/a_bot_push_naming_another_account_is_refused.test.js`.
"""
from __future__ import annotations

import asyncio
import inspect
import io
import json
import types
from unittest.mock import MagicMock, patch

import pytest

from bot.skills import user_middleware as um
from bot.utils import website_sync as ws


class _Msg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


def _update(chat_id=4242):
    upd = MagicMock()
    upd.effective_chat = MagicMock()
    upd.effective_chat.id = chat_id
    upd.effective_chat.type = "private"
    upd.effective_user = MagicMock()
    upd.effective_user.username = "ann"
    upd.message = _Msg()
    ctx = MagicMock()
    ctx.args = []
    return upd, ctx


# What `get_user_portfolio` answers for every linked user: the table's
# defaults, because nothing writes `user_portfolio`. The fixture is the real
# shape so a push built from it is the push the product made.
_UNWRITTEN_PORTFOLIO = {"equity": 10000, "daily_pnl": 0, "positions": [],
                        "trade_history": []}


@pytest.fixture
def website(monkeypatch):
    """Record every way this process can reach the website's sync route.

    `_post` is the one transport; `sync_portfolio` and `sync_in_background`
    are patched too because the background sender posts from a THREAD, and a
    thread that has not run yet would make an assertion on `_post` alone pass
    over a push that was merely late.
    """
    calls = {"post": [], "sync_portfolio": [], "sync_in_background": []}
    monkeypatch.setattr(ws, "_post",
                        lambda path, body, **kw: calls["post"].append((path, body))
                        or {"ok": True})
    monkeypatch.setattr(ws, "sync_portfolio",
                        lambda *a, **kw: calls["sync_portfolio"].append((a, kw)) or True)
    monkeypatch.setattr(ws, "sync_in_background",
                        lambda *a, **kw: calls["sync_in_background"].append((a, kw)))
    return calls


def _nothing_pushed(calls):
    assert calls["sync_in_background"] == [], calls["sync_in_background"]
    assert calls["sync_portfolio"] == [], calls["sync_portfolio"]
    assert not [p for p, _ in calls["post"] if p.startswith("/api/bot/sync")], calls["post"]


class TestLinkPushesNothing:
    def _run_link(self):
        upd, ctx = _update()
        ctx.args = ["tok123"]

        class _Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        body = {"user_id": 77, "email": "ann@example.test", "plan": "free"}
        with patch.object(um, "get_user_by_chat_id", return_value=None), \
             patch.object(um, "_user_lang", return_value="en"), \
             patch.object(um.urllib.request, "urlopen",
                          lambda _req, timeout=0: _Resp(json.dumps(body).encode())), \
             patch.object(um, "_ensure_local_user"), \
             patch.object(um, "link_telegram", return_value=True):
            asyncio.run(um.cmd_link(upd, ctx))
        return upd.message.replies

    def test_a_successful_link_sends_no_sync(self, website):
        replies = self._run_link()
        assert replies and replies[-1].startswith("Linked."), replies
        _nothing_pushed(website)

    def test_the_link_card_promises_no_push(self, website):
        card = self._run_link()[-1]
        assert "/sync" not in card, card


class TestSyncPushesNothing:
    def _run_sync(self):
        upd, ctx = _update()
        user = types.SimpleNamespace(id=77, is_active=True, telegram_chat_id="4242")
        with patch.object(um, "get_user_by_chat_id", return_value=user), \
             patch.object(um, "get_user_settings", return_value=types.SimpleNamespace()), \
             patch.object(um, "_user_lang", return_value="en"):
            asyncio.run(um.cmd_sync(upd, ctx))
        return upd.message.replies

    def test_a_registered_users_sync_sends_nothing(self, website):
        replies = self._run_sync()
        assert len(replies) == 1, replies
        _nothing_pushed(website)

    def test_the_reply_says_nothing_was_sent_and_quotes_no_figure(self, website):
        (reply,) = self._run_sync()
        assert "nothing was sent" in reply, reply
        # The old card: "Dashboard synced. Equity: $10000.00" -- a claim that
        # a push happened, over a figure from a table nothing writes.
        assert "synced" not in reply.lower(), reply
        assert "$" not in reply and "10000" not in reply, reply

    def test_sync_is_still_behind_registration(self, website):
        upd, ctx = _update()
        with patch.object(um, "get_user_by_chat_id", return_value=None):
            asyncio.run(um.cmd_sync(upd, ctx))
        assert "Register at" in upd.message.replies[-1]
        _nothing_pushed(website)


class TestTheBulkPushIsTheAgentsRecord:
    def test_the_payload_names_no_account(self, monkeypatch):
        sent = []
        monkeypatch.setattr(ws, "_post",
                            lambda path, body, **kw: sent.append((path, body))
                            or {"ok": True})
        assert ws.sync_portfolio(250.0, [], []) is True
        ((path, body),) = sent
        assert path == "/api/bot/sync"
        assert "user_id" not in body, body
        assert set(body) == {"equity", "positions", "closed_trades"}, body

    @pytest.mark.parametrize("fn", ["sync_portfolio", "sync_in_background"])
    def test_neither_sender_takes_an_account(self, fn):
        # A parameter is a door: a caller holding a user id would hand it to
        # the one route that writes the agent's rows. The route refuses a
        # named account now, and the sender does not offer one to name.
        params = list(inspect.signature(getattr(ws, fn)).parameters)
        assert params == ["equity", "positions", "closed_trades"], params


class TestTheEngineStillPushesTheAgentsRecord:
    """The one legitimate sender keeps working, and names no account.

    The operator's paper close mirrors the agent's book to the website. It
    used to pass `user_id=1`; with the sender taking no account that keyword
    would raise inside the close callback's own `except`, which logs and
    moves on, so the agent's record would silently stop being mirrored. A
    stub that accepts anything cannot see that, so this drives the real
    engine's callback against a recorder with the sender's real signature.
    """

    def test_a_paper_close_pushes_the_agents_book(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        monkeypatch.setattr("bot.utils.paths.REPO_ROOT", tmp_path)
        seen = []

        def _sender(equity, positions, closed_trades):
            seen.append((equity, positions, closed_trades))

        monkeypatch.setattr(ws, "sync_in_background", _sender)
        from bot.core.engine import RuneClawEngine
        engine = RuneClawEngine()
        engine.risk._state_file = "/dev/null"
        engine.portfolio._on_trade_close(10.0)
        assert len(seen) == 1, "the agent's paper close no longer reaches the website"
        equity, positions, closed = seen[0]
        assert equity == engine.portfolio.snapshot().equity_usd
        assert positions == [] and closed == []
