"""Transient Telegram transport errors are retried; refusals are not.

Incident, 2026-09-20 ~03:11 UTC: a proactive alert died on
`telegram.error.NetworkError: Bad Gateway` — one transient 502 from
Telegram's edge — caught at `logger.debug` and dropped. The alert was
built, addressed and lost, and no surface said so.

The classification these tests pin has one non-obvious edge, and it is the
reason the helper exists as code rather than as an isinstance one-liner at
each send site: **BadRequest SUBCLASSES NetworkError in PTB**. A retry
that admits every NetworkError also retries every payload Telegram
refused ("can't parse entities", "message is too long"), burning seconds
to receive the identical refusal and delaying the caller's real fallback.
"""
import asyncio

import pytest
from telegram.error import (BadRequest, Conflict, Forbidden, InvalidToken,
                            NetworkError, RetryAfter, TimedOut)

from bot.utils.tg_retry import MAX_RETRY_WAIT, is_transient_tg_error, send_with_retry


class TestClassification:
    """What counts as 'the same request is worth sending again'."""

    @pytest.mark.parametrize("exc", [
        NetworkError("Bad Gateway"),          # the 03:11 incident, verbatim
        TimedOut("Timed out"),
        RetryAfter(7),
    ])
    def test_the_transport_shapes_are_transient(self, exc):
        assert is_transient_tg_error(exc)

    @pytest.mark.parametrize("exc", [
        BadRequest("Message can't be parsed"),
        BadRequest("Message is too long"),
        Forbidden("bot was blocked by the user"),
        Conflict("terminated by other getUpdates request"),
        InvalidToken("token revoked"),
        ValueError("our own bug, not Telegram's"),
    ])
    def test_refusals_and_bugs_are_not_transient(self, exc):
        # BadRequest/Forbidden/Conflict/InvalidToken ALL subclass
        # NetworkError — an isinstance test on the parent would admit every
        # one of them.
        assert not is_transient_tg_error(exc)


class TestRetry:
    async def test_a_flaky_edge_gets_the_message_through(self):
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise NetworkError("Bad Gateway")
            return "delivered"

        result = await send_with_retry(flaky, base_delay=0)
        assert result == "delivered"
        assert len(calls) == 3

    async def test_a_refusal_raises_immediately_so_the_fallback_runs(self):
        calls = []

        async def refused():
            calls.append(1)
            raise BadRequest("Can't parse entities")

        with pytest.raises(BadRequest):
            await send_with_retry(refused, base_delay=0)
        assert len(calls) == 1  # the caller's plain-text retry is next, not ours

    async def test_a_dead_edge_exhausts_and_re_raises(self):
        # Re-raised, not swallowed: every caller already has a fallback or
        # an error log for a dead send, and a helper that ate the exception
        # would turn those into dead code.
        calls = []

        async def dead():
            calls.append(1)
            raise NetworkError("Bad Gateway")

        with pytest.raises(NetworkError):
            await send_with_retry(dead, attempts=3, base_delay=0)
        assert len(calls) == 3

    async def test_each_attempt_builds_a_new_coroutine(self):
        # A coroutine object cannot be awaited twice — the factory contract
        # is what makes attempt 3 a new request rather than a replay.
        built = []

        def factory():
            async def _req():
                built.append(1)
                if len(built) < 2:
                    raise TimedOut("Timed out")
                return "ok"
            return _req()

        assert await send_with_retry(factory, base_delay=0) == "ok"
        assert len(built) == 2

    async def test_a_flood_wait_is_honoured_but_capped(self, monkeypatch):
        slept = []

        async def fake_sleep(seconds):
            slept.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        async def flooded():
            raise RetryAfter(3600)  # an hour-long flood wait must not park us

        with pytest.raises(RetryAfter):
            await send_with_retry(flooded, attempts=2, base_delay=1.0)
        assert slept == [MAX_RETRY_WAIT]


class TestTheSendPathSaysWhatItDelivered:
    """`_send` writes one audit line per reply — and never breaks the send.

    The audit block runs AFTER the message went out, so an exception there
    would hand the caller a failure about a reply that WAS delivered. It is
    wrapped for that reason, and reads `effective_chat` through getattr:
    partial update stubs are a legitimate shape at this seam (the
    button-dispatch suite builds one), and three of its tests broke the day
    the first draft read the attribute directly.
    """

    @staticmethod
    def _host(reply_text):
        from types import SimpleNamespace
        from bot.skills.telegram_handler import TelegramHandler

        msg = SimpleNamespace(reply_text=reply_text)
        update = SimpleNamespace(callback_query=None, message=msg,
                                 effective_user=SimpleNamespace(id=1))
        host = SimpleNamespace()
        host._send = TelegramHandler._send.__get__(host)
        # staticmethod: binding it would pass `host` as the text.
        host._split_message = TelegramHandler._split_message
        return host, update

    async def test_a_delivered_reply_is_audited_ok(self, monkeypatch):
        sent = []

        async def reply_text(text, **kw):
            sent.append(text)

        host, update = self._host(reply_text)
        rows = []
        import bot.skills.telegram_handler as th
        monkeypatch.setattr(th, "audit",
                            lambda ch, msg, **kw: rows.append((msg, kw)))

        await host._send(update, "<b>done</b>")

        assert sent == ["<b>done</b>"]
        assert len(rows) == 1
        msg, kw = rows[0]
        assert kw["action"] == "tg_send" and kw["result"] == "ok"
        assert kw["data"]["delivered"] == 1 and kw["data"]["failed"] == 0
        # Chars and counts, never the text: system.jsonl is not the transcript.
        assert "done" not in str(kw["data"])

    async def test_a_reply_nobody_got_is_audited_failed(self, monkeypatch):
        async def broken(text, **kw):
            raise BadRequest("Message can't be parsed")  # not transient, no retry

        host, update = self._host(broken)
        rows = []
        import bot.skills.telegram_handler as th
        monkeypatch.setattr(th, "audit",
                            lambda ch, msg, **kw: rows.append((msg, kw)))
        monkeypatch.setattr(th.system_log, "error", lambda *a, **k: None)

        await host._send(update, "x")

        assert len(rows) == 1
        assert rows[0][1]["result"] == "failed"
        assert rows[0][1]["data"]["delivered"] == 0

    async def test_a_partial_update_stub_does_not_break_a_delivered_send(self):
        # No `effective_chat` attribute at all — the shape that broke three
        # button-dispatch tests. The send must still complete quietly.
        sent = []

        async def reply_text(text, **kw):
            sent.append(text)

        host, update = self._host(reply_text)
        assert not hasattr(update, "effective_chat")
        await host._send(update, "card")
        assert sent == ["card"]
