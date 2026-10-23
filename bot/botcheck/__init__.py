"""Read-only public signals for "is this Telegram bot custodial?".

The catalog is ``catalog.json``. Nothing here holds a key, signs, or stores
a Telegram id. User submissions are appended to a review queue and are not
published.
"""

from bot.botcheck.check import (
    FLAG,
    CatalogError,
    enqueue,
    flag_on,
    load,
    lookup,
    public_document,
    reply_for,
)

__all__ = [
    "FLAG",
    "CatalogError",
    "enqueue",
    "flag_on",
    "load",
    "lookup",
    "public_document",
    "reply_for",
]
