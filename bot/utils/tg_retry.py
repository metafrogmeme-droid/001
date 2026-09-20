"""Retry the transport, never the refusal, on outbound Telegram sends.

Incident, 2026-09-20 ~03:11 UTC: a proactive alert died on
`telegram.error.NetworkError: Bad Gateway` — one transient 502 from
Telegram's edge — and the send path caught it at `logger.debug` and moved
on. The alert was built, addressed and lost, and nothing on any surface
said so. Every send site in the bot had the same shape: try once, fall back
to plain text (which retries the PARSE, not the transport), log, continue.

What this adds is the missing distinction. A Telegram error is one of two
things:

**Transient** — the network, Telegram's edge, a timeout, a flood-limit
`RetryAfter`. The identical request a second later usually succeeds, and
this helper retries it (3 attempts, 1s/2s backoff, or whatever `RetryAfter`
asks for, capped so a minutes-long flood wait cannot park a handler).

**A refusal** — `BadRequest` ("can't parse entities", "message is too
long"), `Forbidden`, `Conflict`, `InvalidToken`. Telegram understood the
request and said no. Retrying buys the identical refusal seconds later and
delays the caller's real fallback (the plain-text resend) for nothing.

The trap the classification walks around: **`BadRequest` SUBCLASSES
`NetworkError` in PTB**, so a naive `isinstance(exc, NetworkError)` retries
every payload Telegram rejected — the same trap `_MSG_SAFE_ERRORS` in
telegram_handler.py documents for the operator-detail path. Hence the exact
`type(exc) is NetworkError` test below.

The last transient failure is RE-RAISED, not swallowed: every caller
already has a fallback or an error log for a dead send, and a helper that
ate the exception would turn those into dead code.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, Optional

try:  # bot/utils stays transport-agnostic — outbound.py imports no PTB either
    from telegram.error import NetworkError, RetryAfter, TimedOut
except ImportError:  # pragma: no cover - PTB is a hard runtime dep of the bot
    NetworkError = RetryAfter = TimedOut = None  # type: ignore[assignment]

#: Never wait longer than this on a single retry, whatever RetryAfter asks.
#: A flood-limit wait of minutes must not park an interactive handler; the
#: attempt fails and the caller's existing fallback/log runs instead.
MAX_RETRY_WAIT = 20.0


def is_transient_tg_error(exc: BaseException) -> bool:
    """True when the SAME request is worth sending again."""
    if exc is None or (NetworkError is None and RetryAfter is None):
        return False
    if isinstance(exc, (RetryAfter, TimedOut)):
        return True
    # Exact type, not isinstance: BadRequest/Forbidden/Conflict/InvalidToken
    # all subclass NetworkError and none of them is transient. The bare
    # NetworkError is what PTB raises for a 502/unknown status — the shape
    # the 03:11 incident was.
    return type(exc) is NetworkError


def _retry_after_seconds(exc: BaseException) -> float:
    """What a RetryAfter asks for, in seconds. 0.0 for anything else.

    PTB has deprecated `retry_after` as a bare number (v22.2+) and will
    return a `datetime.timedelta` in a future major, so both shapes are
    read here rather than the one today's version happens to use.
    """
    raw = getattr(exc, "retry_after", None)
    if raw is None:
        return 0.0
    if isinstance(raw, timedelta):
        return raw.total_seconds()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def send_with_retry(
    make_request: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    logger: Optional[logging.Logger] = None,
    what: str = "send",
) -> Any:
    """Await `make_request()`, retrying transient Telegram transport errors.

    `make_request` is a zero-arg callable returning a FRESH awaitable — a
    coroutine object cannot be awaited twice, and the third attempt must be
    a new request, not a replayed one.

    Delays double per attempt (1s, 2s), except `RetryAfter`, which states
    its own wait. Only the exception's CLASS NAME is ever logged: a
    NetworkError message can carry the bot token via the request URL, which
    is exactly why telegram_handler's `_operator_exc_detail` refuses to
    show one.

    Non-transient errors raise immediately. The final transient failure
    raises too — the caller's fallback and error logging stay live.
    """
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return await make_request()
        except Exception as exc:
            if attempt >= attempts or not is_transient_tg_error(exc):
                raise
            wait = _retry_after_seconds(exc) or delay
            wait = min(wait, MAX_RETRY_WAIT)
            if logger is not None:
                logger.warning(
                    "telegram %s hit %s, retry %d/%d in %.0fs",
                    what, type(exc).__name__, attempt, attempts - 1, wait)
            await asyncio.sleep(wait)
            delay *= 2
    raise RuntimeError("unreachable")  # pragma: no cover
