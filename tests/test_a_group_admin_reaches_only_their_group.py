"""An admin of one group manages that group, and nothing of anyone else's.

`/channel` and `/broadcast` accepted an admin of ANY group the bot sits in as
if they were the bot's operator. So the admin of one group could:
- `/broadcast` any text, under the bot's name, into every registered group;
- switch the operator's channel posting off (`/channel off`);
- read the id of every other group (`/channel`), remove them, add arbitrary
  ids, and post a test message to all of them.
Now a group's admin reaches their own group only: running `/channel` there
adds it, `/channel` shows its own status, and `/channel remove <its id>` takes
it out. The rest is the operator's. Driven on the real forwarder and the real
handler bodies, both arms each time.

`/link` keeps its own door too: it binds a website account to the chat it is
run in, so in a group it bound the account to the group.
"""
from __future__ import annotations

import asyncio
import types
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import bot.skills.telegram_handler as th
from tests.test_a_group_reads_a_teaser import _ctx, _forwarder, _update

GROUP = -100          # the chat `_update("group")` comes from


def _host(is_bot_admin: bool, fwd):
    h = th.TelegramHandler.__new__(th.TelegramHandler)
    h.forwarder = fwd
    h._is_admin = lambda update: is_bot_admin
    h._lang = lambda update: "en"
    h._send = AsyncMock()
    return h


def _as_group_admin(ctx):
    ctx.bot.get_chat_member = AsyncMock(return_value=NS(status="administrator"))
    return ctx


def _said(host) -> str:
    return " ".join(str(c.args[1]) for c in host._send.await_args_list)


def _run(coro):
    return asyncio.run(coro)


def test_a_group_admin_cannot_broadcast_to_every_group(tmp_path, monkeypatch):
    fwd, sent = _forwarder(tmp_path, monkeypatch)
    admin_of_one = _host(False, fwd)
    _run(admin_of_one._cmd_broadcast(_update("group"), _as_group_admin(_ctx("claim", "your", "airdrop"))))
    assert sent == [], "one group's admin posted into every group"
    assert "operator" in _said(admin_of_one).lower() or "admin" in _said(admin_of_one).lower()
    operator = _host(True, fwd)
    _run(operator._cmd_broadcast(_update("private"), _ctx("hello")))
    assert sorted(cid for cid, _ in sent) == sorted([-1001, -1002])


@pytest.mark.parametrize("args", [("off",), ("on",), ("test",), ("add", "-555"),
                                  ("remove", "-1001"), ("full", "-1001")])
def test_a_group_admin_cannot_touch_anyone_elses_setting(tmp_path, monkeypatch, args):
    fwd, sent = _forwarder(tmp_path, monkeypatch)
    before = (fwd.is_enabled, sorted(fwd.group_ids), sorted(fwd.full_ids))
    h = _host(False, fwd)
    _run(h._cmd_channel(_update("group"), _as_group_admin(_ctx(*args))))
    after = (fwd.is_enabled, sorted(set(fwd.group_ids) - {GROUP}), sorted(fwd.full_ids))
    assert after == before, (args, before, after)
    assert sent == [], "a group admin posted to every group"


def test_a_group_admin_sees_only_their_own_group(tmp_path, monkeypatch):
    fwd, _sent = _forwarder(tmp_path, monkeypatch)
    h = _host(False, fwd)
    _run(h._cmd_channel(_update("group"), _as_group_admin(_ctx())))
    said = _said(h)
    assert str(GROUP) in said and "-1001" not in said and "-1002" not in said, said
    assert GROUP in fwd.group_ids, "running /channel in the group did not opt it in"
    op = _host(True, fwd)
    _run(op._cmd_channel(_update("private"), _ctx()))
    assert "-1001" in _said(op) and "-1002" in _said(op), "the operator lost the list"


def test_a_group_admin_can_take_their_own_group_out(tmp_path, monkeypatch):
    fwd, _sent = _forwarder(tmp_path, monkeypatch)
    h = _host(False, fwd)
    _run(h._cmd_channel(_update("group"), _as_group_admin(_ctx())))
    assert GROUP in fwd.group_ids
    _run(h._cmd_channel(_update("group"), _as_group_admin(_ctx("remove", str(GROUP)))))
    assert GROUP not in fwd.group_ids
    assert {-1001, -1002} <= set(fwd.group_ids), "taking one group out took others"


def test_a_member_who_is_not_a_group_admin_is_refused(tmp_path, monkeypatch):
    fwd, _sent = _forwarder(tmp_path, monkeypatch)
    h = _host(False, fwd)
    # A bare /channel: a member let through would opt the group in and be
    # shown its status, so the group's absence is the refusal's evidence.
    ctx = _ctx()
    ctx.bot.get_chat_member = AsyncMock(return_value=NS(status="member"))
    _run(h._cmd_channel(_update("group"), ctx))
    assert GROUP not in fwd.group_ids, "a plain member opted the group in"
    assert str(GROUP) not in _said(h), _said(h)


def test_link_binds_a_private_chat_only(monkeypatch):
    from bot.skills import user_middleware as um
    linked = []
    monkeypatch.setattr(um, "link_telegram", lambda *a: linked.append(a) or True)
    monkeypatch.setattr(um, "get_user_by_chat_id", lambda chat_id: None)
    monkeypatch.setattr(um, "_user_lang", lambda chat_id: "en")
    replies = []

    class _Msg:
        async def reply_text(self, text, **kw):
            replies.append(text)

    def upd(kind):
        return types.SimpleNamespace(
            effective_chat=types.SimpleNamespace(id=-100 if kind != "private" else 42, type=kind),
            effective_user=types.SimpleNamespace(id=42, username="ann"), message=_Msg())
    ctx = types.SimpleNamespace(args=["tok123"], bot=types.SimpleNamespace(username="RuneBot"))
    for kind in ("group", "supergroup", "channel"):
        _run(um.cmd_link(upd(kind), ctx))
        assert linked == [], f"/link in a {kind} bound the account to it"
        assert "private chat" in replies[-1] and "RuneBot" in replies[-1], replies[-1]
    # The private arm still reaches the token check (and fails on the stub,
    # which is the point: it got past the door).
    monkeypatch.setattr(um.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    _run(um.cmd_link(upd("private"), ctx))
    assert "private chat" not in replies[-1], replies[-1]
