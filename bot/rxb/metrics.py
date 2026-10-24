"""V2 look-through. One reading of CBF, UF and LTS.

V_i = sum over j outside SCC(i) of w_ij * V_j * haircut(verif)
    + sum over off-chain edges of w * haircut(verif).

A verified external asset has V = 1. w_ij is edge usd / source NAV, or an
explicit weight when the edge is a wrapper identity and no dollar NAV was
read. LTS = 1 - V. CBF is the sum of weights that stay inside the SCC.
UF is the sum of weights whose verif is none. A missing weight makes the
ratio that needs it unreadable. It is not dropped and it is not zero.

Haircuts are a policy table, not a market measurement: onchain, zk and por
are 1, none is 0, signed is the 72h decay and is unreadable without an age.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from bot.rxb.oracle_math import WAD, weight_wad
from bot.rxb.solver import tarjan

HAIRCUT_ONE = frozenset({"onchain", "zk", "por"})


@dataclass(frozen=True)
class GToken:
    token: str
    nav: int | None = None


@dataclass(frozen=True)
class GEdge:
    src: str
    dst: str | None
    usd: int | None
    kind: str
    verif: str
    weight: Fraction | None = None
    offchain_id: str | None = None
    age_s: int | None = None


def _as_fraction(value: Fraction | None) -> str | None:
    if value is None:
        return None
    return f"{value.numerator}/{value.denominator}"


def haircut(edge: GEdge) -> Fraction | None:
    if edge.verif in HAIRCUT_ONE:
        return Fraction(1)
    if edge.verif == "none":
        return Fraction(0)
    if edge.verif != "signed":
        return None
    if edge.age_s is None:
        return None
    try:
        return Fraction(weight_wad(edge.age_s), WAD)
    except (TypeError, ValueError):
        return None


def _weight(edge: GEdge, nav: int | None) -> Fraction | None:
    if edge.weight is not None:
        if edge.weight < 0:
            return None
        return edge.weight
    if edge.usd is None or nav is None or nav == 0 or edge.usd < 0:
        return None
    return Fraction(edge.usd, nav)


def solve_graph(tokens: list[GToken], edges: list[GEdge]) -> dict[str, dict]:
    """Metrics for every token. Duplicate names make the whole read unreadable."""
    names = [t.token for t in tokens]
    if len(set(names)) != len(names) or any(not n for n in names):
        return {
            t.token or "?": _blank(t.token or "?", "duplicate_or_blank")
            for t in tokens
        }
    nav = {t.token: t.nav for t in tokens}
    by_src: dict[str, list[GEdge]] = {n: [] for n in names}
    known = set(names)
    for edge in edges:
        if edge.src not in known:
            continue
        if edge.dst is None and not edge.offchain_id:
            return {n: _blank(n, "edge_unreadable") for n in names}
        if edge.dst is not None and edge.offchain_id:
            return {n: _blank(n, "edge_unreadable") for n in names}
        by_src[edge.src].append(edge)

    link: dict[str, tuple[str, ...]] = {}
    for name in names:
        dests = []
        for edge in by_src[name]:
            if edge.dst in known and edge.dst != name:
                dests.append(edge.dst)
        link[name] = tuple(dests)
    sccs = tarjan(names, link)
    comp_of: dict[str, int] = {}
    members: dict[int, list[str]] = {}
    for i, comp in enumerate(sccs):
        members[i] = sorted(comp)
        for token in comp:
            comp_of[token] = i

    value: dict[str, Fraction | None] = {}
    out: dict[str, dict] = {}
    for comp in sccs:
        comp_set = set(comp)
        for token in comp:
            cbf = Fraction(0)
            uf = Fraction(0)
            backed = Fraction(0)
            bad_w = False
            bad_v = False
            for edge in by_src[token]:
                w = _weight(edge, nav[token])
                if w is None:
                    bad_w = True
                    continue
                if edge.verif == "none":
                    uf += w
                inside = edge.dst in comp_set
                if inside:
                    cbf += w
                    continue
                h = haircut(edge)
                if h is None:
                    bad_v = True
                    continue
                if edge.dst is None:
                    backed += w * h
                    continue
                other = value.get(edge.dst)
                if other is None:
                    bad_v = True
                    continue
                backed += w * other * h
            if bad_w:
                cbf_v: Fraction | None = None
                uf_v: Fraction | None = None
            else:
                cbf_v = cbf
                uf_v = uf
            if bad_w or bad_v:
                v: Fraction | None = None
                lts: Fraction | None = None
            else:
                v = backed
                lts = 1 - backed
            value[token] = v
            out[token] = {
                "token": token,
                "readable": cbf_v is not None and uf_v is not None and lts is not None,
                "cbf": _as_fraction(cbf_v),
                "uf": _as_fraction(uf_v),
                "lts": _as_fraction(lts),
                "v": _as_fraction(v),
                "scc": members[comp_of[token]],
                "reason": None if cbf_v is not None and uf_v is not None and lts is not None else "unreadable",
            }
    return out


def _blank(token: str, reason: str) -> dict:
    return {
        "token": token,
        "readable": False,
        "cbf": None,
        "uf": None,
        "lts": None,
        "v": None,
        "scc": [],
        "reason": reason,
    }


def cross_venue_dms(venues: list[dict]) -> dict:
    """Day-over-day borrow change of one collateral family across venues.

    A venue is one (chain, protocol). DMS is (now - prev) / prev when at
    least two venues rose and every venue in the family is readable. One
    venue, read completely, is a measured 0: it is not a cross-venue move.
    A missing borrow, or a previous total of 0, is unreadable.
    """
    if not venues:
        return {"readable": False, "dms": None, "reason": "no_venues", "cross_venue": None}
    if any(v.get("borrow_now") is None or v.get("borrow_prev") is None for v in venues):
        return {"readable": False, "dms": None, "reason": "borrow_unreadable", "cross_venue": None}
    try:
        now = [Fraction(v["borrow_now"]) for v in venues]
        prev = [Fraction(v["borrow_prev"]) for v in venues]
    except (TypeError, ValueError, ArithmeticError):
        return {"readable": False, "dms": None, "reason": "borrow_unreadable", "cross_venue": None}
    if any(x < 0 for x in now + prev):
        return {"readable": False, "dms": None, "reason": "borrow_unreadable", "cross_venue": None}
    if len(venues) < 2:
        return {"readable": True, "dms": "0/1", "reason": None, "cross_venue": False}
    rose = sum(1 for a, b in zip(now, prev) if a > b)
    if rose < 2:
        return {"readable": True, "dms": "0/1", "reason": None, "cross_venue": False}
    base = sum(prev, Fraction(0))
    if base == 0:
        return {"readable": False, "dms": None, "reason": "no_prior_borrow", "cross_venue": True}
    change = sum(now, Fraction(0)) - base
    return {
        "readable": True,
        "dms": _as_fraction(change / base),
        "reason": None,
        "cross_venue": True,
    }
