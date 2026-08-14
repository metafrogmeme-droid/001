"""Plant the state, assert what the user is actually told.

The registration path is where a person decides whether this bot works. Every
message on it was built inline in the handler, and every one of them was wrong
in a way no unit test could see, because there was nothing to call — the text
existed only as an argument to `_send` inside a 12,000-line handler.

So these drive the real entry points (`_handle_message`, `_cmd_start`,
`_guard`, the admit button) against a temp store and capture what came back.
The four defects they pin, all found on 2026-08-08:

  1. `_handle_message` registered the user, then said "I don't recognize you
     yet ... Use /start to register, then wait for approval."
  2. `/start` is unguarded, so a stranger got the full equity/positions card
     and the next command answered "locked to its configured operator".
  3. `/approve` announced access that `_is_allowlisted` did not honour.
  4. A stale 24h session was reported as an insufficient ROLE.

RED HERRING, planted in TestTheRedHerring: a user whose record says
`authorized: True` and `role: trader`. That looks like full access and is what
the old copy keyed on — but on a bot with an allowlist it means nothing at all,
because register() writes it for every stranger. Reading it as access is how
`/start` came to hand out a status card to people the next command refused.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.utils.user_store import DEFAULT_AUTO_ROLE, UserStore

OPERATOR = "111"


def _handler(tmp_path):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    h.forwarder = MagicMock()
    h._limiter = SimpleNamespace(allow=lambda _uid: True)
    h.engine = MagicMock()
    h.sent = []

    async def _send(update, text, **kw):
        h.sent.append(text)
    h._send = _send
    return h


def _update(uid=999, name="Ann", text="hello", lang_code="en"):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = uid
    update.effective_user.first_name = name
    update.effective_user.language_code = lang_code
    update.effective_chat = MagicMock()
    update.effective_chat.id = uid
    update.effective_chat.type = "private"
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


def _locked_bot():
    """CONFIG with an allowlist configured — i.e. every live deployment.
    is_live() requires TELEGRAM_CHAT_ID, so the allowlist is never empty
    there, which is why this is the case that mattered and the one nobody
    exercised."""
    p = patch("bot.skills.telegram_handler.CONFIG")
    mc = p.start()
    mc.telegram.chat_id = OPERATOR
    mc.telegram.admin_ids = OPERATOR
    mc.telegram.live_trader_ids = ""
    mc.simulation_mode = True
    # EVERY boolean this suite depends on has to be stated. A MagicMock returns
    # a truthy Mock for any attribute nobody set, so each new feature flag
    # silently arrives switched ON here — `paper_auto_accept` did, and admitted
    # the "stranger" these tests turn away before /approve could be what
    # granted access. The file is about the MANUAL path; the door is shut.
    mc.paper_auto_accept = False
    mc.live_open_to_key_holders = False
    return p


def _open_bot():
    p = patch("bot.skills.telegram_handler.CONFIG")
    mc = p.start()
    mc.telegram.chat_id = ""
    mc.telegram.admin_ids = ""
    mc.telegram.live_trader_ids = ""
    mc.simulation_mode = True
    return p


@pytest.mark.asyncio
class TestFirstContactByFreeText:
    async def test_an_open_bot_welcomes_without_inventing_an_approval(self, tmp_path):
        h = self._h = _handler(tmp_path)
        update, ctx = _update()
        p = _open_bot()
        try:
            await h._handle_message(update, ctx)
        finally:
            p.stop()
        assert h.sent, "a first message must get a reply"
        card = h.sent[-1].lower()
        assert "recognize" not in card and "recognise" not in card, (
            "register() ran on the line above; claiming otherwise sent people "
            "to /start to redo something already done")
        assert "wait for approval" not in card, (
            "no allowlist is configured, so there is no approval step to wait "
            "for — people waited anyway")
        assert h.users.get("999") is not None

    async def test_a_locked_bot_says_approval_is_needed_and_asks_for_it(self, tmp_path):
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        update, ctx = _update()
        p = _locked_bot()
        try:
            await h._handle_message(update, ctx)
        finally:
            p.stop()
        card = h.sent[-1]
        assert "999" in card, "they need their id"
        assert "operator" in card.lower()
        assert ctx.bot.send_message.await_count >= 1, (
            "a person the allowlist turns away used to be a dead end: no way "
            "for them to ask, and nobody told the operator they existed")
        pinged = ctx.bot.send_message.await_args.kwargs
        assert "999" in pinged["text"]
        assert pinged["reply_markup"] is not None, "one-tap approve"

    async def test_the_operator_is_pinged_once_not_once_per_message(self, tmp_path):
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        update, ctx = _update()
        p = _locked_bot()
        try:
            for _ in range(4):
                await h._handle_message(update, ctx)
        finally:
            p.stop()
        assert ctx.bot.send_message.await_count == 1, (
            "four messages, one request — otherwise a stranger tapping around "
            "the menu pages the operator once per command")
        assert len(h.sent) == 4, "but the person is answered every time"

    async def test_a_chinese_client_is_onboarded_in_chinese(self, tmp_path):
        """The bot ships a complete zh translation that a new user only saw by
        knowing /lang existed. Telegram already tells us the client locale."""
        h = _handler(tmp_path)
        update, ctx = _update(lang_code="zh-Hans")
        p = _open_bot()
        try:
            await h._handle_message(update, ctx)
        finally:
            p.stop()
        assert h.users.get("999")["lang"] == "zh"
        assert "歡迎" in h.sent[-1]

    async def test_it_never_overwrites_a_deliberate_language_choice(self, tmp_path):
        h = _handler(tmp_path)
        h.users.register("999", name="Ann")
        h.users.set_lang("999", "en")
        update, _ctx = _update(lang_code="zh-TW")
        assert h._seed_lang_from_telegram(update, "999") is False
        assert h.users.get("999")["lang"] == "en"

    async def test_an_unknown_locale_is_left_unset_not_guessed(self, tmp_path):
        h = _handler(tmp_path)
        h.users.register("999", name="Ann")
        update, _ctx = _update(lang_code="de")
        assert h._seed_lang_from_telegram(update, "999") is False
        assert "lang" not in h.users.get("999"), (
            "unset is the signal /lang and the seeder both read; writing a "
            "guessed 'en' would make a never-chosen preference look chosen")


@pytest.mark.asyncio
class TestTheRedHerring:
    async def test_authorized_true_is_not_access_on_a_locked_bot(self, tmp_path):
        """`authorized: True` is set by register() for every stranger. /start
        keyed its full status card on it and so promised access the very next
        command refused."""
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        record = h.users.register("999", name="Ann")
        # The role is DEFAULT_AUTO_ROLE, whatever that currently is — the point
        # of this test is the `authorized` flag, and pinning the role string
        # here made a role-separation change look like a registration
        # regression. What matters is that the flag is on and access is not.
        assert record["authorized"] is True
        assert record["role"] == DEFAULT_AUTO_ROLE

        update, ctx = _update()
        p = _locked_bot()
        try:
            await h._cmd_start(update, ctx)
        finally:
            p.stop()
        card = h.sent[-1].lower()
        assert "equity" not in card, (
            "the status card promises a working bot to someone every command "
            "will turn away")
        assert "operator" in card

    async def test_the_operator_still_gets_the_real_start_card(self, tmp_path):
        """The guard must not lock out the one person who definitely has
        access — a fix that breaks the operator is not a fix."""
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        p = _locked_bot()
        try:
            assert h._access_state(OPERATOR) == "granted"
        finally:
            p.stop()


@pytest.mark.asyncio
class TestHelpIsTrueForThePersonReadingIt:
    """/help is where every refusal points, so it has to be honest. The
    grouped catalogue already hides operator-only groups because "a command
    you are refused looks exactly like a command that is broken" — 125 refused
    commands is that same argument at full strength."""

    async def test_a_pending_user_gets_the_three_that_work_not_all_125(self, tmp_path):
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        h.users.register("999", name="Ann")
        update, ctx = _update(uid=999)
        p = _locked_bot()
        try:
            await h._cmd_help(update, ctx)
        finally:
            p.stop()
        card = h.sent[-1]
        assert "/lang" in card and "/start" in card
        for refused in ("/fullscan", "/backtest", "/halt"):
            assert refused not in card, (
                f"{refused} is refused for this caller; listing it makes the "
                "bot look broken rather than locked")

    async def test_an_admitted_user_still_gets_the_real_help(self, tmp_path):
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        h.users.register("999", name="Ann")
        h.users.authorize("999", role="trader", by=OPERATOR)
        h.users.tier_label = lambda _u: "Basic"
        update, ctx = _update(uid=999)
        p = _locked_bot()
        try:
            await h._cmd_help(update, ctx)
        finally:
            p.stop()
        assert h.sent, "an approved trader must still get the command guide"
        assert "not approved yet" not in h.sent[0].lower()


@pytest.mark.asyncio
class TestApprovalActuallyGrantsAccess:
    async def test_approve_then_the_next_command_works(self, tmp_path):
        """End to end, the thing that was broken: /approve announced access,
        _guard refused it.

        Runs with PAPER_AUTO_ACCEPT off (see _locked_bot), because this
        exercises the MANUAL approval path: with the door open there is no
        "turned away" state left for /approve to resolve. That path still
        matters — it is what runs when an operator shuts the door, and the bug
        it guards against (an approval the gate does not recognise) is
        orthogonal to who may knock.
        """
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        stranger, ctx = _update(uid=999)
        p = _locked_bot()
        try:
            assert await h._guard(stranger, "scan", ctx) is False
            h.users.authorize("999", role="trader", by=OPERATOR)
            h.sent.clear()
            assert await h._guard(stranger, "scan", ctx) is True, (
                "an approved user must be able to use the bot; this returned "
                "False while /approve said 'Access Granted'")
        finally:
            p.stop()

    async def test_the_admit_button_grants_the_same_thing(self, tmp_path):
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        h.users.register("999", name="Ann")
        admin_update, ctx = _update(uid=int(OPERATOR), name="Ops")
        admin_update.callback_query = MagicMock()
        admin_update.callback_query.answer = AsyncMock()
        admin_update.callback_query.data = "admit:999"
        admin_update.callback_query.edit_message_text = AsyncMock()
        p = _locked_bot()
        try:
            await h._handle_callback(admin_update, ctx)
        finally:
            p.stop()
        assert h.users.is_admitted("999") is True
        assert h.users.get("999")["admitted_by"] == OPERATOR

    async def test_a_non_admin_cannot_admit_with_the_button(self, tmp_path):
        """The callback_data is guessable; the gate is _is_admin, not the
        button's existence."""
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        h.users.register("999", name="Ann")
        h.users.authorize("999", role="trader", by=OPERATOR)   # admitted, not admin
        update, ctx = _update(uid=999, name="Ann")
        update.callback_query = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.data = "admit:4242"
        update.callback_query.edit_message_text = AsyncMock()
        p = _locked_bot()
        try:
            await h._handle_callback(update, ctx)
        finally:
            p.stop()
        assert h.users.get("4242") is None, "a non-admin admitted a stranger"


@pytest.mark.asyncio
class TestTheSessionWindowMeansWhatItSays:
    async def test_a_stale_session_is_not_reported_as_a_role_problem(self, tmp_path):
        from datetime import timedelta

        from bot.compat import UTC
        from datetime import datetime as _dt
        h = _handler(tmp_path)
        h.users.register("111", name="Ops")
        h.users.authorize("111", role="trader", by=OPERATOR)
        h.users._users["111"]["last_seen"] = (
            _dt.now(UTC) - timedelta(hours=30)).isoformat()

        update, ctx = _update(uid=111, name="Ops")
        p = _locked_bot()
        try:
            assert await h._guard(update, "trade", ctx) is False
        finally:
            p.stop()
        card = h.sent[-1]
        assert "/start" in card, "the fix must be in the message"
        assert "cannot use" not in card.lower(), (
            "the trader role HOLDS 'trade'; blaming the role sends them to an "
            "admin for something /start clears")

    async def test_activity_is_persisted_so_a_restart_does_not_expire_it(self, tmp_path):
        """_guard used to refresh last_seen in memory only, so every redeploy
        reverted active users to their registration timestamp and their first
        sensitive command was refused."""
        import json
        from datetime import timedelta

        from bot.compat import UTC
        from datetime import datetime as _dt
        # The stale timestamp has to be ON DISK, not just in memory — that is
        # the whole failure. A first draft planted it in `_users` only, so the
        # reload below read the fresh value authorize() had already saved and
        # the test passed with touch()'s save deleted.
        path = tmp_path / "users.json"
        # 25h, not 23h59m: the window is 24h, so a value just inside it reports
        # "not stale" whether or not touch() saved, and the test passes for
        # the wrong reason. Plant it OUTSIDE the window so only a real save
        # can bring it back in.
        stale = (_dt.now(UTC) - timedelta(hours=25)).isoformat()
        path.write_text(json.dumps({"111": {
            "telegram_id": "111", "name": "Ops", "role": "trader",
            "tier": "basic", "authorized": True, "can_trade_live": False,
            "admitted_at": stale, "admitted_by": OPERATOR,
            "created_at": stale, "last_seen": stale}}), encoding="utf-8")
        h = _handler(tmp_path)
        h.users = UserStore(path)

        update, ctx = _update(uid=111, name="Ops")
        p = _locked_bot()
        try:
            assert await h._guard(update, "status", ctx) is True
        finally:
            p.stop()

        reloaded = UserStore(path)          # the restart
        assert reloaded.permission_denial("111", "trade") != "stale_session", (
            "the user was active seconds ago; only the unsaved timestamp made "
            "them look idle for a day")

    async def test_a_role_that_lacks_the_command_is_still_refused(self, tmp_path):
        """Mutate the guard: the session fix must not hand out permissions."""
        h = _handler(tmp_path)
        h.users.register("999", name="Ann")
        h.users.authorize("999", role="viewer", by=OPERATOR)
        update, ctx = _update(uid=999)
        p = _locked_bot()
        try:
            assert await h._guard(update, "trade", ctx) is False
        finally:
            p.stop()
        assert "viewer" in h.sent[-1]


@pytest.mark.asyncio
class TestFreeTextObeysTheSameGateAsCommands:
    async def test_a_refused_stranger_cannot_chat_to_the_llm(self, tmp_path):
        """Free text skipped the allowlist entirely, so someone the bot
        refused for /scan could still hold a conversation on the operator's
        API key. F-2 with one door left open."""
        h = _handler(tmp_path)
        h.users.seed_admin(OPERATOR)
        h.users.register("999", name="Ann")           # self-registered only
        h._llm_chat = AsyncMock(return_value="should never run")
        update, ctx = _update(uid=999, text="what's the market doing?")
        p = _locked_bot()
        try:
            await h._handle_message(update, ctx)
        finally:
            p.stop()
        assert h._llm_chat.await_count == 0
        assert "999" in h.sent[-1]
