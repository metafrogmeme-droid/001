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
