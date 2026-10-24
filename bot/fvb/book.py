"""Depth stats for one book: slippage ``S(Q)``, ``N_eff`` and ``CR1``.

A book that cannot be read, or that cannot fill ``Q``, contributes nothing.
It is not treated as zero slippage, which would be an infinite weight.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True)
class Book:
    venue: str
    mid: float | None
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    ts: float


@dataclass(frozen=True)
class Depth:
    s_q: float | None
    n_eff: float | None
    cr1: float | None


def _positive_sizes(book: Book) -> list[float]:
    return [lv.size for lv in (*book.bids, *book.asks) if lv.size is not None and lv.size > 0]


def n_eff_and_cr1(book: Book) -> tuple[float, float] | None:
    """Effective number of levels, and the largest level's share of size."""
    sizes = _positive_sizes(book)
    total = sum(sizes)
    if total <= 0:
        return None
    shares = [s / total for s in sizes]
    denom = sum(p * p for p in shares)
    if denom <= 0:
        return None
    return (1.0 / denom, max(sizes) / total)


def slippage_cost(book: Book, q_ref: float) -> float | None:
    """Quote cost of walking the asks for notional ``q_ref``.

    ``None`` when the mid is missing, the book cannot fill ``q_ref``, or the
    walk would price through a crossed book. A fill exactly at the mid is a
    deep book: the cost is a tiny positive floor so the weight stays finite.
    """
    if book.mid is None or book.mid <= 0 or q_ref <= 0:
        return None
    remaining = q_ref
    spent = 0.0
    got = 0.0
    for level in book.asks:
        if level.price is None or level.size is None:
            continue
        if level.price <= 0 or level.size <= 0:
            continue
        level_quote = level.price * level.size
        take = remaining if remaining < level_quote else level_quote
        got += take / level.price
        spent += take
        remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-6 or got <= 0 or spent <= 0:
        return None
    vwap = spent / got
    slip = (vwap - book.mid) * got
    if slip < 0:
        return None
    if slip == 0:
        return spent * 1e-9
    return slip


def depth_of(book: Book, q_ref: float) -> Depth:
    conc = n_eff_and_cr1(book)
    if conc is None:
        return Depth(None, None, None)
    n_eff, cr1 = conc
    return Depth(slippage_cost(book, q_ref), n_eff, cr1)


def book_weight(depth: Depth, q_ref: float, *, n_target: float, cr1_from: float) -> float | None:
    """``Q / S(Q)`` times the concentration haircut.

    ``None`` when slippage could not be read. A haircut that reaches zero
    (a single level far above the concentration line, combined with a
    vanishing effective count) drops the book rather than publishing a zero
    weight as if the book had been measured and found empty of influence
    while still being a number someone might sum.
    """
    if depth.s_q is None or depth.n_eff is None or depth.cr1 is None:
        return None
    if depth.s_q <= 0 or q_ref <= 0 or n_target <= 0:
        return None
    depth_score = q_ref / depth.s_q
    breadth = depth.n_eff / n_target
    if breadth > 1.0:
        breadth = 1.0
    if breadth <= 0:
        return None
    excess = depth.cr1 - cr1_from
    if excess < 0:
        excess = 0.0
    haircut = 1.0 - excess
    if haircut <= 0:
        return None
    weight = depth_score * breadth * haircut
    if weight <= 0:
        return None
    return weight
