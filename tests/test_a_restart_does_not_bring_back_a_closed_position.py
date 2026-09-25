"""A restart must not bring a closed position back as an open one.

Three defects on one file, all driven here:

* **A flat book came back from the backup.** ``_save_positions`` keeps a
  ``.bak`` of the last NON-EMPTY file, and the loader fell back to it whenever
  the main file read ``{}``. So once the book went flat, the main file said
  "nothing open" and the backup still held the last position that closed, and
  every restart loaded that position as "open": the next tick's local stop and
  target check then acted on a position the venue no longer held.
* **"closing" was never written.** ``close_position`` sets the status and
  saves, and the save kept only "open" and "pending_fill". A restart inside
  the close (leg cancels, the market close, the fill polls) lost the row with
  other positions open, or brought it back unflagged from the backup with none.
  The loader's stuck-in-"closing" recovery, and the incident fix built on it
  (``tests/test_recovered_from_closing_dedup.py``), could never be reached.
* **One unreadable closed-trade row cost every row below it.** The loader
  stopped at the first row it could not read and kept the rows above it; the
  next close then wrote that partial list over the file.

Every row whose true state is unknown now waits for reconcile, which asks the
venue, and says so on disk so a second restart cannot forget it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot.core.live_executor as le
from bot.compat import UTC
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor, LivePosition

UID = "9"
CREDS = {"api_key": "a", "api_secret": "b", "passphrase": "c"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    return tmp_path


def _ex():
    return LiveExecutor(user_id=UID, credentials=CREDS, venue="bitget")


def _pos(tid, symbol="BTC/USDT:USDT"):
    return LivePosition(trade_id=tid, symbol=symbol, direction="LONG",
                        entry_price=100.0, quantity=1.0, cost_usd=20.0,
                        stop_loss=95.0, take_profit=110.0, leverage=5)


def _open(ex, *tids):
    for tid in tids:
        ex._positions[tid] = _pos(tid)
    ex._save_positions()


def _close(ex, tid, via_closing=True):
    if via_closing:
        ex._positions[tid].status = "closing"
        ex._save_positions()
    ex._positions[tid].status = "closed"
    ex._save_positions()


# ── the flat book ──────────────────────────────────────────────────────────


class TestAFlatBookStaysFlat:

    @pytest.mark.parametrize("via_closing", [True, False],
                             ids=["through-closing", "open-straight-to-closed"])
    def test_the_last_closed_position_waits_for_reconcile(self, state, via_closing):
        ex = _ex()
        _open(ex, "A")
        _close(ex, "A", via_closing=via_closing)
        assert json.loads(Path(ex._positions_file).read_text()) == {}

        again = _ex()
        # It is still loaded -- the backup is kept because a main file can be
        # wrong -- but nothing may act on it until the venue has been asked.
        assert "A" in again._positions
        assert again.awaiting_reconcile("A")

    def test_a_non_empty_book_defers_nothing(self, state):
        ex = _ex()
        _open(ex, "A", "B")
        again = _ex()
        assert set(again._positions) == {"A", "B"}
        assert not again.awaiting_reconcile("A")
        assert not again.awaiting_reconcile("B")

    def test_an_unparseable_main_file_defers_what_the_backup_holds(self, state):
        ex = _ex()
        _open(ex, "A")
        _open(ex, "A", "B")                 # the backup now holds {A}
        Path(ex._positions_file).write_text("{not json")
        again = _ex()
        assert again.awaiting_reconcile("A")

    def test_the_deferral_is_audited_by_name(self, state, monkeypatch):
        seen = []
        ex = _ex()
        _open(ex, "A")
        _close(ex, "A", via_closing=False)
        monkeypatch.setattr(le, "audit", lambda log, msg, **kw: seen.append(
            (kw.get("result"), (kw.get("data") or {}).get("trade_ids"))))
        _ex()
        assert ("BACKUP_DEFERRED", ["A"]) in seen


# ── closing ────────────────────────────────────────────────────────────────


class TestClosingIsWritten:

    def test_a_position_mid_close_survives_a_restart_beside_others(self, state):
        ex = _ex()
        _open(ex, "A", "B")
        ex._positions["A"].status = "closing"
        ex._save_positions()
        rows = json.loads(Path(ex._positions_file).read_text())
        assert rows["A"]["status"] == "closing", (
            "the save that marks a close in flight dropped the row from disk")

        again = _ex()
        assert set(again._positions) == {"A", "B"}
        assert again._positions["A"].status == "open"
        assert again.awaiting_reconcile("A")
        assert not again.awaiting_reconcile("B")


# ── the deferral survives a second restart ──────────────────────────────────


class TestTheDeferralIsOnDisk:

    def test_a_save_before_reconcile_keeps_it(self, state):
        ex = _ex()
        _open(ex, "A", "B")
        ex._positions["A"].status = "closing"
        ex._save_positions()
        first = _ex()
        first._save_positions()             # any save, before reconcile ran
        second = _ex()
        assert second.awaiting_reconcile("A"), (
            "a restart before reconcile ran reloaded the row as an ordinary "
            "open position")
        assert not second.awaiting_reconcile("B")

    def test_once_reconcile_resolves_it_the_flag_is_gone(self, state):
        ex = _ex()
        _open(ex, "A", "B")
        ex._positions["A"].status = "closing"
        ex._save_positions()
        first = _ex()
        first._recovered_from_closing.discard("A")   # reconcile: still open
        first._save_positions()
        assert "awaiting_reconcile" not in json.loads(
            Path(first._positions_file).read_text())["A"]
        assert not _ex().awaiting_reconcile("A")


# ── the engine's smart exits ask the same question ─────────────────────────


class _Executor:
    def __init__(self, positions, awaiting=()):
        self._positions = {p.trade_id: p for p in positions}
        self.closed = []
        self._awaiting = set(awaiting)

    def awaiting_reconcile(self, trade_id):
        return trade_id in self._awaiting

    async def close_position(self, trade_id, reason="bot_auto", close_price=0):
        self.closed.append(trade_id)
        return f"CLOSED {trade_id}"


def _stale(tid):
    return SimpleNamespace(
        trade_id=tid, symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        stop_loss=90.0, status="open", signal_type="momentum_confluence",
        strategy_type="swing", opened_at=datetime.now(UTC) - timedelta(hours=60))


@pytest.mark.asyncio
async def test_a_smart_exit_does_not_close_a_position_awaiting_reconcile():
    ex = _Executor([_stale("A"), _stale("B")], awaiting={"A"})
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.ws_feed = SimpleNamespace(is_connected=lambda: True,
                                  get_prices=lambda max_age_sec=None: {"BTC/USDT": 100.5})
    eng._last_vwap = {}
    eng._close_notify_callback = None
    eng.live_executor = ex
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.time_stop.enabled = True
    m.time_stop.live_auto_close_enabled = True
    try:
        await eng._evaluate_live_smart_exits(ex)
    finally:
        p.stop()
    assert "A" not in ex.closed
    assert "B" in ex.closed, "the control case: an ordinary stale position still exits"


# ── the closed-trade record ────────────────────────────────────────────────


def _closed_row(tid, opened="2026-09-20T00:00:00+00:00"):
    return {"trade_id": tid, "symbol": "BTC/USDT:USDT", "direction": "LONG",
            "entry_price": 100.0, "quantity": 1.0, "cost_usd": 20.0,
            "stop_loss": 95.0, "take_profit": 110.0, "leverage": 5,
            "close_price": 105.0, "pnl_usd": 5.0, "opened_at": opened,
            "closed_at": "2026-09-21T00:00:00+00:00", "venue": "bitget"}


def _closed_file(ex):
    return Path(ex._closed_trades_file)


class TestOneBadRowCostsOneRow:

    def _plant(self, ex_path, rows):
        ex_path.parent.mkdir(parents=True, exist_ok=True)
        ex_path.write_text(json.dumps(rows))

    def _new_close(self, ex, tid="NEW", symbol="BTC/USDT:USDT"):
        # A second close on the same symbol and entry within two hours is
        # suppressed as a duplicate booking and never saved, so a drive that
        # needs a second SAVE needs a second symbol.
        pos = _pos(tid, symbol)
        pos.status = "closed"
        pos.close_price = 104.0
        pos.pnl_usd = 4.0
        pos.closed_at = datetime.now(UTC)
        ex._append_closed_trade(pos)

    def test_the_rows_below_a_bad_one_are_read(self, state):
        path = state / f"closed_trades_{UID}.json"
        bad = _closed_row("T2", opened="not a time")
        self._plant(path, [_closed_row("T1"), bad, _closed_row("T3")])
        ex = _ex()
        assert [p.trade_id for p in ex._closed_trades] == ["T1", "T3"]
        assert ex._closed_trades_read_failed, (
            "a record with a row nobody could read is partial, and says so")

    def test_the_next_close_keeps_the_bad_row_verbatim(self, state):
        path = state / f"closed_trades_{UID}.json"
        bad = _closed_row("T2", opened="not a time")
        self._plant(path, [_closed_row("T1"), bad, _closed_row("T3")])
        ex = _ex()
        self._new_close(ex)
        rows = json.loads(path.read_text())
        assert [r["trade_id"] for r in rows] == ["T2", "T1", "T3", "NEW"]
        assert rows[0] == bad

    def test_a_file_that_will_not_parse_is_kept_before_the_first_write(
            self, state, monkeypatch):
        path = state / f"closed_trades_{UID}.json"
        path.write_text("[{broken")
        ex = _ex()
        # The copy is named by the second it was made, so the two closes run
        # on two clocks: on one, a copy made twice lands on one name and a
        # repeat would be invisible.
        monkeypatch.setattr(le.time, "time", lambda: 1_000_000.0)
        self._new_close(ex)
        kept = list(state.glob(f"closed_trades_{UID}.json.unreadable-*"))
        assert len(kept) == 1 and kept[0].read_text() == "[{broken"
        assert [r["trade_id"] for r in json.loads(path.read_text())] == ["NEW"]
        # Only once: the second close does not copy the file it wrote itself.
        monkeypatch.setattr(le.time, "time", lambda: 2_000_000.0)
        self._new_close(ex, "NEW2", "ETH/USDT:USDT")
        assert [r["trade_id"] for r in json.loads(path.read_text())] == ["NEW", "NEW2"]
        assert len(list(state.glob(f"closed_trades_{UID}.json.unreadable-*"))) == 1

    def test_a_file_that_is_not_a_list_is_kept_too(self, state):
        path = state / f"closed_trades_{UID}.json"
        path.write_text(json.dumps({"T1": _closed_row("T1")}))
        ex = _ex()
        assert ex._closed_trades_read_failed
        self._new_close(ex)
        assert len(list(state.glob(f"closed_trades_{UID}.json.unreadable-*"))) == 1

    def test_if_the_copy_fails_nothing_is_written_over_it(self, state, monkeypatch):
        path = state / f"closed_trades_{UID}.json"
        path.write_text("[{broken")
        ex = _ex()
        import shutil
        monkeypatch.setattr(shutil, "copy2",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
        self._new_close(ex)
        assert path.read_text() == "[{broken"

    def test_a_readable_file_is_not_copied(self, state):
        path = state / f"closed_trades_{UID}.json"
        self._plant(path, [_closed_row("T1")])
        ex = _ex()
        assert not ex._closed_trades_read_failed
        self._new_close(ex)
        assert list(state.glob("*.unreadable-*")) == []
