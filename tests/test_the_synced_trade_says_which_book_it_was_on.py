"""The website's copy of a trade says which book it was on, when the bot knows.

The share card prints a PAPER or LIVE chip only from a `book` the website
hands it, and the website's row carried none: `routes/sync.js` stored no such
column and `website_sync.py` sent no such field, so the dashboard's share
button claimed neither book for every trade, including the ones the bot knew
perfectly well. The bot knows on both writers: the paper portfolio's rows are
`TradeExecution` records with `is_paper=True`, and the live executor's rows
are the live book by construction (`_sync_live_state_to_website` marks them
`is_paper=False`).

Three values, never two. `_book_of` answers "paper" for a True flag, "live"
for a False one, and None for anything else, and the payload carries the key
only when there is a word to carry: a stand-in that forgot the field, or a
record from before the flag, names no book, the website stores NULL, and the
card claims neither. A default in either direction would print PAPER on a
real-money card or LIVE on a practice one.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

import bot.utils.website_sync as ws
from bot.core.engine import RuneClawEngine
from bot.utils.models import Direction, TradeExecution


def _trade(**kw) -> TradeExecution:
    base = dict(trade_id="TI-1", asset="BTC/USDT", direction=Direction.LONG,
                entry_price=100.0, quantity=1.0, stop_loss=95.0, take_profit=110.0,
                opened_at=datetime(2026, 10, 17, 20, 12, tzinfo=UTC))
    base.update(kw)
    return TradeExecution(**base)


class TestTheReader:
    def test_a_paper_flag_is_paper(self):
        assert ws._book_of(_trade(is_paper=True)) == "paper"
        assert ws._book_of({"is_paper": True}) == "paper"

    def test_a_live_flag_is_live(self):
        assert ws._book_of(_trade(is_paper=False)) == "live"
        assert ws._book_of({"is_paper": False}) == "live"

    @pytest.mark.parametrize("row", [
        {}, {"is_paper": None}, {"is_paper": "yes"}, {"is_paper": 1}, {"is_paper": 0},
        object(),
    ])
    def test_no_bool_is_no_book(self, row):
        """A string, a number, an absent field: none of them is a measurement
        of which book, and a truthiness read would have called 1 paper."""
        assert ws._book_of(row) is None


@pytest.fixture
def posted(monkeypatch):
    """Capture what the sync module posts; the website is never reached."""
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(ws, "_post", lambda path, data, **kw: seen.append((path, data)) or {"ok": True})
    return seen


class TestTheBulkWire:
    def test_a_paper_row_says_paper_on_both_lists(self, posted):
        ws.sync_portfolio(1000.0, [_trade(is_paper=True)],
                          [_trade(trade_id="TI-2", is_paper=True, exit_price=105.0, pnl=5.0,
                                  closed_at=datetime(2026, 10, 19, 22, 35, tzinfo=UTC))])
        _, body = posted[-1]
        assert body["positions"][0]["book"] == "paper"
        assert body["closed_trades"][0]["book"] == "paper"

    def test_a_live_row_says_live(self, posted):
        ws.sync_portfolio(1000.0, [_trade(is_paper=False)], [])
        _, body = posted[-1]
        assert body["positions"][0]["book"] == "live"

    def test_a_row_without_the_flag_sends_no_book(self, posted):
        """Both arms: the key is absent, not null and not a default."""
        ws.sync_portfolio(1000.0, [{
            "asset": "ETH/USDT", "direction": "SHORT", "entry_price": 10.0,
            "quantity": 2.0, "opened_at": "2026-10-17T20:12:00+00:00",
        }], [])
        _, body = posted[-1]
        row = body["positions"][0]
        assert row["symbol"] == "ETH/USDT"
        assert "book" not in row

    def test_the_live_executors_rows_are_the_live_book(self, monkeypatch):
        """`_sync_live_state_to_website` builds dicts from the executor's own
        positions, and those dicts must carry the flag the reader reads."""
        from tests.test_live_website_sync import _live_pos, _stub_engine_for_sync
        seen = {}
        monkeypatch.setattr(ws, "sync_in_background",
                            lambda eq, positions, closed: seen.update(p=positions, c=closed))
        pos = _live_pos("TI-1", "HYPE/USDT:USDT")
        done = _live_pos("TI-2", "ENA/USDT:USDT", status="closed", pnl=0.1, close_price=63.6)
        RuneClawEngine._sync_live_state_to_website(_stub_engine_for_sync([pos], [done]))
        assert ws._book_of(seen["p"][0]) == "live"
        assert ws._book_of(seen["c"][0]) == "live"


class TestTheSingleEventWire:
    def test_the_event_carries_the_same_claim(self, posted):
        ws.sync_trade_event(1, "open", _trade(is_paper=False), 1000.0)
        assert posted[-1][1]["trade"]["book"] == "live"
        ws.sync_trade_event(1, "close", _trade(is_paper=True, exit_price=101.0, pnl=1.0,
                                               closed_at=datetime(2026, 10, 19, tzinfo=UTC)), 1000.0)
        assert posted[-1][1]["trade"]["book"] == "paper"

    def test_an_unflagged_event_claims_nothing(self, posted):
        ws.sync_trade_event(1, "open", {"asset": "ETH/USDT", "direction": "LONG",
                                        "entry_price": 10.0, "quantity": 1.0}, 1000.0)
        assert "book" not in posted[-1][1]["trade"]


def test_the_paper_books_own_fill_is_flagged_paper():
    """The paper portfolio writes `is_paper=True` on the record it books, so
    the close callback's rows name their book without any help."""
    import ast
    import inspect

    import bot.risk.portfolio as pf
    src = inspect.getsource(pf)
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "TradeExecution"]
    assert calls, "the paper book no longer builds a TradeExecution; find its record"
    for c in calls:
        kws = {k.arg: k.value for k in c.keywords}
        assert "is_paper" in kws and isinstance(kws["is_paper"], ast.Constant) \
            and kws["is_paper"].value is True, ast.unparse(c)[:120]
