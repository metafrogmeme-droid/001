"""Every string this product sends a person, scrubbed. One chokepoint.

`_send`'s own comment has called itself "the single chokepoint for all
outbound text" since the F-15 audit, and on the day it was written it was.
Two things happened since.

**The default path stopped going through it.** `chat_streaming_enabled`
defaults to True, so the model's answer is delivered by
`TelegramStream.finish()` -> `message.edit_text(...)`, and every provisional
delta by `_maybe_edit()`. Neither touches `_send`; the `return` above it
sees to that. Only skill-result sends and the non-streaming fallback kept the
scrub, so on a stock deploy the one reply most likely to echo something back
from the user's own message was the one reply nobody scrubbed.

**And the other transport never had one at all.** The web's `_chat_turn`
puts the skill result or the model answer straight into `reply_html`,
aiohttp serialises it, `gateway.js::relay` passes the JSON through
byte-for-byte, and the browser's `sanitizeBotHtml` is a MARKUP allowlist that
does nothing whatever to a credential. It relies on every producer scrubbing
itself, with no backstop. Telegram has both.

WHAT THIS IS NOT. No live credential leak is reachable through the web today
— every web-reachable path carrying driver text already scrubs at its own
site (`live_balance.scrub_reason`, `trade_gate._live_auth_detail`,
`exchange_credentials.rejection_reason`, `chat_tools.run_tool`'s
`RuntimeError("tool failed") from None`). This is defence in depth, and the
argument for it is that "every producer remembers" is a property no test can
check and no reviewer can maintain. A chokepoint new code inherits is one
that cannot be forgotten — the same reason `_fmt_price(None)` returns an em
dash instead of a dozen call sites each checking.

IT KNOWS ONE THING MORE THAN `_send` DID, and that gap is the reason this is
a named function rather than an import. `_redact_string` matches `key=value`
shapes; it does not know the Telegram BOT-TOKEN shape, which carries no `=`
at all. `bot/utils/exc_text._safe_exc_text` has scrubbed that since it was
written, and says in its own docstring that "the shared key=value redactor
does not know it". So the exception path knew about the worst single secret
in the process and the general outbound path did not — two redactors side by
side, one of them a copy that knew less.
"""
from __future__ import annotations

from bot.utils.exc_text import _TG_TOKEN_RE
from bot.utils.logger import _redact_string


def reply_safe(text: str) -> str:
    """``text`` with inline secrets scrubbed. Never raises, never blocks.

    Order matches `_safe_exc_text`'s and for its stated reason: the bot-token
    shape goes first because the shared key=value redactor does not know it.

    It does NOT escape. `_safe_exc_text` escapes because it is handing an
    exception's message into HTML; this runs over text that is ALREADY the
    card — full of `<b>` the surface is supposed to render — and escaping
    here would print the markup instead of applying it.

    A fault returns the text unchanged, deliberately: a scrub that can break
    a chat is worse than the defence in depth it buys, which is the same
    call `_send` made and `firewall.defang_if_flagged` makes one layer up.
    """
    if not text:
        return text
    try:
        return _redact_string(_TG_TOKEN_RE.sub("***REDACTED***", text))
    except Exception:
        return text
