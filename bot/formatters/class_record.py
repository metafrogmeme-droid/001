"""The viewer's live record in an idea's asset class, for the card that offers it.

9 October, live `/parity` (232 strategy exits): Stock 52 trades, 23% won, PF
0.19; ETF 11 trades, PF 0.03; Crypto 159 trades, 45% won, PF 0.73. Every live
order is a tap (no eligibility record ships), so the place that evidence can
change a decision is the card with the Confirm button. The owner wants every
class scannable and tradeable; this puts the class's record beside the door.

ONE READING OF A TRADE. The row is `/parity`'s own: `parity.class_rows` over
the executor's closed positions in the shape its file holds (`closed_trade_row`),
which is `strategy_exits` (filled, priced, not an execution abort) bucketed by
`category_for_symbol` and scored by parity's `_row`. A card that counted a
different population from the report it quotes would be a second answer.
`/classpf` still counts every filled close (execution aborts included), so its
trade counts can run higher than this line's.

WHOSE BOOK. `live_view(user_id)` is the reading every record card takes. Under
per-user live off (the default) it hands every caller the operator's
executor, so the operator's record is shown to the operator only: anyone else
gets no line, never somebody else's trades presented as theirs.

WHAT IS NOT SAID. Fewer than `MIN_TRADES` trades in the class is no line: a
record that small says nothing either way. A record the executor read only in
part is said with that caveat, never as the whole. Counts and a ratio only.
"""
from __future__ import annotations

from typing import Any, Optional

#: Below this many strategy exits in a class the card says nothing about it.
#: The same floor the agent cards use for "low sample" (`app/lib/agent_record.js`).
MIN_TRADES = 10


def class_record(executor: Any, category: str) -> Optional[dict]:
    """``{"category", "trades", "wins", "pf", "partial"}`` for ``category`` on
    ``executor``'s closed record, or None when it holds no strategy exit there.

    The counts and the ratio are `/parity`'s row for the class
    (`parity.class_rows`); ``pf`` is None when the class has no losing trade
    (no ratio over nothing). ``partial`` is `closed_record_partial`'s reading
    of the same executor.
    """
    from bot.backtest.parity import class_rows
    from bot.core.live_executor import closed_trade_row
    from bot.formatters.realized_totals import closed_record_partial

    partial = closed_record_partial(executor)
    rows = [closed_trade_row(p)
            for p in (getattr(executor, "closed_positions", None) or [])]
    row = class_rows(rows).get(category)
    if row is None:
        return None
    return {"category": category, "trades": row["trades"], "wins": row["wins"],
            "pf": row["pf"], "partial": partial}


def class_record_line(engine: Any, user_id: Any, symbol: str) -> Optional[str]:
    """One card line: the viewer's live record in ``symbol``'s asset class, or
    None when there is nothing the viewer may be told about it.

    Never raises: a card is not refused for a line it could not build.
    """
    # No caller is nobody's book, never the operator's.
    if user_id is None or not str(user_id).strip():
        return None
    try:
        from bot.core.market_scanner import category_for_symbol, category_icon

        view = engine.live_view(str(user_id))
        scope, executor = view.get("scope"), view.get("executor")
        if executor is None or scope not in ("operator", "own"):
            return None
        if scope == "operator" and not engine._is_operator_user(user_id):
            return None
        category = category_for_symbol(str(symbol or ""))
        rec = class_record(executor, category)
        if rec is None or rec["trades"] < MIN_TRADES:
            return None
        pf = "—" if rec["pf"] is None else f"{rec['pf']:.2f}"
        caveat = " (record read in part)" if rec["partial"] else ""
        return (f"{category_icon(category)} {category} on your live book: "
                f"{rec['wins']} of {rec['trades']} won · PF {pf}{caveat}")
    except Exception:  # noqa: BLE001 -- the card stands without this line
        return None
