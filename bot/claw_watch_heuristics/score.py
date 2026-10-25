"""Score one watched address. No network.

Weights follow the 25 Oct 2026 research note. Unknown delegation (15) and a
lone third-party pull (25) stay below the alert line. A permit-then-pull
whose sender or every recipient is on the quiet list is not scored.
``0`` is a real zero. A missing sender is not a third-party pull.
"""

from __future__ import annotations

from typing import Optional

from bot.claw_watch_heuristics.alert import DISCLAIMER
from bot.claw_watch_heuristics.allowlist import (
    ALLOW_SPENDERS,
    BAD_7702,
    ENTRYPOINTS,
    GOOD_7702,
    PERMIT2,
    RECIPIENT_QUIET,
    WRAPPED_NATIVE,
)
from bot.claw_watch_heuristics.chains import chain_allowed, chain_label
from bot.claw_watch_heuristics.decode import ZERO, decode_window
from bot.claw_watch_heuristics.flag import flag_on
from bot.claw_watch_heuristics.revoke import prepare_revokes

# Drainer ceilings from the research note. The allowance X-ray uses a
# different cut (2**128). This heuristic does not.
_UNLIMITED_ERC20 = 2 ** 255
_UNLIMITED_P2 = 2 ** 159
# Same raw ceiling as app/public/js/address-poison.js DUST_RAW_MAX.
_DUST_RAW_MAX = 10000

_P2_SIG = frozenset({
    "0x30f28b7a", "0xedd9444b", "0x4fe02b44", "0xfe8ec1a7", "0x36c78516",
})

WEIGHTS = {
    "PERMIT_SUBMITTED_BY_THIRD_PARTY": 30,
    "UNLIMITED_AMOUNT": 10,
    "THIRD_PARTY_PULL": 25,
    "PERMIT_AND_PULL_SAME_TX": 20,
    "MULTI_ASSET_SWEEP": 20,
    "SPLIT_PAYOUT_PATTERN": 25,
    "APPROVAL_TO_EOA": 25,
    "APPROVAL_FOR_ALL_TO_UNKNOWN": 15,
    "VANITY_ZERO_PATTERN": 10,
    "DELEGATION_TO_LISTED_SWEEPER": 80,
    "DELEGATION_TO_UNKNOWN_CONTRACT": 15,
    "ZERO_VALUE_PULL": 35,
    "LOOKALIKE_INBOUND": 35,
    "LOOKALIKE_OF_PAST_RECIPIENT": 25,
    "SPOOFED_TRANSFER_SPRAY": 40,
    "OUTBOUND_NOT_SENT_BY_YOU": 30,
    "DUST_FROM_LOOKALIKE": 20,
    "POISONED_RECIPIENT_SEND": 70,
    "NATIVE_ETH_INTERNAL_PULL": 40,
}

_CHAIN_SENTENCE = "This chain is not a testnet or local fork this watch reads."
_ADDRESS_SENTENCE = "The address could not be read."


def _strength(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _stage(rules: set[str]) -> str:
    if rules & {"DELEGATION_TO_LISTED_SWEEPER", "DELEGATION_TO_UNKNOWN_CONTRACT"}:
        return "delegation"
    if "NATIVE_ETH_INTERNAL_PULL" in rules:
        return "native"
    if "SPLIT_PAYOUT_PATTERN" in rules:
        return "split"
    bait = {
        "ZERO_VALUE_PULL", "LOOKALIKE_INBOUND", "LOOKALIKE_OF_PAST_RECIPIENT",
        "DUST_FROM_LOOKALIKE", "OUTBOUND_NOT_SENT_BY_YOU", "POISONED_RECIPIENT_SEND",
        "SPOOFED_TRANSFER_SPRAY",
    }
    if rules & bait:
        return "bait"
    if rules & {"THIRD_PARTY_PULL", "PERMIT_AND_PULL_SAME_TX", "MULTI_ASSET_SWEEP"}:
        return "pull"
    return "permission"


def _lookalike_key(addr: str) -> str:
    return addr[2:6] + addr[-4:]


def _vanity(addr: str) -> bool:
    return addr.startswith("0x0000") and addr.endswith("0000")


def _dust(value: int, decimals: object = None) -> bool:
    if value == 0 or value <= _DUST_RAW_MAX:
        return True
    if isinstance(decimals, bool) or not isinstance(decimals, int):
        return False
    if not 0 <= decimals <= 36:
        return False
    places = decimals - 4
    if places < 0:
        places = 0
    limit = 1
    for _ in range(places):
        limit *= 10
    return value <= limit


def _delegate_of(code: object) -> Optional[str]:
    if not isinstance(code, str):
        return None
    body = code.lower()
    if not body.startswith("0xef0100") or len(body) < 48:
        return None
    addr = "0x" + body[8:48]
    if len(addr) != 42:
        return None
    return addr


def _self_exec(window: dict, tx: dict, owner: str) -> bool:
    if window.get("code_status") != "read":
        return False
    delegate = _delegate_of(window.get("code"))
    if delegate is None or delegate in BAD_7702:
        return False
    to_raw = tx.get("to")
    to = to_raw if isinstance(to_raw, str) else ""
    return to == owner or to in ENTRYPOINTS


def _code_status(codes: dict, addr: str) -> str:
    row = codes.get(addr)
    if not isinstance(row, dict):
        return "absent"
    status = row.get("status")
    if isinstance(status, str):
        return status
    return "absent"


def _eoa(codes: dict, addr: str) -> Optional[bool]:
    row = codes.get(addr)
    if not isinstance(row, dict) or row.get("status") != "read":
        return None
    code_raw = row.get("code")
    code = code_raw if isinstance(code_raw, str) else "0x"
    if code in ("0x", "0x0", ""):
        return True
    if code.startswith("0xef0100"):
        return True
    return False


def _blank_mem() -> dict:
    return {"trusted": set(), "tainted": {}, "keys": {}}


def _load_mem(raw: object) -> tuple[str, dict]:
    """``(read|unreadable, memory)``. A missing dict is an empty book, which is a reading."""
    if raw is None:
        return ("unreadable", _blank_mem())
    if not isinstance(raw, dict):
        return ("read", _blank_mem())
    trusted_raw = raw.get("trusted")
    tainted_raw = raw.get("tainted")
    keys_raw = raw.get("keys")
    trusted = trusted_raw if isinstance(trusted_raw, list) else []
    tainted = tainted_raw if isinstance(tainted_raw, dict) else {}
    keys = keys_raw if isinstance(keys_raw, dict) else {}
    mem = _blank_mem()
    mem["trusted"] = {a for a in trusted if isinstance(a, str)}
    mem["tainted"] = {
        k: v for k, v in tainted.items() if isinstance(k, str) and isinstance(v, dict)
    }
    for key, vals in keys.items():
        if isinstance(key, str) and isinstance(vals, list):
            mem["keys"][key] = {a for a in vals if isinstance(a, str)}
    return ("read", mem)


def _dump_mem(mem: dict) -> dict:
    return {
        "trusted": sorted(mem["trusted"]),
        "tainted": mem["tainted"],
        "keys": {k: sorted(v) for k, v in sorted(mem["keys"].items())},
    }


def _mimics(mem: dict, addr: str) -> list[str]:
    known = mem["keys"].get(_lookalike_key(addr), set()) - {addr}
    return sorted(known)[:3]


def _finish(chain_id: object, block: object, tx_hash: str, owner: str,
            counterparties: set[str], sigs: list[dict]) -> Optional[dict]:
    if not sigs:
        return None
    best: dict[str, dict] = {}
    for sig in sigs:
        prev = best.get(sig["rule"])
        if prev is None or sig["weight"] > prev["weight"]:
            best[sig["rule"]] = sig
    ordered = list(best.values())
    if len(ordered) == 1 and ordered[0]["rule"] == "VANITY_ZERO_PATTERN":
        return None
    score = min(100, sum(sig["weight"] for sig in ordered))
    rules = {sig["rule"] for sig in ordered}
    finding = {
        "chain_id": chain_id,
        "block": block if isinstance(block, int) and not isinstance(block, bool) else None,
        "tx_hash": tx_hash,
        "subjects": [owner],
        "counterparties": sorted(a for a in counterparties if a),
        "signals": ordered,
        "score": score,
        "signal_strength": _strength(score),
        "stage": _stage(rules),
        "disclaimer": DISCLAIMER,
    }
    finding["revokes"] = prepare_revokes(finding, chain_id)
    return finding


def _push(sigs: list[dict], rule: str, evidence: dict, weight: Optional[int] = None) -> None:
    sigs.append({
        "rule": rule,
        "weight": WEIGHTS[rule] if weight is None else weight,
        "evidence": evidence,
    })


def _split_share(amounts: dict[str, int]) -> Optional[str]:
    if len(amounts) != 2:
        return None
    minor, major = sorted(amounts.values())
    total = minor + major
    if total == 0:
        return None
    if minor * 100 < total * 10 or minor * 100 > total * 35:
        return None
    return f"{minor / total:.3f}"


def _quiet(tx: dict, spenders: set[str], pulls: list[dict]) -> bool:
    to = tx.get("to") or ""
    if to in ALLOW_SPENDERS:
        return True
    if any(spender in ALLOW_SPENDERS for spender in spenders):
        return True
    if pulls and all(row["to"] in RECIPIENT_QUIET for row in pulls):
        return True
    return False


def _sender_known(tx: dict, owner: str) -> Optional[bool]:
    """True when this address sent the tx, False when someone else did, None when unreadable."""
    if tx.get("from_status") != "read":
        return None
    return tx.get("from") == owner


def _remember(mem: dict, addr: str) -> None:
    if not addr or addr == ZERO:
        return
    mem["trusted"].add(addr)
    mem["keys"].setdefault(_lookalike_key(addr), set()).add(addr)


def _taint(mem: dict, addr: str, tx: dict, why: str) -> None:
    if not addr or addr == ZERO or addr in mem["trusted"] or addr in mem["tainted"]:
        return
    if addr in ALLOW_SPENDERS:
        return
    block = tx.get("block")
    mem["tainted"][addr] = {
        "block": block if isinstance(block, int) and not isinstance(block, bool) else None,
        "tx": tx.get("hash") or "",
        "why": why,
    }


def _score_tx(window: dict, tx: dict, mem: dict) -> Optional[dict]:
    owner = window["address"]
    sigs: list[dict] = []
    cps: set[str] = set()
    sent = _sender_known(tx, owner)
    self_exec = _self_exec(window, tx, owner)
    codes_raw = window.get("codes")
    codes = codes_raw if isinstance(codes_raw, dict) else {}

    permit_rows = []
    family: list[dict] = []
    family_cps: set[str] = set()
    for row in tx.get("permit2") or []:
        if row.get("owner") != owner:
            continue
        spender = row.get("spender") or ""
        permit_rows.append(row)
        if sent is not False or self_exec or spender in ALLOW_SPENDERS:
            continue
        _push(family, "PERMIT_SUBMITTED_BY_THIRD_PARTY", {
            "standard": "Permit2",
            "owner": owner,
            "token": row.get("token"),
            "spender": spender,
            "amount": str(row.get("amount")),
        })
        family_cps.add(spender)
        amount = row.get("amount")
        if isinstance(amount, int) and not isinstance(amount, bool) and amount >= _UNLIMITED_P2:
            _push(family, "UNLIMITED_AMOUNT", {"token": row.get("token"), "standard": "Permit2"})

    erc_permits = []
    for row in tx.get("approvals") or []:
        if row.get("owner") != owner:
            continue
        value = row.get("value")
        spender = row.get("spender") or ""
        if not isinstance(value, int) or isinstance(value, bool) or value == 0:
            continue
        if sent is True and spender not in ALLOW_SPENDERS and _eoa(codes, spender) is True:
            _push(sigs, "APPROVAL_TO_EOA", {
                "owner": owner, "spender": spender, "token": row.get("token"),
                "value": str(value), "standard": "ERC-20",
            })
            cps.add(spender)
            if value >= _UNLIMITED_ERC20:
                _push(sigs, "UNLIMITED_AMOUNT", {"token": row.get("token"), "standard": "ERC-20"})
        if sent is False and not self_exec and spender not in ALLOW_SPENDERS:
            erc_permits.append(row)

    for row in tx.get("permit2") or []:
        if row.get("owner") != owner or sent is not True:
            continue
        spender = row.get("spender") or ""
        if spender in ALLOW_SPENDERS or _eoa(codes, spender) is not True:
            continue
        _push(sigs, "APPROVAL_TO_EOA", {
            "owner": owner, "spender": spender, "token": row.get("token"), "standard": "Permit2",
        })
        cps.add(spender)

    for row in tx.get("approval_for_all") or []:
        if row.get("owner") != owner or sent is not True or not row.get("approved"):
            continue
        operator = row.get("operator") or ""
        if operator in ALLOW_SPENDERS:
            continue
        eoa = _eoa(codes, operator)
        weight = 25 if eoa is True else WEIGHTS["APPROVAL_FOR_ALL_TO_UNKNOWN"]
        _push(sigs, "APPROVAL_FOR_ALL_TO_UNKNOWN", {
            "owner": owner, "operator": operator, "token": row.get("token"),
            "operator_is_eoa": eoa is True,
            "code_status": _code_status(codes, operator),
        }, weight=weight)
        cps.add(operator)

    selector = tx.get("selector") or "0x"
    pulls: list[dict] = []
    for tr in tx.get("transfers") or []:
        if tr.get("from") != owner or tr.get("to") in (owner, ZERO, ""):
            continue
        if sent is None:
            continue
        if sent is True:
            value = tr.get("value")
            if tr.get("nft") or not isinstance(value, int) or isinstance(value, bool):
                continue
            dest = tr["to"]
            if not _dust(value, tr.get("decimals")) and dest in mem["tainted"] and dest not in mem["trusted"]:
                _push(sigs, "POISONED_RECIPIENT_SEND", {
                    "recipient": dest,
                    "first_contact": mem["tainted"].get(dest),
                })
                cps.add(dest)
                mimics = _mimics(mem, dest)
                if mimics:
                    _push(sigs, "LOOKALIKE_OF_PAST_RECIPIENT", {"mimics": mimics})
            continue
        if self_exec:
            continue
        dest = tr["to"]
        value = tr.get("value")
        if tr.get("nft"):
            pulls.append(tr)
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        if dest in RECIPIENT_QUIET and value != 0 and not _dust(value, tr.get("decimals")):
            pulls.append(tr)
            continue
        if value == 0:
            _push(sigs, "ZERO_VALUE_PULL", {
                "victim": owner, "counterparty": dest, "token": tr.get("token"),
            })
            cps.add(dest)
            mimics = _mimics(mem, dest)
            if mimics:
                _push(sigs, "LOOKALIKE_OF_PAST_RECIPIENT", {"mimics": mimics, "counterparty": dest})
            _taint(mem, dest, tx, "zero-value transfer recorded from this address")
            continue
        if _dust(value, tr.get("decimals")):
            _push(sigs, "OUTBOUND_NOT_SENT_BY_YOU", {
                "counterparty": dest, "token": tr.get("token"), "value_raw": str(value),
            })
            cps.add(dest)
            mimics = _mimics(mem, dest)
            if mimics:
                _push(sigs, "LOOKALIKE_OF_PAST_RECIPIENT", {"mimics": mimics, "counterparty": dest})
            _taint(mem, dest, tx, "dust transfer recorded from this address")
            continue
        pulls.append(tr)

    spenders = {row.get("spender") or "" for row in permit_rows}
    spenders |= {row.get("spender") or "" for row in erc_permits}
    if (tx.get("to") or "") == PERMIT2 and selector in _P2_SIG and pulls and sent is False and not self_exec:
        _push(family, "PERMIT_SUBMITTED_BY_THIRD_PARTY", {
            "standard": "Permit2-SignatureTransfer",
            "owner": owner,
            "spender": tx.get("to"),
            "token": pulls[0].get("token"),
        })
        family_cps.add(tx.get("to") or "")

    quiet = _quiet(tx, spenders, pulls)
    if not quiet:
        sigs.extend(family)
        cps.update(family_cps)
    if pulls and not quiet:
        recipients = sorted({row["to"] for row in pulls})
        assets = sorted({row.get("token") or "" for row in pulls})
        _push(sigs, "THIRD_PARTY_PULL", {
            "owner": owner,
            "n_transfers": len(pulls),
            "assets": assets[:8],
            "recipients": recipients[:8],
        })
        cps.update(recipients)
        matched_erc = [row for row in erc_permits if row.get("spender") not in ALLOW_SPENDERS]
        permit_here = any(row.get("owner") == owner for row in permit_rows) or matched_erc or (
            (tx.get("to") or "") == PERMIT2 and selector in _P2_SIG
        )
        if permit_here and sent is False:
            _push(sigs, "PERMIT_AND_PULL_SAME_TX", {"owner": owner})
            for row in matched_erc:
                _push(sigs, "PERMIT_SUBMITTED_BY_THIRD_PARTY", {
                    "standard": "ERC-2612",
                    "owner": owner,
                    "spender": row.get("spender"),
                    "token": row.get("token"),
                    "value": str(row.get("value")),
                })
                cps.add(row.get("spender") or "")
                value = row.get("value")
                if isinstance(value, int) and not isinstance(value, bool) and value >= _UNLIMITED_ERC20:
                    _push(sigs, "UNLIMITED_AMOUNT", {"token": row.get("token"), "standard": "ERC-20"})
                break
        tokens = {row.get("token") for row in pulls if not row.get("nft")}
        nfts = [row for row in pulls if row.get("nft")]
        if len(tokens) >= 2 or len(nfts) >= 3:
            _push(sigs, "MULTI_ASSET_SWEEP", {
                "owner": owner, "erc20s": len(tokens), "nfts": len(nfts),
            })
        by_token: dict[str, dict[str, int]] = {}
        for row in pulls:
            if row.get("nft"):
                continue
            value = row.get("value")
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            token = row.get("token") or ""
            by_token.setdefault(token, {})
            by_token[token][row["to"]] = by_token[token].get(row["to"], 0) + value
        for token, amounts in by_token.items():
            share = _split_share(amounts)
            if share is None:
                continue
            _push(sigs, "SPLIT_PAYOUT_PATTERN", {
                "owner": owner, "token": token, "minor_share": share,
                "recipients": sorted(amounts),
            })
            break

    for auth in tx.get("authorizations") or []:
        delegate = auth.get("delegate") or ""
        authority = auth.get("authority")
        if authority not in (None, owner):
            continue
        if authority is None:
            continue
        _delegation(sigs, cps, owner, delegate, "authorization")

    spoofed_from = set()
    spoofed_to = set()
    for tr in tx.get("transfers") or []:
        if tr.get("nft"):
            continue
        if sent is None or tr.get("from") in (tx.get("from"), ZERO, ""):
            continue
        spoofed_from.add(tr.get("from"))
        spoofed_to.add(tr.get("to"))
    if len(spoofed_from) >= 5 and len(spoofed_to) >= 5 and owner in spoofed_from:
        _push(sigs, "SPOOFED_TRANSFER_SPRAY", {
            "distinct_from": len(spoofed_from),
            "distinct_to": len(spoofed_to),
        })

    for tr in tx.get("transfers") or []:
        if tr.get("to") != owner or tr.get("from") in (owner, ZERO, ""):
            continue
        src = tr["from"]
        if tr.get("nft"):
            continue
        value = tr.get("value")
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        mimics = _mimics(mem, src)
        dust = _dust(value, tr.get("decimals"))
        if dust and mimics and src not in ALLOW_SPENDERS and src not in mem["trusted"]:
            _push(sigs, "DUST_FROM_LOOKALIKE", {
                "counterparty": src, "token": tr.get("token"), "value_raw": str(value),
            })
            _push(sigs, "LOOKALIKE_OF_PAST_RECIPIENT", {"mimics": mimics, "counterparty": src})
            cps.add(src)
            _taint(mem, src, tx, "dust inbound from a lookalike")
        elif not dust and mimics and src not in mem["trusted"]:
            _push(sigs, "LOOKALIKE_INBOUND", {
                "counterparty": src, "mimics": mimics, "token": tr.get("token"),
                "value_raw": str(value),
            })
            cps.add(src)

    if sigs:
        for addr in set(cps) | {tx.get("from") or "", tx.get("to") or ""}:
            if addr and _vanity(addr) and addr not in ALLOW_SPENDERS:
                _push(sigs, "VANITY_ZERO_PATTERN", {"address": addr})
                break

    for tr in tx.get("transfers") or []:
        if tr.get("nft"):
            continue
        value = tr.get("value")
        if not isinstance(value, int) or isinstance(value, bool) or _dust(value, tr.get("decimals")):
            continue
        if sent is True and tr.get("from") == owner:
            _remember(mem, tr.get("to") or "")
        if tr.get("to") == owner:
            _remember(mem, tr.get("from") or "")

    return _finish(window.get("chain_id"), tx.get("block"), tx.get("hash") or "", owner, cps, sigs)


def _delegation(sigs: list[dict], cps: set[str], owner: str, delegate: str, source: str) -> None:
    if not delegate or delegate == ZERO or delegate in GOOD_7702:
        return
    if delegate in BAD_7702:
        _push(sigs, "DELEGATION_TO_LISTED_SWEEPER", {
            "authority": owner, "delegate": delegate, "source": source,
        })
    else:
        _push(sigs, "DELEGATION_TO_UNKNOWN_CONTRACT", {
            "authority": owner, "delegate": delegate, "source": source,
        })
    cps.add(delegate)


def _native(window: dict) -> list[dict]:
    internal_raw = window.get("internal")
    internal = internal_raw if isinstance(internal_raw, dict) else {"status": "off"}
    if internal.get("status") != "read":
        return []
    owner = window["address"]
    rows_raw = internal.get("transfers")
    rows = rows_raw if isinstance(rows_raw, list) else []
    findings = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("from") != owner:
            continue
        value = row.get("value")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            continue
        dest = row.get("to") or ""
        if dest in RECIPIENT_QUIET or dest in WRAPPED_NATIVE or dest in (owner, ZERO, ""):
            continue
        sigs: list[dict] = []
        _push(sigs, "NATIVE_ETH_INTERNAL_PULL", {
            "to": dest, "value_raw": str(value),
        })
        finding = _finish(
            window.get("chain_id"), row.get("block"), row.get("tx_hash") or "",
            owner, {dest}, sigs,
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _as_decoded(window: dict) -> dict:
    txs = window.get("transactions")
    if isinstance(txs, list) and window.get("code_status") in ("read", "unreadable", "absent"):
        if not txs or (isinstance(txs[0], dict) and "from_status" in txs[0]):
            if "codes" not in window:
                window = dict(window)
                window["codes"] = {}
            return window
    return decode_window(window)


def score_window(window: dict, memory: object = None) -> dict:
    """Findings for one address. ``findings`` is a list when the window was scored.

    ``memory_status`` is ``unreadable`` when ``memory`` is None, and the
    returned memory is not a book to write back over the one that failed.
    """
    decoded = _as_decoded(window)
    memory_status, mem = _load_mem({} if memory is None else memory)
    if memory is None:
        memory_status = "unreadable"
    owner = decoded.get("address")
    findings: list[dict] = []
    reported: set[str] = set()
    if isinstance(owner, str):
        txs = list(decoded.get("transactions") or [])
        txs.sort(key=lambda tx: (
            0 if isinstance(tx.get("block"), int) and not isinstance(tx.get("block"), bool) else 1,
            tx.get("block") if isinstance(tx.get("block"), int) else 0,
        ))
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            finding = _score_tx(decoded, tx, mem)
            if finding is not None:
                findings.append(finding)
                for sig in finding["signals"]:
                    ev_raw = sig.get("evidence")
                    ev = ev_raw if isinstance(ev_raw, dict) else {}
                    delegate = ev.get("delegate")
                    if isinstance(delegate, str):
                        reported.add(delegate)
        if decoded.get("code_status") == "read":
            delegate = _delegate_of(decoded.get("code"))
            if delegate and delegate not in reported:
                sigs: list[dict] = []
                cps: set[str] = set()
                _delegation(sigs, cps, owner, delegate, "code")
                finding = _finish(decoded.get("chain_id"), None, "", owner, cps, sigs)
                if finding is not None:
                    findings.append(finding)
        findings.extend(_native(decoded))
    internal_raw = decoded.get("internal")
    internal = internal_raw if isinstance(internal_raw, dict) else {"status": "off"}
    tx_raw = decoded.get("transactions")
    txs_for_gap = tx_raw if isinstance(tx_raw, list) else []
    gaps = any(isinstance(tx, dict) and tx.get("transfer_gap") for tx in txs_for_gap)
    if internal.get("transfer_gap") is True:
        gaps = True
    return {
        "findings": findings,
        "memory": _dump_mem(mem),
        "memory_status": memory_status,
        "disclaimer": DISCLAIMER,
        "code_status": decoded.get("code_status"),
        "internal_status": internal.get("status"),
        "internal_reason": internal.get("reason"),
        "transfer_gap": gaps,
    }


def score_request(payload: dict) -> dict:
    """Score a posted window. The flag is not consulted: the route already gated it.

    A refused chain yields ``findings: None``, which is not an empty clear.
    """
    if not isinstance(payload, dict):
        return {
            "chain_allowed": False,
            "findings": None,
            "disclaimer": DISCLAIMER,
            "sentence": _ADDRESS_SENTENCE,
        }
    decoded = decode_window(payload)
    chain_id = decoded.get("chain_id")
    base = {
        "chain_id": chain_id,
        "chain_label": chain_label(chain_id) if isinstance(chain_id, int) else None,
        "disclaimer": DISCLAIMER,
        "code_status": decoded.get("code_status"),
    }
    internal_raw = decoded.get("internal")
    internal = internal_raw if isinstance(internal_raw, dict) else {"status": "off"}
    status_raw = internal.get("status")
    reason_raw = internal.get("reason")
    base["internal"] = {
        "status": status_raw if isinstance(status_raw, str) else "off",
        "reason": reason_raw if isinstance(reason_raw, str) else None,
    }
    if not chain_allowed(chain_id):
        base.update({
            "chain_allowed": False,
            "findings": None,
            "sentence": _CHAIN_SENTENCE,
        })
        return base
    if decoded.get("address") is None:
        base.update({
            "chain_allowed": True,
            "findings": None,
            "sentence": _ADDRESS_SENTENCE,
        })
        return base
    scored = score_window(decoded, payload.get("memory", {}))
    base.update({
        "chain_allowed": True,
        "findings": scored["findings"],
        "memory": scored["memory"],
        "memory_status": scored["memory_status"],
        "transfer_gap": scored["transfer_gap"],
    })
    return base


_MISSING = object()


def consider(window: dict, env: Optional[dict] = None, memory: object = _MISSING) -> dict:
    """The hook a later Claw Watch poll can call.

    While the flag is off this does not score. ``findings`` is None, not an
    empty list: off is not the same as no signals.
    """
    if not flag_on(env):
        return {
            "enabled": False,
            "scored": False,
            "findings": None,
            "memory": None,
            "disclaimer": DISCLAIMER,
        }
    decoded = _as_decoded(window if isinstance(window, dict) else {})
    if not chain_allowed(decoded.get("chain_id")) or decoded.get("address") is None:
        return {
            "enabled": True,
            "scored": False,
            "findings": None,
            "memory": None,
            "disclaimer": DISCLAIMER,
            "reason": "chain" if decoded.get("address") is not None else "address",
        }
    supplied = {} if memory is _MISSING else memory
    scored = score_window(decoded, supplied)
    return {
        "enabled": True,
        "scored": True,
        "findings": scored["findings"],
        "memory": scored["memory"] if scored["memory_status"] == "read" else None,
        "memory_status": scored["memory_status"],
        "disclaimer": DISCLAIMER,
        "code_status": scored["code_status"],
        "internal_status": scored["internal_status"],
        "transfer_gap": scored["transfer_gap"],
    }
