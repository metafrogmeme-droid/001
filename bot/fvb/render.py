"""Text card for a board. No currency sign, no token name."""
from __future__ import annotations

from typing import Mapping

from bot.fvb.packet import LABEL

BANNER = "Admin preview. Not public."


def _num(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unreadable"
    return f"{value:.6g}"


def card_text(board: Mapping[str, object], *, preview: bool) -> str:
    """One card. A missing board says it is not published."""
    lines: list[str] = []
    if preview:
        lines.append(BANNER)
    lines.append(LABEL)
    available = board.get("available")
    if available is not True:
        reason = board.get("reason")
        if reason == "unreadable":
            lines.append("Board unreadable.")
        else:
            lines.append("Board not published.")
        return "\n".join(lines)
    rows = board.get("rows")
    if not isinstance(rows, list) or not rows:
        lines.append("Board not published.")
        return "\n".join(lines)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        symbol = row.get("symbol")
        lines.append(
            f"{symbol} session={row.get('session')} confirmed={row.get('confirmed')} "
            f"last={_num(row.get('last_trade'))} external={_num(row.get('external'))} "
            f"fv={_num(row.get('fv'))} "
            f"band={_num(row.get('band_lo'))}..{_num(row.get('band_hi'))} "
            f"w_ext={_num(row.get('w_ext'))} "
            f"borrow_power={_num(row.get('borrow_power'))} "
            f"liquidation={_num(row.get('liquidation'))} "
            f"oi_scale={_num(row.get('oi_scale'))} unit={row.get('unit')}"
        )
    return "\n".join(lines)
