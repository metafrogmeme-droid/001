"""Linking a website account to a Telegram chat, and unlinking it again.

Two defects, both the same shape as the registration ones — a message
asserting a state the system is not in:

  * `/link` finished with "You now have full access to RUNECLAW. Try: /scan
    /portfolio /fullscan". Linking joins a WEBSITE account to this chat and
    grants no bot commands whatsoever; on any deployment with an allowlist
    configured — every live one, since is_live() requires TELEGRAM_CHAT_ID —
    all three named commands were refused on the next tap. The most important
    message in the flow promised exactly the thing that failed next.

  * `/unlink` deleted the bot's local `user_telegram` row and said "Unlinked
    from {email}. Your data is preserved." The website kept
    `telegram_linked = TRUE` and the stored `telegram_id` — the pair
    routes/credentials.js and routes/controls.js both gate on — so a user who
    believed they had disconnected could still have exchange-key submissions
    and live-trading controls pointed at that chat. One side of a two-sided
    link, reported as the whole thing.

Plus the crash: a 200 from the website with an unexpected body raised KeyError
past every handler into the generic "Something went wrong", which reads as a
bad token and sends the user round the token loop again.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.utils.i18n import t


class _Msg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


def _update(chat_id=4242, username="ann"):
    upd = MagicMock()
    upd.effective_chat = MagicMock()
    upd.effective_chat.id = chat_id
    upd.effective_chat.type = "private"
    upd.effective_user = MagicMock()
    upd.effective_user.username = username
    upd.message = _Msg()
    ctx = MagicMock()
    ctx.args = []
    return upd, ctx


class TestLinkNeverPromisesBotAccess:
    def test_the_success_copy_does_not_claim_the_commands_work(self):
        card = t("link_success", "en", email="a@b.c", plan="free",
                 url="https://example.test")
        assert "full access" not in card.lower(), (
            "linking grants a website account, not bot commands; the allowlist "
            "refused /scan on the very next tap")
        for cmd in ("/fullscan", "/portfolio"):
            assert cmd not in card, (
                f"{cmd} is @guard-ed by the allowlist; naming it here is a "
                "promise the next message breaks")

    def test_it_says_where_bot_access_actually_comes_from(self):
        card = t("link_success", "en", email="a@b.c", plan="free",
                 url="https://example.test").lower()
        assert "allowlist" in card or "approved" in card, (
            "a user refused by /scan right after a successful link needs to "
            "know the two are unrelated, or they retry the link forever")

    def test_both_languages_are_complete(self):
        en = t("link_success", "en", email="a@b.c", plan="free", url="u")
        zh = t("link_success", "zh", email="a@b.c", plan="free", url="u")
        assert zh != en and "{" not in zh and "{" not in en


class TestUnlinkReportsBothSides:
    """The reply must distinguish 'the website confirmed', 'the website
    refused' and 'the website could not be reached'. They are three different
    situations with three different next steps, and all three used to print
    the one sentence that only the first of them earns."""

    def _run_unlink(self, website_result):
        import asyncio

        from bot.skills import user_middleware as um
        upd, ctx = _update()
        user = types.SimpleNamespace(id=7, email="a@b.c", is_active=True,
                                     telegram_chat_id="4242")
        unlinked = []
        with patch.object(um, "get_user_by_chat_id", return_value=user), \
             patch.object(um, "unlink_telegram", side_effect=unlinked.append), \
             patch.object(um, "_user_lang", return_value="en"):
            fake = types.ModuleType("bot.utils.website_sync")
            fake.unlink_telegram_on_website = lambda *_a, **_k: website_result
            with patch.dict(sys.modules, {"bot.utils.website_sync": fake}):
                asyncio.run(um.cmd_unlink(upd, ctx))
        return upd.message.replies[-1], unlinked

    def test_confirmed_reads_as_done(self):
        reply, unlinked = self._run_unlink(True)
        assert unlinked == [7]
        assert "could not reach" not in reply.lower()
        assert "declined" not in reply.lower()

    def test_unreachable_is_not_reported_as_done(self):
        reply, unlinked = self._run_unlink(None)
        assert unlinked == [7], (
            "the local half must still clear — leaving both sides linked "
            "because one was unreachable makes /unlink do nothing at all")
        low = reply.lower()
        assert "could not reach" in low
        assert "still show" in low, "say what may still be true over there"

    def test_a_refusal_is_not_reported_as_done_either(self):
        reply, _ = self._run_unlink(False)
        assert "declined" in reply.lower()
        assert "account settings" in reply.lower(), "name the manual next step"

    def test_the_three_outcomes_are_three_different_messages(self):
        confirmed, _ = self._run_unlink(True)
        refused, _ = self._run_unlink(False)
        unknown, _ = self._run_unlink(None)
        assert len({confirmed, refused, unknown}) == 3

    def test_a_website_exception_degrades_to_unknown_not_to_success(self):
        import asyncio

        from bot.skills import user_middleware as um
        upd, ctx = _update()
        user = types.SimpleNamespace(id=7, email="a@b.c", is_active=True,
                                     telegram_chat_id="4242")

        def _boom(*_a, **_k):
            raise RuntimeError("network down")
        with patch.object(um, "get_user_by_chat_id", return_value=user), \
             patch.object(um, "unlink_telegram"), \
             patch.object(um, "_user_lang", return_value="en"):
            fake = types.ModuleType("bot.utils.website_sync")
            fake.unlink_telegram_on_website = _boom
            with patch.dict(sys.modules, {"bot.utils.website_sync": fake}):
                asyncio.run(um.cmd_unlink(upd, ctx))
        assert "could not reach" in upd.message.replies[-1].lower()


class TestTheWebsiteCallIsTriState:
    """`unlink_telegram_on_website` must never collapse 'unreachable' to a
    boolean. Absent is never a measurement — and here the measurement is
    whether someone else can still act on this chat."""

    def _call(self, post_result):
        from bot.utils import website_sync
        with patch.object(website_sync, "_post", return_value=post_result):
            return website_sync.unlink_telegram_on_website(7, "4242")

    def test_unreachable_is_none(self):
        assert self._call(None) is None

    def test_confirmed_is_true(self):
        assert self._call({"ok": True, "unlinked": True}) is True

    def test_a_refusal_is_false_not_none(self):
        assert self._call({"error": "chat_id does not match this account"}) is False

    def test_a_partial_body_is_not_read_as_success(self):
        assert self._call({"ok": True}) is False


class TestValidateTokenBodies:
    """A 200 with the wrong shape used to raise KeyError into the generic
    "Something went wrong", which reads as a bad token — so the user generated
    another one, against a website that was answering fine."""

    def _run_link(self, body, status=200):
        import asyncio
        import io
        import json
        import urllib.error

        from bot.skills import user_middleware as um
        upd, ctx = _update()
        ctx.args = ["tok123"]

        class _Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _open(_req, timeout=0):
            if status != 200:
                raise urllib.error.HTTPError("u", status, "err", {}, None)
            return _Resp(json.dumps(body).encode())

        with patch.object(um, "get_user_by_chat_id", return_value=None), \
             patch.object(um, "_user_lang", return_value="en"), \
             patch.object(um.urllib.request, "urlopen", _open), \
             patch.object(um, "_ensure_local_user"), \
             patch.object(um, "link_telegram", return_value=True), \
             patch.object(um, "get_user_portfolio", return_value={"equity": 800}):
            asyncio.run(um.cmd_link(upd, ctx))
        return upd.message.replies[-1]

    def test_a_body_without_user_id_does_not_crash(self):
        reply = self._run_link({"email": "a@b.c"})
        assert "Could not validate" in reply, reply

    def test_a_non_numeric_user_id_does_not_crash(self):
        reply = self._run_link({"user_id": "not-a-number", "email": "a@b.c"})
        assert "Could not validate" in reply

    def test_a_good_body_still_links(self):
        reply = self._run_link({"user_id": 7, "email": "a@b.c", "plan": "pro"})
        assert "a@b.c" in reply and "Linked" in reply

    def test_an_expired_token_says_so(self):
        reply = self._run_link({}, status=404)
        assert "expired" in reply.lower()


@pytest.mark.parametrize("needle,why", [
    ("telegram_linked", "the flag every consumer gates on must be cleared"),
    ("telegram_id", "and the OAuth identity column must be discussed"),
])
def test_the_website_endpoint_exists_and_is_bot_authed(needle, why):
    """Source scan, narrowly: the Node half has its own suite, but nothing
    else pins that the route the bot now depends on is BELOW `router.use(botAuth)`
    — an unauthenticated unlink endpoint would let anyone disconnect any
    account they could guess the id of."""
    import pathlib
    src = pathlib.Path("app/routes/sync.js").read_text(encoding="utf-8")
    assert "'/telegram-unlink'" in src, "the bot calls this route"
    assert src.index("router.use(botAuth)") < src.index("'/telegram-unlink'"), (
        "the unlink route must sit behind the shared-secret gate")
    route = src[src.index("'/telegram-unlink'"):]
    route = route[:route.index("\n});") + 4]
    assert needle in route, why


def test_the_unlink_route_does_not_null_the_oauth_identity():
    """telegram_id doubles as the Telegram OAuth login identity (auth.js
    _PROVIDER_ID_COLUMN). Nulling it on unlink would lock a social-login user
    out of their own account, and clearing telegram_linked is already
    sufficient — every consumer requires both."""
    import pathlib
    src = pathlib.Path("app/routes/sync.js").read_text(encoding="utf-8")
    route = src[src.index("'/telegram-unlink'"):]
    route = route[:route.index("\n});") + 4]
    assert "telegram_id = NULL" not in route
    assert "telegram_id=NULL" not in route
