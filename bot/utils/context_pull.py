"""Context lines the website reads for the cards, pulled over the bot channel.

The website holds the one Flovia client (app/lib/flovia.js) and the one key;
the bot asks the website and prints what it says. ``smart_money_reading``
answers the website's own three-valued result: ``{"read": True, ...}`` or
``{"read": False, "reason": ...}``, and a channel that is unconfigured,
unreachable or answering anything but 200 is a reason of its own, never an
empty line. A dead website is remembered for a minute so a scan that renders
five cards pays one timeout, not five.
"""

from __future__ import annotations

import time as _t

from bot.utils.duel_pull import _call, is_configured

#: How long one unreachable answer stands for, per symbol.
UNREACHABLE_MEMO_SEC = 60
_memo: dict[str, tuple[float, dict]] = {}


def _ticker(symbol: object) -> str:
    return str(symbol or "").strip().upper().split("/")[0].split(":")[0]


def smart_money_reading(symbol: object) -> dict:
    """The website's smart-money line for ``symbol``, or why there is none."""
    ticker = _ticker(symbol)
    if not ticker:
        return {"read": False, "reason": "malformed"}
    if not is_configured():
        return {"read": False, "reason": "unconfigured"}
    hit = _memo.get(ticker)
    if hit and hit[0] > _t.monotonic():
        return dict(hit[1])
    status, body = _call(f"/api/bot/sync/context/smart-money?symbol={ticker}")
    reading = body.get("reading") if isinstance(body, dict) else None
    if status == 200 and isinstance(reading, dict) and "read" in reading:
        return reading
    out = {"read": False, "reason": "unreachable",
           "detail": f"website answered {status}" if status is not None else "website unreachable"}
    _memo[ticker] = (_t.monotonic() + UNREACHABLE_MEMO_SEC, out)
    return dict(out)


def forget() -> None:
    """Tests: drop the memo."""
    _memo.clear()
