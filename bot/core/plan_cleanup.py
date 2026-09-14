"""
Which of a symbol's resting stop/take-profit orders a re-place may cancel.

`_place_sl_tp` clears the plan orders it finds on a symbol before it places
new ones — the right idea (a stale stop beside a fresh one is the double-close
the cleanup's own comment names) with no side filter: every plan order on the
symbol, both sides. The bot's own book never holds both sides of one symbol
(the duplicate-symbol guard is direction-agnostic), so the hazard needs a
position the bot did not open — an operator's manual opposite-side position,
or an adopted orphan short beside a bot long — on a HEDGE-mode account. There,
re-placing the long's protection cancelled the short's stop and placed only
one: a real position, unprotected, by the code whose job is protection.

The rule here is the asymmetry `order_state.rows_for_side` already states for
position rows, pointed the other way. A row is CANCELLED only when it is
definitely THIS side's. In hedge mode a row whose side the venue did not state
is KEPT and reported, because the two ways of being wrong are not the same
size: a reduce-only stop that survives on the same side cannot double-close
(the venue refuses a reduce-only order with no position under it), while a
stop stripped from the other side leaves real money naked. One-way accounts
keep the full sweep they always had — only one side can exist there, so every
row is ours — and an account whose mode is not yet known takes the hedge rule,
because the cost of guessing wrong is a naked position on one side and a
duplicate that cannot fire on the other.

The side is read three ways, all documented for the venue: ``posSide`` or
``holdSide`` on the raw row (the UTA strategy-order listing states
``posSide``), else the normalised order side — a stop that CLOSES a long is a
sell, so ``sell`` protects the long and ``buy`` the short. Nothing else is a
reading.
"""

from __future__ import annotations

from typing import Any, Optional

PROTECTS = ("long", "short")


def plan_row_protects(row: Any) -> Optional[str]:
    """The position side a resting plan order protects — ``"long"``,
    ``"short"``, or None when the row states no side the venue documents."""
    if not isinstance(row, dict):
        return None
    info = row.get("info")
    if isinstance(info, dict):
        for key in ("posSide", "holdSide"):
            val = info.get(key)
            if isinstance(val, str) and val.lower() in PROTECTS:
                return val.lower()
    side = row.get("side")
    if isinstance(side, str):
        s = side.lower()
        if s == "sell":
            return "long"      # a close-side sell is a long's stop
        if s == "buy":
            return "short"
    return None


def plan_rows_to_cancel(rows: Any, *, hedge_mode: Optional[bool],
                        protects: str) -> tuple[list, list]:
    """``(cancel, kept)`` over the plan orders found on a symbol before a
    re-place for the position side ``protects``.

    One-way (``hedge_mode`` False): every row is cancelled — one side is all
    the account can hold, so every plan order on the symbol is this
    position's. Hedge mode (True) and an undetected mode (None): a row is
    cancelled only when it definitely protects ``protects``; a row protecting
    the OTHER side is left alone silently (it is somebody's stop), and a row
    whose side could not be read is left alone and handed back in ``kept`` so
    the caller can say so — leaving it is the cheaper mistake.
    """
    if protects not in PROTECTS:
        raise ValueError(f"protects must be one of {PROTECTS}, not {protects!r}")
    if not isinstance(rows, (list, tuple)):
        return [], []
    if hedge_mode is False:
        return list(rows), []
    cancel: list = []
    kept: list = []
    for row in rows:
        side = plan_row_protects(row)
        if side == protects:
            cancel.append(row)
        elif side is None:
            kept.append(row)
        # else: the other side's protection — not ours to touch
    return cancel, kept
