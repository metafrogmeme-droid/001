"""The one place that answers "what is the website's URL?" — bot side.

Eight modules each carried their own copy of

    os.getenv("WEBSITE_URL", "https://<deployment-host>")

which is the same defect `app/lib/public_origin.js` was written to fix on the
web side, one process over. Eight copies means eight places to edit when the
address changes and eight chances to miss one — and the addresses here are
consumed OUTSIDE this process (links a user taps in Telegram, the sync POSTs
that carry the bot's data to the site), so a stale one fails somewhere nobody
is looking.

RUNECLAW now has its own domain (www.humanoid-traders.com, live and verified
2026-07-30) alongside the hosting-provider URL it was born on. Both work. The
point of this module is that switching between them is ONE edit, not eight —
and that `grep` can prove no copy survived.

Resolution order mirrors the web side deliberately, so an operator who sets
one variable does not get a bot and a website that disagree about their own
address:

    WEBSITE_URL → PUBLIC_ORIGIN → APP_BASE_URL → DEFAULT_SITE_URL

The default is the deployment host rather than the new domain ON PURPOSE:
WEBSITE_URL also routes the bot's SYNC POSTs, and a default flip would move
that traffic to a different edge without anyone choosing to. The operator
sets WEBSITE_URL when they are ready to verify sync on the new host; until
then behaviour is byte-identical to the eight copies this replaces.
"""
from __future__ import annotations

import os

#: The address the bot falls back to when nothing is configured. One literal,
#: in one module, so changing it is a single edit with a single test.
DEFAULT_SITE_URL = "https://pmvc58g2.mule.page"

#: The product's own domain. Not the default yet — see the module docstring.
#: Kept here so the operator does not have to hunt for the exact spelling.
PRODUCT_SITE_URL = "https://www.humanoid-traders.com"


def site_url() -> str:
    """The website's base URL, without a trailing slash.

    Never returns empty: a link builder that receives "" produces a
    relative-looking URL that fails silently in a Telegram message.
    """
    for key in ("WEBSITE_URL", "PUBLIC_ORIGIN", "APP_BASE_URL"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val.rstrip("/")
    return DEFAULT_SITE_URL.rstrip("/")
