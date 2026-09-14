"""
Pull Node-side intelligence surfaces for Telegram parity (/exposure /research
/rwa, and the three website chat cards /nft /spot /airdrops).

Cross-venue exposure netting, research dossiers, and the RWA radar are
implemented in the web app (app/lib/exposure.js, research.js, rwa.js) — one
brain, one implementation. These fetch the SAME payloads the web panels render
over the shared-secret sync channel, so the Telegram commands never fork the
logic. All read-only; ``None`` = channel unconfigured or failed (commands say
"link the web app" instead of failing loudly).

`fetch_web_card` goes one step further than a payload: it fetches the CARD
the website's chat intercept renders (`GET /api/bot/sync/card/<name>`), so the
Telegram command carries no formatter of its own — a second formatter is a
second answer about one reading, and the two drifted the moment one was edited.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

from bot.utils.credential_pull import _request, SYNC_SECRET  # reuse the channel

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}$")

#: The website chat cards a Telegram command renders verbatim. A fixed tuple,
#: not whatever a caller names: the route whitelists the same nine, and a
#: name outside it never reaches the wire. The last two are the caller's own
#: wallet, so the route reads `telegram_id` for them and answers `unlinked`
#: for a caller it cannot map to a web account.
WEB_CARDS: tuple[str, ...] = ("nft", "spot", "airdrops", "replay", "letter",
                              "venue_router", "meme_radar", "wallet", "defi")

#: The one argument each of three cards takes — the intercept's own capture
#: group, as a query parameter (`bot/nlp/web_card_args.py` reads it from the
#: words). A card not listed takes none; a name not listed for a card raises
#: at the call, because a seam handing a card an argument it does not take is
#: a programming error and not a value to drop quietly.
WEB_CARD_PARAMS: dict[str, tuple[str, ...]] = {
    "replay": ("stake",), "venue_router": ("base",), "wallet": ("chain",),
}

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
#: Every tag Telegram's HTML parser renders that a website card uses. Any
#: other tag is dropped and its text kept: the website's `<span class="muted">`
#: would otherwise refuse the WHOLE message at Telegram, and the send
#: chokepoint's fallback then strips every tag — the card arriving without
#: its bold is a worse outcome than one muted line arriving plain.
_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)[^<>]*>")
_TELEGRAM_TAGS = frozenset({"b", "i", "code"})


def fetch_exposure(telegram_id: str) -> dict | None:
    """The caller's own cross-venue exposure (perp vs on-chain netting)."""
    if not SYNC_SECRET or not telegram_id:
        return None
    tg = urllib.parse.quote(str(telegram_id)[:32])
    return _request(f"/api/bot/sync/exposure?telegram_id={tg}")


def fetch_research(symbol: str) -> dict | None:
    """A research dossier for a base symbol (venue data + recorded history)."""
    if not SYNC_SECRET:
        return None
    base = str(symbol or "").upper().strip()
    base = re.sub(r"[^A-Z0-9]", "", base).removesuffix("USDT")[:10]
    if not _SYMBOL_RE.match(base):
        return None
    return _request(f"/api/bot/sync/research/{base}")


def fetch_rwa() -> dict | None:
    """The tokenized-RWA sector radar (live venue tickers, read-only)."""
    if not SYNC_SECRET:
        return None
    return _request("/api/bot/sync/rwa")


def fetch_web_card(name: str, telegram_id: str = "", **params: object) -> dict | None:
    """One of the website chat's own cards (`WEB_CARDS`), as the website
    renders it: ``{"reply_html", "intent"}`` — or ``{"reply_html": None,
    "unlinked": True}`` for a per-person card whose caller the website could
    not map to a web account.

    ``telegram_id`` is passed for the cards with a per-person half: the
    airdrops card adds the caller's wallet-readiness hints when their
    Telegram account is linked to a web account and answers the public radar
    otherwise; the wallet and DeFi cards ARE the caller's wallet and answer
    `unlinked` instead. ``params`` are the card's own arguments
    (`WEB_CARD_PARAMS`), sent when given and never defaulted here.
    None = channel unconfigured, name not a card, or the fetch failed — the
    command says which surface could not be read rather than inventing one.
    """
    if not SYNC_SECRET or name not in WEB_CARDS:
        return None
    allowed = WEB_CARD_PARAMS.get(name, ())
    unknown = set(params) - set(allowed)
    if unknown:
        raise TypeError(f"the {name} card takes no {sorted(unknown)} argument")
    parts = []
    if telegram_id:
        parts.append("telegram_id=" + urllib.parse.quote(str(telegram_id)[:32]))
    for key in allowed:
        value = params.get(key)
        if value is None or not str(value).strip():
            continue
        parts.append(f"{key}=" + urllib.parse.quote(str(value).strip()[:32]))
    query = ("?" + "&".join(parts)) if parts else ""
    return _request(f"/api/bot/sync/card/{name}{query}")


def web_card_unlinked(payload: object) -> bool:
    """True when the website answered that this caller maps to no web
    account — a fact of its own, kept apart from a card and from a channel
    that did not answer, because the three get three different sentences."""
    return isinstance(payload, dict) and payload.get("unlinked") is True


def web_card_text(payload: object) -> Optional[str]:
    """The card's HTML as Telegram HTML, or None when the payload holds no card.

    The website's cards join their lines with ``<br>``, which Telegram's HTML
    parser rejects (the whole message fails to send); ``<b>``, ``<i>`` and
    ``<code>`` it renders as the browser does. Any other tag is dropped and
    its text kept (`_TELEGRAM_TAGS`), so a card that grows a ``<span>`` on
    the website arrives here without the span rather than not at all. A
    payload with no string ``reply_html`` — an error body, a proxy page parsed
    as JSON, an older website, the `unlinked` answer — answers None, never an
    empty card.
    """
    if not isinstance(payload, dict):
        return None
    html = payload.get("reply_html")
    if not isinstance(html, str) or not html.strip():
        return None
    text = _BR_RE.sub("\n", html)
    return _TAG_RE.sub(lambda m: m.group(0) if m.group(1).lower() in _TELEGRAM_TAGS else "", text)


def fetch_onchain_flow() -> dict | None:
    """The DEX taker-flow radar (keyless, 24h buy/sell balance per major).

    Feeds the engine's gated on-chain voter — the SAME payload the public
    Markets panel renders. None = channel unconfigured or failed (the voter
    simply contributes nothing; analysis never blocks on this)."""
    if not SYNC_SECRET:
        return None
    return _request("/api/bot/sync/onchain-flow")
