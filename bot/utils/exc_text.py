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

from bot.utils.secret_shapes import TELEGRAM_BOT_TOKEN_RE, URL_QUERY_RE, scrub_diagnostic

# Re-exported under the names their importers use. Both patterns live in
# `bot.utils.secret_shapes` now, the one vocabulary every scrub reads; the
# bot-token shape was born here (PTB puts the request URL,
# https://api.telegram.org/bot<TOKEN>/sendMessage, into some error messages,
# and a key=value redactor never sees a token that carries no `=`).
_TG_TOKEN_RE = TELEGRAM_BOT_TOKEN_RE
_URL_QUERY_RE = URL_QUERY_RE


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

    Order matters. The whole vocabulary first (token-shaped patterns before
    the key=value families, in the table's own order), then every URL's
    query string dropped -- a diagnostic never carries a link a user needs --
    then escaping. Escaping first would break the patterns the redactors
    match.
    """
    try:
        msg = str(exc)
    except Exception:
        return ""
    if not msg:
        return ""
    msg = scrub_diagnostic(msg)
    return html.escape(msg[:limit])
