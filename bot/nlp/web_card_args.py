"""The arguments three website cards take, read from the words the way
both doors agree to read them.

The replay stake ("replay every signal with $1k"), the wallet chain
("my wallet on base") and the venue asset ("best venue for BTC") used to
be captured by a website intercept. Both doors now read them here and
hand them to `replay_card_text`, `wallet_card_text` and
`venue_router_card_text`. On Telegram the same sentence routes to the
same card (`/replay`, `/venue_router`, `/wallet` fetch the website's
rendering over the sync channel), so the argument has to be read the
same way here, or the two surfaces answer one sentence with two
readings. The readers are the one copy of the capture groups those
intercepts used to hold. The slash forms (`/replay 500`,
`/venue_router BTC`, `/wallet base`) read the same token through the same
helpers, so a typed argument and a spoken one cannot drift either.

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
#: The asset slot after "best venue for/to". The verb and the determiner
#: are read as words of their own, so "to go long btc" is BTC and "for
#: longing eth" is ETH. The slot used to take the first two-to-ten letters
#: after "for" or "to", so the card answered "No cross-venue funding data
#: for GO", for ING (the tail of "longing") and for TRADE.
_VENUE_BASE = re.compile(
    r"\b(?:best|cheapest)\s+(?:venue|exchange)s?\s+(?:for|to)"
    r"(?:\s+(?:go|going|be|get|getting))?"
    r"(?:\s+(?:long(?:ing)?|short(?:ing)?|buy(?:ing)?|sell(?:ing)?|trad(?:e|ing)|hold(?:ing)?))?"
    r"(?:\s+(?:the|my|a|an|some))?"
    r"\s+\$?([a-z0-9]{2,10})\b", re.I)
#: What a venue ask names that is not an asset: the market kind and the cost
#: the card ranks by.
_VENUE_NOT_AN_ASSET = frozenset({
    "perp", "perps", "perpetual", "perpetuals", "futures", "spot", "funding",
    "fees", "fee", "rates", "rate", "crypto", "coins", "coin", "tokens", "token",
    "longs", "shorts", "long", "short", "me", "us", "you",
})
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
    ethusdt" → "ETH", "…to go long btc" → "BTC"), '' when it names none. A
    word that is not an asset ("trade on", "for perps") names none, so the
    card is the top five rather than a lookup of a verb."""
    m = _VENUE_BASE.search(str(text or ""))
    if not m or not m.group(1):
        return ""
    word = m.group(1)
    # Lazy: the router imports nothing from here, and this keeps it that way.
    from bot.nlp.intent_router import is_not_a_ticker
    if is_not_a_ticker(word) or word.lower() in _VENUE_NOT_AN_ASSET:
        return ""
    base = word.upper()
    return base[:-4] if base.endswith("USDT") and len(base) > 4 else base


def wallet_chain(text: str) -> str:
    """The chain a wallet ask narrows to ("my wallet on base" → "base"), ''
    for every chain. The word is passed as typed: the website decides whether
    it mirrors that chain and says so if not."""
    m = _WALLET_CHAIN.search(str(text or ""))
    if not m or not m.group(1):
        return ""
    return m.group(1).lower()
