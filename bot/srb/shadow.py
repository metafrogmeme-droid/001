"""Shadow-mode sandwich restitution.

Detect a same-pool front and back around a harmed swap, decide whether a
bond would cover it, and record that decision. Nothing is transferred.

Sources are a recorded fixture, or a devnet, testnet, or mainnet label
read through an injected transport. A mainnet read is observation only:
no key, no signature, and no transfer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bot.srb.flag import flag_on

_WIDE_SLOTS = 3
_BATCH_SECONDS = 30
_CLAIM_WINDOW_SECONDS = 72 * 3600
_DISPUTE_WINDOW_SECONDS = 72 * 3600
_WITHDRAWAL_DELAY_SECONDS = 10 * 24 * 3600
_METHODOLOGY_TIMELOCK_SECONDS = 7 * 24 * 3600

# 10 days covers the claim window and the dispute window.
assert _WITHDRAWAL_DELAY_SECONDS >= _CLAIM_WINDOW_SECONDS + _DISPUTE_WINDOW_SECONDS

_METHODOLOGY = (
    "srb-detector-v0\n"
    "same pool\n"
    "front before the swap and back after, each within 3 slots\n"
    "front and back share a signer or a funding or deposit cluster\n"
    "front buys the same pair the swap buys; back sells it back\n"
    "a missing quote is not a loss\n"
    "a quote at or above the fill is not a loss\n"
)

_SENTENCE = (
    "A same-pool front and back landed around a swap inside the published "
    "slot window. This is a public signal. Nothing is paid."
)

_DEMO = Path(__file__).resolve().parent / "fixtures" / "demo_window.json"


class ShadowError(Exception):
    """A shadow run that did not produce a record."""

    pass


class ClusterRefused(ShadowError):
    """The source is not devnet, testnet, or a recorded fixture."""

    pass


class FixtureUnreadable(ShadowError):
    """A recorded window that cannot be read. Not an empty result."""

    pass


def demo_report() -> dict:
    """The packaged recorded window, as the page and the operator command show it."""
    return build_report(_load_demo())


def build_report(
    fixture: dict,
    *,
    transport: Optional[Callable[[str], Any]] = None,
    ledger_path: Optional[str] = None,
) -> dict:
    """Run one recorded window. Returns a public view and an admin view.

    ``transport`` is called for a devnet, testnet, or mainnet source.
    Mainnet is observation only and does not sign.
    """
    body = _load_body(fixture, transport)
    kind = _classify(str(body.get("cluster") if body.get("cluster") is not None else ""))
    now = _require_int(body, "now")
    swaps = _parse_swaps(body.get("swaps"))
    linkage = _linkage(body.get("linkage"))
    wide = body.get("wide_slots", _WIDE_SLOTS)
    if not isinstance(wide, int) or isinstance(wide, bool) or wide < 0:
        raise FixtureUnreadable("wide_slots")
    active = _active_hash(body.get("methodology_proposal"), now)
    found = _detect(swaps, linkage, wide)
    bond = _bond(body.get("provider"), now)
    release = _maybe_release(bond, body.get("release") is True, now)
    bond = release["bond"]
    outcomes = [
        _evaluate(signal, body, swaps, linkage, bond, now, active)
        for signal in found
    ]
    _apply_epoch(outcomes, bond)
    rows = [_ledger_row(item) for item in outcomes]
    _write_ledger(ledger_path, rows)
    return {
        "public": _public_view(kind, bond, outcomes),
        "admin": _admin_view(kind, bond, outcomes, rows, release["state"]),
    }


def _load_demo() -> dict:
    try:
        loaded = json.loads(_DEMO.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FixtureUnreadable("demo") from exc
    if not isinstance(loaded, dict):
        raise FixtureUnreadable("demo")
    return loaded


def _load_body(fixture: dict, transport: Optional[Callable[[str], Any]]) -> dict:
    if not isinstance(fixture, dict):
        raise FixtureUnreadable("fixture")
    source = fixture.get("cluster")
    if not isinstance(source, str):
        raise FixtureUnreadable("cluster")
    kind = _classify(source)
    if fixture.get("fetch") is True:
        if kind not in {"devnet", "testnet", "mainnet"}:
            raise ClusterRefused("fetch")
        if transport is None:
            raise ClusterRefused("no transport")
        loaded = transport(source)
        if not isinstance(loaded, dict):
            raise FixtureUnreadable("transport")
        inner = loaded.get("cluster")
        if not isinstance(inner, str):
            raise FixtureUnreadable("cluster")
        inner_kind = _classify(inner)
        # A mainnet label on either side stays observation. It must not
        # fall through into the devnet signing path.
        if kind == "mainnet" or inner_kind == "mainnet":
            loaded = dict(loaded)
            loaded["cluster"] = "mainnet"
        return loaded
    return fixture


def _classify(source: str) -> str:
    text = source.strip().lower()
    if not text:
        raise ClusterRefused("empty")
    if "mainnet" in text:
        return "mainnet"
    if text in {"fixture", "recorded"} or text.startswith("fixture:"):
        return "fixture"
    if "devnet" in text:
        return "devnet"
    if "testnet" in text:
        return "testnet"
    raise ClusterRefused("unsupported")


def _require_int(body: dict, key: str) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FixtureUnreadable(key)
    return value


def _parse_swaps(raw: Any) -> list:
    if not isinstance(raw, list) or not raw:
        raise FixtureUnreadable("swaps")
    out = []
    for row in raw:
        if not isinstance(row, dict):
            raise FixtureUnreadable("swap")
        quoted = row.get("quoted_out")
        if quoted is not None and (isinstance(quoted, bool) or not isinstance(quoted, int)):
            raise FixtureUnreadable("quoted_out")
        amount_in = _require_int(row, "amount_in")
        amount_out = _require_int(row, "amount_out")
        if amount_in < 0 or amount_out < 0:
            raise FixtureUnreadable("amount")
        if isinstance(quoted, int) and quoted < 0:
            raise FixtureUnreadable("quoted_out")
        item = {
            "sig": _text(row, "sig"),
            "slot": _require_int(row, "slot"),
            "index": _require_int(row, "index"),
            "signer": _text(row, "signer"),
            "pool": _text(row, "pool"),
            "mint_in": _text(row, "mint_in"),
            "mint_out": _text(row, "mint_out"),
            "amount_in": amount_in,
            "amount_out": amount_out,
            "quoted_out": quoted,
        }
        out.append(item)
    return out


def _text(row: dict, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FixtureUnreadable(key)
    return value


def _linkage(raw: Any) -> dict:
    if raw is None:
        return {"funding": [], "cex": []}
    if not isinstance(raw, dict):
        raise FixtureUnreadable("linkage")
    funding = raw.get("funding", [])
    cex = raw.get("cex", [])
    if not isinstance(funding, list) or not isinstance(cex, list):
        raise FixtureUnreadable("linkage")
    return {"funding": funding, "cex": cex}


def _methodology_hash() -> str:
    return hashlib.sha256(_METHODOLOGY.encode("utf-8")).hexdigest()


def _active_hash(proposal: Any, now: int) -> str:
    pinned = _methodology_hash()
    if proposal is None:
        return pinned
    if not isinstance(proposal, dict):
        raise FixtureUnreadable("methodology_proposal")
    effective = proposal.get("effective_at")
    proposed = proposal.get("methodology_hash")
    if not isinstance(effective, int) or isinstance(effective, bool):
        return pinned
    if not isinstance(proposed, str) or not proposed:
        return pinned
    if now >= effective:
        return proposed
    return pinned


def _detect(swaps: list, linkage: dict, wide: int) -> list:
    ordered = sorted(swaps, key=lambda row: (row["slot"], row["index"], row["sig"]))
    found = []
    for index, victim in enumerate(ordered):
        quoted = victim["quoted_out"]
        if quoted is None or quoted <= 0:
            continue
        if victim["amount_out"] >= quoted:
            continue
        front = _front(ordered, index, victim, wide)
        if front is None:
            continue
        back = _back(ordered, index, victim, front, wide)
        if back is None:
            continue
        if not _paired(front, back, linkage):
            continue
        loss = quoted - victim["amount_out"]
        found.append({
            "victim": victim,
            "front": front,
            "back": back,
            "loss_base_units": loss,
            "impact_bps": (loss * 10_000) // quoted,
            "slot_lo": min(front["slot"], victim["slot"], back["slot"]),
            "slot_hi": max(front["slot"], victim["slot"], back["slot"]),
            "wide": front["slot"] != victim["slot"] or back["slot"] != victim["slot"],
        })
    return found


def _before(left: dict, right: dict) -> bool:
    return (left["slot"], left["index"]) < (right["slot"], right["index"])


def _front(ordered: list, index: int, victim: dict, wide: int) -> Optional[dict]:
    best = None
    for cursor in range(index):
        other = ordered[cursor]
        if not _before(other, victim):
            continue
        if other["pool"] != victim["pool"]:
            continue
        if victim["slot"] - other["slot"] > wide:
            continue
        if other["mint_in"] != victim["mint_in"] or other["mint_out"] != victim["mint_out"]:
            continue
        best = other
    return best


def _back(ordered: list, index: int, victim: dict, front: dict, wide: int) -> Optional[dict]:
    for cursor in range(index + 1, len(ordered)):
        other = ordered[cursor]
        if not isinstance(other, dict):
            continue
        if not _before(victim, other):
            continue
        if other["slot"] - victim["slot"] > wide:
            break
        if other["pool"] != victim["pool"]:
            continue
        if other["mint_in"] != front["mint_out"] or other["mint_out"] != front["mint_in"]:
            continue
        return other
    return None


def _paired(front: dict, back: dict, linkage: dict) -> bool:
    if front["signer"] == back["signer"]:
        return True
    if _funding_together(front["signer"], back["signer"], linkage):
        return True
    return _cex_together(front["signer"], back["signer"], linkage)


def _funding_together(left: str, right: str, linkage: dict) -> bool:
    if left == right:
        return False
    parent: dict = {}

    def find(name: str) -> str:
        parent.setdefault(name, name)
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for edge in linkage["funding"]:
        if not isinstance(edge, list) or len(edge) != 2:
            raise FixtureUnreadable("funding")
        if not isinstance(edge[0], str) or not isinstance(edge[1], str):
            raise FixtureUnreadable("funding")
        union(edge[0], edge[1])
    if left not in parent or right not in parent:
        return False
    return find(left) == find(right)


def _cex_together(left: str, right: str, linkage: dict) -> bool:
    if left == right:
        return False
    for group in linkage["cex"]:
        if not isinstance(group, list):
            raise FixtureUnreadable("cex")
        names = [name for name in group if isinstance(name, str)]
        if len(names) != len(group):
            raise FixtureUnreadable("cex")
        if left in names and right in names:
            return True
    return False


def _bond(raw: Any, now: int) -> dict:
    if not isinstance(raw, dict):
        raise FixtureUnreadable("provider")
    requested = raw.get("unbond_requested_at")
    if requested is not None and (isinstance(requested, bool) or not isinstance(requested, int)):
        raise FixtureUnreadable("unbond_requested_at")
    amount = raw.get("amount_base_units")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise FixtureUnreadable("amount_base_units")
    return {
        "provider_id": _text(raw, "provider_id"),
        "policy_hash": _text(raw, "policy_hash"),
        "bonded": raw.get("bonded") is True,
        "amount_base_units": amount,
        "cover_cap_per_tx_base_units": _cap(raw, "cover_cap_per_tx_base_units"),
        "cover_cap_per_epoch_base_units": _cap(raw, "cover_cap_per_epoch_base_units"),
        "wallet_cap_base_units": _cap(raw, "wallet_cap_base_units"),
        "unbond_requested_at": requested,
        "withdrawable_at": None if requested is None else requested + _WITHDRAWAL_DELAY_SECONDS,
        "observed_at": now,
    }


def _cap(raw: dict, key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FixtureUnreadable(key)
    return value


def _maybe_release(bond: dict, requested: bool, now: int) -> dict:
    if not requested:
        return {"bond": bond, "state": "not_requested"}
    if bond["unbond_requested_at"] is None or bond["withdrawable_at"] is None:
        return {"bond": bond, "state": "held"}
    if now < bond["withdrawable_at"]:
        return {"bond": bond, "state": "held"}
    released = dict(bond)
    released["bonded"] = False
    released["released"] = True
    return {"bond": released, "state": "released"}


def _signer_seed(cluster_kind: str) -> bytes:
    # Derived after classification. Mainnet is refused before any key bytes.
    kind = _classify(cluster_kind)
    if kind == "mainnet":
        raise ClusterRefused("sign")
    return hashlib.sha256(f"srb-shadow-v0|{kind}".encode("utf-8")).digest()


def _sign(cluster_kind: str, message: bytes) -> bytes:
    key = Ed25519PrivateKey.from_private_bytes(_signer_seed(cluster_kind))
    return key.sign(message)


def _verify(cluster_kind: str, message: bytes, signature: bytes) -> bool:
    key = Ed25519PrivateKey.from_private_bytes(_signer_seed(cluster_kind))
    try:
        key.public_key().verify(signature, message)
    except InvalidSignature:
        return False
    return True


def _receipt_message(cluster_kind: str, tx_sig: str, recv_ts: int, provider_id: str,
                     policy_hash: str, max_slippage_bps: int) -> bytes:
    payload = {
        "cluster": cluster_kind,
        "maxSlippageBps": max_slippage_bps,
        "providerId": provider_id,
        "recvTs": recv_ts,
        "routePolicyHash": policy_hash,
        "txSig": tx_sig,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _evaluate(signal: dict, body: dict, swaps: list, linkage: dict, bond: dict,
              now: int, active_hash: str) -> dict:
    victim = signal["victim"]
    front = signal["front"]
    back = signal["back"]
    receipt = body.get("receipt")
    if not isinstance(receipt, dict):
        raise FixtureUnreadable("receipt")
    recv_ts = _require_int(receipt, "recv_ts")
    if "max_slippage_bps" not in receipt:
        raise FixtureUnreadable("max_slippage_bps")
    slippage = _cap(receipt, "max_slippage_bps")
    kind = _classify(str(body.get("cluster")))
    message = _receipt_message(
        kind, victim["sig"], recv_ts, bond["provider_id"], bond["policy_hash"], slippage,
    )
    if kind == "mainnet":
        digest = _observe_mainnet(message)
    else:
        signature = _sign(kind, message)
        if not _verify(kind, message, signature):
            raise ShadowError("receipt")
        digest = hashlib.sha256(message).hexdigest()
    committed = _committed(digest, recv_ts, now, receipt.get("hold_back") is True)
    base = {
        "signal": signal,
        "signal_id": hashlib.sha256(victim["sig"].encode("utf-8")).hexdigest()[:16],
        "loss_base_units": signal["loss_base_units"],
        "would_record_base_units": None,
        "impact_bps": signal["impact_bps"],
        "restitution_bps": None,
    }
    if not committed:
        return {**base, "decision": "withheld", "reason": "uncommitted"}
    claimed = body.get("methodology_hash")
    if claimed is not None:
        if not isinstance(claimed, str):
            raise FixtureUnreadable("methodology_hash")
        if claimed != active_hash:
            return {**base, "decision": "withheld", "reason": "methodology"}
    link = _self_link(victim, front, back, linkage)
    if link is not None:
        return {**base, "decision": "withheld", "reason": link}
    profit = _profit_recipient(front, back, body.get("transfers"), linkage)
    if profit is None:
        return {**base, "decision": "withheld", "reason": "profit_not_shown"}
    if profit == _party(victim["signer"], linkage):
        return {**base, "decision": "withheld", "reason": "profit_to_self"}
    net = _net_loss(swaps, victim["signer"], signal["slot_lo"], signal["slot_hi"])
    if net is None:
        return {**base, "decision": "withheld", "reason": "net_unreadable"}
    if net <= 0:
        return {**base, "decision": "withheld", "reason": "net_loss"}
    dispute = _dispute(body.get("dispute"), swaps, linkage, body.get("wide_slots", _WIDE_SLOTS),
                       recv_ts, now)
    if dispute == "upheld":
        return {**base, "decision": "withheld", "reason": "disputed"}
    if now < recv_ts + _CLAIM_WINDOW_SECONDS:
        return {**base, "decision": "pending", "reason": "window"}
    quoted = victim["quoted_out"]
    slip_units = (quoted * slippage) // 10_000
    capped = min(
        net,
        slip_units,
        bond["cover_cap_per_tx_base_units"],
        bond["wallet_cap_base_units"],
    )
    if capped <= 0:
        return {**base, "decision": "withheld", "reason": "cap"}
    bps = (capped * 10_000) // quoted
    return {
        **base,
        "decision": "would_record",
        "reason": "would_record",
        "would_record_base_units": capped,
        "restitution_bps": bps,
    }


def _batch_root(members: list) -> str:
    payload = "\n".join(sorted(members)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _committed(digest: str, recv_ts: int, now: int, hold_back: bool) -> bool:
    """A receipt counts only after its batch has closed and it is in the root.

    Held-back receipts stay out of the root even when the signature is valid.
    """
    if hold_back or now - recv_ts < _BATCH_SECONDS:
        return False
    members = [digest]
    root = _batch_root(members)
    return digest in members and _batch_root(members) == root


def _self_link(victim: dict, front: dict, back: dict, linkage: dict) -> Optional[str]:
    signers = {front["signer"], back["signer"]}
    if victim["signer"] in signers:
        return "self_signer"
    for other in signers:
        if _funding_together(victim["signer"], other, linkage):
            return "self_funding"
    for other in signers:
        if _cex_together(victim["signer"], other, linkage):
            return "self_cex"
    return None


def _party(name: str, linkage: dict) -> str:
    """The cluster a gain accrues to: funding first, then a deposit group."""
    parent: dict = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for edge in linkage["funding"]:
        if isinstance(edge, list) and len(edge) == 2:
            union(edge[0], edge[1])
    for group in linkage["cex"]:
        if isinstance(group, list) and group:
            head = group[0]
            for other in group[1:]:
                if isinstance(head, str) and isinstance(other, str):
                    union(head, other)
    return find(name)


def _profit_recipient(front: dict, back: dict, transfers: Any, linkage: dict) -> Optional[str]:
    if back["mint_out"] != front["mint_in"]:
        return None
    profit = back["amount_out"] - front["amount_in"]
    if profit <= 0:
        return None
    recipient = _party(back["signer"], linkage)
    if transfers is None:
        return recipient
    if not isinstance(transfers, list):
        raise FixtureUnreadable("transfers")
    for row in transfers:
        if not isinstance(row, dict):
            raise FixtureUnreadable("transfers")
        if row.get("mint") != front["mint_in"]:
            continue
        amount = row.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise FixtureUnreadable("transfers")
        if amount < profit:
            continue
        sender = row.get("from")
        dest = row.get("to")
        if not isinstance(sender, str) or not isinstance(dest, str):
            raise FixtureUnreadable("transfers")
        if _party(sender, linkage) == _party(back["signer"], linkage):
            recipient = _party(dest, linkage)
    return recipient


def _net_loss(swaps: list, signer: str, slot_lo: int, slot_hi: int) -> Optional[int]:
    net = 0
    saw = False
    for row in swaps:
        if row["signer"] != signer:
            continue
        if row["slot"] < slot_lo or row["slot"] > slot_hi:
            continue
        quoted = row["quoted_out"]
        if quoted is None:
            return None
        saw = True
        net += quoted - row["amount_out"]
    if not saw:
        return None
    return net


def _dispute(raw: Any, swaps: list, linkage: dict, wide: int, recv_ts: int, now: int) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FixtureUnreadable("dispute")
    if now > recv_ts + _DISPUTE_WINDOW_SECONDS:
        return "late"
    drop = raw.get("drop_sigs")
    if not isinstance(drop, list):
        raise FixtureUnreadable("dispute")
    kept = [row for row in swaps if row["sig"] not in drop]
    if not _detect(kept, linkage, wide):
        return "upheld"
    return "rejected"


def _apply_epoch(outcomes: list, bond: dict) -> None:
    remaining = bond["cover_cap_per_epoch_base_units"]
    for item in outcomes:
        if item["decision"] != "would_record":
            continue
        units = item["would_record_base_units"]
        if units is None:
            continue
        if units > remaining:
            item["would_record_base_units"] = remaining if remaining > 0 else None
            if remaining <= 0:
                item["decision"] = "withheld"
                item["reason"] = "epoch_cap"
                item["restitution_bps"] = None
            else:
                quoted = item["signal"]["victim"]["quoted_out"]
                item["restitution_bps"] = (remaining * 10_000) // quoted
            remaining = 0
        else:
            remaining -= units


def _ledger_row(item: dict) -> dict:
    if item.get("paid") is True:
        raise ShadowError("paid")
    return {
        "mode": "shadow",
        "paid": False,
        "decision": item["decision"],
        "reason": item["reason"],
        "signal_id": item["signal_id"],
        "loss_base_units": item["loss_base_units"],
        "would_record_base_units": item["would_record_base_units"],
    }


def _write_ledger(path: Optional[str], rows: list) -> None:
    if path is None:
        return
    target = Path(path)
    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
    except (OSError, UnicodeError) as exc:
        raise FixtureUnreadable("ledger") from exc
    lines = [existing.rstrip("\n")] if existing.strip() else []
    for row in rows:
        if row.get("paid") is True:
            raise ShadowError("paid")
        lines.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    text = ("\n".join(line for line in lines if line) + "\n") if lines else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


_LAMPORTS_PER_SOL = 1_000_000_000


def _observe_mainnet(message: bytes) -> str:
    """Hash a mainnet receipt. No key, no signature, no transfer."""
    return hashlib.sha256(message).hexdigest()


def issue_claim_token(cluster: str, signal_id: str) -> dict:
    """A subrogation claim token. Devnet and testnet only.

    Mainnet is refused before a token id is built. The result is a shadow
    record, not a mint and not a key.
    """
    kind = _classify(cluster)
    if kind not in {"devnet", "testnet"}:
        raise ClusterRefused("claim_token")
    if not isinstance(signal_id, str) or not signal_id.strip():
        raise FixtureUnreadable("signal_id")
    token = hashlib.sha256(f"srb-claim-v0|{kind}|{signal_id}".encode("utf-8")).hexdigest()
    return {"kind": "subrogation_claim", "cluster": kind, "signal_id": signal_id, "token": token}


def _sol_text(lamports: int) -> str:
    whole, frac = divmod(lamports, _LAMPORTS_PER_SOL)
    if frac == 0:
        return str(whole)
    return f"{whole}.{frac:09d}".rstrip("0").rstrip(".")


def _admin_label(bond: dict) -> str:
    status = _provider_status(bond)
    if status != "Bonded":
        return status
    amount = bond.get("amount_base_units")
    if isinstance(amount, bool) or not isinstance(amount, int):
        return status
    return f"Bonded ✓ {_sol_text(amount)} SOL"


def _provider_status(bond: dict) -> str:
    if bond.get("released") is True or bond.get("bonded") is not True:
        if bond.get("unbond_requested_at") is not None and bond.get("released") is not True:
            return "Unbonding"
        return "Unbonded"
    if bond.get("unbond_requested_at") is not None:
        return "Unbonding"
    return "Bonded"


def _public_view(kind: str, bond: dict, outcomes: list) -> dict:
    signals = []
    for item in outcomes:
        signal = item["signal"]
        restitution = {"would_record": "recorded", "pending": "pending"}.get(
            item["decision"], "withheld")
        row = {
            "signal_id": item["signal_id"],
            "kind": "public_signal",
            "slot_lo": signal["slot_lo"],
            "slot_hi": signal["slot_hi"],
            "pool_ref": "pool_" + hashlib.sha256(signal["victim"]["pool"].encode("utf-8")).hexdigest()[:8],
            "impact_bps": item["impact_bps"],
            "restitution": restitution,
            "wide": signal["wide"],
            "sentence": _SENTENCE,
        }
        if restitution == "recorded" and item["restitution_bps"] is not None:
            row["restitution_bps"] = item["restitution_bps"]
        signals.append(row)
    return {
        "mode": "shadow",
        "cluster": kind,
        "methodology_hash": _methodology_hash(),
        "pays": False,
        "providers": [{
            "provider_id": bond["provider_id"],
            "status": _provider_status(bond),
            "policy_hash": bond["policy_hash"],
        }],
        "signals": signals,
    }


def _admin_view(kind: str, bond: dict, outcomes: list, rows: list, release: str) -> dict:
    signals = []
    for item in outcomes:
        row = {
            "signal_id": item["signal_id"],
            "reason": item["reason"],
            "loss_base_units": item["loss_base_units"],
            "would_record_base_units": item["would_record_base_units"],
        }
        if item["decision"] == "would_record" and kind in {"devnet", "testnet"}:
            row["claim_token"] = issue_claim_token(kind, item["signal_id"])
        signals.append(row)
    return {
        "label": _admin_label(bond),
        "flag_on": flag_on(),
        "amounts": {
            "bond_base_units": bond["amount_base_units"],
            "signals": signals,
        },
        "ledger": rows,
        "release": release,
    }
