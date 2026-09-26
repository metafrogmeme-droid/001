"""A trade journal the bot could not fully read was written over by the next
close, and what it could not read was gone.

`TradeJournal._load` built entries in one loop inside one `try`, so the first
row it could not read (a key missing, a value of the wrong type) stopped the
load there: the rows ABOVE it were kept and every row BELOW it was dropped. It
did set `read_failed`, and `_save` ignored it, writing the in-memory list over
the file with a plain `open("w")`. Driven:

  * 50 rows, the third missing "sl"  -> 2 loaded -> one record_trade -> the
    file holds 3 rows. Forty-seven closes erased by one.
  * a file cut short mid-write        -> 0 loaded -> one record_trade -> the
    file holds 1 row.

The executor's closed-trade record had this defect and was fixed for it (read
row by row, keep an unreadable row verbatim and write it back, copy a file
that will not parse aside once before the first write over it, write nothing
when that copy fails, write atomically). The journal is the same kind of
record -- the setup record and the weekly review are built on it -- and gets
the same treatment.
"""
from __future__ import annotations

import json
import logging
import os

from bot.core import trade_journal as tj
from bot.core.trade_journal import TradeJournal


def _row(i, **over):
    d = {"trade_id": f"T{i}", "symbol": "BTC/USDT", "direction": "LONG",
         "strategy_type": "swing", "entry": 100.0, "exit": 101.0, "sl": 99.0,
         "tp": 103.0, "pnl": 1.0, "pnl_pct": 1.0, "r_mult": 1.0, "hold_hrs": 2.0,
         "ts": 1000.0 + i, "venue": "bitget", "uid": "7", "qty": 1.0}
    d.update(over)
    return d


def _record(j, trade_id="NEW", **over):
    kw = dict(trade_id=trade_id, symbol="ETH/USDT", direction="LONG",
              strategy_type="swing", entry_price=10.0, exit_price=11.0,
              stop_loss=9.0, take_profit=12.0, pnl=1.0, holding_hours=1.0,
              quantity=1.0)
    kw.update(over)
    return j.record_trade(**kw)


def _rows_with_a_bad_third():
    rows = [_row(i) for i in range(50)]
    del rows[2]["sl"]
    return rows


def _write(path, obj):
    path.write_text(json.dumps(obj))


def _kept_copies(path):
    return sorted(p for p in os.listdir(path.parent)
                  if p.startswith(path.name + ".unreadable-"))


class TestAnUnreadableRowIsKept:
    def test_the_rest_of_the_file_is_read(self, tmp_path):
        p = tmp_path / "trade_journal.json"
        _write(p, _rows_with_a_bad_third())
        j = TradeJournal(str(p))
        assert len(j.closed_entries()) == 49
        assert j.read_failed is True

    def test_one_close_does_not_erase_the_record(self, tmp_path):
        p = tmp_path / "trade_journal.json"
        rows = _rows_with_a_bad_third()
        _write(p, rows)
        _record(TradeJournal(str(p)))
        saved = json.loads(p.read_text())
        assert len(saved) == 51, "one close erased the rows below an unreadable one"
        assert rows[2] in saved, "the row this build could not read was not written back verbatim"
        assert {r["trade_id"] for r in saved} >= {r["trade_id"] for r in rows}

    def test_a_row_that_is_not_an_object_is_kept_too(self, tmp_path):
        p = tmp_path / "trade_journal.json"
        rows = [_row(0), "not a row", [1, 2], _row(1)]
        _write(p, rows)
        j = TradeJournal(str(p))
        assert [e.trade_id for e in j.closed_entries()] == ["T0", "T1"]
        _record(j)
        saved = json.loads(p.read_text())
        assert "not a row" in saved and [1, 2] in saved

    def test_the_kept_row_survives_a_restart(self, tmp_path):
        p = tmp_path / "trade_journal.json"
        rows = _rows_with_a_bad_third()
        _write(p, rows)
        _record(TradeJournal(str(p)))
        again = TradeJournal(str(p))
        assert again.read_failed is True, "a restart forgot the record is partial"
        assert len(again.closed_entries()) == 50
        _record(again, trade_id="NEW2")
        assert rows[2] in json.loads(p.read_text())

    def test_a_readable_file_is_not_marked_partial(self, tmp_path):
        p = tmp_path / "trade_journal.json"
        _write(p, [_row(i) for i in range(3)])
        j = TradeJournal(str(p))
        assert j.read_failed is False
        _record(j)
        assert len(json.loads(p.read_text())) == 4
        assert _kept_copies(p) == []


class TestAFileThatWillNotParse:
    def _cut(self, p):
        text = json.dumps([_row(i) for i in range(50)])
        cut = text[: len(text) // 2]
        p.write_text(cut)
        return cut

    def test_it_is_copied_aside_before_the_first_write(self, tmp_path):
        p = tmp_path / "trade_journal.json"
        cut = self._cut(p)
        j = TradeJournal(str(p))
        assert j.read_failed is True and j.closed_entries() == []
        _record(j)
        kept = _kept_copies(p)
        assert len(kept) == 1, "the file this build could not read was written over unkept"
        assert (tmp_path / kept[0]).read_text() == cut, "the kept copy is not the file as it was"
        assert len(json.loads(p.read_text())) == 1

    def test_it_is_copied_once(self, tmp_path, monkeypatch):
        p = tmp_path / "trade_journal.json"
        self._cut(p)
        j = TradeJournal(str(p))
        clock = iter(range(2_000_000, 2_000_100))
        monkeypatch.setattr(tj.time, "time", lambda: float(next(clock)))
        _record(j, trade_id="A")
        _record(j, trade_id="B")
        assert len(_kept_copies(p)) == 1, "the second write copied the bot's own file aside again"

    def test_a_copy_that_fails_writes_nothing(self, tmp_path, monkeypatch, caplog):
        p = tmp_path / "trade_journal.json"
        cut = self._cut(p)
        j = TradeJournal(str(p))

        def _no_copy(*_a, **_k):
            raise OSError("disk full")
        monkeypatch.setattr(tj.shutil, "copy2", _no_copy)
        with caplog.at_level(logging.WARNING, logger="bot.core.trade_journal"):
            _record(j)
        assert p.read_text() == cut, "a journal nobody kept a copy of was written over"
        assert any("Journal save failed" in r.getMessage() for r in caplog.records)
        monkeypatch.undo()
        _record(j, trade_id="LATER")
        assert len(_kept_copies(p)) == 1, "the copy was never retried"

    def test_a_file_that_is_not_a_list_is_kept_too(self, tmp_path):
        p = tmp_path / "trade_journal.json"
        p.write_text(json.dumps({"entries": [_row(0)]}))
        j = TradeJournal(str(p))
        assert j.read_failed is True
        _record(j)
        kept = _kept_copies(p)
        assert len(kept) == 1
        assert json.loads((tmp_path / kept[0]).read_text()) == {"entries": [_row(0)]}


class TestTheWriteIsAtomic:
    def test_a_write_that_raises_midway_leaves_the_file_whole(self, tmp_path):
        """`json.dump` into an `open("w")` truncates first and raises on the
        first value it cannot serialise, leaving a file cut short: the
        unparseable shape above, made by the journal itself."""
        p = tmp_path / "trade_journal.json"
        rows = [_row(i) for i in range(5)]
        _write(p, rows)
        j = TradeJournal(str(p))
        _record(j, signals_used=[object()])
        assert json.loads(p.read_text()) == rows

    def test_a_failed_write_is_said_at_warning(self, tmp_path, monkeypatch, caplog):
        p = tmp_path / "trade_journal.json"
        j = TradeJournal(str(p))

        def _fail(*_a, **_k):
            raise OSError("read-only filesystem")
        monkeypatch.setattr(tj, "atomic_write_json", _fail)
        with caplog.at_level(logging.WARNING, logger="bot.core.trade_journal"):
            _record(j)
        assert any(r.levelno >= logging.WARNING and "Journal save failed" in r.getMessage()
                   for r in caplog.records)


def test_an_absent_file_is_a_fresh_journal(tmp_path):
    p = tmp_path / "trade_journal.json"
    j = TradeJournal(str(p))
    assert j.read_failed is False
    _record(j)
    assert len(json.loads(p.read_text())) == 1
    assert _kept_copies(p) == []


def test_the_cap_applies_to_what_was_read_and_never_to_what_was_not(tmp_path, monkeypatch):
    """`KEEPS` bounds the readable entries; an unreadable row is written back
    whatever the cap, because trimming it would erase what nobody read."""
    keeps = 3
    monkeypatch.setattr(tj, "KEEPS", keeps)
    p = tmp_path / "trade_journal.json"
    rows = [_row(i) for i in range(5)]
    bad = dict(rows[0])
    del bad["sl"]
    rows[0] = bad
    _write(p, rows)
    _record(TradeJournal(str(p)))
    saved = json.loads(p.read_text())
    assert bad in saved
    assert len(saved) == 1 + keeps
