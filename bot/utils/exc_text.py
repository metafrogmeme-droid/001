"""An exception's message, scrubbed and escaped, for a user's screen.

Pulled out of `bot/skills/telegram_handler.py` in the second slice of the
handler split, because the Guardian command group needs it and a mixin that
imports the handler to get a thirty-line helper would be the cycle the split
exists to remove. Every call site still spells it `_safe_exc_text`, and the
handler re-exports it under that name; `tests/test_f15_user_facing_exceptions.py`
and `tests/test_exception_leak_guard.py` say what it must scrub.
"""
from __future__ import annotations

import html
import re

from bot.utils.logger import _redact_string

# A Telegram bot token has the shape <digits>:<base64ish>, and PTB puts the
# request URL — https://api.telegram.org/bot<TOKEN>/sendMessage — into some
# error messages. The logger's inline redactor only catches `key=value`, so it
# would not touch that. Strip the token shape explicitly before any exception
# message can reach a chat.
#
# No leading \b: the token appears in the URL as `/bot123456789:AA…`, and
# there is no word boundary between `bot` and the digits, so a \b-anchored
# pattern matched nothing and passed the whole token through. The optional
# `bot` prefix is consumed so the replacement swallows it too.
_TG_TOKEN_RE = re.compile(r"(?:bot)?\d{6,12}:[A-Za-z0-9_-]{20,}")

#: A URL with a query string. Credentials ride there as often as in a
#: key=value pair, and once they are a single token no key=value regex
#: sees them. The host answers "which service"; the query never has to.
_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]*")


def _safe_exc_text(exc: BaseException, *, limit: int = 200) -> str:
    """An exception's message with secrets scrubbed, HTML-escaped, for a user.

    F-15 says no secret or internal config ever reaches user-facing text.
    Ten command handlers were sending `html.escape(str(exc))` straight into a
    reply -- escaping stops MARKUP injection and does nothing whatever about
    a credential. A ccxt error carries the request URL, an LLM provider error
    can echo the Authorization header, and a KeyError on config names the key
    it could not find.

    This is deliberately NOT `_operator_exc_detail`. That one is an ALLOWLIST
    built for Telegram's own errors, where the token rides in the URL and the
    only safe answer for an unknown class is silence. Here the class is
    almost never on that list, so the allowlist would return "" and the
    operator would learn nothing about why their scan failed. Different
    question, different tool: show the message, but scrub it first.

    Order matters. The bot-token shape goes first because the shared
    key=value redactor does not know it, then the shared chokepoint, then
    escaping -- escaping first would break the patterns the redactors match.
    """
    try:
        msg = str(exc)
    except Exception:
        return ""
    if not msg:
        return ""
    msg = _TG_TOKEN_RE.sub("***REDACTED***", msg)
    msg = _redact_string(msg)
    # A bare URL can carry credentials in its query string, and no key=value
    # pattern catches "?apiKey=..." once it is one token. Keep the host, drop
    # the rest -- "which venue" is the diagnostic; the path rarely is.
    msg = _URL_QUERY_RE.sub(r"\1?***", msg)
    msg = " ".join(msg.split())
    return html.escape(msg[:limit])

