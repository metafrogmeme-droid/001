"""Turn one chain's reads into events. No network, no storage.

A missing read is ``unread``. It is not an empty list and it is not a clear
chain. Dust and lookalike bait are labelled and not alerts. A transfer the
owner did not send is an approval being used.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from bot.approval_reader import TOPIC_APPROVAL, TOPIC_FOR_ALL, TOPIC_PERMIT2
from bot.drain_watch.tx import unsigned_tx

TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
DUST_RAW_MAX = 10000
LARGE_NATIVE_WEI = 10 ** 18
_MAX_UINT160 = (1 << 160) - 1


@dataclass(frozen=True)
class WatchEvent:
    event_id: str
    kind: str
    alert: bool
    chain_id: int
    chain_name: str
    summary: str
    token: str
    spender: str
    counterparty: str
    signals: dict[str, str]
    tx: Optional[dict]


@dataclass
class ChainOutcome:
    status: str
    advance_to: Optional[int]
    sentence: str
    events: list[WatchEvent] = field(default_factory=list)
    save_approvals: list[tuple] = field(default_factory=list)
    counterparties: list[str] = field(default_factory=list)
    delegate: Optional[tuple[str, str]] = None


def _eid(parts: str) -> str:
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]


def _addr(word: str) -> Optional[str]:
    raw = str(word or "").lower().removeprefix("0x")
    if len(raw) < 40:
        return None
    body = raw[-40:]
    if len(body) != 40 or any(c not in "0123456789abcdef" for c in body):
        return None
    return "0x" + body


def _hex_int(value: object) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    try:
        if text.startswith("0x"):
            return int(text, 16)
        if text.isdigit():
            return int(text, 10)
    except ValueError:
        return None
    return None


def _words(data: str) -> list[int]:
    body = str(data or "").lower().removeprefix("0x")
    if len(body) % 64 != 0 or any(c not in "0123456789abcdef" for c in body):
        return []
    return [int(body[i:i + 64], 16) for i in range(0, len(body), 64)]


def _lookalike(candidate: str, others: list[str]) -> bool:
    me = candidate.lower().removeprefix("0x")
    if len(me) != 40:
        return False
    for other in others:
        them = other.lower().removeprefix("0x")
        if len(them) != 40 or them == me:
            continue
        if me[:4] == them[:4] and me[-4:] == them[-4:]:
            return True
    return False


def _exploit(exploits: Optional[set[tuple[int, str]]], chain_id: int, address: str) -> str:
    if exploits is None:
        return "unread"
    if (chain_id, address.lower()) in exploits:
        return "hit"
    return "miss"


def _age(updated: Optional[int], now: int) -> str:
    if updated is None or updated > now:
        return "unread"
    days = (now - updated) // 86400
    return str(days)


def _family(status: str, delegate: Optional[str], families: dict[str, str]) -> str:
    if status == "unreadable":
        return "unread"
    if status != "delegated" or not delegate:
        return "no_delegation"
    return families.get(delegate.lower(), "unknown")


def _signals(
    *,
    exploit: str,
    age: str,
    unlimited: str,
    family: str,
) -> dict[str, str]:
    return {
        "exploit_list": exploit,
        "approval_age_days": age,
        "unlimited": unlimited,
        "delegate_family": family,
        "claw_guard": "unread",
    }


def events_from_chain(
    *,
    owner: str,
    chain_id: int,
    chain_name: str,
    head: Optional[int],
    cursor: Optional[int],
    logs_ok: bool,
    logs: list[dict],
    txs_ok: bool,
    txs: list[dict],
    senders: dict[str, tuple[str, Optional[str]]],
    delegation_status: str,
    delegation: Optional[str],
    stored_delegate: Optional[tuple[str, str]],
    stored_approvals: dict[tuple[str, str], dict],
    counterparties: list[str],
    exploits: Optional[set[tuple[int, str]]],
    families: dict[str, str],
    now: int,
    day: str,
) -> ChainOutcome:
    """One chain. ``logs_ok`` false means the log read failed, not that it was empty."""
    owner_l = owner.lower()
    family = _family(delegation_status, delegation, families)
    if head is None:
        return ChainOutcome(
            status="unreadable",
            advance_to=None,
            sentence=f"{chain_name} could not be read. That is not a clear result.",
        )
    if cursor is None:
        caught = ChainOutcome(
            status="caught_up",
            advance_to=head,
            sentence=(
                f"{chain_name} is being watched from block {head}. "
                "Earlier history is not alerted."
            ),
            delegate=(
                (delegation_status, (delegation or "").lower())
                if delegation_status in ("none", "delegated") else None
            ),
        )
        if delegation_status == "delegated" and delegation:
            caught.events.append(WatchEvent(
                event_id=_eid(f"del:{chain_id}:{delegation.lower()}:{head}"),
                kind="delegation_changed",
                alert=True,
                chain_id=chain_id,
                chain_name=chain_name,
                summary=f"EIP-7702 delegation on {chain_name} is present at the block watching started.",
                token="",
                spender=delegation.lower(),
                counterparty="",
                signals=_signals(
                    exploit=_exploit(exploits, chain_id, delegation),
                    age="unread",
                    unlimited="unread",
                    family=family,
                ),
                tx=unsigned_tx(kind="delegation_7702", chain_id=chain_id),
            ))
            caught.sentence = (
                f"{chain_name} is being watched from block {head}. "
                "A delegation was already present. Earlier history is not alerted."
            )
        return caught
    if not logs_ok or not txs_ok or delegation_status == "unreadable":
        return ChainOutcome(
            status="unreadable",
            advance_to=None,
            sentence=(
                f"{chain_name} was only partly read, so this pass has no count. "
                "That is not a clear result."
            ),
            events=[WatchEvent(
                event_id=_eid(f"lag:{chain_id}:{day}"),
                kind="index_lag",
                alert=True,
                chain_id=chain_id,
                chain_name=chain_name,
                summary=f"{chain_name} could not be fully read on this pass.",
                token="",
                spender="",
                counterparty="",
                signals=_signals(
                    exploit="unread", age="unread", unlimited="unread", family=family,
                ),
                tx=None,
            )],
        )

    out = ChainOutcome(
        status="ok",
        advance_to=head,
        sentence=f"{chain_name}: no new event in the blocks that were read.",
        delegate=(delegation_status, (delegation or "").lower()),
    )
    new_counterparties: list[str] = []
    alerts = 0

    for tx in txs:
        sender = str(tx.get("sender") or "").lower()
        if sender != owner_l:
            continue
        if tx.get("failed") is True:
            continue
        value = tx.get("value_wei")
        if value is None:
            out.events.append(WatchEvent(
                event_id=_eid(f"native-unread:{chain_id}:{tx.get('hash')}:{day}"),
                kind="native_unread",
                alert=True,
                chain_id=chain_id,
                chain_name=chain_name,
                summary=f"A native transfer on {chain_name} could not be sized.",
                token="",
                spender="",
                counterparty="",
                signals=_signals(
                    exploit="unread", age="unread", unlimited="unread", family=family,
                ),
                tx=None,
            ))
            alerts += 1
            continue
        if isinstance(value, int) and value >= LARGE_NATIVE_WEI:
            dest = str(tx.get("to") or "")
            out.events.append(WatchEvent(
                event_id=_eid(f"native:{chain_id}:{tx.get('hash')}"),
                kind="large_outflow",
                alert=True,
                chain_id=chain_id,
                chain_name=chain_name,
                summary=(
                    f"Large native outflow on {chain_name}. "
                    "The size is the chain's native unit, not a price."
                ),
                token="",
                spender="",
                counterparty=dest.lower() if dest else "",
                signals=_signals(
                    exploit=_exploit(exploits, chain_id, dest) if dest else "unread",
                    age="unread",
                    unlimited="unread",
                    family=family,
                ),
                tx=None,
            ))
            alerts += 1

    for row in logs:
        topics = [str(t).lower() for t in (row.get("topics") or [])]
        if not topics:
            continue
        topic0 = topics[0]
        token = str(row.get("address") or "").lower()
        block = _hex_int(row.get("block"))
        index = _hex_int(row.get("log_index"))
        tx_hash = str(row.get("tx_hash") or "")
        updated = _hex_int(row.get("timestamp"))
        words = _words(str(row.get("data") or ""))
        if block is None or index is None:
            continue
        base_id = f"{chain_id}:{tx_hash}:{index}"

        if topic0 == TOPIC_TRANSFER and len(topics) >= 3:
            src = _addr(topics[1])
            dst = _addr(topics[2])
            amount = words[0] if words else None
            if src == owner_l and dst and amount is not None:
                tx_status, tx_sender = senders.get(tx_hash, ("unreadable", None))
                if tx_status != "ok":
                    out.events.append(WatchEvent(
                        event_id=_eid(f"sender-unread:{chain_id}:{day}"),
                        kind="sender_unread",
                        alert=True,
                        chain_id=chain_id,
                        chain_name=chain_name,
                        summary=(
                            f"A token left the address on {chain_name} and the "
                            "transaction sender could not be read, so whether an "
                            "old approval was used is unread."
                        ),
                        token=token,
                        spender="",
                        counterparty=dst,
                        signals=_signals(
                            exploit="unread", age="unread", unlimited="unread", family=family,
                        ),
                        tx=None,
                    ))
                    alerts += 1
                elif tx_sender and tx_sender.lower() != owner_l:
                    remembered = stored_approvals.get((token, tx_sender.lower()))
                    age = _age(remembered.get("updated") if remembered else None, now)
                    unlimited = remembered.get("unlimited") if remembered else "unread"
                    if unlimited not in ("yes", "no", "unread"):
                        unlimited = "unread"
                    out.events.append(WatchEvent(
                        event_id=_eid(f"used:{base_id}"),
                        kind="approval_used",
                        alert=True,
                        chain_id=chain_id,
                        chain_name=chain_name,
                        summary=(
                            f"An old approval was used on {chain_name}: "
                            "someone other than this address sent the transaction "
                            "that moved the tokens."
                        ),
                        token=token,
                        spender=tx_sender.lower(),
                        counterparty=dst,
                        signals=_signals(
                            exploit=_exploit(exploits, chain_id, tx_sender),
                            age=age,
                            unlimited=str(unlimited),
                            family=family,
                        ),
                        tx=unsigned_tx(
                            kind="erc20", chain_id=chain_id, token=token, spender=tx_sender.lower(),
                        ),
                    ))
                    alerts += 1
                else:
                    known = list(counterparties) + new_counterparties
                    if amount > DUST_RAW_MAX and _lookalike(dst, known):
                        out.events.append(WatchEvent(
                            event_id=_eid(f"lookalike:{base_id}"),
                            kind="lookalike_outflow",
                            alert=True,
                            chain_id=chain_id,
                            chain_name=chain_name,
                            summary=(
                                f"Outgoing transfer on {chain_name} to an address "
                                "that matches the first 4 and last 4 characters "
                                "of a previous counterparty."
                            ),
                            token=token,
                            spender="",
                            counterparty=dst,
                            signals=_signals(
                                exploit=_exploit(exploits, chain_id, dst),
                                age="unread",
                                unlimited="unread",
                                family=family,
                            ),
                            tx=None,
                        ))
                        alerts += 1
                    elif amount > DUST_RAW_MAX:
                        new_counterparties.append(dst)
            elif dst == owner_l and src and amount is not None and amount <= DUST_RAW_MAX:
                known = list(counterparties) + new_counterparties
                label = "lookalike_dust" if _lookalike(src, known) else "dust"
                out.events.append(WatchEvent(
                    event_id=_eid(f"dust:{base_id}"),
                    kind=label,
                    alert=False,
                    chain_id=chain_id,
                    chain_name=chain_name,
                    summary=(
                        f"Hidden dust on {chain_name}. Labelled, not an alert."
                    ),
                    token=token,
                    spender="",
                    counterparty=src,
                    signals=_signals(
                        exploit="unread", age="unread", unlimited="unread", family=family,
                    ),
                    tx=None,
                ))
            continue

        if topic0 == TOPIC_APPROVAL and len(topics) >= 3 and words:
            spender = _addr(topics[2])
            amount = words[0]
            if not spender or token == "0x" + "0" * 40:
                continue
            if amount == 0:
                continue
            out.save_approvals.append((token, spender, updated, "unread"))
            out.events.append(WatchEvent(
                event_id=_eid(f"approval:{base_id}"),
                kind="new_approval",
                alert=True,
                chain_id=chain_id,
                chain_name=chain_name,
                summary=f"New approval on {chain_name}.",
                token=token,
                spender=spender,
                counterparty="",
                signals=_signals(
                    exploit=_exploit(exploits, chain_id, spender),
                    age=_age(updated, now),
                    unlimited="unread",
                    family=family,
                ),
                tx=unsigned_tx(kind="erc20", chain_id=chain_id, token=token, spender=spender),
            ))
            alerts += 1
            continue

        if topic0 == TOPIC_FOR_ALL and len(topics) >= 3 and words:
            spender = _addr(topics[2])
            if not spender:
                continue
            approved = words[0] == 1
            if not approved:
                continue
            out.save_approvals.append((token, spender, updated, "yes"))
            out.events.append(WatchEvent(
                event_id=_eid(f"forall:{base_id}"),
                kind="new_approval_for_all",
                alert=True,
                chain_id=chain_id,
                chain_name=chain_name,
                summary=f"New approval-for-all on {chain_name}.",
                token=token,
                spender=spender,
                counterparty="",
                signals=_signals(
                    exploit=_exploit(exploits, chain_id, spender),
                    age=_age(updated, now),
                    unlimited="yes",
                    family=family,
                ),
                tx=unsigned_tx(
                    kind="approval_for_all", chain_id=chain_id, token=token, spender=spender,
                ),
            ))
            alerts += 1
            continue

        if topic0 == TOPIC_PERMIT2 and len(topics) >= 4 and len(words) >= 2:
            permit_token = _addr(topics[2])
            permit_spender = _addr(topics[3])
            amount = words[0]
            expiration = words[1]
            if not permit_token or not permit_spender:
                continue
            if amount == 0 or (expiration != 0 and expiration < now):
                continue
            if amount > _MAX_UINT160:
                continue
            unlimited = "unread"
            out.save_approvals.append((permit_token, permit_spender, updated, unlimited))
            out.events.append(WatchEvent(
                event_id=_eid(f"permit2:{base_id}"),
                kind="new_permit2",
                alert=True,
                chain_id=chain_id,
                chain_name=chain_name,
                summary=f"New Permit2 allowance on {chain_name}.",
                token=permit_token,
                spender=permit_spender,
                counterparty="",
                signals=_signals(
                    exploit=_exploit(exploits, chain_id, permit_spender),
                    age=_age(updated, now),
                    unlimited=unlimited,
                    family=family,
                ),
                tx=unsigned_tx(
                    kind="permit2", chain_id=chain_id,
                    token=permit_token, spender=permit_spender,
                ),
            ))
            alerts += 1

    if delegation_status == "delegated" and delegation:
        current = ("delegated", delegation.lower())
        if stored_delegate != current:
            out.events.append(WatchEvent(
                event_id=_eid(f"del:{chain_id}:{delegation.lower()}:{head}"),
                kind="delegation_changed",
                alert=True,
                chain_id=chain_id,
                chain_name=chain_name,
                summary=f"EIP-7702 delegation on {chain_name} is new or changed.",
                token="",
                spender=delegation.lower(),
                counterparty="",
                signals=_signals(
                    exploit=_exploit(exploits, chain_id, delegation),
                    age="unread",
                    unlimited="unread",
                    family=family,
                ),
                tx=unsigned_tx(kind="delegation_7702", chain_id=chain_id),
            ))
            alerts += 1
    elif delegation_status == "none" and stored_delegate and stored_delegate[0] == "delegated":
        out.events.append(WatchEvent(
            event_id=_eid(f"del-clear:{chain_id}:{head}"),
            kind="delegation_cleared",
            alert=True,
            chain_id=chain_id,
            chain_name=chain_name,
            summary=f"EIP-7702 delegation on {chain_name} is no longer present.",
            token="",
            spender="",
            counterparty="",
            signals=_signals(
                exploit="unread", age="unread", unlimited="unread", family="no_delegation",
            ),
            tx=None,
        ))
        alerts += 1

    out.counterparties = new_counterparties
    if alerts:
        out.sentence = f"{chain_name}: {alerts} event{'s' if alerts != 1 else ''} to review."
    return out
