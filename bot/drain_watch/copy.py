"""Sentences Claw Watch shows a person.

The product name in this file is Claw Watch. Every alert and the page carry
the same three limits: advisory, fail-open when indexing lags, and unable
to stop a drain by itself.
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional

ADVISORY = (
    "Claw Watch is advisory only. It is fail-open: if indexing lags or this "
    "bot is down, there is no alert. It cannot stop a drain on its own."
)
NEVER_ASK = (
    "This bot never asks for a seed phrase, a private key, or for you to run code."
)
OFFLINE_TX = (
    "The transaction below is unsigned. Your wallet signs it and submits it. "
    "It does not depend on this bot staying online. Nothing here is pre-signed, "
    "and this bot does not relay it."
)
CANCEL_STUB = (
    "A cancel link applies once an on-chain module exists. None is installed, "
    "so there is no cancel transaction."
)
PREVIEW = "Admin preview. Not public."
NOT_FOUND = "Not found."
NOT_AVAILABLE = (
    "Claw Watch is not available in this process, so nothing was stored."
)
NO_CHAT = "I could not tell which chat this is, so nothing was stored."
PAGE_DEFAULT = "https://www.humanoid-traders.com/claw-watch"
_HTTPS = re.compile(r"^https://[^\s]+$")


def page_url(env: Optional[Mapping[str, str]] = None) -> str:
    """Our page. A value that is not https, or that contains whitespace, is ignored."""
    src = env if env is not None else os.environ
    raw = str(src.get("DRAIN_WATCH_PAGE_URL") or "").strip()
    if not _HTTPS.fullmatch(raw):
        return PAGE_DEFAULT
    return raw.split("?", 1)[0]
