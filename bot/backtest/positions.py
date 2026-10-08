"""One position, however many fills closed it.

Audit B4-04. Under the partial-TP ladder (`BACKTEST_PARTIAL_TP`, which every
`--honest` run turns on) a position that reaches TP1 closes in two or three
fills: TP1 and TP2 each bank a slice and the runner closes the rest. Each fill
is a `BacktestTrade` row carrying the position's `trade_id`. Every count and
every win/loss reading was taken over those rows, so a position that reached
TP1 added one or two winning rows (TP1 and TP2 fire only in profit), while a
position stopped before TP1 added one losing row. Trade counts, win rates and
profit factors were all biased toward the ladder's legs.

A position's outcome is the sum of its fills. It wins when its total net PnL
is above zero, loses below, and is flat at exactly zero (the engine's BT-L
rule). The fill rows stay as the record of what was filled; a dollar total is
the same over either.

`positions` is the one reading. The engine's result, the runner's pooled and
bucketed figures, the portfolio's per-symbol table, the validation gate and
the scorecard's public breakdown all count through it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from bot.backtest.funding import combine as funding_combine


@dataclass(frozen=True)
class Position:
    """A position's outcome, summed over its fills.

    It carries the attributes the readers of a `BacktestTrade` row ask for, so
    a reader that counted rows counts positions without a second code path.
    """
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    #: Quantity-weighted over the fills.
    exit_price: float
    entry_time: datetime
    #: The final fill's: the position's outcome is known when it closes.
    exit_time: datetime
    quantity: float
    #: Entry notional, summed over the fills.
    size_usd: float
    #: Gross (before commission), summed.
    pnl_usd: float
    pnl_pct: float
    commission_usd: float
    slippage_usd: float
    net_pnl_usd: float
    #: None when any fill's funding was unpriced, as the run total is.
    funding_usd: Optional[float]
    funding_state: str
    #: The final fill's reason; `exit_reasons` holds every fill's, in order.
    exit_reason: str
    exit_reasons: tuple
    fills: int
    confidence: float
    risk_verdict: str
    entry_regime: str
    setup: str
    signal_type: str
    volume_spike_ratio: Optional[float]


def _merge(rows: list) -> Position:
    first, last = rows[0], rows[-1]
    qty = sum(float(r.quantity) for r in rows)
    size = sum(float(r.size_usd) for r in rows)
    gross = sum(float(r.pnl_usd) for r in rows)
    state = funding_combine([r.funding_state for r in rows])
    return Position(
        trade_id=first.trade_id,
        symbol=first.symbol,
        direction=first.direction,
        entry_price=first.entry_price,
        exit_price=(sum(float(r.exit_price) * float(r.quantity) for r in rows) / qty
                    if qty > 0 else last.exit_price),
        entry_time=first.entry_time,
        exit_time=last.exit_time,
        quantity=round(qty, 10),
        size_usd=round(size, 2),
        pnl_usd=round(gross, 2),
        pnl_pct=round((gross / size * 100) if size > 0 else 0, 2),
        commission_usd=round(sum(float(r.commission_usd) for r in rows), 2),
        slippage_usd=round(sum(float(r.slippage_usd) for r in rows), 2),
        net_pnl_usd=round(sum(float(r.net_pnl_usd) for r in rows), 2),
        funding_usd=(round(sum(r.funding_usd or 0.0 for r in rows), 4)
                     if state != "unpriced" else None),
        funding_state=state,
        exit_reason=last.exit_reason,
        exit_reasons=tuple(r.exit_reason for r in rows),
        fills=len(rows),
        confidence=first.confidence,
        risk_verdict=first.risk_verdict,
        entry_regime=first.entry_regime,
        setup=first.setup,
        signal_type=first.signal_type,
        volume_spike_ratio=first.volume_spike_ratio,
    )


def positions(trades: Iterable) -> list[Position]:
    """The fill rows grouped into positions by `trade_id`.

    Ordered by each position's FINAL fill, the moment its outcome is known, so
    a streak read in this order is a streak of outcomes. Fills inside a
    position keep their recorded order.
    """
    rows = list(trades or [])
    groups: dict[str, list] = {}
    last_at: dict[str, int] = {}
    for i, t in enumerate(rows):
        groups.setdefault(t.trade_id, []).append(t)
        last_at[t.trade_id] = i
    return [_merge(groups[tid]) for tid in sorted(groups, key=last_at.__getitem__)]


def position_rr(rr_fills: Iterable[tuple]) -> list[float]:
    """Each position's realized R, from ``(trade_id, r, quantity)`` per fill.

    A position's R is its fills' R weighted by the quantity each closed. A
    laddered position (TP1 +1.5R on 50%, TP2 +2.5R on 30%, the runner +1.0R
    on 20%) is one position at +1.7R. Beside one stopped at -1R, the average
    per position is +0.35R; averaged per fill it was +1.0R, because the
    ladder put three rows in and the stop put one.
    """
    acc: dict[str, list] = {}
    for tid, r, qty in rr_fills:
        a = acc.setdefault(tid, [0.0, 0.0])
        a[0] += float(r) * float(qty)
        a[1] += float(qty)
    return [num / den for num, den in acc.values() if den > 0]
