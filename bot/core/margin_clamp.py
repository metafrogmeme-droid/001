"""Sizing a live order against the free margin the exchange reported.

RC-2026-017. The clamp lived inline at two sites in `engine.py` and read::

    available = live_bal.get("free", 0.0)

`live_bal["free"]` is built by `LiveExecutor.fetch_balance` from
`balance.get(self._venue.balance_coin, {}).get("free", 0)`, so an absent
balance-coin entry -- a USDC-margined venue, a response shape ccxt parses
differently -- produced `0.0` beside a `total` taken from the raw equity and
therefore still correct. The payload said "you hold $512 of equity and $0 of it
is available", the clamp sized every order at $0, and the audit chain recorded

    Live size clamped: $50.00 -> $0.00 (exchange available)

a dollar figure with the word "exchange" beside it, sealed into a
tamper-evident record, for a number nobody measured.

THREE OUTCOMES, and the deliberate choice is between the last two:

    a number   -- clamp to it, or pass the size through
    0.0        -- MEASURED: capital is fully deployed. Refuse: "insufficient".
    None       -- nobody read it. Refuse: "unreadable".

Refusing on `None` keeps today's OUTCOME exactly -- a $0 order is rejected by
the venue, so nothing opens either way -- while correcting the reason. The
alternative, skipping the clamp when the margin is unreadable, lets the order
through at full risk size: a position that does not open today would open
after, which is a loosening of live-money behaviour dressed as a display fix.
"""
from typing import Any, Optional, Tuple

# `total` and `error` are deliberately untouched by this module. bot/main.py
# classifies startup auth failures on exactly those two keys to select
# "STARTUP: exchange auth FAILED" and call set_live_auth_status(False), which
# halts new live entries. A sibling finding's endorsed remedy proposed
# returning None instead of the zeros dict and would have silently removed
# that halt.


def read_money_field(payload: Any, key: str) -> Optional[float]:
    """The figure the exchange actually reported for `key`, or None.

    `0.0` is a real reading -- fully-deployed capital, an empty wallet -- and
    must not double as "no reading", which is why this tests presence rather
    than truthiness.

    Generalised from `read_free_margin` because `free` was never the only
    field with this problem. The line directly below the one this finding
    fixed read `float(usdt.get("used", 0))`, minting the same fabricated
    measurement from the same absent balance-coin entry, and two other
    surfaces did it with `or 0`. One definition of "what counts as a reading"
    means the next field inherits it.
    """
    if not isinstance(payload, dict):
        return None
    if key not in payload:
        return None
    raw = payload[key]
    if isinstance(raw, bool) or raw is None or raw == "":
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf are not money
        return None
    return f


def read_free_margin(payload: Any) -> Optional[float]:
    """The free margin the exchange actually reported, or None."""
    return read_money_field(payload, "free")


def clamp_to_free_margin(
    size_usd: float, live_bal: Any
) -> Tuple[Optional[float], Optional[str]]:
    """Return (size_to_place, reason).

    `size_to_place` is None when the trade must NOT proceed; `reason` is then
    "unreadable" (nobody read the margin) or "insufficient" (the exchange
    reported no free margin). A size with reason "clamped" was reduced to a
    real reported figure; reason None means it passed through untouched.

    `live_bal` of None means the balance fetch itself failed and the clamp is
    skipped -- pre-existing, documented behaviour at the call sites, unchanged
    here so this commit does not quietly widen what it touches.
    """
    if live_bal is None or live_bal == {}:
        return size_usd, None

    # An error payload carries zeros for every numeric field. Those zeros are
    # the error's filler, not a balance.
    if isinstance(live_bal, dict) and live_bal.get("error"):
        return None, "unreadable"

    available = read_free_margin(live_bal)
    if available is None:
        return None, "unreadable"
    if available <= 0:
        return None, "insufficient"
    if size_usd > available:
        return available, "clamped"
    return size_usd, None
