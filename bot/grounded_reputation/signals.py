"""Graph signals on public payments and first funders.

Each signal is a list of facts, or None when the clock or the config needed
to compute it was not readable. An empty list means the check ran and found
none. A payment moving is not a claim that the payer and the agent are
different parties, and it is not a claim that they are the same party.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from bot.grounded_reputation.addrs import as_int, norm_addr

_BACKED = frozenset({"backed_direct", "backed_escrow_unattributed"})


def graph_signals(
    agent: dict[str, object],
    results: list[dict[str, object]],
    payments: list[dict[str, object]],
    funding: list[dict[str, object]],
    spans: list[dict[str, object]],
    *,
    loop_hops: Optional[int],
    loop_seconds: Optional[int],
    wash_seconds: Optional[int],
    wash_bps: Optional[int],
    burst_seconds: Optional[int],
    burst_count: Optional[int],
    fresh_blocks: Optional[int],
) -> dict[str, object]:
    fund = _funding_index(funding)
    chain = as_int(agent.get("chain_id"))
    loops = _self_loops(results, payments, loop_hops, loop_seconds)
    washes = _washes(results, payments, wash_seconds, wash_bps)
    burst = _burst(results, fund, chain, burst_seconds, burst_count, fresh_blocks)
    common, clusters = _common_funders(agent, results, fund, spans, chain)
    adjacent = _owner_adjacent(agent, results, fund, spans, chain)
    return {
        "self_loop_suspect": loops,
        "wash": washes,
        "burst": burst,
        "common_first_funder": common,
        "owner_adjacent": adjacent,
        "clusters": clusters,
    }


def _funding_index(funding: list[dict[str, object]]) -> dict[tuple[int, str], dict[str, object]]:
    index: dict[tuple[int, str], dict[str, object]] = {}
    for row in funding:
        chain = as_int(row.get("chain_id"))
        who = norm_addr(row.get("address"))
        if chain is None or who is None:
            continue
        index[(chain, who)] = row
    return index


def _label_of(fund: dict[tuple[int, str], dict[str, object]], chain: int, who: str) -> Optional[str]:
    row = fund.get((chain, who))
    if row is None:
        return None
    label = row.get("label")
    if isinstance(label, str) and label.strip() != "":
        return label.strip()
    return None


def _self_loops(
    results: list[dict[str, object]],
    payments: list[dict[str, object]],
    hops: Optional[int],
    window: Optional[int],
) -> Optional[list[dict[str, object]]]:
    if hops is None or window is None:
        return None
    edges = _edges(payments)
    if edges is None:
        return None
    backed = _backed_payments(results)
    if not backed:
        return []
    usable = [row for row in backed if as_int(row["block_time"]) is not None]
    if not usable:
        return None
    found: list[dict[str, object]] = []
    for row in usable:
        start = as_int(row["block_time"])
        payer = norm_addr(row["payer"])
        payee = norm_addr(row["payee"])
        hop_key = row["key"]
        if start is None or payer is None or payee is None or not isinstance(hop_key, str):
            continue
        if _reaches(edges, payee, payer, start, start + window, hops, hop_key):
            found.append({
                "feedback_key": row["feedback_key"],
                "text": "Funds later moved back toward the payer within the configured hops and days.",
            })
    return found


def _washes(
    results: list[dict[str, object]],
    payments: list[dict[str, object]],
    window: Optional[int],
    bps: Optional[int],
) -> Optional[list[dict[str, object]]]:
    if window is None or bps is None:
        return None
    backed = _backed_payments(results)
    if not backed:
        return []
    found: list[dict[str, object]] = []
    usable = 0
    for row in backed:
        start = as_int(row["block_time"])
        payer = norm_addr(row["payer"])
        payee = norm_addr(row["payee"])
        units = as_int(row["raw_units"])
        decimals = as_int(row["decimals"])
        if start is None or payer is None or payee is None or units is None or decimals is None:
            continue
        usable += 1
        own = row["key"]
        for other in payments:
            if other.get("status_ok") is not True:
                continue
            if _skip(other) == own:
                continue
            when = as_int(other.get("block_time"))
            if when is None or when < start or when > start + window:
                continue
            if norm_addr(other.get("payer")) != payee or norm_addr(other.get("payee")) != payer:
                continue
            back = as_int(other.get("raw_units"))
            back_decimals = as_int(other.get("decimals"))
            if back is None or back_decimals != decimals:
                continue
            if _roughly(back, units, bps):
                found.append({
                    "feedback_key": row["feedback_key"],
                    "text": "The payee sent a similar amount back to the payer inside the wash window.",
                })
                break
    if usable == 0:
        return None
    return found


def _burst(
    results: list[dict[str, object]],
    fund: dict[tuple[int, str], dict[str, object]],
    chain: Optional[int],
    window: Optional[int],
    count: Optional[int],
    fresh_blocks: Optional[int],
) -> Optional[list[dict[str, object]]]:
    if window is None or count is None or fresh_blocks is None or chain is None:
        return None
    fresh: list[tuple[int, str]] = []
    checked = False
    for row in results:
        if row.get("class") != "backed_direct":
            continue
        feedback = row.get("feedback")
        if not isinstance(feedback, dict):
            continue
        when = as_int(feedback.get("block_time"))
        block = as_int(feedback.get("block"))
        client = norm_addr(feedback.get("client"))
        if when is None or block is None or client is None:
            continue
        checked = True
        record = fund.get((chain, client))
        if record is None:
            continue
        born = as_int(record.get("first_fund_block"))
        if born is None or block < born or block - born > fresh_blocks:
            continue
        key = row.get("feedback_key")
        if isinstance(key, str):
            fresh.append((when, key))
    if not checked:
        return [] if not any(row.get("class") == "backed_direct" for row in results) else None
    fresh.sort()
    hit: set[str] = set()
    for i, (start, _key) in enumerate(fresh):
        group = [key for when, key in fresh if start <= when <= start + window]
        if len(group) >= count:
            hit.update(group)
    text = "Several backed ratings from freshly funded addresses fell inside the burst window."
    return [{"feedback_key": key, "text": text} for key in sorted(hit)]


def _common_funders(
    agent: dict[str, object],
    results: list[dict[str, object]],
    fund: dict[tuple[int, str], dict[str, object]],
    spans: list[dict[str, object]],
    chain: Optional[int],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if chain is None:
        return [], []
    raters: list[str] = []
    for row in results:
        feedback = row.get("feedback")
        if not isinstance(feedback, dict) or row.get("revoked") is True:
            continue
        client = norm_addr(feedback.get("client"))
        if client is not None and client not in raters:
            raters.append(client)
    by_funder: dict[str, list[str]] = defaultdict(list)
    labelled: list[dict[str, object]] = []
    for rater in raters:
        record = fund.get((chain, rater))
        if record is None:
            continue
        funder = norm_addr(record.get("first_funder"))
        if funder is None:
            continue
        label = _label_of(fund, chain, funder)
        if label is not None:
            labelled.append({
                "client": rater,
                "funder": funder,
                "label": label,
                "excluded_from_cluster": True,
                "text": "The rater's first funder is on the label list. It is reported and not clustered.",
            })
            continue
        by_funder[funder].append(rater)
    reference = _reference_funders(agent, fund, spans, chain)
    found: list[dict[str, object]] = list(labelled)
    clusters: list[dict[str, object]] = []
    for funder, members in by_funder.items():
        shared = len(members) >= 2 or funder in reference
        if not shared:
            continue
        for member in members:
            found.append({
                "client": member,
                "funder": funder,
                "label": None,
                "excluded_from_cluster": False,
                "text": "This rater's first funder is also the first funder of the owner, a wallet, or another rater.",
            })
        if len(members) >= 2:
            clusters.append({
                "method": "first_funder",
                "root": funder,
                "members": list(members),
                "chain_id": chain,
            })
    return found, clusters


def _owner_adjacent(
    agent: dict[str, object],
    results: list[dict[str, object]],
    fund: dict[tuple[int, str], dict[str, object]],
    spans: list[dict[str, object]],
    chain: Optional[int],
) -> list[dict[str, object]]:
    if chain is None:
        return []
    near = _party_addresses(agent, spans)
    found: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in results:
        feedback = row.get("feedback")
        if not isinstance(feedback, dict) or row.get("revoked") is True:
            continue
        client = norm_addr(feedback.get("client"))
        if client is None or client in seen:
            continue
        record = fund.get((chain, client))
        if record is None:
            continue
        funder = norm_addr(record.get("first_funder"))
        if funder is None or funder not in near:
            continue
        seen.add(client)
        found.append({
            "client": client,
            "funder": funder,
            "text": "The rater's first funder is the owner, an operator, or an agent wallet.",
        })
    return found


def _reference_funders(
    agent: dict[str, object],
    fund: dict[tuple[int, str], dict[str, object]],
    spans: list[dict[str, object]],
    chain: int,
) -> set[str]:
    found: set[str] = set()
    for who in _party_addresses(agent, spans):
        record = fund.get((chain, who))
        if record is None:
            continue
        funder = norm_addr(record.get("first_funder"))
        if funder is not None and _label_of(fund, chain, funder) is None:
            found.add(funder)
    return found


def _party_addresses(agent: dict[str, object], spans: list[dict[str, object]]) -> set[str]:
    found: set[str] = set()
    owner = norm_addr(agent.get("owner"))
    if owner is not None:
        found.add(owner)
    operators = agent.get("operators")
    if isinstance(operators, list):
        for item in operators:
            who = norm_addr(item)
            if who is not None:
                found.add(who)
    for span in spans:
        who = norm_addr(span.get("wallet"))
        if who is not None:
            found.add(who)
    return found


def _edges(payments: list[dict[str, object]]) -> Optional[dict[str, list[tuple[str, int, str]]]]:
    edges: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for payment in payments:
        if payment.get("status_ok") is not True:
            continue
        when = as_int(payment.get("block_time"))
        payer = norm_addr(payment.get("payer"))
        payee = norm_addr(payment.get("payee"))
        if when is None or payer is None or payee is None:
            continue
        edges[payer].append((payee, when, _skip(payment)))
    return edges


def _reaches(
    edges: dict[str, list[tuple[str, int, str]]],
    start: str,
    goal: str,
    t0: int,
    t1: int,
    hops: int,
    skip: str,
) -> bool:
    if hops < 1:
        return False
    # (node, hops left, path payment keys)
    stack: list[tuple[str, int]] = [(start, hops)]
    seen: set[tuple[str, int]] = set()
    while stack:
        node, left = stack.pop()
        if left < 1 or (node, left) in seen:
            continue
        seen.add((node, left))
        for nxt, when, key in edges.get(node, []):
            if key == skip or when < t0 or when > t1:
                continue
            if nxt == goal:
                return True
            stack.append((nxt, left - 1))
    return False


def _backed_payments(results: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in results:
        if row.get("class") not in _BACKED:
            continue
        payment = row.get("payment")
        if not isinstance(payment, dict):
            continue
        copied = dict(payment)
        copied["feedback_key"] = row.get("feedback_key")
        copied["key"] = _skip(payment)
        rows.append(copied)
    return rows


def _skip(payment: dict[str, object]) -> str:
    chain = as_int(payment.get("chain_id"))
    tx = payment.get("tx_hash")
    log_index = as_int(payment.get("log_index"))
    word = tx if isinstance(tx, str) else ""
    return f"{chain}:{word}:{log_index}"


def _roughly(back: int, original: int, bps: int) -> bool:
    if original <= 0 or back <= 0:
        return False
    diff = abs(back - original)
    return diff * 10000 <= original * bps
