"""Sentences for /quarantine. No transaction and no link."""
from __future__ import annotations

from typing import Any, Mapping

from bot.quarantine.poison import destination_warning


def list_text(book: Mapping[str, Any], *, preview: bool) -> str:
    lines: list[str] = []
    if preview:
        lines.append("Admin preview. Not public.")
    state = book.get("state")
    if state == "unreadable":
        lines.append("The quarantine list could not be read, so there is no list.")
        lines.append("Nothing was hidden and nothing was touched.")
        return "\n".join(lines)
    assets = book.get("assets") or []
    lines.append("Quarantine. Hidden assets stay hidden. This bot does not approve, transfer, or burn them.")
    lines.append("Read-only. Nothing is signed.")
    if not assets:
        lines.append("Nothing is hidden.")
    else:
        lines.append(f"Hidden: {len(assets)}.")
        for row in assets:
            if not isinstance(row, Mapping):
                continue
            tid = row.get("token_id") or ""
            extra = f" #{tid}" if tid else ""
            lines.append(f"chain {row.get('chain_id')} {row.get('contract')}{extra}")
    lines.append("Hide: /quarantine hide <chain_id> <contract> [token_id]")
    lines.append("Warn: /quarantine warn <destination>")
    return "\n".join(lines)


def warn_text(
    destination: object,
    *,
    counterparties: list[str],
    own: list[str],
) -> str:
    got = destination_warning(destination, counterparties=counterparties, own=own)
    if got.get("ok") is not True:
        return "That destination could not be read. Nothing was sent."
    result = got["result"]
    lines = [
        "Address warning. This does not send and does not change the address.",
    ]
    if result.get("level") == "warn" and result.get("lookalike_of"):
        lines.append(
            "This destination looks like a known counterparty "
            f"{result['lookalike_of']} (first 4 and last 4) and is not that address."
        )
    if result.get("lookalike_of_own"):
        lines.append(
            "This destination looks like your own address "
            f"{result['lookalike_of_own']} (first 4 and last 4) and is not that address."
        )
    if result.get("level") != "warn":
        lines.append("No lookalike in the addresses saved on this list.")
    lines.append("Nothing was signed.")
    return "\n".join(lines)
