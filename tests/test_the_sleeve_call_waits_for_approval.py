"""A sleeve channel call is not posted until an admin approves that one item.

Nothing leaves without approval. Approve sends once per group. Reject sends
nothing. A non-admin cannot approve. A failed group is retried later, and a
group that already has the call is not posted again. A send that fails is a
warning naming the exception class, not a success.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.marketing.channel_forwarder import ChannelForwarder
from bot.skills.telegram_handler import TelegramHandler
from bot.sleeve.channel_queue import (
    approve_item,
    deliver_approved,
    enqueue_current,
    expire_stale,
    queue_file,
)
from bot.sleeve.paper import ALREADY, channel_call_text
from tests.test_the_sleeve_call_reaches_the_channel import (
    _as_newest_closed_day,
    _book_at_first_rebalance,
    _write,
)

MARKER = "MARKER-plain-detail"
G1, G2 = -100, -200


class _Bot:
    def __init__(self, fail=()):
        self.sent: list[tuple[int, str]] = []
        self.fail = set(fail)

    async def send_message(self, chat_id, text="", **kwargs):
        if chat_id in self.fail:
            raise RuntimeError(f"venue rejected {MARKER}")
        self.sent.append((chat_id, text))


def _forwarder(bot, groups):
    fwd = ChannelForwarder.__new__(ChannelForwarder)
    fwd._bot = bot
    fwd._group_ids = set(groups)
    fwd._enabled = True
    fwd._full_ids = set()
    return fwd


def _poster(fwd):
    async def _post(text, skip=None, claim=None, on_sent=None, on_fail=None):
        return await fwd.post_sleeve_call(
            text, skip=skip, claim=claim, on_sent=on_sent, on_fail=on_fail)
    return _post


def _engine(fwd):
    async def _get_exchange():
        raise AssertionError("a recorded day was fetched again")

    eng = NS(scanner=NS(_exchange=object(), _get_exchange=_get_exchange),
             _sleeve_call_callback=_poster(fwd))
    eng._announce_sleeve_call = RuneClawEngine._announce_sleeve_call.__get__(eng)
    eng._record_sleeve_paper_day = RuneClawEngine._record_sleeve_paper_day.__get__(eng)
    return eng


def _flags(on: bool) -> None:
    object.__setattr__(CONFIG, "sleeve_paper_enabled", on)
    object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", on)


def _item():
    return json.loads(queue_file().read_text())["items"][0]


def _ids(bot):
    return [chat for chat, _ in bot.sent]


async def _admin(fwd, args, *, allow=True):
    sent: list[str] = []

    async def _guard(update, command="", ctx=None):
        return allow

    async def _send(update, text, reply_markup=None, edit=False):
        sent.append(text)

    ns = NS(_guard=_guard, _send=_send, forwarder=fwd, _get_tg_id=lambda update: "1")
    await TelegramHandler._cmd_sleeve(ns, NS(), NS(args=args))
    return sent


def _prepare(tmp_path, monkeypatch):
    _, state = _book_at_first_rebalance()
    _as_newest_closed_day(state)
    _write(tmp_path, monkeypatch, state)
    return state


def test_nothing_is_sent_without_approval(tmp_path, monkeypatch):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    bot = _Bot()
    fwd = _forwarder(bot, [G1, G2])
    eng = _engine(fwd)
    try:
        _flags(True)
        assert asyncio.run(eng._record_sleeve_paper_day()) == ALREADY
    finally:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert bot.sent == []
    item = _item()
    assert item["status"] == "pending" and item["sent"] == []
    assert item["text"] == channel_call_text(state)
    assert "Paper only: no order was placed" in item["text"]
    assert "Not investment advice." in item["text"]
    assert queue_file() == tmp_path / "sleeve" / "channel_call_queue.json"


def test_approve_sends_once_per_group_and_a_second_pass_does_not(tmp_path, monkeypatch):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    bot = _Bot()
    fwd = _forwarder(bot, [G1, G2])
    eng = _engine(fwd)
    try:
        _flags(True)
        asyncio.run(eng._record_sleeve_paper_day())
        reply = asyncio.run(_admin(fwd, ["approve", f"sleeve:{state['last_day']}"]))
        assert set(_ids(bot)) == {G1, G2}
        assert all("Not investment advice." in text for _, text in bot.sent)
        assert "Posted to 2" in reply[0]
        asyncio.run(eng._record_sleeve_paper_day())
        again = asyncio.run(_admin(fwd, ["approve", f"sleeve:{state['last_day']}"]))
    finally:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert _ids(bot).count(G1) == 1 and _ids(bot).count(G2) == 1
    assert "Nothing new was sent" in again[0]
    assert set(_item()["sent"]) == {G1, G2}


def test_reject_sends_nothing(tmp_path, monkeypatch):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    bot = _Bot()
    fwd = _forwarder(bot, [G1, G2])
    eng = _engine(fwd)
    try:
        _flags(True)
        asyncio.run(eng._record_sleeve_paper_day())
        reply = asyncio.run(_admin(fwd, ["reject", f"sleeve:{state['last_day']}"]))
        asyncio.run(eng._record_sleeve_paper_day())
        asyncio.run(deliver_approved(_poster(fwd)))
    finally:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert bot.sent == []
    assert "Nothing will be posted" in reply[0]
    assert _item()["status"] == "rejected"


def test_a_non_admin_cannot_approve(tmp_path, monkeypatch):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    bot = _Bot()
    fwd = _forwarder(bot, [G1, G2])
    try:
        _flags(True)
        assert enqueue_current() is True
        reply = asyncio.run(_admin(fwd, ["approve", f"sleeve:{state['last_day']}"], allow=False))
        rejected = asyncio.run(_admin(fwd, ["reject", f"sleeve:{state['last_day']}"], allow=False))
    finally:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert reply == [] and rejected == []
    assert bot.sent == []
    assert _item()["status"] == "pending"


def test_a_failed_group_is_retried_and_a_sent_group_is_not(tmp_path, monkeypatch):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    bot = _Bot(fail={G2})
    fwd = _forwarder(bot, [G1, G2])
    eng = _engine(fwd)
    try:
        _flags(True)
        asyncio.run(eng._record_sleeve_paper_day())
        asyncio.run(_admin(fwd, ["approve", f"sleeve:{state['last_day']}"]))
        assert set(_ids(bot)) == {G1}
        assert _item()["failures"][str(G2)] == "RuntimeError"
        bot.fail.clear()
        asyncio.run(eng._record_sleeve_paper_day())
        asyncio.run(eng._record_sleeve_paper_day())
    finally:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert _ids(bot).count(G1) == 1
    assert _ids(bot).count(G2) == 1
    assert set(_item()["sent"]) == {G1, G2}
    assert _item()["failures"] == {}


def test_a_failed_send_is_a_warning_and_not_a_success(tmp_path, monkeypatch, caplog):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    bot = _Bot(fail={G1, G2})
    fwd = _forwarder(bot, [G1, G2])
    eng = _engine(fwd)
    lg = logging.getLogger("runeclaw.system")
    saved = lg.propagate
    lg.propagate = True
    try:
        _flags(True)
        asyncio.run(eng._record_sleeve_paper_day())
        assert approve_item(f"sleeve:{state['last_day']}") == "approved"
        with caplog.at_level(logging.WARNING):
            assert asyncio.run(eng._announce_sleeve_call()) is False
    finally:
        lg.propagate = saved
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert bot.sent == []
    assert _item()["sent"] == []
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings and all("RuntimeError" in r.getMessage() for r in warnings
                            if "Sleeve channel post" in r.getMessage())
    assert any("Sleeve channel post" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in warnings)
    assert MARKER not in caplog.text


def test_a_sleeve_post_strips_a_dollar_before_it_is_sent():
    bot = _Bot()
    fwd = _forwarder(bot, [G1])
    result = asyncio.run(fwd.post_sleeve_call("Fee paid $12.50 today"))
    assert result["sent"] == [G1]
    assert "$" not in bot.sent[0][1]


def test_two_passes_do_not_post_one_group_twice(tmp_path, monkeypatch):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()

    class _Hold(_Bot):
        async def send_message(self, chat_id, text="", **kwargs):
            self.sent.append((chat_id, text))
            entered.set()
            await release.wait()

    bot = _Hold()
    fwd = _forwarder(bot, [G1])

    async def _both():
        _flags(True)
        assert enqueue_current() is True
        assert approve_item(f"sleeve:{state['last_day']}") == "approved"
        first = asyncio.create_task(deliver_approved(_poster(fwd)))
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = asyncio.create_task(deliver_approved(_poster(fwd)))
        await asyncio.sleep(0)
        release.set()
        await first
        await second

    try:
        asyncio.run(_both())
    finally:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert _ids(bot) == [G1]


def test_a_stale_unapproved_call_expires_without_being_sent(tmp_path, monkeypatch):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    bot = _Bot()
    fwd = _forwarder(bot, [G1])
    old = datetime.now(UTC) - timedelta(days=8)
    try:
        _flags(True)
        assert enqueue_current(now=old) is True
        assert expire_stale() == 1
        asyncio.run(deliver_approved(_poster(fwd)))
        reply = asyncio.run(_admin(fwd, ["approve", f"sleeve:{state['last_day']}"]))
    finally:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert bot.sent == []
    assert _item()["status"] == "expired"
    assert "expired" in reply[0] and "Nothing will be posted" in reply[0]


def test_an_old_approved_call_is_still_retried(tmp_path, monkeypatch):
    was = (CONFIG.sleeve_paper_enabled, CONFIG.sleeve_channel_calls_enabled)
    state = _prepare(tmp_path, monkeypatch)
    bot = _Bot()
    fwd = _forwarder(bot, [G1])
    old = datetime.now(UTC) - timedelta(days=8)
    try:
        _flags(True)
        assert enqueue_current(now=old) is True
        assert approve_item(f"sleeve:{state['last_day']}") == "approved"
        assert expire_stale() == 0
        asyncio.run(deliver_approved(_poster(fwd)))
    finally:
        object.__setattr__(CONFIG, "sleeve_paper_enabled", was[0])
        object.__setattr__(CONFIG, "sleeve_channel_calls_enabled", was[1])
    assert _ids(bot) == [G1]
    assert _item()["status"] == "approved"
