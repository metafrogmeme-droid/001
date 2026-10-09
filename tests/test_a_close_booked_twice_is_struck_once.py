"""One exchange close, booked in two of the operator's books, is struck once.

8 October: keys for the operator's own Bitget sub-account were linked with
`/connect`, so two executors managed one account. The OPEN/USDT short closed
once on the exchange and was booked twice: at 18:05 by the Close button's
exchange-direct path into the per-user book (`closed_trades_<id>.json`) and
the journal under the operator's id, and at 18:11 by the operator book's stale
adopted record. `/duplicates` finds the second record and a tap strikes it.

The books here are the real executor's file format (`closed_trade_row`, read
back by `LiveExecutor._closed_row_to_position`), the real `TradeJournal`, and
the real `/duplicates` command and `dupstrike:` button through the real
`TelegramHandler`. The engine is a namespace carrying the real executor, the
real journal and the engine's own `_is_operator_user`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from cryptography.fernet import Fernet

from bot.compat import UTC
from bot.config import CONFIG
from bot.core import duplicate_closes as dc
from bot.core.engine import RuneClawEngine
from bot.core.exchange_credentials import ExchangeCredentialStore
from bot.core.live_executor import LiveExecutor, LivePosition, closed_trade_row
from bot.core.trade_journal import TradeJournal

OP = "1001"          # the operator's Telegram id
FOLLOWER = "2002"
DAY = datetime(2026, 10, 8, tzinfo=UTC)


def _pos(tid, *, symbol="OPEN/USDT:USDT", side="SHORT", entry=0.1091, opened=(17, 27),
         closed=(18, 11), pnl=0.48, stop=0.0, reason="manual"):
    return LivePosition(
        trade_id=tid, symbol=symbol, direction=side, entry_price=entry, quantity=300.0,
        cost_usd=6.5, stop_loss=stop, take_profit=0.0, leverage=5, status="closed",
        close_price=0.1075, pnl_usd=pnl,
        opened_at=DAY.replace(hour=opened[0], minute=opened[1]),
        closed_at=DAY.replace(hour=closed[0], minute=closed[1]), close_reason=reason)


def _expired(tid, closed):
    return _pos(tid, opened=(17, 0), closed=closed, pnl=0.0, reason="expired")


GHOST = "TI-adopted-OPENUSDT-1759944420"     # the operator book's adopted record
MANUAL = "TI-manual-OPENUSDT-1759946700"     # the Close button's row in the per-user book


def _write_book(tmp_path, uid, rows, extra=()):
    path = tmp_path / f"closed_trades_{uid}.json"
    path.write_text(json.dumps([closed_trade_row(p) for p in rows] + list(extra),
                               indent=2, default=str))
    return path


def _journal(tmp_path, rows):
    j = TradeJournal(journal_file=str(tmp_path / "trade_journal.json"))
    for p, uid in rows:
        j.record_trade(trade_id=p.trade_id, symbol=p.symbol, direction=p.direction,
                       strategy_type="", entry_price=p.entry_price, exit_price=0.1075,
                       stop_loss=p.stop_loss, take_profit=0.0, quantity=p.quantity,
                       pnl=p.pnl_usd, holding_hours=0.5, user_id=uid)
        # Live, the journal is written at the close (`journal_live_close`), so
        # its stamp is the close time; here it is set to the record's.
        j._entries[-1].timestamp = p.closed_at.timestamp()
    return j


@pytest.fixture
def books(tmp_path, monkeypatch):
    """The 8 October record: the operator's ledger with the adopted close, the
    operator's per-user book with the Close button's row (and a BTC close the
    operator's ledger never got, and rows this build cannot read), a
    follower's book with a copied OPEN trade, and the journal of all three.
    Each ledger also holds an OPEN limit that expired at the same price, as
    ledgers do: an order that never filled is not a close on either side."""
    # The operator is the configured chat; the engine module reads its own
    # CONFIG at call time, so that is the one the operator check is asked of.
    cfg = dataclasses.replace(
        CONFIG, telegram=dataclasses.replace(CONFIG.telegram, chat_id=OP, admin_ids=""))
    monkeypatch.setattr("bot.core.engine.CONFIG", cfg)
    # The real credential store, empty: the operator ran /disconnect.
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    store = ExchangeCredentialStore(creds_file=str(tmp_path / "creds.enc"),
                                    key_file=str(tmp_path / ".key"))
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", lambda: store)
    op = LiveExecutor(state_dir=str(tmp_path))
    op._closed_trades = [_pos(GHOST), _expired("T-op-expired", (17, 50))]
    assert op._save_closed_trades()
    manual = _pos(MANUAL, opened=(17, 20), closed=(18, 5))
    btc = _pos("TI-manual-BTCUSDT-1", symbol="BTC/USDT:USDT", side="LONG",
               entry=62000.0, opened=(9, 0), closed=(10, 0), pnl=-1.2)
    _write_book(tmp_path, OP, [btc, manual, _expired("T-user-expired", (18, 0))],
                extra=[{"unreadable": True}, "not a row"])
    copied = _pos("T-follow-OPEN-1", opened=(17, 26), closed=(18, 6))
    _write_book(tmp_path, FOLLOWER, [copied])
    journal = _journal(tmp_path, [(_pos(GHOST), ""), (manual, OP), (copied, FOLLOWER)])
    engine = NS(live_executor=op, journal=journal, _user_executors={},
                _operator_account_users=set(), _user_store=None)
    engine._is_operator_user = RuneClawEngine._is_operator_user.__get__(engine)
    engine.store = store
    return engine


# ── the one close, two records ──────────────────────────────────────────────

def test_one_position_closed_twice_on_record_is_one_close():
    assert dc.same_exchange_close(_pos(GHOST), _pos(MANUAL, opened=(17, 20), closed=(18, 5)))
    # Entries a tick apart, both holding periods known and overlapping: one
    # account holds one position per symbol and side.
    assert dc.same_exchange_close(_pos(GHOST), _pos(MANUAL, entry=0.1092, closed=(18, 5)))


@pytest.mark.parametrize("other", [
    _pos(MANUAL, symbol="ONE/USDT:USDT", closed=(18, 5)),            # another symbol
    _pos(MANUAL, side="LONG", closed=(18, 5)),                       # the other side
    _pos(MANUAL, opened=(18, 30), closed=(19, 0)),                   # re-entered at the price
])
def test_a_different_position_is_not_the_same_close(other):
    assert not dc.same_exchange_close(_pos(GHOST), other)


def test_a_limit_is_held_from_its_fill_not_its_placement():
    # Placed at 14:00, filled at 18:20: the position entered after the other
    # closed at 18:10, so the two never overlapped. Counted from the order's
    # placement they would, and with entries apart that is the only thing
    # that could call them one close.
    kept = _pos(GHOST, entry=0.1150, opened=(14, 0), closed=(18, 40))
    kept.filled_at = DAY.replace(hour=18, minute=20)
    other = _pos(MANUAL, opened=(17, 0), closed=(18, 10))
    assert not dc.same_exchange_close(kept, other)
    kept.filled_at = DAY.replace(hour=17, minute=30)      # entered before it closed
    assert dc.same_exchange_close(kept, other)


def test_closes_hours_apart_with_no_opening_on_record_are_two():
    kept, other = _pos(GHOST), _pos(MANUAL, closed=(15, 0))
    kept.opened_at = other.opened_at = None
    assert not dc.same_exchange_close(kept, other)
    other.closed_at = DAY.replace(hour=17, minute=0)        # inside the window
    assert dc.same_exchange_close(kept, other)


def test_with_an_opening_unknown_only_the_entry_can_tell():
    unknown = _pos(MANUAL, closed=(18, 5))
    unknown.opened_at = None
    # The same entry: one close.
    assert dc.same_exchange_close(_pos(GHOST), unknown)
    # Entries apart and one holding period unknown: nothing says they overlap.
    unknown.entry_price = 0.1150
    assert not dc.same_exchange_close(_pos(GHOST), unknown)


def test_no_close_time_is_no_match():
    p = _pos(MANUAL)
    p.closed_at = None
    assert not dc.same_exchange_close(_pos(GHOST), p)


# ── the search ──────────────────────────────────────────────────────────────

def test_the_close_buttons_row_is_found_and_the_followers_copy_is_not(books):
    r = dc.find_duplicate_closes(books)
    assert not r.operator_read_failed and r.operator_closes == 1
    (s,) = r.strays
    assert (s.book, s.trade_id, s.kept_trade_id) == (OP, MANUAL, GHOST)
    assert s.in_ledger and s.in_journal and not s.account_proven
    assert s.direction == "SHORT" and s.pnl_usd == 0.48 and s.kept_pnl_usd == 0.48
    # The BTC close only that book holds is said, not struck; the follower's
    # book is never searched; the unreadable row is said.
    assert r.only_in_book == {OP: 1}
    assert r.books_compared == 1
    assert r.unread == [OP]


def test_a_real_close_is_no_duplicate_of_a_limit_that_expired_at_its_price(books):
    # The operator's book holds only the expired limit: the per-user close is
    # the one record of that trade, never a duplicate to strike.
    books.live_executor._closed_trades = [_expired("T-op-expired", (18, 2))]
    r = dc.find_duplicate_closes(books)
    assert r.strays == [] and r.operator_closes == 0
    assert r.only_in_book == {OP: 2}


def test_a_book_whose_keys_open_the_operator_account_is_searched(books, tmp_path):
    _write_book(tmp_path, "3003", [_pos("TI-manual-OPEN-3003", closed=(18, 5))])
    books._operator_account_users = {"3003"}
    strays = {s.book: s for s in dc.find_duplicate_closes(books).strays}
    assert set(strays) == {OP, "3003"} and strays["3003"].account_proven


def test_an_operator_id_s_book_holding_other_keys_is_not_compared(books):
    # Keys still linked that the account check did not find open the
    # operator's account: a second account of the operator's own, perhaps,
    # where the same trade is a second trade.
    books.store.set(OP, "k" * 16, "s" * 16, "p" * 8)
    r = dc.find_duplicate_closes(books)
    assert r.strays == [] and r.not_compared == [OP] and r.books_compared == 0
    text, buttons = dc.duplicates_card(r)
    assert f"Book {OP} holds keys not found to open the operator's account" in text
    assert buttons == []
    # Found to open the operator's account, it is compared, and said proven.
    books._operator_account_users = {OP}
    (s,) = dc.find_duplicate_closes(books).strays
    assert s.account_proven


def test_a_store_that_would_not_read_places_no_book(books, monkeypatch):
    def _boom():
        raise OSError("unreadable")
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", _boom)
    r = dc.find_duplicate_closes(books)
    assert r.strays == [] and r.not_compared == [OP]


def test_a_record_read_in_part_compares_nothing(books):
    books.live_executor._closed_trades_read_failed = True
    r = dc.find_duplicate_closes(books)
    assert r.operator_read_failed and r.strays == []
    text, buttons = dc.duplicates_card(r)
    assert "read in part" in text and buttons == []


def test_the_journal_row_is_found_when_the_book_is_gone(books, tmp_path):
    (tmp_path / f"closed_trades_{OP}.json").unlink()
    (s,) = dc.find_duplicate_closes(books).strays
    assert (s.trade_id, s.in_ledger, s.in_journal) == (MANUAL, False, True)


# ── the strike ──────────────────────────────────────────────────────────────

def _weekly_open_count(journal):
    return sum(1 for e in journal.closed_entries() if e.symbol.startswith("OPEN"))


def test_the_strike_moves_the_row_out_of_the_book_and_the_journal(books, tmp_path):
    (s,) = dc.find_duplicate_closes(books).strays
    assert _weekly_open_count(books.journal) == 3      # operator, operator's id, follower
    out = dc.strike_duplicate(books, s.token, struck_by=OP,
                              now=datetime(2026, 10, 9, 17, 0, tzinfo=UTC))
    assert not out.refused and out.ledger_row and out.journal_row

    book = json.loads((tmp_path / f"closed_trades_{OP}.json").read_text())
    # The BTC close and the expired limit stay, and both rows this build
    # cannot read stay verbatim.
    assert [r["trade_id"] for r in book[:2]] == ["TI-manual-BTCUSDT-1", "T-user-expired"]
    assert book[2:] == [{"unreadable": True}, "not a row"]
    on_disk = TradeJournal(journal_file=str(tmp_path / "trade_journal.json"))
    assert sorted((e.trade_id, e.user_id) for e in on_disk.closed_entries()) == sorted(
        [(GHOST, ""), ("T-follow-OPEN-1", FOLLOWER)])
    assert _weekly_open_count(books.journal) == 2

    struck = json.loads((tmp_path / dc.STRUCK_FILE).read_text())
    assert [(x["kind"], x["trade_id"], x["duplicate_of"], x["struck_by"]) for x in struck] == [
        ("ledger", MANUAL, GHOST, OP), ("journal", MANUAL, GHOST, OP)]
    assert struck[0]["row"]["pnl_usd"] == 0.48 and struck[1]["row"]["uid"] == OP

    # The operator's own record is untouched, and nothing is left to strike.
    assert [p.trade_id for p in books.live_executor._closed_trades] == [GHOST, "T-op-expired"]
    assert dc.find_duplicate_closes(books).strays == []
    again = dc.strike_duplicate(books, s.token, struck_by=OP)
    assert "no longer on record" in again.refused


def test_a_live_book_is_struck_in_memory_so_its_next_save_keeps_it_out(books, tmp_path):
    ex = LiveExecutor(user_id=OP, state_dir=str(tmp_path))
    assert MANUAL in [p.trade_id for p in ex._closed_trades]
    books._user_executors = {OP: ex}
    (s,) = dc.find_duplicate_closes(books).strays
    assert dc.strike_duplicate(books, s.token, struck_by=OP).ledger_row
    assert MANUAL not in [p.trade_id for p in ex._closed_trades]
    assert ex._save_closed_trades()
    assert MANUAL not in (tmp_path / f"closed_trades_{OP}.json").read_text()


def test_a_row_that_cannot_be_kept_is_not_taken_out(books, tmp_path):
    (tmp_path / dc.STRUCK_FILE).write_text('{"not": "a list"}')
    (s,) = dc.find_duplicate_closes(books).strays
    out = dc.strike_duplicate(books, s.token, struck_by=OP)
    assert "could not be kept" in out.refused and not out.ledger_row
    assert MANUAL in (tmp_path / f"closed_trades_{OP}.json").read_text()
    assert _weekly_open_count(books.journal) == 3


def test_an_unknown_token_strikes_nothing(books, tmp_path):
    out = dc.strike_duplicate(books, "0" * 16, struck_by=OP)
    assert out.refused and out.struck is None
    assert not (tmp_path / dc.STRUCK_FILE).exists()


# ── the journal's strike ────────────────────────────────────────────────────

def test_the_journal_strikes_only_the_owner_it_is_given(tmp_path):
    j = _journal(tmp_path, [(_pos("T-1"), ""), (_pos("T-1"), FOLLOWER), (_pos("T-1"), OP)])
    assert j.strike("T-1", user_id="9999") is None
    row = j.strike("T-1", user_id=OP)
    assert row["uid"] == OP and row["trade_id"] == "T-1"
    assert sorted(e.user_id for e in j.closed_entries()) == ["", FOLLOWER]


def test_a_journal_that_could_not_be_saved_keeps_the_entry(tmp_path, monkeypatch):
    j = _journal(tmp_path, [(_pos("T-1"), OP)])
    monkeypatch.setattr(j, "_save", lambda: False)
    assert j.strike("T-1", user_id=OP) is None
    assert [e.trade_id for e in j.closed_entries()] == ["T-1"]


# ── the card, the command, the button ───────────────────────────────────────

def test_the_card_names_both_records_and_offers_one_tap(books):
    text, buttons = dc.duplicates_card(dc.find_duplicate_closes(books))
    assert "kept: operator book · 08 Oct 18:11 UTC · net $+0.48" in text
    assert (f"duplicate: book {OP} (an operator id's book with no keys linked now: "
            "strike only if it traded the operator's account)") in text
    assert "in its ledger + journal" in text
    assert f"Book {OP} also holds 1 close(s)" in text
    assert [label for label, _ in buttons] == ["Strike 1: OPEN/USDT 08 Oct 18:05 UTC"]


def test_the_card_with_nothing_found_says_what_it_compared(books, tmp_path):
    (tmp_path / f"closed_trades_{OP}.json").unlink()
    books.journal.strike(MANUAL, user_id=OP)
    text, buttons = dc.duplicates_card(dc.find_duplicate_closes(books))
    assert "None found: 0 second book(s)" in text and "1 filled close(s)" in text
    assert buttons == []


def _handler(engine, *, admin):
    from bot.skills.telegram_handler import TelegramHandler

    class _Users:
        def get(self, tid):
            return {"role": "admin" if admin else "viewer"}

        def has_permission(self, tid, perm):
            return True

        def get_lang(self, tid):
            return "en"

        def get_tier(self, tid):
            return "free"

        def is_admitted(self, tid):
            return True

        def permission_denial(self, tid, perm):
            return None

        def register(self, *args, **kwargs):
            return None

    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = engine
    h.users = _Users()
    h._limiter = type("L", (), {"allow": staticmethod(lambda uid: True)})()
    h._check_auth = lambda update: True
    h.sent = []

    async def _send(update, text, **kw):
        h.sent.append((text, kw.get("reply_markup")))
    h._send = _send
    return h


def _update(data=None):
    class _Query:
        async def answer(self):
            return None
    q = _Query()
    q.data = data
    q.message = None
    return NS(callback_query=q if data else None, message=NS(text="/duplicates"),
              effective_user=NS(id=int(OP)), effective_chat=NS(id=1))


def test_the_command_lists_and_the_tap_strikes(books, tmp_path):
    h = _handler(books, admin=True)
    asyncio.run(h._cmd_duplicates(_update(), None))
    (text, kb), = h.sent
    (row,), = kb.to_dict()["inline_keyboard"]
    assert MANUAL in text and row["callback_data"].startswith("dupstrike:")
    h.sent.clear()
    asyncio.run(h._handle_callback(_update(row["callback_data"]), None))
    (text, kb), = h.sent
    assert text.startswith(f"✅ Struck <code>{MANUAL}</code>") and "None found" in text
    assert kb is None
    assert MANUAL not in (tmp_path / f"closed_trades_{OP}.json").read_text()


def test_neither_door_opens_for_a_non_admin(books, tmp_path):
    h = _handler(books, admin=False)
    asyncio.run(h._cmd_duplicates(_update(), None))
    assert MANUAL not in h.sent[0][0]
    token = dc.find_duplicate_closes(books).strays[0].token
    asyncio.run(h._handle_callback(_update(f"dupstrike:{token}"), None))
    assert "Admin only" in h.sent[-1][0]
    assert MANUAL in (tmp_path / f"closed_trades_{OP}.json").read_text()
    assert not (tmp_path / dc.STRUCK_FILE).exists()
