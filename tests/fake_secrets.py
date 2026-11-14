"""Obvious fake credentials for tests, assembled at runtime.

Parts are public placeholders. They are joined only in memory so the
repository never contains a contiguous Telegram bot token or Google API
key literal. GitHub secret scanning flags those shapes even when the
value is only a fixture.

This does not make a historical leak safe. Values that were once in the
tree stay in git history.
"""

from __future__ import annotations

# Documentary bot ids, not a production bot.
FAKE_TELEGRAM_BOT_ID = "123456789"
FAKE_TELEGRAM_BOT_ID_LONG = "1234567890"

# 35 characters, starts with AA, clearly labelled as a test fixture.
FAKE_TELEGRAM_BOT_SECRET = "AA" + "TESTFAKE" + "TOKENPLACEHOLDER" + ("0" * 9)

# Usual Google API key length (AIza + 35), and long enough for PROVIDER_KEY_RE.
FAKE_GOOGLE_API_KEY = "AIza" + "Sy" + "TESTNOTAREAL" + "GOOGLEAPIKEY" + ("0" * 9)


def fake_telegram_bot_token(
    bot_id: str = FAKE_TELEGRAM_BOT_ID,
    secret: str = FAKE_TELEGRAM_BOT_SECRET,
) -> str:
    """Return `<bot_id>:<secret>` without writing that string in source."""
    return f"{bot_id}:{secret}"


def fake_telegram_bot_token_aaf_v() -> str:
    """The long-id AAF+v fixture used by the older seam tests."""
    return f"{FAKE_TELEGRAM_BOT_ID_LONG}:{'AAF' + 'v' * 32}"
