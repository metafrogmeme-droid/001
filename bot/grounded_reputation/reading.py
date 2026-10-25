"""The evidence reading for one agent.

Counts stay split. A missing index is not a row of zeros. Bucket edges that
cannot be read leave the histogram out. The seal is sha256 of the canonical
reading. This module does not call a model and does not write a verdict.
"""
from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional

from bot.grounded_reputation.addrs import as_int
from bot.grounded_reputation.chains import chain_allowed, default_escrows, token_decimals
from bot.grounded_reputation.flag import BUCKETS, DEFAULT_BUCKETS, DEFAULT_WINDOW_DAYS, WINDOW_DAYS
from bot.grounded_reputation.match import RULE_VERSION, classify_feedbacks
from bot.grounded_reputation.signals import graph_signals

FOOTER = "Unbacked does not mean fake."
LIMITS = (
    "A payment shows that value moved. It does not show that a service happened, "
    "or that the payer and the agent are different parties. Nothing here is a verdict."
)
BUCKET_BASIS = "USDC token units at the token's decimals. Not a market price."

_COUNT_KEYS = (
    "backed_direct",
    "escrow",
    "failed",
    "tampered",
    "none",
    "uri_unresolvable",
    "revoked",
)


def window_seconds(env: Optional[Mapping[str, str]] = None) -> Optional[int]:
    """Seconds in the payment window. Unset means 30 days. Unreadable means None."""
    src = env if env is not None else os.environ
    raw = src.get(WINDOW_DAYS)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_WINDOW_DAYS * 86400
    days = as_int(str(raw).strip())
    if days is None or days < 0:
        return None
    return days * 86400


def bucket_edges(env: Optional[Mapping[str, str]] = None) -> Optional[tuple[Decimal, ...]]:
    """Upper edges of the value buckets. Unset means 0.01, 1, 10. Unreadable means None."""
    src = env if env is not None else os.environ
    raw = src.get(BUCKETS)
    if raw is None or str(raw).strip() == "":
        raw = DEFAULT_BUCKETS
    parts = [part.strip() for part in str(raw).split(",") if part.strip()]
    parsed: list[Decimal] = []
    for part in parts:
        try:
            edge = Decimal(part)
        except InvalidOperation:
            return None
        if not edge.is_finite() or edge <= 0:
            return None
        parsed.append(edge)
    if len(parsed) < 1:
        return None
    if parsed != sorted(parsed) or len(set(parsed)) != len(parsed):
        return None
    return tuple(parsed)


def reading_from_payload(payload: object, env: Optional[Mapping[str, str]] = None) -> dict[str, object]:
    """Classify a posted bundle. Refuses a mainnet chain unless the read switch is on."""
    if not isinstance(payload, dict):
        return _closed("payload_unreadable")
    window = window_seconds(env)
    if window is None:
        return _closed("window_unreadable")
    edges = bucket_edges(env)
    agent = payload.get("agent")
    if not isinstance(agent, dict):
        return _not_indexed(window, edges)
    chain = as_int(agent.get("chain_id"))
    if not chain_allowed(chain, env):
        return _closed("chain_refused")
    feedbacks = payload.get("feedbacks")
    payments = payload.get("payments")
    spans = payload.get("wallet_spans")
    funding = payload.get("funding")
    if not isinstance(feedbacks, list) or not isinstance(payments, list) or not isinstance(spans, list):
        return _closed("payload_unreadable")
    if funding is None:
        funding = []
    if not isinstance(funding, list):
        return _closed("payload_unreadable")
    escrows = payload.get("escrows")
    if escrows is None:
        escrows = default_escrows()
    if not isinstance(escrows, list):
        return _closed("payload_unreadable")
    history = payload.get("history_readable")
    history_readable = True if history is None else history is True
    if chain is None:
        return _closed("chain_refused")
    return build_reading(
        agent,
        feedbacks,
        payments,
        spans,
        funding,
        escrows,
        window_seconds=window,
        edges=edges,
        history_readable=history_readable,
        env=env,
    )


def build_reading(
    agent: dict[str, object],
    feedbacks: list[dict[str, object]],
    payments: list[dict[str, object]],
    spans: list[dict[str, object]],
    funding: list[dict[str, object]],
    escrows: list[dict[str, object]],
    *,
    window_seconds: int,
    edges: Optional[tuple[Decimal, ...]],
    history_readable: bool,
    env: Optional[Mapping[str, str]] = None,
) -> dict[str, object]:
    chain = as_int(agent.get("chain_id"))
    agent_id = as_int(agent.get("agent_id"))
    if chain is None or agent_id is None:
        return _closed("payload_unreadable")
    prepared = [_with_decimals(row, chain) for row in payments]
    results = classify_feedbacks(
        feedbacks,
        prepared,
        spans,
        escrows,
        window_seconds=window_seconds,
        history_readable=history_readable,
        agent_chain_id=chain,
    )
    counts = _counts(results)
    buckets, unreadable_amounts = _buckets(results, edges)
    src = env if env is not None else os.environ
    signals = graph_signals(
        agent,
        results,
        prepared,
        funding,
        spans,
        loop_hops=_opt_int(src.get("GROUNDED_REPUTATION_LOOP_HOPS"), 3),
        loop_seconds=_opt_seconds(src.get("GROUNDED_REPUTATION_LOOP_DAYS"), 7),
        wash_seconds=_opt_seconds(src.get("GROUNDED_REPUTATION_WASH_DAYS"), 1),
        wash_bps=_opt_int(src.get("GROUNDED_REPUTATION_WASH_BPS"), 500),
        burst_seconds=_opt_int(src.get("GROUNDED_REPUTATION_BURST_SECONDS"), 600),
        burst_count=_opt_int(src.get("GROUNDED_REPUTATION_BURST_COUNT"), 3),
        fresh_blocks=_opt_int(src.get("GROUNDED_REPUTATION_FRESH_BLOCKS"), 43200),
    )
    body: dict[str, object] = {
        "ok": True,
        "readable": True,
        "indexed": True,
        "rule_version": RULE_VERSION,
        "chain_id": chain,
        "agent_id": agent_id,
        "window_days": window_seconds // 86400,
        "counts": counts,
        "value_buckets": buckets["direct"] if edges is not None else None,
        "escrow_buckets": buckets["escrow"] if edges is not None else None,
        "buckets_unreadable": edges is None,
        "amount_unreadable": unreadable_amounts,
        "bucket_basis": BUCKET_BASIS,
        "escrow_label": "escrow, can't attribute",
        "signals": signals,
        "rows": [_public_row(row) for row in results],
        "limits": LIMITS,
        "footer": FOOTER,
        "summary": None,
        "ai_written": False,
    }
    body["seal_hash"] = seal_reading(body)
    return body


def seal_reading(body: dict[str, object]) -> str:
    """sha256 of the reading without its own seal."""
    copied = {key: value for key, value in body.items() if key != "seal_hash"}
    raw = json.dumps(copied, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _counts(results: list[dict[str, object]]) -> dict[str, int]:
    counts = {key: 0 for key in _COUNT_KEYS}
    for row in results:
        if row.get("revoked") is True or row.get("reason") == "revoked":
            counts["revoked"] += 1
            continue
        klass = row.get("class")
        if klass == "backed_direct":
            counts["backed_direct"] += 1
        elif klass == "backed_escrow_unattributed":
            counts["escrow"] += 1
        elif klass == "proof_failed":
            counts["failed"] += 1
        elif klass == "proof_tampered":
            counts["tampered"] += 1
        elif klass == "no_proof":
            counts["none"] += 1
        elif klass == "uri_unresolvable":
            counts["uri_unresolvable"] += 1
    return counts


def _buckets(
    results: list[dict[str, object]],
    edges: Optional[tuple[Decimal, ...]],
) -> tuple[dict[str, object], int]:
    empty: dict[str, object] = {"direct": None, "escrow": None}
    if edges is None:
        return empty, 0
    direct = [0 for _ in range(len(edges) + 1)]
    escrow = [0 for _ in range(len(edges) + 1)]
    missed = 0
    for row in results:
        klass = row.get("class")
        if klass not in ("backed_direct", "backed_escrow_unattributed"):
            continue
        if row.get("revoked") is True:
            continue
        payment = row.get("payment")
        placed = _place(payment, edges)
        if placed is None:
            missed += 1
            continue
        if klass == "backed_direct":
            direct[placed] += 1
        else:
            escrow[placed] += 1
    return {
        "direct": _labelled(direct, edges),
        "escrow": _labelled(escrow, edges),
    }, missed


def _place(payment: object, edges: tuple[Decimal, ...]) -> Optional[int]:
    if not isinstance(payment, dict):
        return None
    units = as_int(payment.get("raw_units"))
    decimals = as_int(payment.get("decimals"))
    if units is None or decimals is None or units < 0 or decimals < 0:
        return None
    amount = Decimal(units) / (Decimal(10) ** decimals)
    for index, edge in enumerate(edges):
        if amount < edge:
            return index
    return len(edges)


def _labelled(counts: list[int], edges: tuple[Decimal, ...]) -> list[dict[str, object]]:
    labels = _bucket_labels(edges)
    return [{"label": labels[i], "count": counts[i]} for i in range(len(labels))]


def _bucket_labels(edges: tuple[Decimal, ...]) -> list[str]:
    labels = []
    previous: Optional[Decimal] = None
    for edge in edges:
        if previous is None:
            labels.append(f"under ${_fmt(edge)}")
        else:
            labels.append(f"${_fmt(previous)} to under ${_fmt(edge)}")
        previous = edge
    if previous is not None:
        labels.append(f"${_fmt(previous)} or more")
    return labels


def _fmt(edge: Decimal) -> str:
    text = format(edge, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _with_decimals(payment: dict[str, object], chain_hint: int) -> dict[str, object]:
    copied = dict(payment)
    if as_int(copied.get("decimals")) is not None:
        return copied
    chain = as_int(copied.get("chain_id"))
    if chain is None:
        chain = chain_hint
    decimals = token_decimals(chain, copied.get("token"))
    if decimals is not None:
        copied["decimals"] = decimals
    return copied


def _public_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "feedback_key": row.get("feedback_key"),
        "class": row.get("class"),
        "reason": row.get("reason"),
        "label": row.get("label"),
        "payment_key": row.get("payment_key"),
        "revoked": row.get("revoked") is True,
    }


def _not_indexed(window: Optional[int], edges: Optional[tuple[Decimal, ...]]) -> dict[str, object]:
    return {
        "ok": True,
        "readable": True,
        "indexed": False,
        "counts": None,
        "value_buckets": None,
        "escrow_buckets": None,
        "buckets_unreadable": edges is None,
        "window_days": None if window is None else window // 86400,
        "rule_version": RULE_VERSION,
        "summary": None,
        "ai_written": False,
        "limits": LIMITS,
        "footer": FOOTER,
        "note": "This agent is not in the local index.",
    }


def _closed(reason: str) -> dict[str, object]:
    return {
        "ok": False,
        "readable": False,
        "indexed": None,
        "counts": None,
        "error": reason,
        "summary": None,
        "ai_written": False,
    }


def _opt_int(raw: Optional[str], default: int) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return default
    parsed = as_int(str(raw).strip())
    if parsed is None or parsed < 0:
        return None
    return parsed


def _opt_seconds(raw: Optional[str], default_days: int) -> Optional[int]:
    days = _opt_int(raw, default_days)
    if days is None:
        return None
    return days * 86400


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)
