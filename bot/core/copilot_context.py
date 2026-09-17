"""What the trade co-pilot can READ about a ticket — one reading, one book.

``trade_copilot.review`` is pure and takes no network by design, so somebody
has to hand it the three facts it cannot derive from entry/stop/target: the
equity the margin is a share OF, the caller's existing side on the symbol, and
the engine's own lean. For the life of that function the answer was: nobody.
Driven, ``engine_bias`` and ``existing_exposure`` appear nowhere in this tree
outside the review's own signature and the two gateway lines that read them off
the request body — and the ONE caller of that endpoint
(``app/routes/webtrade.js``) sends a fixed six-key body carrying neither. A
parameter READ by the function and WRITTEN by nobody is the
``capability_answer.extras`` shape with the arrow reversed: a socket with no
cable, on two of the six subjects the co-pilot's own header promises it
reviews. The two branches were covered by tests that call ``review()`` with the
kwargs directly — asserted in a place no production caller can read, which is
the ``SKILL_TO_FEATURE`` rot this repository already has a paragraph about.

THE EQUITY HAD A CABLE AND IT WENT TO THE WRONG BOOK. The gateway read
``engine.user_portfolios.get(tg_id).snapshot().equity_usd`` — the PAPER
portfolio — for a ticket that in live mode opens a real position, so "Margin is
12% of your equity" was a share of the $10k paper baseline. That is the bug
``resolve_display_equity`` exists to have ended, arriving through a door nobody
had pointed at it.

ONE BOOK, and ``CONFIG.is_live()`` decides which. In live mode the executor and
the balance both come out of ``engine.live_view(user_id)``, for the reason that
reading states about itself — "the three can never describe two accounts" — and
the exposure is read off THAT executor's own positions, so a margin share and a
stacking note can never describe different accounts. In paper mode both come
off the one ``UserPortfolio`` object, which cannot disagree with itself either.

EVERY ABSENCE CARRIES ITS OWN REASON, because the co-pilot prints them. "Not
checked — no margin on this ticket" is a thing the reader fixes in the form;
"not checked — your live balance could not be read just now" is a thing they
check at the venue. One sentence for both would be the failure this module is
part of fixing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

from bot.config import CONFIG
from bot.core.user_memory_store import base_symbol
from bot.utils.logger import system_log

#: Which book the ticket's figures describe. ``no_account`` is LIVE with no
#: account this caller may view — never the operator's, which is what
#: ``viewer_executor`` is the guard for. ``unreadable`` is a book that raised.
BOOK_PAPER = "paper"
BOOK_LIVE = "live"
BOOK_NONE = "no_account"
BOOK_UNREADABLE = "unreadable"

#: A side the caller already holds. ``flat`` is a MEASUREMENT — the book was
#: read and holds nothing on this symbol — and is not the same fact as ``None``,
#: which is a book nobody could read. The co-pilot renders them differently.
SIDE_LONG = "long"
SIDE_SHORT = "short"
SIDE_FLAT = "flat"


def _side_word(raw: Any) -> Optional[str]:
    """``Direction.LONG`` / ``"LONG"`` / ``"buy"`` -> ``"long"``. None otherwise.

    None rather than a guess: a venue-supplied side this build does not
    recognise must not be signed as one of the two, which is the rule the
    instrument row's ``side()`` already states for the dashboard.
    """
    word = str(getattr(raw, "value", raw) or "").strip().lower()
    if word in ("long", "buy"):
        return SIDE_LONG
    if word in ("short", "sell"):
        return SIDE_SHORT
    return None


def _net_side(positions: Any, base: str) -> tuple[Optional[str], str]:
    """``(side, reason)`` — the caller's side on ``base``, or ``flat``.

    ``None`` with the reason when no single side can be stated, and the reason
    is not one sentence for two facts. A row whose side this build cannot read
    and a book holding BOTH sides are different things: the first is a failed
    read, the second is a measurement the co-pilot simply has no note for. They
    both leave the check unrun, and "could not be read" is FALSE of the second —
    it was read, and it is two sides.

    Both sides are NOT netted into whichever is bigger: the quantities are in
    base units and the two legs may be at different leverage, so a net taken
    here would be arithmetic on figures that do not add.
    """
    sides: set[str] = set()
    for p in positions:
        sym = getattr(p, "symbol", None) or getattr(p, "asset", None)
        if base_symbol(sym) != base:
            continue
        if str(getattr(p, "status", "open") or "open").lower() not in ("open", "pending_fill"):
            continue
        side = _side_word(getattr(p, "direction", None))
        if side is None:
            # A row on THIS symbol whose side this build cannot read. Reporting
            # the other rows as the whole picture would be a partial total
            # printed as whole, on the one symbol the ticket is about.
            return None, f"a {base} position on this account has a side this build cannot read"
        sides.add(side)
    if not sides:
        return SIDE_FLAT, ""
    if len(sides) == 1:
        return sides.pop(), ""
    return None, f"this account holds BOTH a long and a short on {base}"


def _engine_bias(engine: Any, base: str) -> tuple[Optional[str], str]:
    """``(side, reason)`` — the direction the ENGINE currently proposes on ``base``.

    Read off ``engine._pending_ideas``, which is the engine's own live opinion:
    a pending idea IS "BTC long, right now, at these levels". It is cache-only
    and sync — no candles are fetched — which is what lets the co-pilot keep its
    "no network" promise while finally having the input.

    A ``source == "manual"`` idea is EXCLUDED and that exclusion is the whole
    correctness of this reading. Manual ideas are tickets the CALLER registered
    (``manual_trade.build_manual_idea`` stamps them), so counting one would tell
    somebody "aligned with the engine's long bias" about their own earlier
    ticket — the engine agreeing with the user because the user said it first.

    AGE-GATED HERE rather than trusted. ``_pending_ideas`` is swept for TTL by
    the tick loop, so between sweeps it can hold an idea older than
    ``CONFIG.pending_idea_ttl``; a record with no age is read as current, and
    this one has one.
    """
    ideas = getattr(engine, "_pending_ideas", None)
    if ideas is None:
        return None, "the engine's open ideas could not be read"
    try:
        ttl = float(getattr(CONFIG, "pending_idea_ttl", 300) or 300)
        best = None
        best_ts = None
        for idea in list(ideas.values()):
            if str(getattr(idea, "source", "") or "") == "manual":
                continue
            if base_symbol(getattr(idea, "asset", None)) != base:
                continue
            ts = getattr(idea, "timestamp", None)
            if ts is None:
                continue
            age = (datetime.now(UTC) - ts).total_seconds()
            if age > ttl or age < 0:
                continue
            if best_ts is None or ts > best_ts:
                best, best_ts = idea, ts
        if best is None:
            return None, f"the engine has no open idea on {base} right now"
        side = _side_word(getattr(best, "direction", None))
        if side is None:
            return None, f"the engine's open idea on {base} names no readable side"
        return side, ""
    except Exception as exc:
        system_log.debug("Co-pilot: engine bias unreadable: %s", exc)
        return None, "the engine's open ideas could not be read"


async def ticket_context(engine: Any, user_id: str, symbol: Any) -> dict:
    """Everything the co-pilot needs and cannot derive, from ONE book.

    Returns ``{book, equity_usd, exposure, engine_bias, unread}`` where
    ``unread`` maps a co-pilot check name to the sentence explaining why it
    could not run. A key present in ``unread`` is a check that will not run; a
    key absent from it is one that will.
    """
    base = base_symbol(symbol)
    unread: dict[str, str] = {}

    bias, bias_why = (None, "the ticket's symbol could not be read")
    if base:
        bias, bias_why = _engine_bias(engine, base)
    if bias is None:
        unread["engine_bias"] = bias_why

    # A REASON PER SUBJECT, not one sentence handed to both. On the live branch
    # the balance and the positions are separate reads that fail independently,
    # so "your live balance was not read recently" under an EXPOSURE row would
    # name the wrong failure — this slice's own subject, one field over.
    book, equity, positions, eq_why, pos_why = await _read_book(
        engine, str(user_id or ""))
    if equity is None:
        unread["size_vs_equity"] = eq_why or "the equity for this account could not be read"

    exposure: Optional[str] = None
    if not base:
        unread["existing_exposure"] = "the ticket's symbol could not be read"
    elif positions is None:
        unread["existing_exposure"] = (
            pos_why or "the open positions for this account could not be read")
    else:
        exposure, expo_why = _net_side(positions, base)
        if exposure is None:
            unread["existing_exposure"] = expo_why

    return {"book": book, "equity_usd": equity, "exposure": exposure,
            "engine_bias": bias, "unread": unread}


async def _read_book(
    engine: Any, user_id: str
) -> tuple[str, Optional[float], Any, str, str]:
    """``(book, equity, positions, equity_why, positions_why)`` for the account
    this ticket executes on.

    The balance fetch is a CACHE FILL, not the reading: ``get_user_live_equity``
    writes the per-user (or operator) cache and ``live_view`` then answers the
    executor and the balance OF THAT BOOK together. Reading the fetch's return
    value directly would reintroduce the split this module exists to close —
    ``get_user_live_equity`` falls back to the operator's balance for a caller
    ``viewer_executor`` answers ``None`` for, so the equity and the positions
    could describe two accounts again.
    """
    if not CONFIG.is_live():
        try:
            portfolio = engine.user_portfolios.get(user_id)
            equity = float(portfolio.snapshot().equity_usd)
            return BOOK_PAPER, equity, list(portfolio.open_positions), "", ""
        except Exception as exc:
            # One object answers both here, so one failure really is both.
            system_log.debug("Co-pilot: paper book unreadable: %s", exc)
            _why = "the paper book for this account could not be read"
            return BOOK_UNREADABLE, None, None, _why, _why
    try:
        await engine.get_user_live_equity(user_id)
    except Exception as exc:
        # Not fatal and not silent: the cache simply stays as it was, and
        # `live_view` below reports whatever age-gated figure it really holds.
        system_log.debug("Co-pilot: live balance refresh failed: %s", exc)
    try:
        view = engine.live_view(user_id)
    except Exception as exc:
        system_log.debug("Co-pilot: live view unreadable: %s", exc)
        _why = "the live account for this caller could not be read"
        return BOOK_UNREADABLE, None, None, _why, _why
    if view.get("scope") == "none" or view.get("executor") is None:
        _why = "no live account on this build is linked to you"
        return BOOK_NONE, None, None, _why, _why
    total = view.get("total")
    # A distinct name from the paper branch's: one variable bound to a `float`
    # there and to `float | None` here is a type the whole function would have
    # to satisfy, and the mypy ratchet said so.
    live_equity = float(total) if isinstance(total, (int, float)) else None
    eq_why = "" if live_equity is not None else "your live balance was not read recently"
    pos_why = ""
    try:
        positions = list(view["executor"].open_positions)
    except Exception as exc:
        system_log.debug("Co-pilot: live positions unreadable: %s", exc)
        positions = None
        pos_why = "your open positions could not be read from the venue"
    return BOOK_LIVE, live_equity, positions, eq_why, pos_why
