"""The /rclaw card: the $RCLAW record rendered, with nothing invented.

Three values, not two, for every field a chain can leave empty: a ``null``
authority is *none (revoked)*, which is the fact a holder wants; a ``null``
presale field is *not announced yet* (a ``null`` sale link: *not created
yet*); a value of a shape the record did not promise, or a sale link off
smithii.io, is *unreadable*. None of them is ever a blank cell or ``None``.
"""
from __future__ import annotations

import html
import re
from typing import Any, Mapping, Optional

NOT_ANNOUNCED = "not announced yet"
AUTHORITY_NONE = "none (revoked)"
UNREADABLE = "unreadable"
SALE_LINK_NONE = ("not created yet. It is posted here and on the website's /token page "
                  "before the sale opens; a sale link anywhere else is not ours.")

# The only sale link the card will print: https on smithii.io or a subdomain,
# written plainly. A sale link is what a phishing clone forges first, so
# anything else is unreadable, never a link. The website holds the same rule
# (app/public/js/token-page.js, SALE_URL); tests/fixtures/sale_url_cases.json
# holds both to it.
SALE_URL = re.compile(r"https://(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*smithii\.io(?:/[A-Za-z0-9._~%/?#=&+-]*)?")
SALE_URL_MAX = 300

# The presale terms, in the order a buyer reads them: (record key, label).
# Every key is a key of the record's presale block, and the test holds this
# list to the record both ways, so a term the record announces is printed.
TERMS = (
    ("date", "Date"),
    ("price", "Price"),
    ("venue", "Venue"),
    ("allocation_tokens", "For sale"),
    ("hard_cap", "Hard cap"),
    ("per_wallet", "Per wallet"),
    ("whitelist", "Whitelist"),
    ("claim", "Claim"),
    ("refunds", "Refunds"),
    ("after_sale", "After the sale"),
)


def authority_text(value: Any) -> str:
    """``null`` on an SPL mint means the authority was revoked: say that."""
    if value is None:
        return AUTHORITY_NONE
    if isinstance(value, str) and value.strip():
        return f"held by {html.escape(value)}"
    return UNREADABLE


def supply_text(raw: Any) -> str:
    """``"1000000000"`` -> ``1,000,000,000``; anything else raises."""
    text = str(raw)
    if not text.isdigit():
        raise ValueError("supply_tokens is not a whole number")
    return f"{int(text):,}"


def is_sale_url(value: Any) -> bool:
    """True only for a link the card may print: see ``SALE_URL``."""
    return (isinstance(value, str) and len(value) <= SALE_URL_MAX
            and SALE_URL.fullmatch(value) is not None)


def _term_text(presale: Mapping[str, Any], key: str, symbol: str) -> str:
    """A presale term in three values: absent is unreadable, ``null`` is not announced."""
    if key not in presale:
        return UNREADABLE
    value = presale[key]
    if value is None:
        return NOT_ANNOUNCED
    if key == "allocation_tokens":
        text = str(value)
        # isascii: str.isdigit() also accepts "²", which int() then refuses.
        return f"{int(text):,} {html.escape(symbol)}" if text.isascii() and text.isdigit() else UNREADABLE
    return html.escape(str(value))


def _sale_link_text(presale: Mapping[str, Any]) -> str:
    if "sale_url" not in presale:
        return UNREADABLE
    value = presale["sale_url"]
    if value is None:
        return SALE_LINK_NONE
    if not is_sale_url(value):
        return UNREADABLE
    url = html.escape(value)
    return f'<a href="{url}">{url}</a>'


def presale_lines(presale: Mapping[str, Any], symbol: str = "RCLAW") -> list[str]:
    status = presale.get("status")
    if status == "coming_soon":
        head = "🗓 <b>Presale: coming soon</b>"
    elif status == "announced":
        head = "🗓 <b>Presale: announced</b>"
    elif isinstance(status, str) and status.strip():
        head = f"🗓 <b>Presale: {html.escape(status.replace('_', ' '))}</b>"
    else:
        head = f"🗓 <b>Presale: status {UNREADABLE}</b>"
    lines = [head]
    lines.extend(f"{label}: {_term_text(presale, key, symbol)}" for key, label in TERMS)
    lines.append(f"Sale link: {_sale_link_text(presale)}")
    if status == "coming_soon":
        lines.append("It will be announced here, on the website's /token page and on the "
                     "project's X account before anything opens.")
    elif status == "announced":
        lines.append("Smithii's sale contract enforces the price, the hard cap, the per-wallet "
                     "limits and the claim; the soft cap and everything after the sale are the "
                     "team's commitments, not the contract's. We never DM first and never ask "
                     "for a seed phrase.")
    return lines


def env_mint_line(record_mint: str, env_mint: Optional[str]) -> str:
    """For the operator: what the tier gate's ``RCLAW_MINT`` names, in three values."""
    if not env_mint:
        return "🔧 RCLAW_MINT is unset in this bot's env: the tier gate is inert."
    if env_mint == record_mint:
        return "🔧 RCLAW_MINT matches this mint."
    return ("🔧 RCLAW_MINT in this bot's env is a different address — the tier gate "
            "reads that one, not this mint.")


def render_token_card(record: Mapping[str, Any], *, operator: bool = False,
                      env_mint: Optional[str] = None) -> str:
    mint = html.escape(str(record["mint"]))
    decimals = record["decimals"]
    if not isinstance(decimals, int) or isinstance(decimals, bool):
        raise ValueError("decimals is not an integer")
    lines = [
        f"🪙 <b>${html.escape(str(record['symbol']))} — {html.escape(str(record['name']))}</b>",
        f"Chain: {html.escape(str(record['chain']))} ({html.escape(str(record['cluster']))}) "
        f"· {html.escape(str(record['standard']))}",
        f"Mint: <code>{mint}</code>",
        f"Supply: {supply_text(record['supply_tokens'])} ({decimals} decimals) · "
        f"fixed: mint authority {authority_text(record['mint_authority'])}",
        f"Freeze authority: {authority_text(record['freeze_authority'])}",
    ]
    created = record.get("created_at")
    if isinstance(created, str) and len(created) >= 10:
        lines.append(f"Created: {html.escape(created[:10])} (UTC)")
    elif created is not None:
        lines.append(f"Created: {UNREADABLE}")
    lines.append(f'🔗 <a href="{html.escape(str(record["explorer"]))}">View on Solscan</a>')
    lines.append("")
    lines.extend(presale_lines(record["presale"], str(record["symbol"])))
    lines.append("")
    lines.append(f"⚠️ Only this mint address is ${html.escape(str(record['symbol']))}. "
                 "Any other address using the name is not ours.")
    if operator:
        lines.append("")
        lines.append(env_mint_line(str(record["mint"]), env_mint))
    return "\n".join(lines)
