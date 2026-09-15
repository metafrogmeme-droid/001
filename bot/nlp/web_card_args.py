"""The arguments three website cards take, read from the words the way the
intercepts read them.

The website's replay, venue-router and wallet intercepts each carry one
argument inside the sentence — a stake ("replay every signal with $1k"), an
asset ("best venue for BTC"), a chain ("my wallet on base") — captured by the
intercept's own regex and handed to its renderer. On Telegram the same
sentence routes to the same card (`/replay`, `/venue_router`, `/wallet`
fetch the website's rendering over the sync channel), so the argument has to
be read the same way here, or the two surfaces answer one sentence with two
readings. These three readers mirror `app/lib/replay.js`,
`app/lib/venue_router.js` and `app/lib/wallet.js`'s capture groups; the slash
forms (`/replay 500`, `/venue_router BTC`, `/wallet base`) read the same
token through the same helpers, so a typed argument and a spoken one cannot
drift either.

None of them answers a default: an absent stake is None (the route defaults
to the website's $1000), an absent asset is '' (the top five), an absent
chain is '' (every chain). A default written here would be a second copy of
the website's.
"""
from __future__ import annotations

import re
from typing import Optional

_STAKE_TOKEN = re.compile(r"^\$?(\d[\d,]*\.?\d*)\s*(k|m)?$", re.I)
_REPLAY_STAKE = re.compile(
    r"(?:every|all|each)\s+(?:signal|trade|position)s?\b.*?\$?(\d[\d,]*\.?\d*)\s*(k|m)?\b", re.I)
_VENUE_BASE = re.compile(
    r"\b(?:best|cheapest)\s+(?:venue|exchange)"
    r"(?:\s+(?:for|to)\s+(?:be\s+)?(?:long|short)?\s*\$?([a-z0-9]{2,10}))?", re.I)
_WALLET_CHAIN = re.compile(
    r"\b(?:my wallet|wallet (?:balance|portfolio|holdings)|on[- ]chain (?:balance|portfolio|holdings))\b"
    r"(?:\s+on\s+([a-z]+))?", re.I)


def stake_from_token(token: str) -> Optional[float]:
    """"$1k" → 1000.0, "500" → 500.0, "2m" → 2000000.0; anything else None."""
    m = _STAKE_TOKEN.match(str(token or "").strip())
    if not m:
        return None
    try:
        stake = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    if unit == "k":
        stake *= 1e3
    elif unit == "m":
        stake *= 1e6
    return stake if stake > 0 else None


def replay_stake(text: str) -> Optional[float]:
    """The stake a what-if ask names after its object ("… every signal with
    $1k"), or None when it names none."""
    m = _REPLAY_STAKE.search(str(text or ""))
    if not m or not m.group(1):
        return None
    return stake_from_token(m.group(1) + (m.group(2) or ""))


def venue_base(text: str) -> str:
    """The asset a routing ask names ("best venue for BTC" → "BTC", "…to short
    ethusdt" → "ETH"), '' when it names none."""
    m = _VENUE_BASE.search(str(text or ""))
    if not m or not m.group(1):
        return ""
    base = m.group(1).upper()
    return base[:-4] if base.endswith("USDT") and len(base) > 4 else base


def wallet_chain(text: str) -> str:
    """The chain a wallet ask narrows to ("my wallet on base" → "base"), ''
    for every chain. The word is passed as typed: the website decides whether
    it mirrors that chain and says so if not."""
    m = _WALLET_CHAIN.search(str(text or ""))
    if not m or not m.group(1):
        return ""
    return m.group(1).lower()
