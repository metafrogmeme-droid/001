"""The per-person position cap counts the person's LIVE positions.

`docs/MULTI_VENUE_RISK_SPLIT.md` records the decision: caps per PERSON,
because money is not per venue, and "two venues each with their own max 5 is
ten positions against one person's money". A per-user engine gets its person
totals from `set_person_totals_fn`, and that function summed
`user_portfolios.venue_readings`: the PAPER practice books. Driven with
per-user live on: a person holding three live positions on bitget and three
on bybit, against a cap of five, read `OPEN_POSITIONS: 3 OK` (the active
venue's count), while the person totals said `open_positions=0,
equity_usd=10000.0`, a practice book nobody had traded.

In live mode the totals now come from the person's live books
(`_live_person_readings` -> `venue_aggregate.position_totals`). A linked
venue whose book is saved but not loaded is a count nobody read, so the total
is a floor and the cap refuses. The practice books stop touching the live
caps. Paper mode keeps them, because there they are the book.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.risk.venue_aggregate import VenueReading, position_totals
from tests.test_core import _DEFAULT_ATR, _DEFAULT_MAX_POS, _make_idea

UID = "777"


def _ex(venue, n, uid=UID):
    return NS(_venue=NS(id=venue), user_id=uid,
              open_positions=[f"{venue}-{i}" for i in range(n)])


def _store(venues=(), raises=False):
    def _list(uid):
        if raises:
            raise RuntimeError("store unreadable")
        return list(venues) if uid == UID else []
    return NS(list_venues=_list)


@pytest.fixture
def per_user(monkeypatch):
    original = engine_mod.CONFIG.per_user_live_enabled
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", True)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store())
    try:
        yield monkeypatch
    finally:
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", original)


@pytest.fixture
def live(per_user):
    per_user.setattr(type(engine_mod.CONFIG), "is_live", lambda self: True)
    return per_user


def _engine(**executors):
    eng = RuneClawEngine()
    eng._is_operator_user = lambda uid: False
    eng._user_executors = dict(executors)
    return eng


def _positions_line(eng, active_count=3):
    chk = eng.risk_for(UID).evaluate(
        _make_idea(), atr=_DEFAULT_ATR, live_equity=1000.0,
        max_position_usd=_DEFAULT_MAX_POS, live_open_count=active_count,
        live_mode=True, live_book=[], live_account="bitget")
    lines = [x for x in chk.checks_passed + chk.checks_failed
             if x.startswith(("OPEN_POSITIONS", "MAX_POSITIONS"))]
    assert len(lines) == 1, lines
    return lines[0], lines[0] in chk.checks_failed


class TestTheLiveBooksAreCounted:
    def test_two_venues_count_against_one_cap(self, live):
        eng = _engine(**{UID: _ex("bitget", 3), f"bybit/{UID}": _ex("bybit", 3)})
        line, refused = _positions_line(eng)
        assert refused and line == "MAX_POSITIONS: 6 >= 5", line

    def test_the_practice_book_is_not_counted_live(self, live):
        eng = _engine(**{UID: _ex("bitget", 1)})
        live.setattr(eng.user_portfolios, "venue_readings",
                     lambda uid: [VenueReading(venue="bitget", open_positions=9,
                                               equity_usd=10.0,
                                               daily_pnl_usd=-5.0)])
        t = eng.risk_for(UID)._person_totals()
        assert t.open_positions == 1
        assert t.equity_usd is None and t.daily_pnl_usd is None
        line, refused = _positions_line(eng, active_count=1)
        assert not refused, line

    def test_another_persons_executor_is_not_counted(self, live):
        eng = _engine(**{UID: _ex("bitget", 2), "888": _ex("bitget", 4, "888"),
                         "bybit/888": _ex("bybit", 4, "888")})
        assert eng.risk_for(UID)._person_totals().open_positions == 2

    def test_a_complete_reading_leaves_drawdown_and_daily_loss_alone(self, live):
        """No person-level equity or daily P&L is summed live: the engine
        already records every close the person makes, and a venue's balance is
        read only when it is traded. The two gates keep their own figures and
        refuse nothing for want of one."""
        eng = _engine(**{UID: _ex("bitget", 1), f"bybit/{UID}": _ex("bybit", 1)})
        chk = eng.risk_for(UID).evaluate(
            _make_idea(), atr=_DEFAULT_ATR, live_equity=1000.0,
            max_position_usd=_DEFAULT_MAX_POS, live_open_count=1,
            live_mode=True, live_book=[], live_account="bitget")
        lines = chk.checks_passed + chk.checks_failed
        assert "DAILY_LOSS: 0.0% OK" in chk.checks_passed, lines
        assert any(x.startswith("DRAWDOWN: 0.0% OK") for x in chk.checks_passed)


class TestAVenueNobodyLoadedIsAFloor:
    def test_a_saved_book_with_no_executor_refuses_by_name(self, live):
        live.setattr("bot.core.exchange_credentials.get_credential_store",
                     lambda: _store(["bitget", "bybit"]))
        live.setattr("bot.core.live_executor.saved_book_holds_positions",
                     lambda uid, state_dir: state_dir is not None)
        eng = _engine(**{UID: _ex("bitget", 1)})
        line, refused = _positions_line(eng, active_count=1)
        assert refused, line
        assert "FLOOR" in line.upper(), line
        assert "bybit" in line, line

    def test_a_linked_venue_with_nothing_saved_counts_zero(self, live):
        live.setattr("bot.core.exchange_credentials.get_credential_store",
                     lambda: _store(["bitget", "bybit"]))
        live.setattr("bot.core.live_executor.saved_book_holds_positions",
                     lambda uid, state_dir: False)
        eng = _engine(**{UID: _ex("bitget", 1)})
        t = eng.risk_for(UID)._person_totals()
        assert (t.open_positions, t.venues_read, t.unreadable) == (1, 2, ())
        line, refused = _positions_line(eng, active_count=1)
        assert not refused, line

    def test_a_saved_book_that_raises_is_unread(self, live):
        live.setattr("bot.core.exchange_credentials.get_credential_store",
                     lambda: _store(["bybit"]))

        def _raise(uid, state_dir):
            raise OSError("disk")

        live.setattr("bot.core.live_executor.saved_book_holds_positions", _raise)
        eng = _engine(**{UID: _ex("bitget", 1)})
        assert eng.risk_for(UID)._person_totals().unreadable == ("bybit",)

    def test_an_unknown_linked_venue_is_unread_not_the_default_book(self, live):
        """`normalize_venue` answers '' for a venue this build does not know,
        and '' is the default venue's path: the unknown venue would have been
        counted off bitget's saved book."""
        live.setattr("bot.core.exchange_credentials.get_credential_store",
                     lambda: _store(["zzz"]))
        asked: list = []
        live.setattr("bot.core.live_executor.saved_book_holds_positions",
                     lambda uid, state_dir: asked.append(state_dir) or False)
        eng = _engine(**{UID: _ex("bitget", 1)})
        t = eng.risk_for(UID)._person_totals()
        assert t.unreadable == ("zzz",)
        assert asked == []

    def test_an_executor_whose_venue_cannot_be_read_still_counts(self, live):
        eng = _engine(**{UID: NS(_venue=None, user_id=UID,
                                 open_positions=["a", "b"]),
                         f"bybit/{UID}": _ex("bybit", 1)})
        assert eng.risk_for(UID)._person_totals().open_positions == 3

    def test_a_store_that_cannot_list_makes_the_set_unknown(self, live):
        live.setattr("bot.core.exchange_credentials.get_credential_store",
                     lambda: _store(raises=True))
        eng = _engine(**{UID: _ex("bitget", 2)})
        t = eng.risk_for(UID)._person_totals()
        assert t.open_positions == 2
        assert t.unreadable == ("linked venues",)
        line, refused = _positions_line(eng, active_count=2)
        assert refused, line


class TestPaperModeKeepsThePracticeBooks:
    def test_paper_totals_are_the_practice_books(self, per_user):
        per_user.setattr(type(engine_mod.CONFIG), "is_live", lambda self: False)
        eng = _engine(**{UID: _ex("bitget", 3)})
        per_user.setattr(eng.user_portfolios, "venue_readings",
                         lambda uid: [VenueReading(venue="bitget",
                                                   open_positions=2,
                                                   equity_usd=900.0,
                                                   daily_pnl_usd=-3.0)])
        t = eng.risk_for(UID)._person_totals()
        assert (t.open_positions, t.equity_usd, t.daily_pnl_usd) == (2, 900.0, -3.0)


class TestPositionTotals:
    def test_it_sums_and_names_what_it_could_not_read(self):
        t = position_totals([VenueReading(venue="bitget", open_positions=2),
                             VenueReading(venue="bybit"),
                             VenueReading(venue="bingx", open_positions=0)])
        assert (t.open_positions, t.venues_read, t.unreadable) == (2, 2, ("bybit",))
        assert t.equity_usd is None and t.daily_pnl_usd is None
        assert not t.complete

    def test_nothing_is_an_empty_complete_reading(self):
        t = position_totals([])
        assert (t.open_positions, t.venues_read, t.unreadable) == (0, 0, ())
