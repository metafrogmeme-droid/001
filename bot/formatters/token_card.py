"""The /token card: the $RCLAW record rendered, with nothing invented.

Three values, not two, for every field a chain can leave empty: a ``null``
authority is *none (revoked)*, which is the fact a holder wants; a ``null``
presale field is *not announced yet*; a value of a shape the record did not
promise is *unreadable*. None of them is ever a blank cell or ``None``.
"""
from __future__ import annotations

import html
from typing import Any, Mapping, Optional

NOT_ANNOUNCED = "not announced yet"
AUTHORITY_NONE = "none (revoked)"
UNREADABLE = "unreadable"


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


def presale_lines(presale: Mapping[str, Any]) -> list[str]:
    status = presale.get("status")
    if status == "coming_soon":
        head = "🗓 <b>Presale: coming soon</b>"
    elif isinstance(status, str) and status.strip():
        head = f"🗓 <b>Presale: {html.escape(status.replace('_', ' '))}</b>"
    else:
        head = f"🗓 <b>Presale: status {UNREADABLE}</b>"
    cells = []
    for key, label in (("date", "Date"), ("price", "Price"), ("venue", "Venue")):
        value = presale.get(key)
        cells.append(f"{label}: {NOT_ANNOUNCED if value is None else html.escape(str(value))}")
    return [head, " · ".join(cells)]


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
    lines.extend(presale_lines(record["presale"]))
    lines.append("It will be announced here, on the website's /token page and on the "
                 "project's X account before anything opens.")
    lines.append("")
    lines.append(f"⚠️ Only this mint address is ${html.escape(str(record['symbol']))}. "
                 "Any other address using the name is not ours.")
    if operator:
        lines.append("")
        lines.append(env_mint_line(str(record["mint"]), env_mint))
    return "\n".join(lines)
