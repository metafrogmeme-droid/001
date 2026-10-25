"""Match one rating to at most one payment.

The file hash is decided before this module sees a proof. Here the rules are:

- the payment is the cited tx hash, or the cited EIP-3009 nonce
- the payer is the rater, not the transaction sender
- the payee is the agent wallet at the payment block, unless the payee is a
  known escrow
- the payment is at or before the rating, and the rating is inside the window
- a payment key is given to the earliest rating that passes, and to no other

A failed check names its reason. It does not become a count of zero backed.
"""
from __future__ import annotations

from typing import Optional

from bot.grounded_reputation.addrs import as_int, norm_addr, norm_word, same_addr
from bot.grounded_reputation.chains import token_decimals
from bot.grounded_reputation.wallet import wallet_at

RULE_VERSION = "v0.1"
ESCROW_LABEL = "escrow, can't attribute"

_SUCCESS = frozenset({"backed_direct", "backed_escrow_unattributed"})


def feedback_key(feedback: dict[str, object]) -> str:
    chain = as_int(feedback.get("chain_id"))
    agent = as_int(feedback.get("agent_id"))
    client = norm_addr(feedback.get("client")) or ""
    index = as_int(feedback.get("feedback_index"))
    return f"{chain}:{agent}:{client}:{index}"


def payment_key(payment: dict[str, object]) -> str:
    chain = as_int(payment.get("chain_id"))
    tx = norm_word(payment.get("tx_hash")) or ""
    log_index = as_int(payment.get("log_index"))
    return f"{chain}:{tx}:{log_index}"


def classify_feedbacks(
    feedbacks: list[dict[str, object]],
    payments: list[dict[str, object]],
    spans: list[dict[str, object]],
    escrows: list[dict[str, object]],
    *,
    window_seconds: int,
    history_readable: bool,
    agent_chain_id: int,
) -> list[dict[str, object]]:
    """One result per feedback, in input order. Uniqueness is applied after."""
    drafted: list[dict[str, object]] = []
    for feedback in feedbacks:
        drafted.append(_one(
            feedback,
            payments,
            spans,
            escrows,
            window_seconds=window_seconds,
            history_readable=history_readable,
            agent_chain_id=agent_chain_id,
        ))
    _assign_unique(drafted)
    return drafted


def _one(
    feedback: dict[str, object],
    payments: list[dict[str, object]],
    spans: list[dict[str, object]],
    escrows: list[dict[str, object]],
    *,
    window_seconds: int,
    history_readable: bool,
    agent_chain_id: int,
) -> dict[str, object]:
    base: dict[str, object] = {
        "feedback_key": feedback_key(feedback),
        "feedback": feedback,
        "payment_key": None,
        "class": "no_proof",
        "reason": "absent",
        "label": None,
        "revoked": feedback.get("revoked") is True,
    }
    pre = _precheck(feedback)
    if pre is not None:
        base["class"] = pre[0]
        base["reason"] = pre[1]
        return base
    proof = _proof(feedback)
    if proof is None:
        return base
    found, lookup_reason = _lookup(payments, proof)
    if lookup_reason == "wrong_chain":
        base["class"] = "proof_failed"
        base["reason"] = "wrong_chain"
        return base
    if lookup_reason == "nonce_ambiguous":
        base["class"] = "proof_failed"
        base["reason"] = "nonce_ambiguous"
        return base
    if not found:
        base["class"] = "proof_failed"
        base["reason"] = "tx_not_found"
        return base
    assessed: list[tuple[dict[str, object], str, Optional[str], Optional[str]]] = []
    for payment in found:
        klass, reason, label = _assess(
            payment,
            feedback,
            proof,
            spans,
            escrows,
            window_seconds=window_seconds,
            history_readable=history_readable,
            agent_chain_id=agent_chain_id,
        )
        assessed.append((payment, klass, reason, label))
    successes = [row for row in assessed if row[1] in _SUCCESS]
    if len(successes) > 1:
        base["class"] = "proof_failed"
        base["reason"] = "tx_ambiguous"
        return base
    chosen = successes[0] if successes else assessed[0]
    payment, klass, reason, label = chosen
    base["class"] = klass
    base["reason"] = reason
    base["label"] = label
    if klass in _SUCCESS:
        base["payment_key"] = payment_key(payment)
        base["payment"] = payment
    return base


def _precheck(feedback: dict[str, object]) -> Optional[tuple[str, str]]:
    uri = feedback.get("feedback_uri")
    has_uri = isinstance(uri, str) and uri.strip() != ""
    file_info = feedback.get("file")
    if not isinstance(file_info, dict):
        if not has_uri:
            return ("no_proof", "absent")
        return ("uri_unresolvable", "fetch_failed")
    if file_info.get("keccak_ok") is False and file_info.get("ipfs") is not True:
        return ("proof_tampered", "hash_mismatch")
    if file_info.get("resolved") is not True:
        if not has_uri:
            return ("no_proof", "absent")
        reason = file_info.get("reason")
        if not isinstance(reason, str) or reason == "":
            reason = "fetch_failed"
        return ("uri_unresolvable", reason)
    if feedback.get("revoked") is True:
        return ("proof_failed", "revoked")
    proof = file_info.get("proof")
    if not isinstance(proof, dict):
        return ("no_proof", "absent")
    if norm_word(proof.get("tx_hash")) is None and norm_word(proof.get("nonce")) is None:
        return ("no_proof", "absent")
    return None


def _proof(feedback: dict[str, object]) -> Optional[dict[str, object]]:
    file_info = feedback.get("file")
    if not isinstance(file_info, dict):
        return None
    proof = file_info.get("proof")
    if not isinstance(proof, dict):
        return None
    return proof


def _lookup(
    payments: list[dict[str, object]],
    proof: dict[str, object],
) -> tuple[list[dict[str, object]], Optional[str]]:
    tx = norm_word(proof.get("tx_hash"))
    nonce = norm_word(proof.get("nonce"))
    chain = as_int(proof.get("chain_id"))
    if tx is not None:
        hits = [row for row in payments if norm_word(row.get("tx_hash")) == tx]
        return _filter_chain(hits, chain)
    if nonce is not None:
        hits = [row for row in payments if norm_word(row.get("auth_nonce")) == nonce]
        txs = {norm_word(row.get("tx_hash")) for row in hits}
        txs.discard(None)
        if len(txs) > 1:
            return [], "nonce_ambiguous"
        return _filter_chain(hits, chain)
    return [], None


def _filter_chain(
    hits: list[dict[str, object]],
    chain: Optional[int],
) -> tuple[list[dict[str, object]], Optional[str]]:
    if chain is None or not hits:
        return hits, None
    on_chain = [row for row in hits if as_int(row.get("chain_id")) == chain]
    if not on_chain:
        return [], "wrong_chain"
    return on_chain, None


def _assess(
    payment: dict[str, object],
    feedback: dict[str, object],
    proof: dict[str, object],
    spans: list[dict[str, object]],
    escrows: list[dict[str, object]],
    *,
    window_seconds: int,
    history_readable: bool,
    agent_chain_id: int,
) -> tuple[str, Optional[str], Optional[str]]:
    if payment.get("status_ok") is not True:
        return ("proof_failed", "tx_failed", None)
    if not _token_ok(payment):
        return ("proof_failed", "wrong_token", None)
    pay_time = as_int(payment.get("block_time"))
    rated_at = as_int(feedback.get("block_time"))
    if pay_time is None or rated_at is None:
        return ("proof_failed", "time_unreadable", None)
    if pay_time > rated_at:
        return ("proof_failed", "payment_after_feedback", None)
    if rated_at > pay_time + window_seconds:
        return ("proof_failed", "past_window", None)
    client = norm_addr(feedback.get("client"))
    payer = norm_addr(payment.get("payer"))
    if client is None or payer is None or payer != client:
        return ("proof_failed", "payer_mismatch", None)
    claimed_from = norm_addr(proof.get("from_address"))
    if claimed_from is not None and claimed_from != payer:
        return ("proof_failed", "payer_mismatch", None)
    if payment.get("method") == "eip3009":
        authorizer = norm_addr(payment.get("authorizer"))
        if authorizer is None or authorizer != payer:
            return ("proof_failed", "payer_mismatch", None)
    payee = norm_addr(payment.get("payee"))
    claimed_to = norm_addr(proof.get("to_address"))
    if claimed_to is not None and (payee is None or claimed_to != payee):
        return ("proof_failed", "payee_mismatch", None)
    pay_chain = as_int(payment.get("chain_id"))
    if payee is not None and pay_chain is not None and _is_escrow(pay_chain, payee, escrows):
        return ("backed_escrow_unattributed", None, ESCROW_LABEL)
    if history_readable is not True:
        return ("proof_failed", "wallet_unreadable", None)
    same_chain = pay_chain is not None and pay_chain == agent_chain_id
    resolved = wallet_at(
        spans,
        payment.get("block"),
        at_time=payment.get("block_time"),
        same_chain=same_chain,
    )
    if resolved.get("readable") is not True:
        return ("proof_failed", "wallet_unreadable", None)
    wallet = norm_addr(resolved.get("wallet")) if resolved.get("wallet") is not None else None
    if wallet is None or payee != wallet:
        return ("proof_failed", "payee_mismatch", None)
    return ("backed_direct", None, None)


def _token_ok(payment: dict[str, object]) -> bool:
    chain = as_int(payment.get("chain_id"))
    if chain is None:
        return False
    return token_decimals(chain, payment.get("token")) is not None


def _is_escrow(chain_id: int, payee: str, escrows: list[dict[str, object]]) -> bool:
    for row in escrows:
        if as_int(row.get("chain_id")) != chain_id:
            continue
        if same_addr(row.get("address"), payee):
            return True
    return False


def _assign_unique(rows: list[dict[str, object]]) -> None:
    order = sorted(
        (row for row in rows if row.get("class") in _SUCCESS),
        key=_success_key,
    )
    used: set[str] = set()
    for row in order:
        key = row.get("payment_key")
        if not isinstance(key, str):
            continue
        if key in used:
            row["class"] = "proof_failed"
            row["reason"] = "reused"
            row["label"] = None
            row["payment_key"] = None
            row.pop("payment", None)
            continue
        used.add(key)


def _success_key(row: dict[str, object]) -> tuple[int, int, int, int, int, str]:
    # The earliest rating keeps the payment. A missing block sorts after a
    # known block, and a missing time sorts after a known time at that block,
    # so an unread clock does not take the payment from a rating whose clock
    # was read. The placeholders are not displayed.
    feedback = row.get("feedback")
    block_known = 1
    block = 0
    index = 0
    client = ""
    when = 0
    dated = 0
    if isinstance(feedback, dict):
        parsed_block = as_int(feedback.get("block"))
        parsed_index = as_int(feedback.get("feedback_index"))
        parsed_when = as_int(feedback.get("block_time"))
        if parsed_block is not None:
            block_known = 0
            block = parsed_block
        index = parsed_index if parsed_index is not None else 0
        if parsed_when is not None:
            dated = 1
            when = parsed_when
        who = norm_addr(feedback.get("client"))
        client = who if who is not None else ""
    return (block_known, block, -dated, when, index, client)
