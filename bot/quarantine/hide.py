"""Hide a token or NFT, and refuse to touch it.

Hiding omits the row from a balance view. It does not delete the balance
and it does not build a transaction. Approve, transfer, and burn of a
hidden asset are refused. EVM disposal is refused for every asset: this
build does not offer it.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from bot.quarantine.book import contract_key
from bot.quarantine.flag import flag_on

_TOUCH = frozenset({
    "approve", "transfer", "transferfrom", "burn",
    "setapprovalforall", "permit",
})
_EVM_DISPOSAL = frozenset({"burn", "destroy"})
_SOLANA_LATER = frozenset({"burn", "close", "closeaccount"})


def _hidden_keys(book: Mapping[str, Any]) -> set[str]:
    raw = book.get("contracts")
    if isinstance(raw, set):
        return raw
    return set()


def is_hidden(asset: Mapping[str, Any], book: Mapping[str, Any]) -> bool:
    """True when this asset is on the hide list, or the caller marked it unwanted.

    ``unwanted is True`` is a mark the caller set. Any other value, including
    a missing field, is not a mark.
    """
    if asset.get("unwanted") is True:
        return True
    key = contract_key(asset.get("chain_id"), asset.get("contract"), asset.get("token_id") or "")
    if key is None:
        return False
    keys = _hidden_keys(book)
    if key in keys:
        return True
    # A whole-contract hide covers every token id under it.
    head = key.split(":", 2)
    if len(head) >= 2 and f"{head[0]}:{head[1]}" in keys:
        return True
    return False


def visible_assets(
    rows: object,
    book: Mapping[str, Any],
) -> dict[str, Any]:
    """Split a balance list into what may be shown and what is hidden.

    An unreadable hide list hides nothing and does not pretend the view
    was filtered: ``visible`` is None. A row that is not a mapping is
    left out and counted as unread, not as hidden and not as zero.
    """
    if book.get("state") == "unreadable":
        return {"state": "unreadable", "visible": None, "hidden": None, "unread_rows": None}
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {"state": "unreadable", "visible": None, "hidden": None, "unread_rows": None}
    visible: list[Any] = []
    hidden = 0
    unread = 0
    for row in rows:
        if not isinstance(row, Mapping):
            unread += 1
            continue
        if is_hidden(row, book):
            hidden += 1
            continue
        visible.append(row)
    return {"state": "read", "visible": visible, "hidden": hidden, "unread_rows": unread}


def touch_decision(
    action: object,
    asset: Mapping[str, Any],
    book: Mapping[str, Any],
) -> dict[str, Any]:
    """Whether the bot may call this contract. Never returns a transaction.

    EVM burn and every other disposal stay off. Solana burn and close are
    not in this build. A hidden asset is not approved, transferred, or burned.
    """
    act = str(action or "").strip().lower()
    chain = str(asset.get("chain") or "").strip().lower()
    refused = {"refused": True, "reason": "unknown_action", "tx": None}
    if chain == "solana" and act in _SOLANA_LATER:
        return {"refused": True, "reason": "not_in_this_build", "tx": None}
    if act in _EVM_DISPOSAL:
        return {"refused": True, "reason": "evm_disposal_off", "tx": None}
    if act not in _TOUCH:
        return refused
    if book.get("state") == "unreadable":
        return {"refused": True, "reason": "list_unreadable", "tx": None}
    if is_hidden(asset, book):
        return {"refused": True, "reason": "quarantined", "tx": None}
    return {"refused": False, "reason": None, "tx": None}


def _token_hidden(item: Mapping[str, Any], hidden: set[str]) -> bool:
    key = contract_key(item.get("chain_id"), item.get("token") or "", "")
    if key is None:
        return False
    return key in hidden


def apply_withhold(
    result: Mapping[str, Any],
    *,
    hidden: Optional[set[str]] = None,
    list_state: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Drop panic-button rows the bot must not call.

    With the flag off the result is returned unchanged. An unreadable list
    does not drop rows: the reply says the list could not be read.
    """
    if not flag_on(env):
        return dict(result)
    out = dict(result)
    if list_state == "unreadable":
        out["quarantine_list"] = "unreadable"
        return out
    if not hidden:
        return out
    items = out.get("items")
    if not isinstance(items, list):
        return out
    kept = []
    dropped = 0
    for item in items:
        if isinstance(item, Mapping) and _token_hidden(item, hidden):
            dropped += 1
            continue
        kept.append(item)
    if dropped:
        out["items"] = kept
        out["quarantined_untouched"] = dropped
    return out
