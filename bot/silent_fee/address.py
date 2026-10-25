"""One public address. A key or a seed is refused and not echoed."""

from __future__ import annotations

import re
from typing import Optional

_ADDR = re.compile(r"^0[xX][0-9a-fA-F]{40}$")
_HEX64 = re.compile(r"^(0[xX])?[0-9a-fA-F]{64}$")

NOT_AN_ADDRESS = (
    "That is not a wallet address. Send one address: 0x and 40 hex characters."
)
SECRET_REPLY = "Nothing was stored."
WINDOW_REPLY = "The window is 30 or 90 days."


def looks_like_secret(raw: str) -> bool:
    """A private key or a word-list seed. The text is not echoed."""
    text = (raw or "").strip()
    if _HEX64.fullmatch(text):
        return True
    words = text.split()
    if len(words) >= 12 and all(re.fullmatch(r"[A-Za-z]+", word) for word in words):
        return True
    return False


def parse_address(raw: str) -> Optional[str]:
    text = (raw or "").strip()
    if not _ADDR.fullmatch(text):
        return None
    return text.lower()
