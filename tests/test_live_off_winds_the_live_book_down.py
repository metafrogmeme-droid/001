"""Live off stops new live entries; it does not abandon a live position.

2026-10-31: `/livepositions` showed an INJ long opened 14 October, 417 hours
old, priced 10% through its own stop, with both time exits "due now". Live
had been turned off on 18 October (`/golive OFF`), and every path that manages
a live position asked `CONFIG.is_live()` and was told no: the monitor, the
stop self-heal, reconcile, `/closeall` ("No live executor available.") and the
emergency flatten. The owner closed it by hand on the venue.

Now a live book that still holds a row while live is off WINDS DOWN
(`RuneClawEngine._winding_down_executors`): the monitor keeps its stops,
exits and reconcile running with every entry refused (`WIND_DOWN_HALT`), no
orphan is adopted, and `/closeall` and the emergency flatten close it. A flat
book is untouched, so a paper deployment is the deployment it was.

Driven through the real `_check_open_positions`, `flatten_all_positions` and
`_cmd_close_all`, both arms each.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace as NS

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.skills import telegram_handler as th
from bot.utils.logger import system_log


class _Book:
    """A live executor's book that records what was asked of it."""

    def __init__(self, held=("INJ/USDT",), user_id=None, unreadable=False):
        self.user_id = user_id
        self._unreadable = unreadable
        self._rows = {f"t{i}": NS(symbol=s, status="open") for i, s in enumerate(held)}
        self.calls: list = []
        self.open_positions: list = []
        self._closed_trades: list = []
        self._last_close_data = None

    @property
    def _positions(self):
        if self._unreadable:
            raise RuntimeError("book unreadable")
        return self._rows

    async def check_positions(self, entry_halt=None):
        self.calls.append(("check", entry_halt))
        return []

    async def reconcile_positions(self):
        self.calls.append(("reconcile",))
        return []

    async def verify_and_fix_sltp(self):
        self.calls.append(("sltp",))

    async def sync_positions_from_exchange(self):
        self.calls.append(("sync",))

    async def close_all_positions(self, reason=""):
        self.calls.append(("close_all", reason))
        return [f"{s} closed" for s in (r.symbol for r in self._rows.values())]


@pytest.fixture
def mode(monkeypatch):
    """Set `is_live()` and record every orphan-adoption sweep."""
    adopted: list = []

    async def _sync(eng):
        adopted.append(eng)
        return []

    monkeypatch.setattr(engine_mod, "sync_portfolio_with_exchange", _sync)

    def _set(live: bool):
        monkeypatch.setattr(type(engine_mod.CONFIG), "is_live", lambda self: live)
        return adopted
    return _set


def _engine(book, users=None):
    eng = RuneClawEngine()
    eng.live_executor = book
    eng._user_executors = dict(users or {})
    eng._last_sltp_verify_ts = 0.0
    return eng


# ── which books wind down ────────────────────────────────────────────────────

class TestWhichBooksWindDown:
    def test_live_has_none_the_live_path_runs_instead(self, mode):
        mode(True)
        assert RuneClawEngine._winding_down_executors(_engine(_Book())) == []

    def test_off_a_held_book_winds_down_and_a_flat_one_does_not(self, mode):
        mode(False)
        held, flat = _Book(), _Book(held=())
        assert _engine(held)._winding_down_executors() == [held]
        assert _engine(flat)._winding_down_executors() == []

    def test_an_unreadable_book_may_hold_one_and_counts(self, mode):
        mode(False)
        book = _Book(unreadable=True)
        assert _engine(book)._winding_down_executors() == [book]

    def test_a_users_held_book_counts_beside_a_flat_operator(self, mode):
        mode(False)
        mine = _Book(user_id="424242")
        eng = _engine(_Book(held=()), users={"424242": mine})
        assert eng._winding_down_executors() == [mine]

    def test_a_closed_row_is_not_held(self, mode):
        mode(False)
        book = _Book()
        book._rows["t0"].status = "closed"
        assert _engine(book)._winding_down_executors() == []


# ── the monitor keeps managing it ────────────────────────────────────────────

class TestTheMonitorWindsItDown:
    def test_off_a_held_book_is_managed_and_takes_on_nothing(self, mode):
        adopted = mode(False)
        book = _Book()
        asyncio.run(_engine(book)._check_open_positions())
        assert ("check", RuneClawEngine.WIND_DOWN_HALT) in book.calls, book.calls
        assert ("reconcile",) in book.calls and ("sltp",) in book.calls
        assert adopted == [], "a book winding down adopts no orphan"

    def test_off_only_the_held_book_is_asked_and_a_flat_one_is_not(self, mode):
        mode(False)
        flat, mine = _Book(held=()), _Book(user_id="424242")
        asyncio.run(_engine(flat, users={"424242": mine})._check_open_positions())
        assert flat.calls == [], "a flat book is not on the venue's clock"
        assert {c[0] for c in mine.calls} >= {"sltp", "sync", "check", "reconcile"}

    def test_off_a_flat_book_is_left_alone(self, mode):
        adopted = mode(False)
        book = _Book(held=())
        asyncio.run(_engine(book)._check_open_positions())
        assert book.calls == [] and adopted == []

    def test_live_runs_the_live_path_with_its_own_halt_and_adoption(self, mode):
        adopted = mode(True)
        book = _Book()
        eng = _engine(book)
        asyncio.run(eng._check_open_positions())
        halts = [c[1] for c in book.calls if c[0] == "check"]
        assert halts == [eng._entry_halt_reason(book)]
        assert RuneClawEngine.WIND_DOWN_HALT not in halts
        assert adopted == [eng]

    def test_the_wind_down_is_said_once_not_every_tick(self, mode, caplog):
        mode(False)
        eng = _engine(_Book())
        saved = system_log.propagate
        system_log.propagate = True
        try:
            with caplog.at_level(logging.INFO, logger=system_log.name):
                asyncio.run(eng._check_open_positions())
                asyncio.run(eng._check_open_positions())
        finally:
            system_log.propagate = saved
        said = [r for r in caplog.records if "still hold positions" in r.getMessage()]
        assert len(said) == 1, [r.getMessage() for r in said]


# ── /closeall and the emergency flatten close it ─────────────────────────────

class TestTheFlattenClosesIt:
    def test_off_a_held_book_is_closed(self, mode):
        mode(False)
        book = _Book()
        rows = asyncio.run(_engine(book).flatten_all_positions(reason="admin_closeall"))
        assert ("close_all", "admin_closeall") in book.calls
        assert [r["account"] for r in rows] == ["operator"]
        assert rows[0]["messages"] == ["INJ/USDT closed"]

    def test_off_a_flat_book_closes_nothing(self, mode):
        mode(False)
        book = _Book(held=())
        assert asyncio.run(_engine(book).flatten_all_positions()) == []
        assert book.calls == []

    def test_live_closes_as_before(self, mode):
        mode(True)
        book = _Book()
        rows = asyncio.run(_engine(book).flatten_all_positions(reason="x"))
        assert ("close_all", "x") in book.calls and len(rows) == 1


def _closeall_reply(engine) -> str:
    sent: list = []

    async def _send(update, text, **kw):
        sent.append((text, kw.get("reply_markup")))

    host = NS(_send=_send, _is_admin=lambda update: True, engine=engine)
    asyncio.run(th.TelegramHandler._cmd_close_all(host, None, None))
    assert len(sent) == 1, sent
    return sent[0]


class TestTheCloseallDoor:
    def test_off_over_a_held_book_offers_the_confirm(self, mode):
        mode(False)
        text, keyboard = _closeall_reply(_engine(_Book()))
        assert "Flatten ALL open positions" in text and keyboard is not None
        assert "No live executor available" not in text

    def test_off_over_a_flat_book_says_there_is_nothing_live(self, mode):
        mode(False)
        text, keyboard = _closeall_reply(_engine(_Book(held=())))
        assert text == "No live executor available." and keyboard is None

    def test_live_offers_the_confirm_as_before(self, mode):
        mode(True)
        text, keyboard = _closeall_reply(_engine(_Book(held=())))
        assert "Flatten ALL open positions" in text and keyboard is not None
