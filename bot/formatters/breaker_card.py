"""The circuit-breaker trip card and the drawdown early-warning card, pure.

Both were built inline in `ProactiveMonitor`, so nothing could plant a state
and read what the operator would be told. Driven on the real engine and the
real monitor, one tick printed, from a `drawdown_status()` answering 8.59%
against a 7.00% limit:

    - Drawdown: see cause                 (the figure was retained; the comment
                                           over the line said it was not)
    - Daily loss: -0.09% (of equity)      (a +$8 WINNING day: the gate's
                                           MAGNITUDE behind a hard-coded minus)
    - Open Positions: 0                   (a book nobody read, on a restart)
    - Triggered At: 15:15:58 UTC          (the moment the monitor's first pass
                                           NOTICED a breaker restored open)

and beside it, for the same 8.59%:

    ⚠️ DRAWDOWN AT 85% OF LIMIT           (123% of it)
    The risk engine halts all entries at 100% of the limit.
    Consider reducing size ...            (the breaker had already tripped)

The tier label was fixed at 85 for any fraction past 0.85, so the header named
a tier the measurement was past, and the sentence promised a halt that had
happened. Each line here is three-valued where the reading is: a figure with
its source and limit, or the word "unread" -- never `N/A` for a measured zero,
never `0` for a book nobody read, never a minus sign over a sign nobody took.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from bot.formatters.drawdown_card import drawdown_source_note

SEP = "─" * 16

#: Below the limit, the early-warning tiers; 100 is the limit itself, which is
#: not an early warning: past it the breaker has tripped or trips on the next
#: entry evaluation.
TIERS = (50, 75, 85, 100)


def _num(v: Any) -> Optional[float]:
    """A finite float, or None: a card must not print a MagicMock, a NaN or
    a string as a figure."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def drawdown_line(drawdown: Any) -> str:
    """``(dd, source, limit)`` from `enforced_drawdown`, as one line.

    Each half is validated on its own: a payload can carry a readable figure
    and no limit, and folding them would drop the half that arrived."""
    try:
        dd, source, limit = drawdown
    except (TypeError, ValueError):
        return "unread (the gate's reading could not be read)"
    dd_f, lim_f = _num(dd), _num(limit)
    if dd_f is None:
        # Two absences, two sentences: a gate nobody could read, and a LIVE
        # deployment whose only figure is the paper snapshot's -- after a
        # restart no live equity has been read yet, and the trip card used
        # to print "0.00% (paper snapshot)" under a live trip.
        why = ("no live equity has been read since this process started"
               if source == "paper_in_live"
               else "the gate's reading could not be read")
        return f"unread ({why})" + (f", limit {lim_f:.2f}%" if lim_f is not None else "")
    note = drawdown_source_note(source if isinstance(source, str) else None)
    if lim_f is None:
        return f"{dd_f:.2f}%{note}, limit unread"
    return f"{dd_f:.2f}%{note}, limit {lim_f:.2f}%"


def daily_pnl_line(daily: Any) -> str:
    """``(signed pct, basis)`` from `RiskEngine.last_daily_pnl_reading`.

    None is a day nobody has measured since this process started -- the
    gate measures it at evaluation time, and a fresh boot has evaluated
    nothing. A measured ``0.00%`` prints as a figure: it is a reading."""
    try:
        pct, basis = daily
    except (TypeError, ValueError):
        return "unread (no evaluation has measured today since this process started)"
    pct_f = _num(pct)
    if pct_f is None:
        return "unread (no evaluation has measured today since this process started)"
    basis_txt = {"live": " (realized live closes)", "paper": " (paper book)"}.get(
        basis if isinstance(basis, str) else "", "")
    return f"{pct_f:+.2f}% of equity{basis_txt}"


def positions_line(book: Any) -> str:
    """``(open, resting)`` counts of the operator's book, or None for a book
    nobody read. A resting limit order is not an open position, and a count
    of zero is a reading."""
    if (not isinstance(book, (tuple, list)) or len(book) != 2
            or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in book)):
        return "unread"
    n_open, n_resting = int(book[0]), int(book[1])
    if n_resting > 0:
        return f"{n_open} (+{n_resting} resting order{'s' if n_resting != 1 else ''})"
    return str(n_open)


def _stamp(at: float, with_day: bool) -> str:
    dt = datetime.fromtimestamp(at, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC" if with_day else "%H:%M:%S UTC")


def tripped_at_line(tripped_at: Any, noticed_at: float, restored: bool) -> tuple[str, str]:
    """``(label, value)``: the trip's OWN recorded time when there is one.

    A fresh trip with no time on record (an engine stand-in without the
    field) is labelled as the moment it was noticed, which is what it is; a
    restored breaker with none says the time is not on record, because the
    boot is not when it tripped."""
    at = _num(tripped_at)
    if at is not None and at >= 0:
        return "Tripped at", _stamp(at, with_day=restored)
    if restored:
        return "Tripped at", "not on record (before this process started)"
    return "Noticed at", f"{_stamp(float(noticed_at), with_day=False)} (the trip's own time is not on record)"


def trip_card(*, cause: str, drawdown: Any, daily: Any, book: Any,
              tripped_at: Any, noticed_at: float, restored: bool) -> tuple[str, str]:
    """``(title, body)`` for a breaker found open.

    ``restored`` is the monitor's FIRST pass finding it open: the trip
    happened before this process started, and the card says so rather than
    announcing a fresh trip at boot time."""
    label, when = tripped_at_line(tripped_at, noticed_at, restored)
    if restored:
        title = "Circuit Breaker OPEN at startup"
        head = ("\U0001f6a8 <b>CIRCUIT BREAKER OPEN AT STARTUP</b>\n"
                f"{SEP}\n"
                "The breaker was tripped before this process started and is "
                "still open: <b>new entries are halted</b>.\n\n")
    else:
        title = "Circuit Breaker TRIPPED"
        head = ("\U0001f6a8 <b>CIRCUIT BREAKER TRIPPED</b>\n"
                f"{SEP}\n"
                "The risk engine has <b>halted all new entries</b>.\n\n")
    body = (
        head
        + f"- Reason: <code>{cause}</code>\n"
        + f"- Drawdown: <code>{drawdown_line(drawdown)}</code>\n"
        + f"- Daily P&L: <code>{daily_pnl_line(daily)}</code>\n"
        + f"- Open positions: <code>{positions_line(book)}</code>\n"
        + f"- {label}: <code>{when}</code>\n\n"
        + "If the reason looks wrong (e.g. a stale drawdown after an auth "
          "blip), <code>/resume</code> re-seeds the high-water mark and "
          "clears it.\n\n"
        + "\U0001f6e1 Open positions are still monitored for SL/TP.\n"
        + f"{SEP}\n"
        + "\U0001f449 /status — review engine state\n"
        + "\U0001f449 /positions — inspect open trades\n"
        + "\U0001f449 /reset — clear after review"
    )
    return title, body


def drawdown_tier(frac: float) -> int:
    """The highest tier ``frac`` (drawdown / limit) has reached, or 0."""
    tier = 0
    for t in TIERS:
        if frac >= t / 100.0:
            tier = t
    return tier


def tier_card(*, frac: float, dd: float, source: Optional[str], limit: float,
              breaker_open: bool) -> Optional[tuple[str, str, str]]:
    """``(title, body, severity)`` for a tier crossed, or None for no card.

    The header names the MEASURED fraction of the limit, never the tier
    label: at 123% the old card said "85% OF LIMIT". Past the limit the
    sentence says what is true of the breaker -- it has tripped, in which
    case the trip card is the card and this returns None, or it trips on
    the next entry evaluation and nothing has been halted yet."""
    pct_of_limit = frac * 100.0
    past = frac >= 1.0
    if past and breaker_open:
        return None
    sev = "CRITICAL" if frac >= 0.85 else "WARNING"
    note = drawdown_source_note(source)
    # The percent is account equity versus its peak. Open marks, fees, funding
    # and transfers move that equity, and a book of winning closes can still
    # sit under the peak. "Drawdown" alone was read as closed losses. Name the
    # basis; do not say the closes lost.
    if source == "live":
        basis = (
            "This percent is live account equity versus its high-water mark, "
            "open positions marked to market included. It is not closed-trade "
            "profit and loss.\n"
        )
    else:
        basis = ""
    if past:
        tail = ("That is past the limit. The breaker trips on the next entry "
                "evaluation; nothing has been halted yet.\n"
                "Review open risk now. If the reading is wrong (a stale peak "
                "after an auth blip), <code>/resume</code> re-seeds the "
                "high-water mark.\n")
    else:
        tail = ("The risk engine halts all entries at 100% of the limit.\n"
                "Consider reducing size or reviewing open risk now.\n")
    body = (
        f"⚠️ <b>DRAWDOWN AT {pct_of_limit:.0f}% OF LIMIT</b>\n"
        f"{SEP}\n"
        f"- Current drawdown: <code>{dd:.2f}%</code>{note}\n"
        f"- Circuit-breaker limit: <code>{limit:.2f}%</code>\n\n"
        + basis
        + tail
        + f"{SEP}\n"
        "\U0001f449 /status — review engine state\n"
        "\U0001f449 /positions — inspect open trades"
    )
    return f"Drawdown {pct_of_limit:.0f}% of limit", body, sev
