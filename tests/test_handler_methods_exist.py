"""`/leverage set 10` crashed on a method that had never existed.

Three commands (/leverage, /backup, /mystrategy) were written against
`self._reply(...)` — 21 call sites — and the method was defined nowhere. All
21 were latent AttributeErrors; the first live invocation was high-conviction
step 2 (`/leverage set 10`, update 732737136, 2026-07-30). The global error
handler caught it, so the operator saw "AttributeError · type only" instead
of a working command.

Tests could not have caught this by exercising each command (the broken ones
simply had no tests). The structural check below catches the CLASS of bug:
every `self.<name>(` call in the handler's source must resolve to something
that exists on the class or is assigned somewhere in it. A typo'd or
never-written method fails here at test time instead of at the operator.
"""
from __future__ import annotations

import inspect
import re

from bot.skills.telegram_handler import TelegramHandler


def test_reply_exists_and_delegates_to_the_redacting_send():
    # The named fix. It must route through _send — that is the F-15
    # redaction chokepoint every outgoing message goes through.
    assert hasattr(TelegramHandler, "_reply")
    src = inspect.getsource(TelegramHandler._reply)
    assert "self._send(" in src


def test_every_self_method_call_is_defined():
    src = inspect.getsource(TelegramHandler)
    called = set(re.findall(r"self\.(\w+)\(", src))
    # Names assigned anywhere in the class body (self.x = ..., including
    # callables stored as attributes in __init__) count as defined.
    assigned = set(re.findall(r"self\.(\w+)\s*=", src))
    defined = set(dir(TelegramHandler)) | assigned
    missing = sorted(c for c in called if c not in defined)
    assert missing == [], (
        f"methods called on self but defined nowhere: {missing} — "
        "each of these is a live AttributeError waiting for its first caller "
        "(exactly how /leverage set 10 died)"
    )
