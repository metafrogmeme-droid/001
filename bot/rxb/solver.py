"""Look-through over an issuer graph.

V(i) = External(i) + fresh attestation + claims on other SCCs - Debt(i).

Claims inside an SCC are zero. External is the on-chain face that sits
outside the SCC; the caller classifies that before asking. CBF is
``1 - V / NAV`` and is not clamped. UF is the share of NAV that is neither
on-chain face (circular included) nor a fresh attestation, and it does not
go negative.

None is an unread input. A stored zero is a measurement. NAV of None or 0,
or an unreadable V, makes CBF None rather than 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

FRESH_S = 72 * 3600
CBF_LINE = Fraction(1, 10)
UF_LINE = Fraction(2, 5)


@dataclass(frozen=True)
class Claim:
    to: str
    amount: int | None


@dataclass(frozen=True)
class Issuer:
    token: str
    nav: int | None
    external: int | None
    onchain_face: int | None
    debt: int | None
    attested: int | None
    attestation_age_s: int | None
    claims: tuple[Claim, ...] = ()
    borrower_count: int | None = None


def _frac(num: int | None, den: int | None) -> str | None:
    if num is None or den is None or den == 0:
        return None
    f = Fraction(num, den)
    return f"{f.numerator}/{f.denominator}"


def _as_fraction(value: Fraction | None) -> str | None:
    if value is None:
        return None
    return f"{value.numerator}/{value.denominator}"


def tarjan(nodes: Iterable[str], edges: dict[str, tuple[str, ...]]) -> list[list[str]]:
    """SCCs in dependency order: a component is listed after the ones it needs."""
    index = 0
    stack: list[str] = []
    onstack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    sccs: list[list[str]] = []

    def strong(v: str) -> None:
        nonlocal index
        indices[v] = low[v] = index
        index += 1
        stack.append(v)
        onstack.add(v)
        for w in edges.get(v, ()):
            if w not in indices:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], indices[w])
        if low[v] == indices[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                onstack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    for v in nodes:
        if v not in indices:
            strong(v)
    return sccs


def _fresh(node: Issuer) -> tuple[Fraction | None, bool | None]:
    """(fresh amount, stale). A missing age on a present amount is neither."""
    if node.attested is None:
        return None, None
    if node.attested < 0:
        return None, None
    if node.attested == 0 and node.attestation_age_s is None:
        return Fraction(0), False
    age = node.attestation_age_s
    if age is None or age < 0:
        return None, None
    if age > FRESH_S:
        return Fraction(0), True
    return Fraction(node.attested), False


def _concentration(count: int | None) -> str:
    if count is None:
        return "unreadable"
    if count < 0:
        return "unreadable"
    if count == 1:
        return "single_borrower"
    return "not_single"


def _blank(token: str, reason: str, concentration: str) -> dict:
    return {
        "token": token,
        "readable": False,
        "reason": reason,
        "cbf": None,
        "uf": None,
        "excess": None,
        "stale": None,
        "tripped": None,
        "reasons": [],
        "scc": [],
        "edges": [],
        "concentration": concentration,
        "v": None,
    }


def solve_issuers(issuers: list[Issuer]) -> dict[str, dict]:
    """One reading per token. Duplicate tokens make every row unreadable."""
    if len({n.token for n in issuers}) != len(issuers) or any(not n.token for n in issuers):
        return {
            n.token or "?": _blank(n.token or "?", "duplicate_or_blank", _concentration(n.borrower_count))
            for n in issuers
        }
    by = {n.token: n for n in issuers}
    edges: dict[str, tuple[str, ...]] = {}
    bad_edge: set[str] = set()
    for node in issuers:
        dests: list[str] = []
        for claim in node.claims:
            if claim.amount is None or claim.amount < 0 or not claim.to:
                bad_edge.add(node.token)
                continue
            if claim.amount == 0:
                continue
            if claim.to not in by:
                bad_edge.add(node.token)
                continue
            dests.append(claim.to)
        edges[node.token] = tuple(dests)

    sccs = tarjan(by, edges)
    comp_of: dict[str, int] = {}
    members: dict[int, list[str]] = {}
    for i, comp in enumerate(sccs):
        members[i] = sorted(comp)
        for token in comp:
            comp_of[token] = i

    fresh: dict[str, tuple[Fraction | None, bool | None]] = {t: _fresh(n) for t, n in by.items()}
    value: dict[str, Fraction | None] = {}

    for comp in sccs:
        comp_set = set(comp)
        for token in comp:
            node = by[token]
            amount, _stale = fresh[token]
            if token in bad_edge or node.external is None or node.debt is None or amount is None:
                value[token] = None
                continue
            if node.external < 0 or node.debt < 0:
                value[token] = None
                continue
            total = Fraction(node.external) + amount - Fraction(node.debt)
            failed = False
            for claim in node.claims:
                if claim.amount is None or claim.amount <= 0 or claim.to in comp_set:
                    continue
                other = by.get(claim.to)
                if other is None or other.nav is None or other.nav == 0:
                    failed = True
                    break
                other_v = value.get(claim.to)
                if other_v is None:
                    failed = True
                    break
                total += Fraction(claim.amount, other.nav) * other_v
            value[token] = None if failed else total

    out: dict[str, dict] = {}
    for token, node in by.items():
        v = value[token]
        amount, stale = fresh[token]
        cbf: Fraction | None
        if v is None or node.nav is None or node.nav == 0:
            cbf = None
        else:
            cbf = 1 - (v / node.nav)
        uf: Fraction | None
        excess: Fraction | None
        if node.nav is None or node.nav == 0 or node.onchain_face is None or amount is None:
            uf = None
            excess = None
        elif node.onchain_face < 0:
            uf = None
            excess = None
        else:
            accounted = Fraction(node.onchain_face) + amount
            if accounted > node.nav:
                uf = Fraction(0)
                excess = accounted - node.nav
            else:
                uf = (node.nav - accounted) / node.nav
                excess = Fraction(0)
        flags: list[str] = []
        known = True
        if cbf is None:
            known = False
        elif cbf > CBF_LINE:
            flags.append("cbf")
        if uf is None:
            known = False
        elif uf > UF_LINE:
            flags.append("uf")
        if stale is None:
            known = False
        elif stale:
            flags.append("stale")
        if flags:
            tripped: bool | None = True
        elif not known:
            tripped = None
        else:
            tripped = False
        edge_rows = []
        comp_set = set(members[comp_of[token]])
        for claim in node.claims:
            edge_rows.append({
                "to": claim.to,
                "face_over_nav": _frac(claim.amount, node.nav),
                "inside_scc": claim.to in comp_set and claim.to in by,
            })
        out[token] = {
            "token": token,
            "readable": cbf is not None and uf is not None and stale is not None,
            "reason": None if cbf is not None and uf is not None and stale is not None else "unreadable",
            "cbf": _as_fraction(cbf),
            "uf": _as_fraction(uf),
            "excess": _as_fraction(excess),
            "stale": stale,
            "tripped": tripped,
            "reasons": flags,
            "scc": members[comp_of[token]],
            "edges": edge_rows,
            "concentration": _concentration(node.borrower_count),
            "v": _as_fraction(v),
        }
    return out
