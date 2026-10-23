"""Weekly approval hygiene: what to flag, and the words we send.

Read-only. The rules for "unlimited" are revoke.cash's, so a digest can be
checked against that site:

* ERC-20 and Permit2 are unlimited only when total supply was read, that
  supply is non-zero, and the open allowance is greater than it
  (``formatErc20Allowance``: ``totalSupply && allowance > totalSupply``).
  A missing supply is not "limited" and not "unlimited".
* An ERC-721 ``ApprovalForAll`` is unlimited. A single-token approval is not.
* "Older than 90 days" is strict: a last-update exactly 90 days of 86400
  seconds ago is not older.
* A spender is on the public exploit list only when that list was read and
  the spender's address is on it for that chain. A missing list is not "none".

Nothing here signs, holds an allowance, or asks for a key.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

# Launch chains. Names are the ones the digest uses.
CHAINS: tuple[tuple[int, str], ...] = (
    (1, "Ethereum"),
    (8453, "Base"),
    (43114, "Avalanche C-Chain"),
    (42161, "Arbitrum"),
)

STALE_AFTER_SECONDS = 90 * 86400
WEEK_SECONDS = 7 * 86400
MESSAGE_MAX = 1000
MAX_ADDRESSES = 5
MIN_INTERVAL_SEC = 60
MIN_POLL_SEC = 30
DEFAULT_POLL_SEC = 3600

_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX64 = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

HEADER = (
    "Approval hygiene. This note is read-only. "
    "This bot never holds a permission and never signs. "
    "Revoke from your own wallet."
)
EXPLOIT_UNREAD = (
    "The public exploit list could not be read, so spenders on it are not included."
)
ALL_UNREAD = (
    "The approval lists could not be read just now, so there is no count. "
    "This is not a clean result."
)
SUPPLY_GAP = (
    "Some allowances could not be compared with total supply, so they are not marked unlimited."
)
AGE_GAP = "The age of some approvals could not be read, so they are not marked older than 90 days."

NOT_OPTED = (
    "You have not added an address, so there is no digest. "
    "/watch followed by an address opts in. Nothing is stored until you do."
)
INVALID_ADDRESS = (
    "That is not a wallet address. Send one address: 0x followed by 40 hex characters."
)
SECRET_REFUSAL = (
    "Nothing was stored. This bot only accepts a wallet address. "
    "It never asks for a seed phrase or a private key."
)
DUPLICATE = "That address is already watched. Nothing new was stored."
FULL = (
    f"This chat already watches {MAX_ADDRESSES} addresses, which is the maximum. "
    "/stop removes them."
)
STOPPED = "Removed. No addresses and no digest schedule are stored for this chat."
STOPPED_EMPTY = "Nothing was stored for this chat."
NO_CHAT = "I could not tell which chat this is, so nothing was stored."
DIGEST_HELP = (
    "Approval hygiene is optional. /watch <address> stores that address "
    f"(up to {MAX_ADDRESSES}) under an anonymous id and sends a weekly read-only note "
    "about unlimited approvals, approvals older than 90 days, and spenders "
    "on the public exploit list, with a link to revoke.cash. "
    "You revoke in your own wallet. This bot never signs and never asks for "
    "a seed phrase or a private key. /stop deletes every stored address and the schedule."
)

# A request for a secret. The refusals above name the words in order to decline
# them. The two halves stay apart in this file so the pattern does not match
# its own source.
_ASK = r"(?i)\b(send|paste|enter|share|tell me|what is|give)\b.{0,80}\b("
_SECRET_WORD = r"seed|mnemonic|private key|secret key"
SECRET_REQUEST = re.compile(_ASK + _SECRET_WORD + r")\b")


@dataclass(frozen=True)
class OpenApproval:
    """One open approval the reader has already reduced to its latest state."""

    chain_id: int
    chain_name: str
    token: str
    spender: str
    standard: str
    amount: Optional[int]
    total_supply: Optional[int]
    supply_known: bool
    last_updated: Optional[int]


@dataclass(frozen=True)
class ChainRead:
    chain_id: int
    chain_name: str
    status: str  # "ok", "unreadable", "truncated"
    approvals: tuple[OpenApproval, ...] = ()
    # Events we could not tell apart from a closed approval. They are not counted.
    unresolved: int = 0


@dataclass(frozen=True)
class AddressSummary:
    address: str
    unlimited: int
    unlimited_complete: bool
    stale: int
    stale_complete: bool
    scam: int
    gaps: tuple[str, ...]
    any_read: bool


def digest_interval_sec(env: Optional[dict] = None) -> int:
    """Seconds between digests. Unset or unusable values stay at one week.

    The raw value is never returned and never logged here.
    """
    e = env if env is not None else os.environ
    raw = str(e.get("APPROVAL_DIGEST_INTERVAL_SEC") or "").strip()
    if not raw:
        return WEEK_SECONDS
    try:
        n = int(raw)
    except ValueError:
        return WEEK_SECONDS
    if n < MIN_INTERVAL_SEC:
        return WEEK_SECONDS
    return n


def digest_poll_sec(env: Optional[dict] = None) -> int:
    """How often the running process looks for a due digest."""
    e = env if env is not None else os.environ
    raw = str(e.get("APPROVAL_DIGEST_POLL_SEC") or "").strip()
    if not raw:
        return DEFAULT_POLL_SEC
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_POLL_SEC
    if n < MIN_POLL_SEC:
        return DEFAULT_POLL_SEC
    return n


def normalize_address(raw: str) -> Optional[str]:
    s = (raw or "").strip()
    if not _ADDR.fullmatch(s):
        return None
    return s.lower()


def looks_like_secret(raw: str) -> bool:
    """A private key or a word-list seed. Not stored, not echoed."""
    s = (raw or "").strip()
    if _HEX64.fullmatch(s):
        return True
    words = s.split()
    if len(words) >= 12 and all(re.fullmatch(r"[A-Za-z]+", w) for w in words):
        return True
    return False


def command_argument(text: str) -> str:
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def revoke_url(address: str) -> str:
    return f"https://revoke.cash/address/{address}"


def short_address(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


def parse_iso(text: str) -> datetime:
    if not _ISO.fullmatch(text or ""):
        raise ValueError("time")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def format_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def shift_iso(text: str, seconds: int) -> str:
    return format_iso(parse_iso(text) + timedelta(seconds=seconds))


def unlimited_of(row: OpenApproval) -> Optional[bool]:
    """True, False, or None when revoke.cash's own rule cannot be applied."""
    if row.standard == "erc721_all":
        return True
    if row.standard == "erc721_single":
        return False
    if row.standard not in ("erc20", "permit2"):
        return None
    if row.amount is None or not row.supply_known or row.total_supply is None:
        return None
    # 0n is falsy in the JavaScript this copies, so a zero supply is not "Unlimited".
    if row.total_supply <= 0:
        return False
    return row.amount > row.total_supply


def stale_of(updated: Optional[int], now: int) -> Optional[bool]:
    if updated is None:
        return None
    if updated > now:
        return False
    return (now - updated) > STALE_AFTER_SECONDS


def scam_of(row: OpenApproval, exploits: Optional[set[tuple[int, str]]]) -> Optional[bool]:
    if exploits is None:
        return None
    return (row.chain_id, row.spender.lower()) in exploits


def unread_chains() -> tuple[ChainRead, ...]:
    return tuple(
        ChainRead(chain_id=cid, chain_name=name, status="unreadable")
        for cid, name in CHAINS
    )


def summarize_address(
    address: str,
    chains: Sequence[ChainRead],
    now: int,
    exploits: Optional[set[tuple[int, str]]],
) -> AddressSummary:
    unlimited = 0
    stale = 0
    scam = 0
    unlimited_complete = True
    stale_complete = True
    gaps: list[str] = []
    any_read = False
    for chain in chains:
        if chain.status != "ok":
            gaps.append(chain.chain_name)
            continue
        any_read = True
        if chain.unresolved:
            unlimited_complete = False
            stale_complete = False
        for row in chain.approvals:
            flag = unlimited_of(row)
            if flag is None:
                unlimited_complete = False
            elif flag:
                unlimited += 1
            age = stale_of(row.last_updated, now)
            if age is None:
                stale_complete = False
            elif age:
                stale += 1
            listed = scam_of(row, exploits)
            if listed:
                scam += 1
    return AddressSummary(
        address=address,
        unlimited=unlimited,
        unlimited_complete=unlimited_complete,
        stale=stale,
        stale_complete=stale_complete,
        scam=scam,
        gaps=tuple(gaps),
        any_read=any_read,
    )


def _noun(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _count_line(n: int, singular: str, plural: str, *, complete: bool, partial: bool, zero: str) -> Optional[str]:
    if not complete and n == 0:
        return None
    if n == 0:
        line = zero
    else:
        line = f"You have {n} {_noun(n, singular, plural)}."
    if partial:
        line = line[:-1] + " on the chains that were read."
    return line


def _address_block(summary: AddressSummary, *, scam_known: bool) -> str:
    partial = bool(summary.gaps)
    lines: list[str] = [short_address(summary.address)]
    if not summary.any_read:
        lines.append(ALL_UNREAD)
    else:
        unlimited = _count_line(
            summary.unlimited, "unlimited approval", "unlimited approvals",
            complete=summary.unlimited_complete, partial=partial,
            zero="You have no unlimited approvals.",
        )
        stale = _count_line(
            summary.stale, "approval older than 90 days", "approvals older than 90 days",
            complete=summary.stale_complete, partial=partial,
            zero="You have no approvals older than 90 days.",
        )
        if unlimited:
            lines.append(unlimited)
        elif not summary.unlimited_complete:
            lines.append(SUPPLY_GAP)
        if not summary.unlimited_complete and summary.unlimited > 0:
            lines.append(SUPPLY_GAP)
        if stale:
            lines.append(stale)
        elif not summary.stale_complete:
            lines.append(AGE_GAP)
        if not summary.stale_complete and summary.stale > 0:
            lines.append(AGE_GAP)
        if scam_known:
            scam = _count_line(
                summary.scam,
                "approval to a spender on the public exploit list",
                "approvals to spenders on the public exploit list",
                complete=True, partial=partial,
                zero="You have no approvals to a spender on the public exploit list.",
            )
            if scam:
                lines.append(scam)
    if summary.gaps:
        names = ", ".join(summary.gaps)
        lines.append(f"{names} could not be read in full, so those chains are not included.")
    lines.append(revoke_url(summary.address))
    return "\n".join(lines)


def render_digest(summaries: Sequence[AddressSummary], *, scam_known: bool) -> list[str]:
    """One or more Telegram messages, each at most 1000 characters."""
    if not summaries:
        return [NOT_OPTED]
    blocks = [_address_block(s, scam_known=scam_known) for s in summaries]
    footer_parts: list[str] = []
    if not scam_known:
        footer_parts.append(EXPLOIT_UNREAD)
    if summaries and all(not s.gaps for s in summaries):
        names = ", ".join(name for _cid, name in CHAINS[:-1]) + " and " + CHAINS[-1][1]
        footer_parts.append(f"Chains read: {names}.")
    footer = "\n".join(footer_parts)
    return _pack(HEADER, blocks, footer)


def _pack(header: str, blocks: list[str], footer: str) -> list[str]:
    messages: list[str] = []
    current = header
    pending_footer = footer

    def _flush() -> None:
        nonlocal current
        if current:
            messages.append(current)
            current = header

    for block in blocks:
        piece = "\n\n" + block
        room = len(pending_footer) + 2 if pending_footer else 0
        if len(current) + len(piece) + room <= MESSAGE_MAX:
            current += piece
            continue
        if current != header:
            _flush()
        if len(current) + len(piece) + room <= MESSAGE_MAX:
            current += piece
            continue
        # The block alone does not fit under the header. Keep the link, drop caveats.
        shrunk = _shrink(block)
        piece = "\n\n" + shrunk
        if len(header) + len(piece) > MESSAGE_MAX:
            piece = "\n\n" + shrunk[: MESSAGE_MAX - len(header) - 2]
        if current != header:
            _flush()
        current = header + piece
    if pending_footer:
        extra = "\n\n" + pending_footer
        if len(current) + len(extra) <= MESSAGE_MAX:
            current += extra
        else:
            _flush()
            current = pending_footer if len(pending_footer) <= MESSAGE_MAX else pending_footer[:MESSAGE_MAX]
    if current:
        messages.append(current)
    for text in messages:
        if len(text) > MESSAGE_MAX:
            raise RuntimeError("digest message length")
    return messages


def _shrink(block: str) -> str:
    rows = block.split("\n")
    link = next((r for r in rows if r.startswith("https://revoke.cash/address/")), "")
    head = rows[0] if rows else ""
    body = "\n".join(r for r in rows[1:] if r != link and not r.startswith("https://"))
    # Keep the counts (they start with "You have" or the unread sentence) and the link.
    kept = [head]
    for row in body.split("\n"):
        if row.startswith("You have") or row.startswith("The approval") or row.startswith("Some "):
            kept.append(row)
    if link:
        kept.append(link)
    text = "\n".join(kept)
    if len(text) <= 800:
        return text
    return "\n".join(part for part in (head, link) if part)


def watch_reply(status: str, address: str, count: int) -> str:
    if status == "duplicate":
        return DUPLICATE
    if status == "full":
        return FULL
    if status != "added":
        return INVALID_ADDRESS
    return (
        f"Watching {short_address(address)}. This chat has {count} of {MAX_ADDRESSES} addresses. "
        "A read-only digest is sent once a week. /digest sends one now. "
        "/stop deletes every stored address and the schedule. "
        "This bot never asks for a seed phrase or a private key, and it never signs."
    )


def stop_reply(removed: bool) -> str:
    return STOPPED if removed else STOPPED_EMPTY
