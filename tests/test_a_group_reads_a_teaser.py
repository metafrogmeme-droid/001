"""A group is a public room: a live call is answered in a private chat.

The website keeps a live call for signed-in members (`app/lib/signal_access.js`).
Telegram answered a group exactly as it answers a private chat: about ten
commands printed the live calls or the operator's book into the group, their
buttons did the same, free text was routed to the same skills and the chat
model's prompt carried the pending ideas and the open positions, any group
the bot was written in enrolled itself for the channel posts, and those posts
were the full call cards.

`bot/skills/public_room.py` is the line: in a group only a command on its
allowlist answers and every other one points at the private chat; a button
tapped there does the same; free text gets the public chat a website visitor
gets; and the channel posts a teaser (the market, and where a member reads
it) unless a bot admin marked the group as a members' room. Every gate here
is driven both ways: refused in a room, served in a private chat.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import CommandHandler

from bot.nlp.conversation_store import ConversationStore
from bot.skills import public_room
from bot.skills import telegram_handler as th
from bot.utils.models import Direction, TradeIdea

MEMBER = 424242
BOT = "RuneClawBot"


class _Users:
    def get(self, tg_id):
        return {"authorized": True, "role": "admin", "name": "Op"}


def _handler():
    h = th.TelegramHandler.__new__(th.TelegramHandler)
    h.conversations = ConversationStore(max_messages_per_user=50, max_users=200)
    h.users = _Users()
    h._is_allowlisted = lambda update: True
    h._bot_username = AsyncMock(return_value=BOT)
    h._lang = lambda update: "en"
    return h


def _update(kind: str = "private", text: str = "/x", data=None):
    reply = AsyncMock()
    msg = NS(text=text, reply_text=reply, chat=NS(id=-100 if kind != "private" else MEMBER))
    query = None
    if data is not None:
        query = NS(data=data, answer=AsyncMock(), message=NS(reply_text=reply))
    return NS(effective_user=NS(id=MEMBER, first_name="Op"),
              effective_chat=NS(id=msg.chat.id, type=kind, title="Room"),
              message=msg, callback_query=query, get_bot=lambda: NS())


def _ctx(*args):
    return NS(args=list(args), bot=NS(send_message=AsyncMock()))


def _said(update) -> str:
    return " ".join(str(c.args[0]) for c in update.message.reply_text.call_args_list)


# ── the reading ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind, room", [
    ("group", True), ("supergroup", True), ("channel", True), ("private", False),
])
def test_a_room_is_the_three_public_chat_types(kind, room):
    assert public_room.is_public_room(_update(kind)) is room


def test_a_chat_type_python_telegram_bot_hands_as_an_enum_reads_as_its_word():
    from telegram.constants import ChatType
    assert public_room.is_public_room(NS(effective_chat=NS(type=ChatType.SUPERGROUP)))
    assert not public_room.is_public_room(NS(effective_chat=NS(type=ChatType.PRIVATE)))


def test_an_update_naming_no_chat_type_is_not_a_room():
    """This gate refuses in rooms and nowhere else."""
    assert not public_room.is_public_room(NS(effective_chat=MagicMock(spec=["id"])))
    assert not public_room.is_public_room(NS())


# ── commands, both arms ─────────────────────────────────────────────────

MEMBER_COMMANDS = ["latest_signal", "positions", "open_positions", "livepositions",
                   "portfolio", "orders", "fullscan", "analyze", "scalp", "playbook",
                   "dashboard", "start", "news", "patterns"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", MEMBER_COMMANDS)
async def test_a_member_command_in_a_room_points_at_the_private_chat(cmd):
    h = _handler()
    ran = AsyncMock()
    room = _update("supergroup", text=f"/{cmd}")
    await h._remembering(cmd, ran)(room, _ctx())
    ran.assert_not_awaited()
    said = _said(room)
    assert f"https://t.me/{BOT}" in said, said
    assert f"/{cmd}" in said, "the room is told which command to send privately"

    private = _update("private", text=f"/{cmd}")
    await h._remembering(cmd, ran)(private, _ctx())
    ran.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", sorted(public_room.ROOM_COMMANDS))
async def test_a_room_command_answers_in_the_room(cmd):
    h = _handler()
    ran = AsyncMock()
    await h._remembering(cmd, ran)(_update("group", text=f"/{cmd}"), _ctx())
    ran.assert_awaited_once()


class _Builder:
    def __init__(self):
        self.app = _App()

    def __getattr__(self, name):
        def _chain(*a, **k):
            return self
        return _chain

    def build(self):
        return self.app


class _App:
    def __init__(self):
        self.handlers: list = []
        self.bot_data: dict = {}
        self.error_handlers: list = []

    def add_handler(self, handler):
        self.handlers.append(handler)

    def add_error_handler(self, fn):
        self.error_handlers.append(fn)


def _registered() -> set[str]:
    from unittest.mock import patch
    h = _handler()
    h.engine = NS()
    h._known_commands = []
    with patch.object(th, "Application", NS(builder=lambda: _Builder())):
        app = h.build_app()
    return {c for x in app.handlers if isinstance(x, CommandHandler) for c in x.commands}


def test_every_room_command_is_a_registered_command():
    """Two-way: a listed name the bot no longer registers is a stale entry
    that would read as a decision, and the list must stay a short one."""
    registered = _registered()
    assert len(registered) >= 140, len(registered)
    assert public_room.ROOM_COMMANDS <= registered, (
        sorted(public_room.ROOM_COMMANDS - registered))
    assert len(public_room.ROOM_COMMANDS) < len(registered) / 5


# ── buttons, both arms ──────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["latest_signal", "positions", "confirm:TI-1", "pane:portfolio"])
async def test_a_button_tapped_in_a_room_opens_in_a_private_chat(data):
    h = _handler()
    dispatcher = AsyncMock()
    room = _update("group", data=data)
    await h._remembering_button(dispatcher)(room, _ctx())
    dispatcher.assert_not_awaited()
    room.callback_query.answer.assert_awaited_once()
    assert room.callback_query.answer.call_args.kwargs.get("show_alert") is True
    assert "private chat" in room.callback_query.answer.call_args.args[0]

    private = _update("private", data=data)
    await h._remembering_button(dispatcher)(private, _ctx())
    dispatcher.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_language_picker_still_works_in_a_room():
    h = _handler()
    dispatcher = AsyncMock()
    await h._remembering_button(dispatcher)(_update("group", data="lang:es"), _ctx())
    dispatcher.assert_awaited_once()


# ── free text in a room ─────────────────────────────────────────────────

def _texting_handler():
    h = _handler()
    h.users = NS(get=MagicMock(return_value=None), register=MagicMock())
    h.forwarder = NS(detect_group=MagicMock())
    h.engine = None
    h._get_tg_id = lambda update: str(MEMBER)
    h._llm_chat = AsyncMock(return_value=("A sweep takes liquidity.", {"model": "m"}))
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["analyze BTC", "show my positions", "scalp scan"])
async def test_a_room_asking_for_a_member_feature_is_pointed_at_the_private_chat(text):
    h = _texting_handler()
    room = _update("supergroup", text=text)
    await h._handle_message(room, _ctx())
    h._llm_chat.assert_not_awaited()
    assert f"https://t.me/{BOT}" in _said(room)
    h.users.register.assert_not_called()


@pytest.mark.asyncio
async def test_a_room_question_gets_the_public_chat_and_registers_nobody():
    from bot.web import public_chat_quota as quota
    saved = quota._snapshot()
    try:
        quota._restore(None)
        h = _texting_handler()
        room = _update("group", text="what is a liquidity sweep")
        await h._handle_message(room, _ctx())
        h._llm_chat.assert_awaited_once()
        kw = h._llm_chat.call_args.kwargs
        assert kw["public"] is True, "a room got the members' chat"
        assert kw["user_id"] == "" and kw["is_admin"] is False
        assert kw["surface"] == "telegram"
        assert "A sweep takes liquidity." in _said(room)
        h.users.register.assert_not_called()
        h.forwarder.detect_group.assert_not_called()
        key = public_room.room_visitor_key(str(MEMBER))
        assert quota._snapshot()["counts"].get(key) == 1, "the preview allowance was not counted"
        assert str(MEMBER) not in key, "the count table names an account"
    finally:
        quota._restore(saved)


@pytest.mark.asyncio
async def test_a_room_whose_preview_is_spent_is_pointed_at_the_private_chat():
    from bot.web import public_chat_quota as quota
    saved = quota._snapshot()
    try:
        quota._restore({"day": quota._today(), "counts": {
            public_room.room_visitor_key(str(MEMBER)): quota.daily_limit()}})
        h = _texting_handler()
        room = _update("group", text="what is a liquidity sweep")
        await h._handle_message(room, _ctx())
        h._llm_chat.assert_not_awaited()
        assert f"https://t.me/{BOT}" in _said(room)
    finally:
        quota._restore(saved)


# ── the channel posts ───────────────────────────────────────────────────

TIA = dict(id="T1", asset="TIA/USDT", direction=Direction.SHORT,
           entry_price=0.58820, stop_loss=0.60164, take_profit=0.55065,
           confidence=0.71, reasoning="r", strategy_type="swing")


def _forwarder(tmp_path, monkeypatch, full=()):
    from bot.marketing import channel_forwarder as cf
    monkeypatch.setattr(cf, "_CONFIG_PATH", tmp_path / "channel_config.json")
    fwd = cf.ChannelForwarder()
    sent: list = []

    class _Bot:
        username = BOT

        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))

    fwd.set_bot(_Bot())
    for gid in (-1001, -1002):
        fwd.add_group(gid)
    for gid in full:
        fwd.set_full(gid, True)
    return fwd, sent


def _to(sent, gid) -> str:
    return "\n".join(t for g, t in sent if g == gid)


def test_a_group_reads_a_teaser_and_a_members_room_the_card(tmp_path, monkeypatch):
    fwd, sent = _forwarder(tmp_path, monkeypatch, full=(-1002,))
    asyncio.run(fwd.post_signal(TradeIdea(**TIA)))
    teaser, card = _to(sent, -1001), _to(sent, -1002)
    assert "TIA/USDT" in teaser and f"https://t.me/{BOT}" in teaser
    for secret in ("SHORT", "0.5882", "0.6016", "0.5506", "Confidence"):
        assert secret not in teaser, f"the teaser told a room the call's {secret}"
    assert "SHORT" in card and "0.5882" in card, "the members' room did not get the card"


def test_an_open_is_a_teaser_and_a_close_is_posted_whole(tmp_path, monkeypatch):
    fwd, sent = _forwarder(tmp_path, monkeypatch, full=(-1002,))
    asyncio.run(fwd.post_trade_opened({"symbol": "TIA/USDT", "direction": "SHORT",
                                       "entry": 0.5882, "stop": 0.6016, "target": 0.5506}))
    assert "TIA/USDT" in _to(sent, -1001)
    assert not any(s in _to(sent, -1001) for s in ("SHORT", "0.5882", "0.6016"))
    assert "0.5882" in _to(sent, -1002)
    sent.clear()
    asyncio.run(fwd.post_trade_closed("<b>TIA/USDT</b> SHORT closed -1.20%", outcome="loss"))
    assert "SHORT closed" in _to(sent, -1001), "a resolved trade is everyone's"
    assert "SHORT closed" in _to(sent, -1002)


def test_a_members_room_is_remembered_and_forgotten_with_its_group(tmp_path, monkeypatch):
    from bot.marketing import channel_forwarder as cf
    fwd, _ = _forwarder(tmp_path, monkeypatch, full=(-1002,))
    again = cf.ChannelForwarder()
    assert again.full_ids == [-1002], "the choice did not survive a restart"
    again.remove_group(-1002)
    assert cf.ChannelForwarder().full_ids == []
    # Re-added, it is a new group and gets teasers: the old choice must not
    # come back with it, which a filter on the read alone would hide.
    again.add_group(-1002)
    assert cf.ChannelForwarder().full_ids == []


def test_without_a_readable_handle_the_teaser_names_the_door_in_words(tmp_path, monkeypatch):
    fwd, sent = _forwarder(tmp_path, monkeypatch)
    fwd._bot.username = None
    asyncio.run(fwd.post_signal(TradeIdea(**TIA)))
    assert "a private chat with the RUNECLAW bot" in _to(sent, -1001)


def test_writing_in_a_group_no_longer_enrols_it():
    """The auto-enrol line is gone from the message handler: a group is added
    by `/channel`, run by a bot admin or that group's admin."""
    import inspect

    from tests.source_scan import code_only
    src = code_only(inspect.getsource(th.TelegramHandler._handle_message))
    assert "detect_group" not in src


# ── /channel full|teaser ────────────────────────────────────────────────

def _channel_host(is_bot_admin: bool, fwd):
    h = th.TelegramHandler.__new__(th.TelegramHandler)
    h.forwarder = fwd
    h._is_admin = lambda update: is_bot_admin
    h._lang = lambda update: "en"
    h._send = AsyncMock()
    h._cmd_channel_impl = h._cmd_channel
    return h


@pytest.mark.asyncio
async def test_only_a_bot_admin_makes_a_group_a_members_room(tmp_path, monkeypatch):
    fwd, _ = _forwarder(tmp_path, monkeypatch)
    group_admin = _channel_host(False, fwd)
    ctx = _ctx("full", "-1001")
    ctx.bot.get_chat_member = AsyncMock(return_value=NS(status="administrator"))
    await group_admin._cmd_channel_impl(_update("group"), ctx)
    assert fwd.full_ids == [], "a group admin made their own group a members' room"

    bot_admin = _channel_host(True, fwd)
    await bot_admin._cmd_channel_impl(_update("private"), _ctx("full", "-1001"))
    assert fwd.full_ids == [-1001]
    await bot_admin._cmd_channel_impl(_update("private"), _ctx("teaser", "-1001"))
    assert fwd.full_ids == []
    await bot_admin._cmd_channel_impl(_update("private"), _ctx("full", "-999"))
    fwd.add_group(-999)
    assert fwd.full_ids == [], "an unregistered id became a members' room on being added"
