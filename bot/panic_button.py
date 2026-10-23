"""Panic Button. Read-only, and off unless PANIC_BUTTON is ``1`` or ``true``.

One address in, one reply out. The address is not written to the digest
watch list and is not kept after the reply. Revokes are described on the
web page; this module never signs, never broadcasts, and holds no key.

The open approvals come from the approval reader (the same one the weekly
digest uses). Unlimited and "older than 180 days" use that digest's rules.
The public exploit list is Revoke.cash's. EIP-7702 is the account's code,
read by the same reader. BNB Chain is not in that reader's chain list, so
it is not added here.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from bot.approval_hygiene import (
    CHAINS,
    EXPLOIT_UNREAD,
    INVALID_ADDRESS,
    MESSAGE_MAX,
    WEEK_SECONDS,
    ChainRead,
    OpenApproval,
    looks_like_secret,
    normalize_address,
    short_address,
    stale_of,
    unlimited_of,
    unread_chains,
)
from bot.approval_hygiene import (
    SECRET_REQUEST as _SECRET_WORD_CHECK,
)
from bot.approval_reader import ApprovalReader, ExploitDirectory

FLAG = "PANIC_BUTTON"
NOT_AVAILABLE = "not available yet"
SEED_WARNING = (
    "If you entered your seed phrase anywhere, revoking is not enough. "
    "Move your funds to a new wallet."
)
NEVER_ASK = "We never ask for your seed phrase or private key."
CANNOT_RECOVER = "We can't recover lost funds."
NOT_ADVICE = "Not financial advice."
COULD_READ = "These are the approvals we could read."
SECRET_REPLY = "Nothing was stored. " + NEVER_ASK
DEFAULT_PAGE = "https://www.humanoid-traders.com/panic"
HASH_CAP = 40
_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")

# No delegate has been reviewed as known-good. Every active 7702 delegation
# therefore ranks as not known-good. Add an address only with a review a
# reader can check.
KNOWN_GOOD_DELEGATES: frozenset[str] = frozenset()

WHY_EXPLOIT = "on the public exploit list"
WHY_DELEGATION = "active 7702 delegation to a contract we don't recognize"
WHY_KNOWN = "7702 delegation to a contract we recognize"
WHY_RECENT_UNLIM = "unlimited allowance set in the last 7 days"
WHY_RECENT_FORALL = "ApprovalForAll set in the last 7 days"
WHY_UNLIM = "unlimited allowance"
WHY_FORALL = "ApprovalForAll"
WHY_STALE = "approval older than 180 days"
WHY_OPEN = "open approval"


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only for ``1`` or ``true``. Anything else, including unset, is off."""
    src = env if env is not None else os.environ
    raw = str(src.get(FLAG) or "").strip().lower()
    return raw == "1" or raw == "true"


def refuses_secret(raw: str) -> bool:
    """The digest's secret check: a pasted key or seed, or a request that
    names one (``_SECRET_WORD`` via ``SECRET_REQUEST``). The text is not echoed.
    """
    if looks_like_secret(raw):
        return True
    return _SECRET_WORD_CHECK.search(raw or "") is not None


def page_url(env: Optional[Mapping[str, str]] = None) -> str:
    """The web page. The user's address is never appended."""
    src = env if env is not None else os.environ
    raw = str(src.get("PANIC_PAGE_URL") or "").strip()
    if not raw.startswith("https://") or any(ch in raw for ch in " \t\r\n"):
        return DEFAULT_PAGE
    return raw.split("?", 1)[0]


def _when(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _recent(updated: Optional[int], now: int) -> bool:
    if updated is None or updated > now:
        return False
    return (now - updated) <= WEEK_SECONDS


def _kind(standard: str) -> str:
    if standard == "erc721_all":
        return "approval_for_all"
    return standard


def classify_approval(
    row: OpenApproval,
    *,
    now: int,
    exploits: Optional[set[tuple[int, str]]],
) -> tuple[int, str]:
    """Worst matching tier. A higher tier wins when an item matches two."""
    if exploits is not None and (row.chain_id, row.spender.lower()) in exploits:
        return 1, WHY_EXPLOIT
    forall = row.standard == "erc721_all"
    unlimited = unlimited_of(row) is True
    recent = _recent(row.last_updated, now)
    if forall and recent:
        return 3, WHY_RECENT_FORALL
    if unlimited and recent:
        return 3, WHY_RECENT_UNLIM
    if forall:
        return 4, WHY_FORALL
    if unlimited:
        return 4, WHY_UNLIM
    if stale_of(row.last_updated, now) is True:
        return 5, WHY_STALE
    return 6, WHY_OPEN


def classify_delegation(
    chain_id: int,
    delegate: str,
    *,
    exploits: Optional[set[tuple[int, str]]],
    known_good: frozenset[str],
) -> tuple[int, str]:
    lowered = delegate.lower()
    if exploits is not None and (chain_id, lowered) in exploits:
        return 1, WHY_EXPLOIT
    if lowered in known_good:
        return 6, WHY_KNOWN
    return 2, WHY_DELEGATION


def _item(
    *,
    rank: int,
    chain_id: int,
    chain: str,
    kind: str,
    token: str,
    spender: str,
    why: str,
    updated: Optional[str],
    permit2_expires: Optional[str] = None,
    nft_id: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "rank": rank,
        "chain_id": chain_id,
        "chain": chain,
        "kind": kind,
        "token": token.lower() if token else "",
        "spender": spender.lower(),
        "why": why,
        "updated": updated,
        "permit2_expires": permit2_expires,
        "nft_id": nft_id,
    }


def read_panic(
    address: str,
    *,
    reader: ApprovalReader,
    exploits: ExploitDirectory,
    now: int,
    known_good: frozenset[str] = KNOWN_GOOD_DELEGATES,
) -> dict[str, Any]:
    """Ranked items for one address. The address is not retained here."""
    try:
        chains: Sequence[ChainRead] = reader.read(address)
    except Exception:
        chains = unread_chains()
    try:
        listed = exploits.get()
    except Exception:
        listed = None
    unread: list[str] = []
    items: list[dict[str, Any]] = []
    seen_chains = {c.chain_id for c in chains}
    for chain in chains:
        if chain.status != "ok":
            unread.append(chain.chain_name)
        else:
            for row in chain.approvals:
                rank, why = classify_approval(row, now=now, exploits=listed)
                nft = str(row.nft_id) if row.nft_id is not None else None
                items.append(_item(
                    rank=rank, chain_id=row.chain_id, chain=row.chain_name,
                    kind=_kind(row.standard), token=row.token, spender=row.spender,
                    why=why, updated=_when(row.last_updated),
                    permit2_expires=_when(row.expiration) if row.standard == "permit2" else None,
                    nft_id=nft,
                ))
        try:
            status, delegate = reader.delegation(chain.chain_id, address)
        except Exception:
            status, delegate = "unreadable", None
        if status == "unreadable":
            if chain.chain_name not in unread:
                unread.append(chain.chain_name)
        elif status == "delegated" and delegate:
            rank, why = classify_delegation(
                chain.chain_id, delegate, exploits=listed, known_good=known_good,
            )
            items.append(_item(
                rank=rank, chain_id=chain.chain_id, chain=chain.chain_name,
                kind="delegation_7702", token="", spender=delegate,
                why=why, updated=None,
            ))
    for chain_id, name in CHAINS:
        if chain_id not in seen_chains and name not in unread:
            unread.append(name)
    items.sort(key=lambda it: (it["rank"], it["chain"], it["kind"], it["token"], it["spender"]))
    return {
        "items": items,
        "unread_chains": unread,
        "exploit_list_read": listed is not None,
    }


def _line(n: int, item: Mapping[str, Any]) -> str:
    if item["kind"] == "delegation_7702":
        who = f"delegation {short_address(str(item['spender']))}"
    else:
        who = (
            f"token {short_address(str(item['token']))} · "
            f"spender {short_address(str(item['spender']))}"
        )
    extra = ""
    if item["kind"] == "permit2":
        exp = item.get("permit2_expires")
        extra = f" · Permit2 expires {exp}" if exp else " · Permit2 expiry could not be read"
    when = f"updated {item['updated']}" if item.get("updated") else "updated time could not be read"
    return f"{n}. {item['chain']} · {who}{extra} · {item['why']} · {when}"


def never_dm(url: str) -> str:
    return f"We'll never DM you first. The only page is {url}"


def render_messages(result: Mapping[str, Any], url: str) -> list[str]:
    header = "\n\n".join([
        SEED_WARNING, NEVER_ASK, never_dm(url), CANNOT_RECOVER, NOT_ADVICE, COULD_READ,
    ])
    lines: list[str] = []
    items = result.get("items") or []
    if not items:
        lines.append("No open approval was found on the chains that could be read.")
    for i, item in enumerate(items, start=1):
        lines.append(_line(i, item))
    unread = result.get("unread_chains") or []
    if unread:
        lines.append("Could not read: " + ", ".join(unread) + ".")
    if not result.get("exploit_list_read"):
        lines.append(EXPLOIT_UNREAD)
    lines.append(f"Revoke in your own wallet: {url}")
    lines.append("This bot never signs.")
    return _pack(header, lines)


def _pack(header: str, lines: Sequence[str]) -> list[str]:
    messages: list[str] = []
    current = header
    for line in lines:
        piece = "\n\n" + line
        if len(current) + len(piece) <= MESSAGE_MAX:
            current += piece
            continue
        messages.append(current)
        current = header + piece if len(header) + len(piece) <= MESSAGE_MAX else line[:MESSAGE_MAX]
    if current:
        messages.append(current)
    return messages


def on_panic(
    argument: str,
    *,
    reader: ApprovalReader,
    exploits: ExploitDirectory,
    now_iso: str,
    env: Optional[Mapping[str, str]] = None,
    known_good: frozenset[str] = KNOWN_GOOD_DELEGATES,
) -> list[str]:
    """The reply. Nothing is written. The watch list is not touched."""
    if not flag_on(env):
        return [NOT_AVAILABLE]
    if refuses_secret(argument):
        return [SECRET_REPLY]
    address = normalize_address(argument)
    if address is None:
        return [INVALID_ADDRESS]
    try:
        now = int(datetime.fromisoformat(now_iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        now = int(time.time())
    result = read_panic(
        address, reader=reader, exploits=exploits, now=now, known_good=known_good,
    )
    return render_messages(result, page_url(env))


def seal_receipt(hashes: Any, now: Optional[str] = None) -> dict[str, Any]:
    """The time the caller reported these revoke hashes. Hashes only.

    Returned to the caller. Not written anywhere. No address, no chat id,
    no amounts. At most HASH_CAP hashes, each 0x and 64 hex digits.
    """
    if not isinstance(hashes, list) or not hashes or len(hashes) > HASH_CAP:
        return {"error": "bad hashes"}
    clean: list[str] = []
    for item in hashes:
        if not isinstance(item, str) or not _HASH.fullmatch(item):
            return {"error": "bad hashes"}
        clean.append(item.lower())
    moment = now or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {"cleaned_at": moment, "hashes": clean}


def _stdin_body() -> dict[str, Any]:
    try:
        body = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {"error": "bad request"}
    if not isinstance(body, dict):
        return {"error": "bad request"}
    return body


def main() -> int:
    body = _stdin_body()
    if body.get("error"):
        json.dump({"error": "bad request"}, sys.stdout)
        return 0
    if body.get("op") == "receipt":
        json.dump(seal_receipt(body.get("hashes")), sys.stdout)
        return 0
    if not flag_on():
        json.dump({"available": False, "message": NOT_AVAILABLE}, sys.stdout)
        return 0
    got = body.get("address")
    raw = got if isinstance(got, str) else ""
    if refuses_secret(raw):
        json.dump({
            "available": True, "refused": "secret", "message": SECRET_REPLY,
        }, sys.stdout)
        return 0
    address = normalize_address(raw)
    if address is None:
        json.dump({
            "available": True, "refused": "address", "message": INVALID_ADDRESS,
        }, sys.stdout)
        return 0
    result = read_panic(
        address, reader=ApprovalReader(), exploits=ExploitDirectory(), now=int(time.time()),
    )
    result["available"] = True
    result["warning"] = SEED_WARNING
    result["never_ask"] = NEVER_ASK
    result["read_line"] = COULD_READ
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
