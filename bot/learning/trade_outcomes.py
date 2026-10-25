"""Closed trades recorded for the strategy scorer.

Decision memory is the record the confidence nudge, the calibrator, voter
weights and setup expectancy train on. A paper close stays out of it unless
``LEARN_FROM_PAPER_CLOSES`` is on, because a paper fill is ticker-priced and
adaptive confidence (on by default) would treat it as a live setup.

This file is the other record. Every non-practice close is appended here, with
the strategy name stored on the trade, and that strategy's scorecard is
rebuilt from these rows alone. Nothing here is read by the nudge or by the
three learners above. Nothing here proposes a change or applies one.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from bot.utils.win_rate import profit_factor, win_stats

from .models import DecisionMemory, TradeOutcome
from .strategy_eval import StrategyEvaluator

logger = logging.getLogger("runeclaw.learning.outcomes")

#: A close whose row named no strategy. Scored under this label so it still
#: counts, and so it is not filed as "swing" — that word is the model's
#: default, and a default is not a strategy someone chose.
UNNAMED_STRATEGY = "not recorded"


def as_pnl(value: Any) -> Optional[float]:
    """A finite P&L, or None when the close was not priced.

    ``None``, a bool, NaN and an infinity are not a result. ``0.0`` is a
    measured break-even and is returned as ``0.0``.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def strategy_name(value: Any) -> str:
    """The strategy string on the close, or ``UNNAMED_STRATEGY`` when it has none."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return UNNAMED_STRATEGY


def stored_trade_id(mode: str, trade_id: Any) -> str:
    """The dedupe key: the mode and the trade's own id.

    A paper fill and an exchange fill must not collapse because they happened
    to carry the same id. An empty id is not a key — the caller records the
    row once and cannot recognise it later.
    """
    raw = trade_id if isinstance(trade_id, str) else ("" if trade_id is None else str(trade_id))
    raw = raw.strip()
    if not raw:
        return ""
    return f"{mode}:{raw}"


def load_outcomes(store) -> tuple[Optional[list[TradeOutcome]], bool]:
    """``(rows, unreadable)``.

    A missing file is ``([], False)``: nothing has been recorded. A file that
    is there and will not read is ``(None, True)``.
    """
    path = store._files["trade_outcome"]
    try:
        if not path.exists():
            return [], False
    except OSError as exc:
        logger.error("Trade outcomes could not be stated (%s)", type(exc).__name__)
        return None, True
    try:
        return store.get_trade_outcomes(strict=True), False
    except Exception as exc:  # noqa: BLE001 — an unread file is not an empty one
        logger.error("Trade outcomes could not be read (%s)", type(exc).__name__)
        return None, True


def outcome_stats(rows: Optional[list]) -> dict:
    """Closes, win rate and profit factor over the rows that carry a P&L.

    ``rows is None`` means the file could not be read: every figure is None,
    not zero.
    """
    if rows is None:
        return {"closes": None, "wins": None, "losses": None, "flat": None,
                "scored": None, "unscored": None, "rate": None,
                "profit_factor": None, "last": None, "unreadable": True}
    shaped = [{"pnl": r.pnl_result} for r in rows]
    stats = win_stats(shaped)
    last = None
    for r in rows:
        stamp = r.closed_at or r.timestamp_utc
        if last is None or (stamp is not None and stamp > last):
            last = stamp
    return {
        "closes": stats["total"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "flat": stats["flat"],
        "scored": stats["scored"],
        "unscored": stats["unscored"],
        "rate": stats["rate"],
        "profit_factor": profit_factor(shaped),
        "last": last,
        "unreadable": False,
    }


def _rescore(store, evaluator: StrategyEvaluator, strategy: str) -> None:
    """Rebuild one strategy's scorecard from the outcome rows, and from nothing else."""
    rows, bad = load_outcomes(store)
    if bad or rows is None:
        return
    mine = [r for r in rows if strategy_name(r.strategy) == strategy and r.pnl_result is not None]
    if not mine:
        return
    decisions = [
        DecisionMemory(
            strategy_signal=strategy,
            symbol=r.symbol,
            direction=r.direction,
            pnl_result=r.pnl_result,
            market_regime=r.market_regime,
            mode=r.mode,
            paper_trade_id=r.trade_id,
            source=r.source,
        )
        for r in mine
    ]
    evaluator.evaluate_strategy(strategy, decisions)


def record_scored_outcome(
    store,
    evaluator: StrategyEvaluator,
    *,
    symbol: str,
    direction: str,
    pnl_result: Any,
    strategy: str = "",
    market_regime: str = "",
    trade_id: str = "",
    mode: str = "paper",
    source: str = "paper_outcome",
    closed_at: Optional[datetime] = None,
) -> bool:
    """Append one close and rescore its strategy. True when a new row was stored.

    A close with no readable P&L is not stored. A trade id already on the
    record is not stored again. An unreadable file is left alone.
    """
    pnl = as_pnl(pnl_result)
    if pnl is None:
        return False
    rows, bad = load_outcomes(store)
    if bad or rows is None:
        return False
    key = stored_trade_id(mode, trade_id)
    if key and any(r.trade_id == key for r in rows):
        return False
    name = strategy_name(strategy)
    before = len(rows)
    store.record_trade_outcome(TradeOutcome(
        symbol=symbol if isinstance(symbol, str) else "",
        direction=direction if isinstance(direction, str) else "",
        strategy=name,
        pnl_result=pnl,
        market_regime=market_regime if isinstance(market_regime, str) else "",
        trade_id=key,
        mode=mode if isinstance(mode, str) and mode else "paper",
        source=source if isinstance(source, str) and source else "paper_outcome",
        closed_at=closed_at,
    ))
    after, bad_after = load_outcomes(store)
    if bad_after or after is None or len(after) <= before:
        return False
    _rescore(store, evaluator, name)
    return True


def ingest_closes(store, evaluator: StrategyEvaluator, rows: list[dict]) -> int:
    """Append the closes not already stored, then rescore each strategy once.

    Each dict carries symbol, direction, pnl, strategy, mode, source, and
    optionally trade_id, market_regime and closed_at. Returns how many new
    rows were stored. An unreadable file stores nothing.
    """
    existing, bad = load_outcomes(store)
    if bad or existing is None:
        return 0
    seen = {r.trade_id for r in existing if r.trade_id}
    pending: list[TradeOutcome] = []
    names: set[str] = set()
    for row in rows:
        pnl = as_pnl(row.get("pnl"))
        if pnl is None:
            continue
        raw_mode = row.get("mode")
        mode = raw_mode if isinstance(raw_mode, str) and raw_mode else "paper"
        key = stored_trade_id(mode, row.get("trade_id"))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        name = strategy_name(row.get("strategy"))
        names.add(name)
        symbol = row.get("symbol")
        direction = row.get("direction")
        regime = row.get("market_regime")
        source = row.get("source")
        closed = row.get("closed_at")
        pending.append(TradeOutcome(
            symbol=symbol if isinstance(symbol, str) else "",
            direction=direction if isinstance(direction, str) else "",
            strategy=name,
            pnl_result=pnl,
            market_regime=regime if isinstance(regime, str) else "",
            trade_id=key,
            mode=mode,
            source=source if isinstance(source, str) and source else "paper_outcome",
            closed_at=closed if isinstance(closed, datetime) else None,
        ))
    if not pending:
        return 0
    before = len(existing)
    for item in pending:
        store.record_trade_outcome(item)
    after, bad_after = load_outcomes(store)
    if bad_after or after is None or len(after) <= before:
        return 0
    for name in names:
        _rescore(store, evaluator, name)
    return len(after) - before
