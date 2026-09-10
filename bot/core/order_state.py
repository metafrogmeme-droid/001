"""What a venue's order status MEANS — one vocabulary, read one way.

`live_executor.py` asks this question at four sites off the same
`fetch_order` call, and three of them agree::

    _verify_order_fill      status in ("closed", "filled") and filled > 0   -> FILLED
                            status in ("canceled","cancelled","expired","rejected") -> gone
    _check_pending_limit    status in ("closed","filled","partially_filled") -> FILLED
    execute()               order_status not in ("closed","filled")          -> not filled

The fourth read ``"closed"`` as CANCELLED, and it was the one site that then
did ``del self._positions[trade_id]``. CCXT's unified vocabulary is
``open / closed / canceled / expired / rejected``, where **closed means the
order is done — filled**. So the ordinary way a venue reports a fill was
being booked as a cancel, the position was dropped from the book, and the
operator was told ``CANCELLED``: a live leveraged position with no stop, no
monitor and no record. The ``elif filled > 0`` branch written directly beneath
it — which stamps ``filled_at`` and places the stop — could only ever be
reached for a PARTIAL fill, because the terminal status matched first.

Nothing here does I/O. The point is that the vocabulary is one importable
thing rather than four tuples typed out from memory.
"""

from __future__ import annotations

from typing import Optional

# Terminal: the order is done and quantity changed hands.
FILLED_STATUSES = frozenset({"closed", "filled"})

# Some quantity changed hands and the remainder may still rest.
PARTIAL_STATUSES = frozenset({"partially_filled", "partially filled", "partial"})

# Terminal: the order is gone and (on its own) moved no quantity.
CANCELLED_STATUSES = frozenset({"canceled", "cancelled", "expired", "rejected"})


def read_amount(info, key: str) -> Optional[float]:
    """A venue payload -> a quantity, or None when it was not measured.

    NULL-PRESERVING. `float(info.get("filled") or 0)` reads an absent field, a
    JSON null, an empty string and a real 0 as the same 0 — and every caller
    here branches on `> 0`, so "nothing filled" and "nobody looked" took the
    same path. A measured 0.0 is a genuine reading and survives as 0.0.
    """
    if not isinstance(info, dict):
        return None
    raw = info.get(key)
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val == val else None          # NaN is not a reading


def order_status(info) -> str:
    """Normalised lowercase status, or "" when the venue did not state one.

    "" is distinct from every known status on purpose: it is the input that
    must NOT resolve to a verdict.
    """
    if not isinstance(info, dict):
        return ""
    raw = info.get("status")
    if raw is None:
        return ""
    return str(raw).strip().lower()


def is_filled_status(status: str) -> bool:
    """True for a status that means quantity changed hands."""
    return status in FILLED_STATUSES or status in PARTIAL_STATUSES


#: close_position's "the position is still there" answers. Each is RETURNED,
#: not raised, with the record kept tracked; the answer itself says whether
#: the close re-placed a stop on what it kept (its residual path re-places
#: where it can, its final handler re-places nothing). The first three come
#: from the market-close path; the rest from the pending-limit cancel path,
#: which no post-fill guard can reach today (they all run on an open
#: position) but which the same reader must not file under "closed".
CLOSE_KEPT_OPEN_MARKERS = (
    "CLOSE NOT CONFIRMED",
    "RESIDUAL REMAINS",
    "not found or already closed",
    "Failed to cancel limit order",
    "Could NOT verify the cancel",
    "filled while cancelling",
    "already filled",
    "limit order is gone, but",
)

#: The headings the post-fill guards, the ladder and the monitor put over a
#: close that did NOT happen: a rejected flatten, one close_position kept
#: open, and a position escalated because no stop could be placed. With
#: CLOSE_KEPT_OPEN_MARKERS this is the whole vocabulary a reader of monitor
#: messages needs — alerts_monitor and the engine's loop used to each carry
#: a hand-typed subset of it, and the subset missed the close's own lower-
#: case "kept OPEN" answers.
KEPT_OPEN_HEADINGS = (
    "CLOSE FAILED",
    "KEPT OPEN",
    "DID NOT COMPLETE",
    "URGENT",
    "UNPROTECTED POSITION",
)

#: The one "closed" answer with no card behind it: the close was booked and
#: the report after it raised, before the close slot was written.
CLOSE_CARD_NOT_RENDERED = "the close card could not be rendered"


def close_did_not_happen(msg) -> bool:
    """True when a monitor/guard message reports a close that did NOT happen —
    a flatten close_position rejected or could not complete, or a position
    escalated as unprotected because no stop could be placed. The position is
    still there: the chain must not audit it as auto-closed, and no close
    card may stand in for the text.

    Derived from the one vocabulary, so a new answer in
    CLOSE_KEPT_OPEN_MARKERS is read here without a second list.
    """
    text = msg if isinstance(msg, str) else ""
    return any(k in text for k in CLOSE_KEPT_OPEN_MARKERS + KEPT_OPEN_HEADINGS)


def close_card_is_wrong(msg) -> bool:
    """True when a rendered close card would misdescribe this message.

    Three shapes: the close did not happen (the text is the only warning that
    the position is live); a safety abort that DID close the position but
    whose text — the stop could not be placed — is the thing the operator
    must read; and the booked close whose card was never built. For each of
    these the shared close slot holds an EARLIER close, possibly of the same
    symbol, and a symbol-only guard lets that earlier card through.
    """
    text = msg if isinstance(msg, str) else ""
    return (close_did_not_happen(text)
            or "ENTRY ABORTED" in text
            or CLOSE_CARD_NOT_RENDERED in text)


def flatten_outcome(close_msg) -> str:
    """Read close_position's verdict off its return value.

    close_position signals failure by RETURN VALUE, not by raising: a venue
    error comes back as ``"CLOSE FAILED for …"`` with the position's status
    restored to open, and an unconfirmed or partial close comes back as a
    "kept OPEN" message whose own text says whether the close re-placed a
    stop on what it kept. live_executor says so beside ``_reattempt_post_fill_sl``.
    The post-fill flatten guards did not honour it: each read "the coroutine
    returned" as "the position is closed", so a rejected flatten rested the
    symbol, skipped the stop and headed its card CLOSED over an open,
    over-levered position — and the handler each had written for a failed
    close was reachable only by a raise the real close never makes.

    Three values, because the three states need three different answers:

    ``"closed"``     the position is gone; the caller may stand down.
    ``"failed"``     open and NOT re-protected by the close; treat it exactly
                     like a close that raised.
    ``"kept_open"``  open in whole or in part, or not this caller's to close
                     (already closed, or closing under another path), and
                     already handled by close_position: the caller must not
                     claim a close and must not place a second set of stops.

    Unreadable — ``None``, or not a string — is ``"failed"``: absent is never
    a close. ``tests/test_flatten_outcome.py`` pins every LITERAL return of
    ``_close_position_inner`` against this reading (a ``return name`` is
    classified where the name is built), so a new answer must be classified
    there before it can ship.
    """
    if not isinstance(close_msg, str) or not close_msg:
        return "failed"
    if "CLOSE FAILED" in close_msg:
        return "failed"
    if any(marker in close_msg for marker in CLOSE_KEPT_OPEN_MARKERS):
        return "kept_open"
    return "closed"


def pending_cancel_verdict(order_info) -> dict:
    """After cancelling a resting limit order: what does the venue's order say?

    Four outcomes, and the fourth is the one that was missing::

        filled       quantity changed hands — there is a POSITION to protect
        cancelled    the order is gone and moved nothing
        still_open   the venue answered and the order is still resting
        unreadable   nobody could tell, so no verdict is available

    Returns ``{state, filled_qty, status, detail}``. ``filled_qty`` is
    ``None`` whenever the quantity was not measured — including in the
    ``filled`` state, where the terminal status is the reading and the size
    is a separate one that may be absent. Callers must not substitute 0.

    A cancel with a partial fill behind it reads **filled**, not cancelled:
    the cancelled remainder is not what matters, the quantity that already
    became a position is.
    """
    if not isinstance(order_info, dict):
        return {"state": "unreadable", "filled_qty": None, "status": "",
                "detail": "no order payload from the venue"}

    status = order_status(order_info)
    qty = read_amount(order_info, "filled")

    if status in CANCELLED_STATUSES:
        if qty is not None and qty > 0:
            return {"state": "filled", "filled_qty": qty, "status": status,
                    "detail": f"{status} with {qty} already filled — "
                              f"the filled part is a position"}
        if qty is None:
            return {"state": "cancelled", "filled_qty": None, "status": status,
                    "detail": f"{status} (filled quantity not stated)"}
        return {"state": "cancelled", "filled_qty": qty, "status": status,
                "detail": status}

    if is_filled_status(status):
        return {"state": "filled", "filled_qty": qty, "status": status,
                "detail": f"venue reports {status}"}

    if qty is not None and qty > 0:
        return {"state": "filled", "filled_qty": qty, "status": status or "unstated",
                "detail": f"{qty} filled while cancelling"}

    if status and qty is not None:
        return {"state": "still_open", "filled_qty": qty, "status": status,
                "detail": f"still {status}, nothing filled"}

    if status:
        return {"state": "still_open", "filled_qty": None, "status": status,
                "detail": f"still {status}, filled quantity not stated"}

    return {"state": "unreadable", "filled_qty": qty, "status": "",
            "detail": "venue stated no order status"}


def position_presence(positions) -> dict:
    """A venue position list -> present / flat / unreadable.

    ``any(abs(float(p.get("contracts", 0) or 0)) > 0 for p in positions)`` reads
    a row that states no size as a row with NO POSITION, so an unparseable
    payload and a genuinely flat book arrive at the same `False` — and callers
    act on that `False` by deleting local tracking. An empty list IS a reading
    (the venue looked and found nothing); a row it could not parse is not.

    Returns ``{state, detail}``. ``present`` short-circuits on the first row
    with size, because one readable position settles the question no matter
    what the other rows say.
    """
    if not isinstance(positions, (list, tuple)):
        return {"state": "unreadable", "detail": "no position list from the venue"}
    unreadable = 0
    for row in positions:
        if not isinstance(row, dict):
            unreadable += 1
            continue
        qty = read_amount(row, "contracts")
        if qty is None:
            unreadable += 1
            continue
        if abs(qty) > 0:
            return {"state": "present", "detail": f"{abs(qty)} contracts on the venue"}
    if unreadable:
        return {"state": "unreadable",
                "detail": f"{unreadable} position row(s) stated no size"}
    return {"state": "flat", "detail": "venue lists no exposure on this symbol"}


def rows_for_side(rows, symbol: str, side: str) -> list:
    """The venue rows that could be ``symbol``/``side`` — unreadable included.

    A companion to `position_presence` rather than a replacement: that function
    answers "is there exposure in this list", and a close verification needs
    "is there exposure still on MY side". Pre-filtering and then asking is the
    composition; hand-rolling the whole reading is how
    `_verify_position_closed` ended up with the exact expression this module's
    docstring quotes as the defect.

    A ROW IS DROPPED ONLY WHEN IT IS DEFINITELY SOMEBODY ELSE'S. A row whose
    symbol or side the venue did not state is KEPT, because dropping it would
    silently turn "we could not tell whose this is" into "our side is flat" —
    the same absent-reads-as-a-measurement move one field over. Non-dict rows
    are kept for the same reason: `position_presence` is the thing that gets to
    call them unreadable.
    """
    # The same type guard `position_presence` opens with, and for a sharper
    # reason here: `for row in "not a list"` iterates CHARACTERS, so a string
    # payload would come back as a list of ten unreadable rows rather than
    # nothing. Its own test caught that.
    if not isinstance(rows, (list, tuple)):
        return []
    kept = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        row_symbol = row.get("symbol")
        if row_symbol is not None and row_symbol != symbol:
            continue
        row_side = row.get("side")
        if row_side is not None and str(row_side).lower() != str(side).lower():
            continue
        kept.append(row)
    return kept
