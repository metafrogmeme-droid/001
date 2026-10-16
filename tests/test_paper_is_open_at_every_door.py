"""Paper is open at every door, not only behind a guarded command.

The owner asked for paper trading with no admin approval. PAPER_AUTO_ACCEPT
already existed and defaulted on, and `_guard` admitted a stranger to paper on
their first guarded command. But that was the only door that did. A newcomer
whose first touch was /start, /help, a plain message or a button was told "this
bot admits people by hand", and the operator was paged with an access request,
while the same person typing /scan a moment later was let in with nobody
asked. The website's gateway refused a Telegram-linked account outright and
sent it to Telegram to wait.

Every door now asks one function, `TelegramHandler._admit_for_paper`. Driven
here through the real entry points against a temp store, each door both ways:
- with the flag on, a newcomer is admitted as paper (role ``paper``,
  ``admitted_by="auto-accept"``), is not told to wait, and nobody is paged;
- with it off, the door is shut and asks the operator, as before.
And what the door never does:
- re-admit someone an admin revoked;
- admit anyone from a group, where talking registers nobody;
- grant more than paper.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.utils.user_store import SELF_ADMISSION_BY, SELF_ADMISSION_ROLE, UserStore

OPERATOR = "111"
NEWCOMER = "999"


def _handler(tmp_path):
    from bot.skills.telegram_handler import TelegramHandler
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = UserStore(tmp_path / "users.json")
    h.users.seed_admin(OPERATOR)
    h.forwarder = MagicMock()
    h._limiter = SimpleNamespace(allow=lambda _uid: True)
    h.engine = MagicMock()
    h.sent = []

    async def _send(update, text, **kw):
        h.sent.append(text)
    h._send = _send
    return h


def _update(uid=int(NEWCOMER), name="Ann", text="hello", chat_type="private"):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = uid
    update.effective_user.first_name = name
    update.effective_user.language_code = "en"
    update.effective_chat = MagicMock()
    update.effective_chat.id = uid
    update.effective_chat.type = chat_type
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = []
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


def _bot(auto_accept: bool):
    """An allowlisted bot, as every live deployment is. Every flag this file
    depends on is stated: a MagicMock answers truthy for anything unset."""
    p = patch("bot.skills.telegram_handler.CONFIG")
    mc = p.start()
    mc.telegram.chat_id = OPERATOR
    mc.telegram.admin_ids = OPERATOR
    mc.telegram.live_trader_ids = ""
    mc.simulation_mode = True
    mc.paper_auto_accept = auto_accept
    mc.live_open_to_key_holders = False
    return p


def _operator_was_paged(ctx) -> bool:
    return any(int(c.kwargs.get("chat_id", 0) or 0) == int(OPERATOR)
               for c in ctx.bot.send_message.call_args_list)


def _admitted_as_paper(h, uid=NEWCOMER) -> bool:
    rec = h.users.get(uid) or {}
    return (h.users.is_admitted(uid) is True
            and rec.get("role") == SELF_ADMISSION_ROLE
            and rec.get("admitted_by") == SELF_ADMISSION_BY)


# ── /start ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestStart:
    async def test_a_newcomer_is_admitted_to_paper_and_nobody_is_paged(self, tmp_path):
        h = _handler(tmp_path)
        update, ctx = _update()
        p = _bot(auto_accept=True)
        try:
            try:
                await h._cmd_start(update, ctx)
            except Exception:
                # The admitted path goes on to draw the status card from a mock
                # engine; what this test is about happened before that.
                pass
        finally:
            p.stop()
        assert _admitted_as_paper(h), h.users.get(NEWCOMER)
        assert not _operator_was_paged(ctx), "an open door asked the operator anyway"
        assert not any("wait" in s.lower() and "operator" in s.lower() for s in h.sent), h.sent

    async def test_with_the_door_shut_start_still_asks_the_operator(self, tmp_path):
        h = _handler(tmp_path)
        update, ctx = _update()
        p = _bot(auto_accept=False)
        try:
            await h._cmd_start(update, ctx)
        finally:
            p.stop()
        assert h.users.is_admitted(NEWCOMER) is False
        assert _operator_was_paged(ctx)
        assert "equity" not in h.sent[-1].lower()

    async def test_a_revoked_member_is_not_let_back_in(self, tmp_path):
        h = _handler(tmp_path)
        h.users.register(NEWCOMER, name="Ann")
        h.users.authorize(NEWCOMER, role="trader", by=OPERATOR)
        h.users.revoke(NEWCOMER)
        update, ctx = _update()
        p = _bot(auto_accept=True)
        try:
            await h._cmd_start(update, ctx)
        finally:
            p.stop()
        assert h.users.is_admitted(NEWCOMER) is False, "/start undid an admin's /revoke"
        assert "equity" not in h.sent[-1].lower()


# ── /help ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestHelp:
    async def test_a_newcomer_reads_the_help_they_can_use(self, tmp_path):
        h = _handler(tmp_path)
        h.users.tier_label = lambda _u: "Basic"
        update, ctx = _update()
        p = _bot(auto_accept=True)
        try:
            await h._cmd_help(update, ctx)
        finally:
            p.stop()
        assert _admitted_as_paper(h)
        assert not _operator_was_paged(ctx)
        assert "not approved yet" not in h.sent[-1].lower()

    async def test_with_the_door_shut_help_is_the_three_that_work(self, tmp_path):
        h = _handler(tmp_path)
        update, ctx = _update()
        p = _bot(auto_accept=False)
        try:
            await h._cmd_help(update, ctx)
        finally:
            p.stop()
        assert h.users.is_admitted(NEWCOMER) is False
        assert "/fullscan" not in h.sent[-1]

    async def test_help_in_a_group_admits_nobody(self, tmp_path):
        """A group is a public room: talking there registers nobody and
        enrols nothing (bot/skills/public_room.py)."""
        h = _handler(tmp_path)
        update, ctx = _update(chat_type="supergroup")
        p = _bot(auto_accept=True)
        try:
            await h._cmd_help(update, ctx)
        finally:
            p.stop()
        assert h.users.is_admitted(NEWCOMER) is False


# ── a first message, and the next one ─────────────────────────────────────

@pytest.mark.asyncio
class TestFreeText:
    async def test_a_first_message_admits_and_says_so(self, tmp_path):
        h = _handler(tmp_path)
        update, ctx = _update()
        p = _bot(auto_accept=True)
        try:
            await h._handle_message(update, ctx)
        finally:
            p.stop()
        assert _admitted_as_paper(h)
        assert not _operator_was_paged(ctx)
        from bot.formatters.onboarding import welcome_notice
        granted = welcome_notice("Ann", NEWCOMER, access="granted", lang="en")
        shut = welcome_notice("Ann", NEWCOMER, access="needs_approval",
                              operator_notified=False, lang="en")
        assert granted != shut
        assert h.sent and h.sent[-1].startswith(granted), h.sent

    async def test_a_registered_stranger_s_next_message_is_let_through(self, tmp_path):
        """Registered on an earlier visit while the door was shut, writing
        again after it opened: admitted, and the message goes on to the rate
        limiter rather than a refusal."""
        h = _handler(tmp_path)
        h.users.register(NEWCOMER, name="Ann")
        h._limiter = SimpleNamespace(allow=lambda _uid: False)   # stop right after the gate
        update, ctx = _update()
        p = _bot(auto_accept=True)
        try:
            await h._handle_message(update, ctx)
        finally:
            p.stop()
        assert _admitted_as_paper(h)
        assert not _operator_was_paged(ctx)
        update.message.reply_text.assert_awaited()   # the rate-limit reply, past the gate

    async def test_with_the_door_shut_the_next_message_is_refused(self, tmp_path):
        h = _handler(tmp_path)
        h.users.register(NEWCOMER, name="Ann")
        update, ctx = _update()
        p = _bot(auto_accept=False)
        try:
            await h._handle_message(update, ctx)
        finally:
            p.stop()
        assert h.users.is_admitted(NEWCOMER) is False
        assert _operator_was_paged(ctx)


# ── a button ──────────────────────────────────────────────────────────────

def _tap(data: str):
    update, ctx = _update()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.data = data
    update.callback_query.edit_message_text = AsyncMock()
    return update, ctx


@pytest.mark.asyncio
class TestButton:
    async def test_a_newcomer_s_tap_is_admitted_and_handled(self, tmp_path):
        h = _handler(tmp_path)
        update, ctx = _tap("lang:es")
        p = _bot(auto_accept=True)
        try:
            await h._handle_callback(update, ctx)
        finally:
            p.stop()
        assert _admitted_as_paper(h)
        assert not _operator_was_paged(ctx)
        assert (h.users.get(NEWCOMER) or {}).get("lang") == "es", (
            "admitted, the tap must go on to do what it was for")

    async def test_with_the_door_shut_the_tap_is_refused(self, tmp_path):
        h = _handler(tmp_path)
        update, ctx = _tap("lang:es")
        p = _bot(auto_accept=False)
        try:
            await h._handle_callback(update, ctx)
        finally:
            p.stop()
        assert h.users.is_admitted(NEWCOMER) is False
        assert (h.users.get(NEWCOMER) or {}).get("lang") != "es"


# ── the website, for a Telegram-linked account ────────────────────────────

class TestWebsiteGateway:
    def test_a_linked_account_is_admitted_to_paper_from_the_website(self, tmp_path):
        from bot.web.user_gateway import _guard_user
        h = _handler(tmp_path)
        p = _bot(auto_accept=True)
        try:
            refused = _guard_user(h, NEWCOMER, "scan", "Ann")
        finally:
            p.stop()
        assert refused is None, getattr(refused, "text", refused)
        assert _admitted_as_paper(h)

    def test_with_the_door_shut_the_website_says_not_approved(self, tmp_path):
        from bot.web.user_gateway import _guard_user
        h = _handler(tmp_path)
        p = _bot(auto_accept=False)
        try:
            refused = _guard_user(h, NEWCOMER, "scan", "Ann")
        finally:
            p.stop()
        assert refused is not None and refused.status == 403
        assert "not_allowlisted" in refused.text
        assert h.users.is_admitted(NEWCOMER) is False

    def test_a_revoked_account_stays_refused_on_the_website(self, tmp_path):
        from bot.web.user_gateway import _guard_user
        h = _handler(tmp_path)
        h.users.register(NEWCOMER, name="Ann")
        h.users.authorize(NEWCOMER, role="trader", by=OPERATOR)
        h.users.revoke(NEWCOMER)
        p = _bot(auto_accept=True)
        try:
            refused = _guard_user(h, NEWCOMER, "scan", "Ann")
        finally:
            p.stop()
        assert refused is not None and refused.status == 403
        assert h.users.is_admitted(NEWCOMER) is False

    def test_a_stand_in_handler_admits_nobody(self, tmp_path):
        """A handler whose admission answers anything but a real True has
        admitted nobody: a mock's truthy return must not open the door."""
        from bot.web.user_gateway import _admit_web_caller_for_paper
        stand_in = MagicMock()
        assert _admit_web_caller_for_paper(stand_in, NEWCOMER, "Ann") is False
        stand_in._admit_for_paper.side_effect = RuntimeError("store down")
        assert _admit_web_caller_for_paper(stand_in, NEWCOMER, "Ann") is False


# ── a guarded command, the door that always admitted ──────────────────────

@pytest.mark.asyncio
class TestGuardedCommand:
    async def test_a_newcomer_s_first_command_is_admitted(self, tmp_path):
        h = _handler(tmp_path)
        update, ctx = _update()
        p = _bot(auto_accept=True)
        try:
            assert await h._guard(update, "scan", ctx) is True
        finally:
            p.stop()
        assert _admitted_as_paper(h)
        assert not _operator_was_paged(ctx)

    async def test_with_the_door_shut_the_command_is_refused(self, tmp_path):
        h = _handler(tmp_path)
        update, ctx = _update()
        p = _bot(auto_accept=False)
        try:
            assert await h._guard(update, "scan", ctx) is False
        finally:
            p.stop()
        assert h.users.is_admitted(NEWCOMER) is False
        assert _operator_was_paged(ctx)


# ── what the door grants ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_door_grants_paper_and_nothing_more(tmp_path):
    """Admitted by /start, the newcomer is a practice account: no operator
    controls, and a confirm is a practice fill (never a live order)."""
    from bot.core.practice_fill import self_admitted_paper_caller
    h = _handler(tmp_path)
    update, ctx = _update()
    p = _bot(auto_accept=True)
    try:
        try:
            await h._cmd_start(update, ctx)
        except Exception:
            pass
        assert _admitted_as_paper(h)
        for control in ("halt", "reset", "mode"):
            assert not h.users.has_permission(NEWCOMER, control), control
    finally:
        p.stop()
    assert self_admitted_paper_caller(h.users, NEWCOMER) is True, (
        "a self-admitted account's confirms are practice fills, never live orders")


def test_every_first_contact_door_asks_the_one_function():
    """A door added tomorrow that admits by hand, or refuses without asking,
    is the split this file exists for. Each first-contact entry point reaches
    the one function; nothing else calls `authorize` with the self-admission
    stamp."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]

    def body(path, name):
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.unparse(node)
        raise AssertionError(f"{path}::{name} is gone")

    for path, name, call in [
        ("bot/skills/start_commands.py", "_cmd_start", "_admit_newcomer"),
        ("bot/skills/start_commands.py", "_cmd_help", "_admit_newcomer"),
        ("bot/skills/telegram_handler.py", "_handle_message", "_admit_newcomer"),
        ("bot/skills/telegram_handler.py", "_guard", "_admit_for_paper"),
        ("bot/skills/callback_handler.py", "_handle_callback", "_admit_newcomer"),
        ("bot/web/user_gateway.py", "_guard_user", "_admit_web_caller_for_paper"),
    ]:
        assert call in body(path, name), f"{path}::{name} no longer asks {call}"

    stamped = []
    for path in root.joinpath("bot").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "SELF_ADMISSION_BY" not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "authorize"
                    and any(isinstance(k.value, ast.Name) and k.value.id == "SELF_ADMISSION_BY"
                            for k in node.keywords)):
                stamped.append(str(path.relative_to(root)))
    assert stamped == ["bot/skills/telegram_handler.py"], stamped
