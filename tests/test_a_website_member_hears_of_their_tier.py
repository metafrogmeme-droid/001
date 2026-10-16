"""A website-only member is told when /set_tier changes their tier.

A Telegram member gets a Telegram message from /set_tier. A website-only
member (`web:<site user id>`) has no chat, so the operator could raise or
lower their tier and the first they heard of it was their plan card, if they
opened it. Now the bot asks the website to email them, once per /set_tier,
with a request of its own: never inferred from the whole-book tier mirror,
whose first push after a deploy also "changes" every row it corrects.

Driven on the real `/set_tier` body and the real store; both arms each time.
"""
from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from bot.utils.user_store import UserStore


@pytest.fixture
def store(tmp_path):
    st = UserStore(str(tmp_path / "users.json"))
    st.register("101", name="m101")
    st.register("web:7", name="w7")
    return st


def _set_tier(store, monkeypatch, *args):
    """Drive the real `/set_tier`; return (Telegram messages, tier notices)."""
    from bot.skills.access_commands import AccessCommands
    from bot.utils import website_sync

    monkeypatch.setattr(website_sync, "sync_tiers_in_background", lambda m, **kw: None)
    notices = []
    monkeypatch.setattr(website_sync, "notify_tier_change_in_background",
                        lambda member, tier: notices.append((member, tier)))

    async def send(update, text, *a, **k):
        return None
    # `_get_tg_id` names who made the change, which the one write path
    # records (bot/core/member_admin.py); the real handler supplies it.
    host = types.SimpleNamespace(users=store, _is_admin=lambda u: True,
                                 _lang=lambda u: "en", _send=send,
                                 _get_tg_id=lambda u: "999")
    bot = types.SimpleNamespace(send_message=AsyncMock())
    ctx = types.SimpleNamespace(args=list(args), bot=bot)
    asyncio.run(AccessCommands._cmd_set_tier(host, object(), ctx))
    return bot.send_message.await_args_list, notices


def test_a_website_member_is_emailed_and_a_telegram_member_is_messaged(store, monkeypatch):
    told, notices = _set_tier(store, monkeypatch, "web:7", "pro")
    assert store.get_tier("web:7") == "pro"
    assert notices == [("web:7", "pro")], notices
    assert told == [], "a website-only member has no chat to write to"
    # The other arm: a Telegram member is told in Telegram, and not emailed.
    told, notices = _set_tier(store, monkeypatch, "101", "elite")
    assert notices == [], notices
    assert told and "Your tier changed" in told[-1].kwargs["text"]


def test_a_refused_set_tier_tells_nobody(store, monkeypatch):
    # The operator tier is refused for a website account; nothing changed,
    # so there is nothing to tell.
    told, notices = _set_tier(store, monkeypatch, "web:7", "admin")
    assert store.get_tier("web:7") == "basic"
    assert notices == [] and told == []
    # An unknown member is refused before anything is written.
    told, notices = _set_tier(store, monkeypatch, "web:99", "pro")
    assert notices == [] and told == []


def test_the_notice_is_its_own_request_and_is_never_retried(monkeypatch):
    from bot.utils import website_sync as ws
    calls = []

    def post(path, body, **kw):
        calls.append((path, body, kw))
        return {"sent": True}
    monkeypatch.setattr(ws, "_post", post)
    assert ws.notify_tier_change("web:7", "pro") == {"sent": True}
    assert calls == [("/api/bot/sync/tier-notice", {"member": "web:7", "tier": "pro"}, {})], calls
    # No `retries=`: an email is an event, and a retried POST whose answer
    # was lost would send the member the same email twice.
    assert "retries" not in calls[0][2]
    # Unreachable is reported as None, not as a notice sent.
    monkeypatch.setattr(ws, "_post", lambda p, b, **k: None)
    assert ws.notify_tier_change("web:7", "pro") is None


def test_the_tier_mirror_never_asks_for_a_notice(monkeypatch):
    """The whole-book push goes to /tiers only. If it also reached the notice
    route, a deploy's first push would mail every member it corrects."""
    from bot.utils import website_sync as ws
    paths = []
    monkeypatch.setattr(ws, "_post", lambda p, b, **k: paths.append(p) or {"ok": True, "updated": 1})
    monkeypatch.setattr(ws, "_last_tiers_sent", "")
    assert ws.sync_tiers({"web:7": "pro", "101": "elite"})
    assert paths and set(paths) == {"/api/bot/sync/tiers"}, paths
