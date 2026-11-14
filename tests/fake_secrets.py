"""Obvious fake credentials for tests, assembled at runtime.

Parts are public placeholders. They are joined only in memory so the
repository never contains a contiguous Telegram bot token, Google API
key, provider key, JWT, or PEM private-key literal. GitHub secret
scanning flags those shapes even when the value is only a fixture.

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


def join_secret(*parts: str) -> str:
    """Join placeholder parts in memory. No part should be a complete secret."""
    return "".join(parts)


def fake_telegram_bot_token(
    bot_id: str = FAKE_TELEGRAM_BOT_ID,
    secret: str = FAKE_TELEGRAM_BOT_SECRET,
) -> str:
    """Return `<bot_id>:<secret>` without writing that string in source."""
    return join_secret(bot_id, ":", secret)


def fake_telegram_bot_token_aaf_v() -> str:
    """The long-id AAF+v fixture used by the older seam tests."""
    return join_secret(FAKE_TELEGRAM_BOT_ID_LONG, ":", "AAF", "v" * 32)


def fake_openai_key() -> str:
    return join_secret("sk-", "proj-", "TESTFAKE", "OPENAIKEY", "0" * 16)


def fake_anthropic_key() -> str:
    return join_secret("sk-", "ant-", "api03-", "TESTFAKE", "ANTHROPICKEY", "0" * 12)


def fake_xai_key() -> str:
    return join_secret("xai-", "TESTFAKE", "XAITOKENPLACEHOLDER", "0" * 8)


def fake_jwt() -> str:
    header = join_secret("eyJ", "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    payload = join_secret("eyJ", "zdWIiOiIxMjM0NTY3ODkwIn0")
    signature = join_secret("TEST", "SIGNATUREPLACEHOLDER00")
    return join_secret(header, ".", payload, ".", signature)


def fake_pem_private_key() -> str:
    return join_secret(
        "-----BEGIN PRIVATE KEY-----",
        "\n",
        "TESTNOTAREALPRIVATEKEYMATERIAL0000",
        "\n",
        "-----END PRIVATE KEY-----",
    )
