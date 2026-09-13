"""One closed trade, read from the record and told three-valued.

The seam behind the chat's ``trade_postmortem`` tool and ``/postmortem``. The
web welcome card and the dashboard's "Ask AI" button promise "a post-mortem
of any trade", and every phrasing of that request reached the model with two
prices and a P&L — no stop, no signal type, no regime, no exit reason, no R —
so the post-mortem was narrated: a thesis the model inferred from the price
path, told back as the bot's reasoning. The record holds more than the model
was handed. The caller's own closed position carries the plan (entry, stop,
target), the outcome (exit, net P&L, fees, close reason), the strategy and
signal type, the hold time and the origin; the journal entry for that trade,
when there is one, carries the regime it closed in, and — for closes that
were scored at entry — the confidence and the signals. Everything here is
read from those two records or said to be absent.

Pure: takes a BOOK and a journal, reads nothing else. The book is either the
caller's live executor (``closed_positions``, LivePosition rows) or the
caller's paper portfolio (``trade_history``, TradeExecution rows) — the same
rows ``/journal`` and the ``trade_journal`` tool render — and the card says
which. The trade is resolved from the CALLER's book (``engine.viewer_executor``
or ``engine.user_portfolios.get``), so a trade id can only ever name a close
on the caller's own account. The journal is a single store fed by every
account's close callback; it is read by an id the caller already owns, the
entry must DESCRIBE the position it is attached to (adopted ids are built
from symbol + a one-second timestamp and collide across accounts), and an
entry that records its owner is handed only to that owner.

Two kinds of row are not trades and are not told as one. An order that never
filled (expired, cancelled, rejected, price-drift, stale) is appended to the
closed book with ``pnl_usd=0.0`` — a measured break-even on capital that was
never deployed, and the most recent "close" on the book right after a limit
order lapsed. It is skipped when the latest close is looked up and, when it
is named by id, told as an order that never filled, with no Outcome figures.
"""
from __future__ import annotations

import html
import re
from typing import Any, Optional

from bot.core.live_executor import position_size_basis
from bot.utils.close_reason import NON_FILL_CLOSE_REASONS
from bot.utils.leveraged_return import realized_margin_return_pct
from bot.utils.trade_filter import NON_TRADE_CLOSE_REASONS

#: A close reason as the record writes it: "TP HIT (exchange)", "SL HIT
#: (inferred)", "TRAILING SL HIT", "manually closed", "CLOSED (unknown)",
#: "time_stop", "flatten: manual". Letters, digits, space and light
#: punctuation, bounded, and escaped before it is printed. The first draft
#: accepted a bare identifier and so rendered EVERY exchange-reconciled reason
#: as "not recorded" — an absence manufactured from a value on record.
_REASON = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _\-:().,/]{0,63}")
#: A Python exception's text is not a close reason, whatever charset it fits.
_EXC_TEXT = re.compile(r"[A-Z]\w*(?:Error|Exception|Timeout|Warning)\b\s*:")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-:.]{0,63}")

READINGS = ("found", "no_match", "none", "no_fills", "unreadable")


def _f(v: Any) -> Optional[float]:
    """A finite float, or None. Absent is never a number."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x or x in (float("inf"), float("-inf")) else x


def _px(v: Any) -> Optional[float]:
    """A price the record actually holds. ``0.0`` is the writer's placeholder
    for a price it did not have (adoption records it, and the journal held
    ``exit=0.0`` for every live close while the engine read a field the
    position does not carry), and no filled trade prices at zero."""
    x = _f(v)
    return x if x is not None and x > 0 else None


def _attr(pos: Any, *names: str) -> Any:
    """The first of ``names`` the row carries (live and paper rows spell the
    same fields differently: ``symbol``/``asset``, ``close_price``/
    ``exit_price``, ``pnl_usd``/``pnl``). None when it carries none."""
    for n in names:
        if hasattr(pos, n):
            return getattr(pos, n)
    return None


def _symbol_of(pos: Any) -> str:
    return str(_attr(pos, "symbol", "asset") or "")


def _direction_of(pos: Any) -> str:
    d = _attr(pos, "direction")
    d = getattr(d, "value", d)
    return str(d or "").upper()


def _is_paper(pos: Any) -> bool:
    return bool(getattr(pos, "is_paper", False))


def _base(symbol: Any) -> str:
    s = str(symbol or "").upper().strip().replace("$", "")
    return s.split("/")[0].split(":")[0]


def _money(v: Optional[float]) -> str:
    return f"${v:+,.2f}" if v is not None else "not priced"


def _price(v: Optional[float]) -> str:
    return f"${v:,.4f}" if v is not None else "not recorded"


def valid_trade_id(raw: Any) -> Optional[str]:
    s = str(raw or "").strip()
    return s if s and _ID.fullmatch(s) else None


def never_filled(pos: Any) -> bool:
    """An order that never became a position: a non-fill close reason with no
    P&L. Two vocabularies say which reasons those are — ``close_reason``'s
    (the parity report's) and ``trade_filter``'s (the cards') — and they
    differ by three words, so both are read. A non-zero P&L under a non-fill
    label is a mislabelled real trade and is never dropped."""
    reason = str(_attr(pos, "close_reason") or "").strip().lower()
    if reason not in NON_FILL_CLOSE_REASONS and reason not in NON_TRADE_CLOSE_REASONS:
        return False
    pnl = _f(_attr(pos, "pnl_usd", "pnl"))
    return pnl is None or abs(pnl) < 1e-9


def rows_of(book: Any) -> Optional[list]:
    """The closed rows of a book, or None when they cannot be read. A live
    executor carries ``closed_positions``; a paper portfolio carries
    ``trade_history``."""
    for name in ("closed_positions", "trade_history"):
        if hasattr(book, name):
            try:
                return list(getattr(book, name))
            except TypeError:
                return None
    return None


def find_closed_trade(book: Any, *, symbol: Optional[str] = None,
                      trade_id: Optional[str] = None) -> tuple[str, Any]:
    """``(reading, position)`` — one of ``found`` / ``no_match`` / ``none`` /
    ``no_fills`` / ``unreadable``. The most recent FILLED close wins a tie, by
    ``closed_at`` when the records carry it and by list order when they do
    not. A trade id names any row, filled or not — the caller asked for that
    one — and the renderer says which it was."""
    rows = rows_of(book)
    if rows is None:
        return "unreadable", None
    if not rows:
        return "none", None

    def _when(p: Any):
        return getattr(p, "closed_at", None)

    ordered = sorted(enumerate(rows), key=lambda ip: (
        (_when(ip[1]).timestamp() if _when(ip[1]) is not None else float("-inf")), ip[0]))
    rows = [p for _, p in ordered]
    if trade_id:
        for p in reversed(rows):
            if str(getattr(p, "trade_id", "") or "") == str(trade_id):
                return "found", p
        return "no_match", None
    if symbol:
        want = _base(symbol)
        rows = [p for p in rows if _base(_symbol_of(p)) == want]
        if not rows:
            return "no_match", None
    filled = [p for p in rows if not never_filled(p)]
    if not filled:
        return "no_fills", None
    return "found", filled[-1]


def _entry_describes(entry: Any, pos: Any) -> bool:
    """Does this journal entry record THIS close? The journal is one store
    with ids that are not unique across accounts (``TI-adopted-{SYM}-{ts}``
    is symbol + second, and two accounts adopting the same coin in one tick
    get byte-identical ids), so an entry is attached only when its symbol,
    direction and — where both sides priced the close — P&L agree with the
    position it was written from."""
    if entry is None or pos is None:
        return False
    if _base(getattr(entry, "symbol", "")) != _base(_symbol_of(pos)):
        return False
    if str(getattr(entry, "direction", "") or "").upper() != _direction_of(pos):
        return False
    e_pnl = _f(getattr(entry, "pnl", None))
    p_pnl = _f(_attr(pos, "pnl_usd", "pnl"))
    if e_pnl is not None and p_pnl is not None and abs(e_pnl - round(p_pnl, 2)) > 0.011:
        return False
    return True


def journal_entry_for(journal: Any, pos: Any, *, user_id: str = "") -> Any:
    """The journal's record of this close, or None — never a raise, never a
    search by anything but the id the caller already owns, and never an
    entry that does not describe the position it would be attached to."""
    tid = str(getattr(pos, "trade_id", "") or "") if pos is not None else ""
    if not tid or journal is None:
        return None
    try:
        finder = getattr(journal, "find_trade", None)
        if not callable(finder):
            return None
        entry = finder(tid, user_id=str(user_id or ""))
    except Exception:
        return None
    return entry if _entry_describes(entry, pos) else None


def _hold_hours(pos: Any) -> Optional[float]:
    o, c = getattr(pos, "opened_at", None), getattr(pos, "closed_at", None)
    try:
        if o is None or c is None:
            return None
        h = (c - o).total_seconds() / 3600.0
        return h if h >= 0 else None
    except Exception:
        return None


def realized_r(entry: Optional[float], sl: Optional[float], qty: Optional[float],
               pnl: Optional[float]) -> Optional[float]:
    """Net P&L over the DOLLAR risk the stop defined — ``|entry - stop| *
    quantity`` — with the sign of the P&L, for either direction. None when
    any of the three is not on record or the risk is zero. (The journal's own
    R divides by the PRICE distance alone and negates it for shorts, which is
    why it is not printed here.)"""
    if entry is None or sl is None or pnl is None or not qty or qty <= 0:
        return None
    risk = abs(entry - sl) * qty
    return pnl / risk if risk > 0 else None


def _planned_r(entry: Optional[float], sl: Optional[float], tp: Optional[float],
               direction: str) -> Optional[float]:
    if entry is None or sl is None or tp is None:
        return None
    risk = entry - sl if direction == "LONG" else sl - entry
    reward = tp - entry if direction == "LONG" else entry - tp
    return reward / risk if risk > 0 else None


def _margin_of(pos: Any) -> Optional[float]:
    """The margin the return is measured against. A live row records it
    (``cost_usd``, read through `position_size_basis` so margin is never
    confused with notional); a paper row defines it — the paper book books
    ``entry * quantity / leverage`` at close — so it is derived there and
    nowhere else."""
    margin, _notional = position_size_basis(pos)
    if margin is not None or not _is_paper(pos):
        return margin
    entry = _px(getattr(pos, "entry_price", None))
    qty = _f(getattr(pos, "quantity", None))
    lev = _f(getattr(pos, "leverage", None))
    if entry is None or not qty or qty <= 0:
        return None
    lev = lev if lev is not None and lev >= 1 else 1.0
    return entry * qty / lev


def _printable_reason(reason: str) -> bool:
    return bool(_REASON.fullmatch(reason)) and not _EXC_TEXT.match(reason)


def _reason_line(reason: str) -> str:
    if not reason:
        return "  Close reason: not recorded"
    if _printable_reason(reason):
        return f"  Closed via <code>{html.escape(reason)}</code>"
    return "  Close reason: on record but not shown (unrecognised text)"


_FOOTER = ("<i>Fields marked not recorded are unknown for this trade — nothing "
           "above was inferred, and nothing missing should be guessed.</i>")


def render_postmortem(reading: str, pos: Any, entry: Any = None, *,
                      symbol_asked: Optional[str] = None,
                      trade_id_asked: Optional[str] = None,
                      closes_on_record: Optional[int] = None,
                      non_fills_on_record: Optional[int] = None,
                      book: str = "live") -> str:
    """The post-mortem text for a reading from `find_closed_trade`.

    Three-valued on every field. A stop that is not on record gives
    "R unknown — no stop on record" rather than an R computed against zero
    (`r_multiple_for` refuses that arithmetic and this says why); a P&L the
    venue never priced says so beside the exit; a trade with no journal
    entry says its regime, confidence and signals were not recorded; and an
    order that never filled has no Outcome at all. The footer is written for
    both readers — the human who ran `/postmortem` and the model that called
    the tool — so it states what is unknown rather than instructing anyone.
    """
    which = "live" if book == "live" else "paper"
    if reading == "unreadable":
        return (f"⚠️ Your closed {which} trades could not be read right now, so "
                "there is nothing to post-mortem yet. Try again in a moment.")
    if reading == "none":
        return (f"\U0001f4cb No closed {which} trades on this account yet — "
                "nothing to post-mortem.")
    if reading in ("no_match", "no_fills"):
        what = (f"trade <code>{html.escape(str(trade_id_asked))}</code>" if trade_id_asked
                else f"<b>{html.escape(_base(symbol_asked))}</b> trade" if symbol_asked
                else "trade")
        if reading == "no_fills":
            n = (f" — {non_fills_on_record} order(s) on record never filled (expired, "
                 "cancelled or rejected), and an order that never became a trade "
                 "has no outcome to review" if non_fills_on_record else "")
            return f"\U0001f4cb No filled {which} {what} on this account{n}."
        n = f" ({closes_on_record} close(s) on record)" if closes_on_record is not None else ""
        return f"\U0001f4cb No closed {which} {what} on this account{n}."

    direction = _direction_of(pos) or "?"
    symbol = html.escape(_symbol_of(pos) or "?")
    origin = str(getattr(pos, "origin", "") or "")
    tid = valid_trade_id(getattr(pos, "trade_id", None))
    unfilled = never_filled(pos)
    # An adopted position was never assigned a strategy or a signal: the
    # dataclass defaults ("swing", "momentum_confluence") are what a reader
    # sees there, and stating them would hand the model a thesis for a trade
    # this bot did not open.
    if origin == "adopted":
        strategy = signal = "not applicable (adopted position)"
    else:
        strategy = html.escape(str(getattr(pos, "strategy_type", "") or "")) or "not recorded"
        signal = html.escape(str(getattr(pos, "signal_type", "") or "")) or "not recorded"
    entry_px = _px(getattr(pos, "entry_price", None))
    sl = _px(getattr(pos, "stop_loss", None))
    tp = _px(getattr(pos, "take_profit", None))
    exit_px = _px(_attr(pos, "close_price", "exit_price"))
    pnl = _f(_attr(pos, "pnl_usd", "pnl"))
    fees = _f(getattr(pos, "commission", None))
    margin = _margin_of(pos)
    lev = _f(getattr(pos, "leverage", None))
    qty = _f(getattr(pos, "quantity", None))
    reason = str(_attr(pos, "close_reason") or "")
    fill_source = str(getattr(pos, "fill_source", "") or "")

    title = f"POST-MORTEM — {symbol} {direction}" + (" — order never filled" if unfilled else "")
    lines = [f"\U0001f4cb <b>{title}</b> ({which.upper()})",
             f"Trade id: <code>{html.escape(tid)}</code>" if tid else "Trade id: not shown",
             f"Strategy: <code>{strategy}</code> · Signal: <code>{signal}</code>"]
    if origin == "adopted":
        lines.append("Adopted from the exchange — not opened by this bot, so the "
                     "plan below is what the venue reported, not a thesis of ours.")
    elif origin == "reclaimed":
        lines.append("The bot's own order, re-tracked after a restart.")

    planned = _planned_r(entry_px, sl, tp, direction)
    lines.append("")
    lines.append("<b>Plan</b>")
    lines.append(f"  Entry {_price(entry_px)} · Stop {_price(sl) if sl is not None else 'none on record'}"
                 f" · Target {_price(tp) if tp is not None else 'none on record'}")
    lines.append(f"  Planned R: {planned:.2f}R" if planned is not None
                 else "  Planned R: unknown (entry, stop or target not on record)")

    lines.append("")
    lines.append("<b>Outcome</b>")
    if unfilled:
        lines.append("  This order never filled" + (f" (<code>{html.escape(reason)}</code>)"
                                                    if reason and _printable_reason(reason) else "")
                     + " — no capital was at risk, so there is no exit, P&L, R or "
                     "return to review, and nothing was won or lost.")
        lines.append("")
        lines.append(_FOOTER)
        return "\n".join(lines)
    exit_line = f"  Exit {_price(exit_px)}"
    if exit_px is None and fill_source == "unread":
        exit_line += " (the close could not be read from the venue)"
    lines.append(exit_line)
    pnl_line = f"  Net P&L {_money(pnl)}"
    if pnl is None:
        pnl_line += " — this close could not be priced; do not call it flat"
    elif fees is not None:
        pnl_line += f" (fees ${fees:,.2f})"
    lines.append(pnl_line)
    if pnl is not None:
        r = realized_r(entry_px, sl, qty, pnl)
        lines.append(f"  Realized R: {r:+.2f}R" if r is not None
                     else "  Realized R: unknown — " + (
                         "no stop on record" if sl is None else
                         "quantity not on record" if not qty else
                         "the stop equals the entry on record"))
    ret = realized_margin_return_pct(pnl, margin) if pnl is not None else None
    if ret is not None:
        lev_s = f" at {lev:.0f}x" if lev is not None and lev >= 1 else ""
        lines.append(f"  Return on margin: {ret:+.2f}%{lev_s} (net of fees)")
    elif pnl is not None:
        lines.append("  Return on margin: unknown — margin not on record")
    hold = _hold_hours(pos)
    lines.append(f"  Held {hold:.1f}h" if hold is not None else "  Hold time: not recorded")
    lines.append(_reason_line(reason))

    lines.append("")
    lines.append("<b>Thesis at entry</b> (from the trade journal)")
    if entry is None:
        lines.append("  No journal entry for this trade — its regime, confidence "
                     "and signals were not recorded. Do not infer them.")
    else:
        regime = str(getattr(entry, "regime", "") or "")
        session = str(getattr(entry, "session", "") or "")
        vol = str(getattr(entry, "volatility", "") or "")
        conf = _f(getattr(entry, "confidence", None))
        signals = [str(s) for s in (getattr(entry, "signals_used", None) or []) if str(s)]
        lines.append(f"  Regime: {html.escape(regime) if regime else 'not recorded'}"
                     f" · Session: {html.escape(session) if session else 'not recorded'}"
                     f" · Volatility: {html.escape(vol) if vol else 'not recorded'}")
        # A confidence of exactly 0 is the dataclass default the live close
        # path leaves in place — it never scored this trade — not a scored 0.
        lines.append(f"  Confidence at entry: {conf:.0%}" if conf else
                     "  Confidence at entry: not recorded (this close was not scored at entry)")
        lines.append("  Signals: " + (", ".join(html.escape(s) for s in signals[:12])
                                     if signals else "not recorded"))
        lessons = [str(x) for x in (getattr(entry, "lessons", None) or []) if str(x)]
        tags = [str(x) for x in (getattr(entry, "tags", None) or []) if str(x)]
        if tags:
            lines.append("  Tags: " + ", ".join(html.escape(t) for t in tags[:8]))
        if lessons:
            lines.append("  Journal notes:")
            lines.extend(f"    • {html.escape(x)}" for x in lessons[:5])

    lines.append("")
    lines.append(_FOOTER)
    return "\n".join(lines)


def postmortem_for(book: Any, journal: Any, *, symbol: Optional[str] = None,
                   trade_id: Optional[str] = None, user_id: str = "",
                   book_kind: str = "live") -> str:
    """Resolve, look up, render — the one call the skill and the command make."""
    reading, pos = find_closed_trade(book, symbol=symbol, trade_id=trade_id)
    closes = non_fills = None
    if reading in ("no_match", "no_fills"):
        rows = rows_of(book) or []
        # "(N close(s) on record)" counts the whole book — the point of the
        # figure is that the book is not empty, only empty of what was asked.
        closes = len(rows)
        if symbol:
            rows = [p for p in rows if _base(_symbol_of(p)) == _base(symbol)]
        non_fills = sum(1 for p in rows if never_filled(p))
    entry = journal_entry_for(journal, pos, user_id=user_id) if pos is not None else None
    return render_postmortem(reading, pos, entry, symbol_asked=symbol,
                             trade_id_asked=trade_id, closes_on_record=closes,
                             non_fills_on_record=non_fills, book=book_kind)
