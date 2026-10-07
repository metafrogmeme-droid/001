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
              breaker_open: bool,
              equity_source: Optional[str] = None) -> Optional[tuple[str, str, str]]:
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
    # What the equity includes is the reading's own (`equity_source`, from
    # the balance the gate read). The coin's wallet balance excludes open
    # positions, and a reading that does not say gets no claim either way.
    if source == "live":
        from bot.core.equity_basis import MARKED, WALLET, equity_basis
        kind = equity_basis(equity_source)
        if kind == MARKED:
            includes = ", open positions marked to market included"
        elif kind == WALLET:
            includes = (", read from the coin's wallet balance: open positions' "
                        "unrealized profit and loss is not in it")
        else:
            includes = ""
        basis = (
            "This percent is live account equity versus its high-water mark"
            f"{includes}. It is not closed-trade profit and loss.\n"
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


# ── the cleared card, and the engine's HALTED card ────────────────────────
#
# Both were built inline in `ProactiveMonitor` beside the trip card, and both
# said something nobody had measured. The cleared card printed "Risk limits
# are back within tolerance. Trading operations have resumed." for every
# open-to-closed transition: a manual /reset after a drawdown trip, where the
# limit was not recovered but the peak discarded; a daily-loss reset at UTC
# rollover; a streak cool-off. The HALTED card printed the moment the monitor
# NOTICED as "Halted At" and the monitor's last SAMPLE as "Previous State",
# the same shape the trip card was cured of (`tripped_at_line`). Each line
# here reads the engine's own record (`RiskEngine.last_breaker_clear`, the
# transition into HALTED in `state_history`) or says the record is missing.

#: Verb-less, so the sentence after it can be read either way round.
_HOW = {
    "manual": "the operator (/reset or /resume)",
    "daily_rollover": "the daily-loss auto-reset at UTC day rollover",
    "streak_cooloff": "the loss-streak cool-off (STREAK_BREAKER_AUTORESET_HOURS)",
}


def trip_dedup_key(tripped_at: Any) -> str:
    """One dedup key PER TRIP, never one for all trips.

    The key used to be the constant ``cb_tripped`` under a five-minute
    cooldown, so a trip, a /reset and a second trip inside five minutes sent
    the HALTED card and no trip card: the operator saw a halt with no reason
    on it. A trip with its own recorded time is its own event; one without
    (an engine stand-in) keeps the constant, which is what it can say."""
    at = _num(tripped_at)
    return f"cb_tripped:{int(at)}" if at is not None and at >= 0 else "cb_tripped"


def clear_dedup_key(clear: Any) -> str:
    """One dedup key PER CLEAR, for the same reason."""
    at = _num(clear.get("at")) if isinstance(clear, dict) else None
    return f"cb_cleared:{int(at)}" if at is not None and at >= 0 else "cb_cleared"


def cleared_by_line(how: Any, at: Any, noticed_at: float) -> str:
    """``Cleared by``: the closing path, on the engine's own clock.

    A record this process does not hold (an engine stand-in without the
    field, or a breaker a build before this record closed) is said to be
    missing, with the moment the monitor noticed labelled as that."""
    at_f = _num(at)
    stamp = _stamp(at_f, with_day=False) if at_f is not None and at_f >= 0 else None
    who = _HOW.get(how) if isinstance(how, str) else None
    if who is None:
        return ("not on record (this process did not close it; noticed at "
                f"{_stamp(float(noticed_at), with_day=False)})")
    return f"{who} at {stamp}" if stamp else f"{who} (time not on record)"


def _cleared_meaning(how: Any, person_peak: Any) -> str:
    """What the clear DID, which is never "the limits are back"."""
    person = person_peak if isinstance(person_peak, str) else ""
    if how == "manual":
        text = ("Nothing was measured as back within tolerance: the trip was "
                "overridden. The drawdown peak is forgotten and re-measured "
                "from the next equity read, and today's daily-loss budget "
                "restarts from zero.")
        if person == "reseeded":
            text += " The person-level peak was re-seeded too."
        elif person.startswith("unreadable"):
            cls = person.split(":", 1)[1] if ":" in person else "unknown"
            text += (" The person-level peak could <b>not</b> be re-seeded "
                     f"(store unreadable: <code>{cls}</code>); a person-level "
                     "drawdown can re-trip on the next evaluation.")
        return text
    if how == "daily_rollover":
        return ("The UTC day rolled over, so today's daily-loss budget is "
                "fresh. The drawdown peak is unchanged; if today is also a "
                "loss, the breaker re-trips on the next evaluation.")
    if how == "streak_cooloff":
        return ("The cool-off since the last loss elapsed, so the streak "
                "counter is zero. The drawdown peak and the daily-loss "
                "budget are unchanged.")
    return ("How it cleared is not on record, so this card cannot say what "
            "is back within tolerance. /status shows the gate's reading now.")


def gate_after_clear_line(gate: Optional[str]) -> tuple[str, bool]:
    """``(line, open)`` from `trading_blocked_by` read AFTER the clear.

    "" is an open gate; a reason string means entries are still refused
    (the resume card's vocabulary, one reading); None means the gate could
    not be read, which is said rather than rounded to "resumed"."""
    if gate is None:
        return ("⚪ Could not read the entry gate after the clear — "
                "check /status before trusting this card.", False)
    if gate:
        from bot.warroom.warroom_bot import resume_gate_line
        return resume_gate_line(gate).strip(), False
    return "New entries are <b>open</b>.", True


def cleared_card(*, clear: Any, gate: Optional[str], noticed_at: float) -> tuple[str, str]:
    """``(title, body)`` for a breaker found closed after it was open.

    ``clear`` is `RiskEngine.last_breaker_clear` (a dict, or None / a
    stand-in's attribute, both read as "not on record"); ``gate`` is
    `trading_blocked_by` read after the clear. The word "resumed" never
    appears: the card says the gate is open, or what still refuses, or that
    it could not read the gate."""
    rec = clear if isinstance(clear, dict) else {}
    # A gate that is not a string (a stand-in, a mock) is a gate nobody read.
    gate = gate if isinstance(gate, str) else None
    how = rec.get("how")
    cause = rec.get("cause")
    cause_txt = cause if isinstance(cause, str) and cause else "not on record"
    tripped = _num(rec.get("tripped_at"))
    when = (f", at <code>{_stamp(tripped, with_day=False)}</code>"
            if tripped is not None and tripped >= 0 else "")
    gate_line, is_open = gate_after_clear_line(gate)
    body = (
        "✅ <b>CIRCUIT BREAKER CLEARED</b>\n"
        f"{SEP}\n"
        f"- Cleared by: <code>{cleared_by_line(how, rec.get('at'), noticed_at)}</code>\n"
        f"- It had tripped on: <code>{cause_txt}</code>{when}\n\n"
        + _cleared_meaning(how, rec.get("person_peak")) + "\n\n"
        + gate_line + "\n"
        + ("\U0001f680 The engine resumes scanning on its next cycle.\n" if is_open else "")
        + f"{SEP}\n"
        + "\U0001f449 /status — confirm engine state\n"
        + "\U0001f449 /health — check system vitals"
    )
    return "Circuit Breaker Cleared", body


def halted_card(*, prev_state: Optional[str], reason: str, halted_at: Any,
                last_seen: str, noticed_at: float) -> tuple[str, str]:
    """``(title, body)`` for the engine entering HALTED.

    ``prev_state`` / ``reason`` / ``halted_at`` are the engine's own
    transition record (the last `StateTransition` into HALTED); with none on
    record the card prints the monitor's last sample and notice time, each
    labelled as that, instead of calling them the engine's."""
    at = _num(halted_at)
    when = _stamp(at, with_day=False) if at is not None and at >= 0 else "not on record"
    if prev_state:
        lines = (
            f"- Previous state: <code>{prev_state}</code>\n"
            f"- Halted at: <code>{when}</code>\n"
            + (f"- Reason: <code>{reason}</code>\n" if reason else "")
        )
    else:
        lines = (
            f"- Last state seen by the monitor: <code>{last_seen or 'UNKNOWN'}</code>\n"
            f"- Noticed at: <code>{_stamp(float(noticed_at), with_day=False)}</code> "
            "(the engine's own transition is not on record)\n"
        )
    body = (
        "⛔ <b>ENGINE HALTED</b>\n"
        f"{SEP}\n"
        + lines
        + "\nNo new scans or analyses will run.\n"
        "All automated trading is paused; open positions are still "
        "monitored for SL/TP.\n"
        "The breaker's own card says why it tripped; if none arrived, "
        "/status shows the trip reason.\n"
        f"{SEP}\n"
        "\U0001f449 /status — review engine details\n"
        "\U0001f449 /health — check system vitals\n"
        "\U0001f449 /reset — resume after review"
    )
    return "Engine HALTED", body
