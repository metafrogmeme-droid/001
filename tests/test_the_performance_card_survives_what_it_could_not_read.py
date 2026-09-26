"""/performance survives what it could not read, and says so.

Three defects on one card, each driven through the real handler:

- **An adopted close the venue never priced took the whole card down.**
  `realized_totals(adopted)["net"]` is None when adopted closes exist and none
  could be priced, the ordinary case for an orphan whose entry the venue never
  stated, and the handler did `round(None, 2)`. The caller got no card.
- **A partial record was printed as the record.** `closed_positions` holds
  only the rows the executor could read, and `closed_trades_read_failed`
  says when that is not all of them. The portfolio card already says so; this
  one printed the win rate and all-time total as whole.
- **An "exchange trade history fallback" never ran.** It asked Bitget's
  ccxt client for `fetch_my_trades(symbol=None)`, which is refused before
  anything is sent, and built rows with `LivePosition(side=..., qty=...)`,
  which are not fields. It audited an ERROR on every /performance of an empty
  record and loaded nothing, so it is deleted.
"""
from __future__ import annotations

import pytest

import bot.skills.portfolio_commands as pc
from bot.formatters.realized_totals import CLOSED_RECORD_UNREAD
from bot.formatters.rich_cards import render_live_portfolio_summary
from bot.skills.portfolio_commands import PortfolioCommands
from tests.test_the_record_cards_read_the_callers_book import Stand, _Closed


class _Live:
    def is_live(self):
        return True

    def __getattr__(self, name):
        return getattr(_REAL_CONFIG, name)


_REAL_CONFIG = pc.CONFIG


class Book:
    def __init__(self, closed, read_failed=False):
        self.closed_positions = closed
        self.open_positions = []
        self.closed_trades_read_failed = read_failed
        self.exchange_asked = 0

    async def _get_exchange(self):
        self.exchange_asked += 1
        raise AssertionError("the card asked the venue")


async def _card(monkeypatch, book, audits=None):
    monkeypatch.setattr(pc, "CONFIG", _Live())
    if audits is not None:
        monkeypatch.setattr(pc, "audit", lambda log, msg, **kw: audits.append(kw),
                            raising=False)
    me = Stand({"scope": "own", "executor": book, "balance": None,
                "total": None, "age_s": None})
    await PortfolioCommands._cmd_performance(me, object(), object())
    return "\n".join(me.sent)


@pytest.mark.asyncio
async def test_an_unpriced_adopted_close_does_not_take_the_card_down(monkeypatch):
    book = Book([_Closed(137.42, "T-1"), _Closed(None, "TI-adopted-SOL-1")])
    said = await _card(monkeypatch, book)
    assert "PERFORMANCE" in said, f"no card was sent: {said!r}"
    assert "Excluded 1 adopted orphan (—)" in said, said
    assert "$+137.42" in said


@pytest.mark.asyncio
async def test_a_priced_adopted_close_keeps_its_figure(monkeypatch):
    book = Book([_Closed(137.42, "T-1"), _Closed(-4.5, "TI-adopted-SOL-1")])
    said = await _card(monkeypatch, book)
    assert "Excluded 1 adopted orphan ($-4.50)" in said, said


@pytest.mark.asyncio
async def test_a_partial_record_says_so_above_the_figures(monkeypatch):
    said = await _card(monkeypatch, Book([_Closed(137.42, "T-1")], read_failed=True))
    assert CLOSED_RECORD_UNREAD in said, said
    assert said.index(CLOSED_RECORD_UNREAD) < said.index("Returns"), (
        "the caveat is read after the figures it qualifies")


@pytest.mark.asyncio
async def test_a_readable_record_says_nothing_about_it(monkeypatch):
    said = await _card(monkeypatch, Book([_Closed(137.42, "T-1")]))
    assert CLOSED_RECORD_UNREAD not in said


@pytest.mark.asyncio
async def test_an_empty_record_asks_no_venue_and_audits_no_error(monkeypatch):
    audits: list = []
    book = Book([])
    await _card(monkeypatch, book, audits)
    assert book.exchange_asked == 0
    assert not [a for a in audits if a.get("action") == "perf_exchange_fallback"]


def test_both_cards_say_the_partial_record_in_one_sentence():
    lines = render_live_portfolio_summary(
        equity=100.0, open_count=0, exposure=None, exposure_note="",
        realized_pnl=None, total_closed=0, win_rate=None, unscored=0,
        read_failed=True)
    assert any(CLOSED_RECORD_UNREAD in line for line in lines), lines
