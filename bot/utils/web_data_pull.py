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
#: not whatever a caller names: the route whitelists the same three, and a
#: name outside it never reaches the wire.
WEB_CARDS: tuple[str, ...] = ("nft", "spot", "airdrops")

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


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


def fetch_web_card(name: str, telegram_id: str = "") -> dict | None:
    """One of the website chat's own cards (`WEB_CARDS`), as the website
    renders it: ``{"reply_html", "intent"}``.

    ``telegram_id`` is passed only so the airdrops card can add the caller's
    wallet-readiness hints when their Telegram account is linked to a web
    account; the website answers the public radar for anybody it cannot map.
    None = channel unconfigured, name not a card, or the fetch failed — the
    command says which surface could not be read rather than inventing one.
    """
    if not SYNC_SECRET or name not in WEB_CARDS:
        return None
    query = ""
    if telegram_id:
        query = "?telegram_id=" + urllib.parse.quote(str(telegram_id)[:32])
    return _request(f"/api/bot/sync/card/{name}{query}")


def web_card_text(payload: object) -> Optional[str]:
    """The card's HTML as Telegram HTML, or None when the payload holds no card.

    The website's cards join their lines with ``<br>``, which Telegram's HTML
    parser rejects (the whole message fails to send); ``<b>``, ``<i>`` and
    ``<code>`` it renders as the browser does, and the website uses nothing
    else. A payload with no string ``reply_html`` — an error body, a proxy
    page parsed as JSON, an older website — answers None, never an empty card.
    """
    if not isinstance(payload, dict):
        return None
    html = payload.get("reply_html")
    if not isinstance(html, str) or not html.strip():
        return None
    return _BR_RE.sub("\n", html)


def fetch_onchain_flow() -> dict | None:
    """The DEX taker-flow radar (keyless, 24h buy/sell balance per major).

    Feeds the engine's gated on-chain voter — the SAME payload the public
    Markets panel renders. None = channel unconfigured or failed (the voter
    simply contributes nothing; analysis never blocks on this)."""
    if not SYNC_SECRET:
        return None
    return _request("/api/bot/sync/onchain-flow")
