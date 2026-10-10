"""One exchange close, booked in two of the operator's books.

8 October: the operator linked new API keys for the SAME Bitget sub-account
with `/connect`, so a per-user executor and the operator's executor managed one
account (`check_operator_account_links` and the `/connect` refusal stop that
now). The OPEN/USDT short closed once on the exchange and was booked twice:

- 18:05, the Close button's exchange-direct path, into the per-user book's
  ledger (`closed_trades_<id>.json`) and the journal under the operator's id;
- 18:11, the operator book's stale adopted record, into the operator's ledger,
  the journal, the learning store, the risk feed and the public channels.

Refusing the second executor stops a new duplicate. It does not take back the
one already written: the journal's weekly review counts that close twice, and
the per-user ledger stays on disk after `/disconnect`, to be loaded again by
any book built for that id. This finds such rows and, on the operator's tap,
strikes them (`/duplicates`).

**The operator's own record is the reference.** A row is a duplicate only when
it sits in a book of the operator's account and matches a close the operator's
ledger holds. A book is the operator's account when its keys were found to open
it (`check_operator_account_links`), or when it is an operator id's book with no
keys linked now (after `/disconnect`), which the card says so the operator can
judge. An operator id's book that still holds keys `check_operator_account_links`
did not find to open the operator's account may be a second account of the
operator's own; it is not compared, and the card names it. A follower's book is
never searched: a copied trade at the same price on another account is a second
trade, not a second record.

**A struck row is moved, not deleted.** It leaves the ledger and the journal and
is kept, with what it duplicated, who struck it and when, in
`struck_closes.json` beside the ledger. Nothing is struck without a tap, and the
tap re-reads the books first.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from bot.compat import UTC
from bot.utils.json_store import (
    UNREADABLE,
    StoreUnreadable,
    read_json_store,
    update_json_store,
)
from bot.utils.logger import audit, trade_log

logger = logging.getLogger(__name__)

STRUCK_FILE = "struck_closes.json"
_BOOK_PREFIX = "closed_trades_"


def _norm_symbol(sym: Any) -> str:
    return str(sym or "").split(":")[0].replace("/", "").upper()


def _norm_side(side: Any) -> str:
    return str(getattr(side, "value", side) or "").upper()


def _aware(when: Any) -> Optional[datetime]:
    if not isinstance(when, datetime):
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _filled(p: Any) -> bool:
    """A record of a fill, not an order that ended without one
    (`is_filled_close`, the rule every record reader takes)."""
    from bot.utils.close_reason import is_filled_close

    return is_filled_close(getattr(p, "close_reason", None), _num(getattr(p, "pnl_usd", None)))


def same_exchange_close(kept: Any, other: Any) -> bool:
    """Do ``kept`` and ``other`` record ONE close on one account?

    Same symbol and side, closed within `SAME_CLOSE_WINDOW_S` of each other,
    neither opened after the other closed, and either the entries within
    `SAME_CLOSE_ENTRY_TOL` or both holding periods known (they overlap, by the
    check before): one account holds one position per symbol and side, so two
    records of it open at once are one position. The booking guard's own
    thresholds; this compares two recorded closes where that guard compares a
    booking with the clock.
    """
    from bot.core.live_executor import SAME_CLOSE_ENTRY_TOL, SAME_CLOSE_WINDOW_S
    from bot.core.position_telemetry import entered_at

    if _norm_symbol(getattr(kept, "symbol", "")) != _norm_symbol(getattr(other, "symbol", "")):
        return False
    if _norm_side(getattr(kept, "direction", "")) != _norm_side(getattr(other, "direction", "")):
        return False
    k_closed, o_closed = _aware(getattr(kept, "closed_at", None)), _aware(getattr(other, "closed_at", None))
    if k_closed is None or o_closed is None:
        return False
    if abs((k_closed - o_closed).total_seconds()) > SAME_CLOSE_WINDOW_S:
        return False
    # When each ENTERED, not when its order was placed: a limit can rest for
    # hours before it fills (`entered_at`, the reading every hold takes).
    k_open, o_open = _aware(entered_at(kept)), _aware(entered_at(other))
    if (k_open is not None and k_open > o_closed) or (o_open is not None and o_open > k_closed):
        return False
    k_entry, o_entry = _num(getattr(kept, "entry_price", None)), _num(getattr(other, "entry_price", None))
    if k_entry and o_entry and k_entry > 0 and abs(k_entry - o_entry) / k_entry <= SAME_CLOSE_ENTRY_TOL:
        return True
    return k_open is not None and o_open is not None


@dataclass(frozen=True)
class StrayClose:
    """A close in a second book that the operator's ledger already holds."""
    book: str
    trade_id: str
    symbol: str
    direction: str
    entry_price: Optional[float]
    closed_at: Optional[datetime]
    pnl_usd: Optional[float]
    in_ledger: bool
    in_journal: bool
    kept_trade_id: str
    kept_closed_at: Optional[datetime]
    kept_pnl_usd: Optional[float]
    #: True when the book's keys were found to open the operator's account;
    #: False when it is an operator id's book with no keys linked now.
    account_proven: bool

    @property
    def token(self) -> str:
        """A short name for the tap, stable across restarts: it carries no id."""
        return hashlib.sha256(f"{self.book}|{self.trade_id}".encode()).hexdigest()[:16]


@dataclass
class DuplicateReport:
    strays: list = field(default_factory=list)
    #: Book id -> filled closes found only in that book (not in the operator's).
    only_in_book: dict = field(default_factory=dict)
    #: Book ids whose ledger could not be read, whole or in part.
    unread: list = field(default_factory=list)
    #: Operator ids' books holding keys not found to open the operator's
    #: account: not compared.
    not_compared: list = field(default_factory=list)
    #: Book id -> (why it was not compared, whether that is a verdict). A
    #: verdict is "another account"; anything else is "could not tell".
    why_not_compared: dict = field(default_factory=dict)
    #: The exception class when `/duplicates`' fresh link check could not run:
    #: the books were then placed by the last check, and the card says so.
    link_check_failed: str = ""
    #: The operator's own record was read in part: nothing is compared.
    operator_read_failed: bool = False
    books_compared: int = 0
    operator_closes: int = 0


@dataclass
class _JournalRow:
    """A journal entry, in the shape `same_exchange_close` reads."""
    trade_id: str
    symbol: str
    direction: str
    entry_price: Optional[float]
    closed_at: Optional[datetime]
    opened_at: Optional[datetime]
    pnl_usd: Optional[float]


def _journal_row(e: Any) -> _JournalRow:
    ts = _num(getattr(e, "timestamp", None))
    closed = datetime.fromtimestamp(ts, tz=UTC) if ts else None
    hold = _num(getattr(e, "holding_hours", None))
    opened = closed - timedelta(hours=hold) if (closed is not None and hold) else None
    return _JournalRow(
        trade_id=str(getattr(e, "trade_id", "") or ""),
        symbol=str(getattr(e, "symbol", "") or ""),
        direction=str(getattr(e, "direction", "") or ""),
        entry_price=_num(getattr(e, "entry_price", None)),
        closed_at=closed, opened_at=opened,
        pnl_usd=_num(getattr(e, "pnl", None)))


def _ledger_dir(engine: Any) -> str:
    return os.path.dirname(str(engine.live_executor._closed_trades_file)) or "."


def _book_path(engine: Any, book: str) -> str:
    return os.path.join(_ledger_dir(engine), f"{_BOOK_PREFIX}{book}.json")


PROVEN, UNLINKED, UNCONFIRMED = "proven", "unlinked", "unconfirmed"


def _bitget_linked(book: str) -> Optional[bool]:
    """Whether Bitget keys are stored for ``book``; None when the store could
    not be read, which is not "nothing linked". Reads the record, decrypts
    nothing."""
    try:
        from bot.core.exchange_credentials import get_credential_store

        store = get_credential_store()
        if store.file_unreadable:
            return None
        return "bitget" in store.list_venues(book)
    except Exception:
        return None


def _standing(engine: Any, book: str) -> Optional[str]:
    """Whether ``book`` is the operator's account: PROVEN (its keys open it),
    UNLINKED (an operator id's book with no keys now), UNCONFIRMED (an operator
    id's book holding keys not found to open it, or a store that would not
    read), or None (not the operator's: never searched)."""
    try:
        if book in set(getattr(engine, "_operator_account_users", ()) or ()):
            return PROVEN
        if not engine._is_operator_user(book):
            return None
    except Exception:
        return None
    return UNLINKED if _bitget_linked(book) is False else UNCONFIRMED


#: Whose account ID would not read, in the card's words.
_UNREAD_SIDE = {"theirs": "its keys", "operator": "the operator's own keys",
                "both": "either set of keys"}


def _why_not_compared(engine: Any, book: str) -> tuple[str, bool]:
    """(why an UNCONFIRMED book was not compared, whether that is a verdict).

    The card used to say "keys not found to open the operator's account"
    for every case. That sentence fits three different facts, and only one
    of them (another account) means leave the book alone: a key the check
    never read, or one Bitget refused the account read, is "could not tell",
    and the operator's next step differs."""
    from bot.core.exchange_credentials import LINK_OTHER_ACCOUNT

    if _bitget_linked(book) is None:
        return "the credential store would not read, so its link could not be checked", False
    reading = (getattr(engine, "_operator_link_readings", None) or {}).get(book)
    if reading is None:
        return ("its Bitget keys have not been checked against the operator's "
                "account"), False
    if reading.verdict == LINK_OTHER_ACCOUNT:
        return ("its Bitget keys open a different account (the account IDs differ), "
                "where the same trade would be a second trade"), True
    who = _UNREAD_SIDE.get(reading.unread, "the keys")
    return (f"the account ID behind {who} could not be read "
            f"({reading.cause or 'no reason given'}), so it is not known whether "
            f"they open the operator's account"), False


async def refresh_operator_links(engine: Any) -> str:
    """Re-read the operator ids' Bitget links now, before a search places
    their books. The marks were set at boot and every six hours, and only
    with per-user live on; a link the card calls unconfirmed may never have
    been read. Returns "" when the check ran, else the exception class: the
    books are then placed by the last check, and the card says so."""
    try:
        await engine.check_operator_account_links(operators_only=True)
    except Exception as exc:
        return type(exc).__name__
    return ""


def _live_book(engine: Any, path: str) -> Any:
    """The executor in memory that owns ``path``, or None."""
    for ex in list((getattr(engine, "_user_executors", None) or {}).values()):
        if os.path.abspath(str(getattr(ex, "_closed_trades_file", ""))) == os.path.abspath(path):
            return ex
    return None


def _book_ids(engine: Any) -> list:
    """The per-user ledgers beside the operator's, by book id."""
    out: list[str] = []
    try:
        names = sorted(os.listdir(_ledger_dir(engine)))
    except OSError:
        return out
    for name in names:
        if name.startswith(_BOOK_PREFIX) and name.endswith(".json"):
            out.append(name[len(_BOOK_PREFIX):-len(".json")])
    return out


def _read_book(engine: Any, book: str) -> tuple[list, bool]:
    """(rows, read_in_full) for one per-user book: the live executor's rows
    when one holds it, else the file's readable rows."""
    from bot.core.live_executor import LiveExecutor

    path = _book_path(engine, book)
    ex = _live_book(engine, path)
    if ex is not None:
        return list(getattr(ex, "_closed_trades", []) or []), not bool(
            getattr(ex, "closed_trades_read_failed", False))
    read = read_json_store(path, shape=list)
    if read.state == UNREADABLE:
        return [], False
    rows, whole = [], True
    for item in (read.data or []):
        try:
            rows.append(LiveExecutor._closed_row_to_position(item))
        except Exception:
            whole = False
    return rows, whole


def find_duplicate_closes(engine: Any) -> DuplicateReport:
    """Every close a second book of the operator's account holds that the
    operator's ledger already records. Reads only."""
    report = DuplicateReport()
    op = engine.live_executor
    if bool(getattr(op, "closed_trades_read_failed", False)):
        # A record read in part cannot say what it does not hold: a row
        # missing from it would make its twin look like the only copy, and
        # a row it does hold could not be told from one it lost.
        report.operator_read_failed = True
        return report
    # A limit that never filled is in every ledger (expired, cancelled), and
    # it is not a close: compared, a real close in a second book would read
    # as the duplicate of an order that expired at its price in this one,
    # and a tap would strike the only record of a trade.
    kept_rows = [k for k in list(getattr(op, "_closed_trades", []) or []) if _filled(k)]
    report.operator_closes = len(kept_rows)

    def _match(row: Any) -> Any:
        for k in kept_rows:
            if same_exchange_close(k, row):
                return k
        return None

    journal = getattr(engine, "journal", None)
    entries = list(journal.closed_entries()) if journal is not None else []
    in_journal = {(str(e.trade_id), str(getattr(e, "user_id", "") or "")) for e in entries}

    covered: set = set()
    standing: dict[str, Optional[str]] = {}

    def _stand(book: str) -> Optional[str]:
        if book not in standing:
            standing[book] = _standing(engine, book)
            if standing[book] == UNCONFIRMED:
                report.not_compared.append(book)
                report.why_not_compared[book] = _why_not_compared(engine, book)
        return standing[book]

    for book in _book_ids(engine):
        if _stand(book) not in (PROVEN, UNLINKED):
            continue
        rows, whole = _read_book(engine, book)
        if not whole:
            report.unread.append(book)
        report.books_compared += 1
        proven = standing[book] == PROVEN
        for r in rows:
            tid = str(getattr(r, "trade_id", "") or "")
            covered.add((tid, book))
            if not _filled(r):
                continue
            k = _match(r)
            if k is None:
                report.only_in_book[book] = report.only_in_book.get(book, 0) + 1
                continue
            report.strays.append(StrayClose(
                book=book, trade_id=tid, symbol=str(r.symbol), direction=_norm_side(r.direction),
                entry_price=_num(r.entry_price), closed_at=_aware(r.closed_at),
                pnl_usd=_num(getattr(r, "pnl_usd", None)),
                in_ledger=True, in_journal=(tid, book) in in_journal,
                kept_trade_id=str(k.trade_id), kept_closed_at=_aware(k.closed_at),
                kept_pnl_usd=_num(getattr(k, "pnl_usd", None)), account_proven=proven))

    # A journal entry under an operator's id whose book is gone: the journal
    # still counts it. Paper closes journal under no id, so an entry that
    # carries one is a live per-user close.
    for e in entries:
        book = str(getattr(e, "user_id", "") or "")
        tid = str(getattr(e, "trade_id", "") or "")
        if not book or (tid, book) in covered:
            continue
        if _stand(book) not in (PROVEN, UNLINKED):
            continue
        row = _journal_row(e)
        k = _match(row)
        if k is None:
            continue
        covered.add((tid, book))
        report.strays.append(StrayClose(
            book=book, trade_id=tid, symbol=row.symbol, direction=_norm_side(row.direction),
            entry_price=row.entry_price, closed_at=row.closed_at, pnl_usd=row.pnl_usd,
            in_ledger=False, in_journal=True,
            kept_trade_id=str(k.trade_id), kept_closed_at=_aware(k.closed_at),
            kept_pnl_usd=_num(getattr(k, "pnl_usd", None)),
            account_proven=standing[book] == PROVEN))
    return report


@dataclass
class StrikeOutcome:
    struck: Optional[StrayClose] = None
    ledger_row: bool = False
    journal_row: bool = False
    #: Why nothing was struck, said to the operator; empty when it was.
    refused: str = ""


def _remove_from_file(path: str, trade_id: str) -> Optional[list]:
    """The rows taken out of the ledger file at ``path``, or None when the file
    would not read or the write did not land (it is then left as it was). The
    one read-modify-write a store takes (`update_json_store`), so a file that
    will not read is never written over; rows it cannot parse are written back
    as they were."""
    taken: list = []

    def _take(data: list) -> bool:
        taken.extend(d for d in data
                     if isinstance(d, dict) and str(d.get("trade_id", "")) == trade_id)
        if not taken:
            return False
        data[:] = [d for d in data if d not in taken]
        return True

    try:
        update_json_store(path, _take, shape=list, indent=2, default=str)
    except (StoreUnreadable, OSError):
        return None
    return taken


def _append_struck(engine: Any, records: list) -> bool:
    """Add ``records`` to `struck_closes.json`. False when the file would not
    read (never written over: a human reads it) or the write did not land."""
    path = os.path.join(_ledger_dir(engine), STRUCK_FILE)
    try:
        update_json_store(path, lambda kept: kept.extend(records), shape=list,
                          indent=2, default=str)
        return True
    except (StoreUnreadable, OSError) as exc:
        logger.warning("Struck-close record not written: %s", type(exc).__name__)
        return False


def strike_duplicate(engine: Any, token: str, *, struck_by: str,
                     now: Optional[datetime] = None) -> StrikeOutcome:
    """Strike the duplicate named by ``token``, after reading the books again.

    The ledger row and the journal row are kept in `struck_closes.json` first;
    a row that could not be kept is not taken out. Audited either way.
    """
    from bot.core.live_executor import closed_trade_row

    now = now or datetime.now(UTC)
    report = find_duplicate_closes(engine)
    if report.operator_read_failed:
        return StrikeOutcome(refused="the operator's record was read in part, so "
                                     "nothing can be called a duplicate of it")
    stray = next((s for s in report.strays if s.token == str(token or "")), None)
    if stray is None:
        return StrikeOutcome(refused="it is no longer on record as a duplicate "
                                     "(struck already, or the books changed)")
    base = {"book": stray.book, "trade_id": stray.trade_id,
            "duplicate_of": stray.kept_trade_id, "struck_at": now.isoformat(),
            "struck_by": str(struck_by or ""),
            "reason": "one exchange close booked in two of the operator's books"}
    out = StrikeOutcome(struck=stray)

    if stray.in_ledger:
        path = _book_path(engine, stray.book)
        ex = _live_book(engine, path)
        if ex is not None:
            rows = [p for p in ex._closed_trades if str(p.trade_id) == stray.trade_id]
            raw = [closed_trade_row(p) for p in rows]
            if not rows or not _append_struck(engine, [dict(base, kind="ledger", row=r) for r in raw]):
                return StrikeOutcome(refused="the struck row could not be kept, so it "
                                             "was not taken out")
            ex._closed_trades = [p for p in ex._closed_trades if p not in rows]
            if not ex._save_closed_trades():
                ex._closed_trades.extend(rows)
                return StrikeOutcome(refused="that book's ledger could not be written")
        else:
            read = read_json_store(path, shape=list)
            raw = [d for d in (read.data or []) if isinstance(d, dict)
                   and str(d.get("trade_id", "")) == stray.trade_id]
            if not raw or not _append_struck(engine, [dict(base, kind="ledger", row=r) for r in raw]):
                return StrikeOutcome(refused="the struck row could not be kept, so it "
                                             "was not taken out")
            if not _remove_from_file(path, stray.trade_id):
                return StrikeOutcome(refused="that book's ledger could not be written")
        out.ledger_row = True

    journal = getattr(engine, "journal", None)
    if stray.in_journal and journal is not None:
        entry = next((e for e in journal.closed_entries()
                      if str(e.trade_id) == stray.trade_id
                      and str(getattr(e, "user_id", "") or "") == stray.book), None)
        if entry is not None and _append_struck(
                engine, [dict(base, kind="journal", row=journal._entry_to_row(entry))]):
            out.journal_row = journal.strike(stray.trade_id, user_id=stray.book) is not None

    audit(trade_log,
          f"Struck a duplicate close: {stray.symbol} {stray.direction} {stray.trade_id} "
          f"in book {stray.book}, a second record of {stray.kept_trade_id} "
          f"(ledger {'out' if out.ledger_row else 'untouched'}, "
          f"journal {'out' if out.journal_row else 'untouched'})",
          action="strike_duplicate_close", result="STRUCK",
          data={"book": stray.book, "trade_id": stray.trade_id,
                "duplicate_of": stray.kept_trade_id, "struck_by": str(struck_by or "")})
    return out


# ── The card ─────────────────────────────────────────────────────────────────

def _when(t: Optional[datetime]) -> str:
    return t.strftime("%d %b %H:%M UTC") if t is not None else "time unread"


def _usd(x: Optional[float]) -> str:
    return "net unread" if x is None else f"net ${x:+,.2f}"


def duplicates_card(report: DuplicateReport) -> tuple[str, list]:
    """(HTML text, [(button label, token), ...]) for `/duplicates`. Private
    and operator-only, so the net figures are dollars."""
    import html

    head = "🧾 <b>Closes booked twice</b>"
    if report.operator_read_failed:
        return (f"{head}\n\nThe operator's closed-trade record was read in part, so "
                "nothing can be compared with it. Nothing is struck until it reads "
                "in full."), []
    lines = [head, ""]
    if not report.strays:
        lines.append(f"✅ None found: {report.books_compared} second book(s) of the "
                     f"operator's account, and the journal, compared with the "
                     f"{report.operator_closes} filled close(s) in the operator's ledger.")
    else:
        lines.append("One exchange close, recorded in the operator's book and again "
                     "in a second book of the operator's account. The operator's row "
                     "stays; the second is the duplicate.")
        for i, s in enumerate(report.strays, 1):
            where = " + ".join(w for w, on in (("ledger", s.in_ledger),
                                               ("journal", s.in_journal)) if on)
            account = ("its keys open the operator's account" if s.account_proven
                       else "an operator id's book with no keys linked now: strike "
                            "only if it traded the operator's account")
            entry = "entry unread" if s.entry_price is None else f"entry {s.entry_price:g}"
            lines += [
                "",
                f"{i}. <b>{html.escape(s.symbol)}</b> {html.escape(s.direction)} · {entry}",
                f"   kept: operator book · {_when(s.kept_closed_at)} · "
                f"{_usd(s.kept_pnl_usd)} · <code>{html.escape(s.kept_trade_id)}</code>",
                f"   duplicate: book {html.escape(s.book)} ({account}) · "
                f"{_when(s.closed_at)} · {_usd(s.pnl_usd)} · "
                f"<code>{html.escape(s.trade_id)}</code> · in its {where}",
            ]
        lines += ["", "A tap strikes that duplicate: it leaves the book's ledger and the "
                      f"journal and is kept in <code>{STRUCK_FILE}</code>."]
    for book, n in sorted(report.only_in_book.items()):
        lines.append(f"\nBook {html.escape(book)} also holds {n} close(s) the operator's "
                     "record does not; they are not duplicates and are left as they are.")
    could_not_tell = False
    for book in report.not_compared:
        why, verdict = report.why_not_compared.get(
            book, ("its keys were not found to open the operator's account", False))
        could_not_tell = could_not_tell or not verdict
        lines.append(f"\nBook {html.escape(book)} was not compared: {html.escape(why)}.")
    if could_not_tell:
        lines.append("If those keys trade the operator's account, /disconnect removes "
                     "the link and /duplicates then searches that book.")
    if report.link_check_failed:
        lines.append(f"\n⚠️ The link check could not run now "
                     f"({html.escape(report.link_check_failed)}); books are placed by "
                     "the last one.")
    for book in report.unread:
        lines.append(f"\n⚠️ Book {html.escape(book)} was read in part: a row it could "
                     "not read was not compared.")
    buttons = [(f"Strike {i}: {s.symbol.split(':')[0]} {_when(s.closed_at)}", s.token)
               for i, s in enumerate(report.strays, 1)]
    return "\n".join(lines), buttons


def strike_outcome_line(outcome: StrikeOutcome) -> str:
    import html

    if outcome.refused or outcome.struck is None:
        return f"⚪ Nothing struck: {html.escape(outcome.refused or 'no such duplicate')}."
    s = outcome.struck
    parts = [w for w, on in (("its ledger", outcome.ledger_row),
                             ("the journal", outcome.journal_row)) if on]
    return (f"✅ Struck <code>{html.escape(s.trade_id)}</code> ({html.escape(s.symbol)} "
            f"{html.escape(s.direction)}, book {html.escape(s.book)}) from "
            f"{' and '.join(parts) or 'nothing'}. Kept in <code>{STRUCK_FILE}</code>.")
