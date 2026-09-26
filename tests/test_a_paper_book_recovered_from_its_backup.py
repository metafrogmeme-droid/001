"""A paper book recovered from its backup reopened a close, and could never be
saved again.

The practice books (`data/portfolio_<user>.json`) keep a `.json.bak`, and the
loader falls back to it when the primary will not parse. Driven on the old
code, three things went wrong in a row:

* The backup was a copy of the PREVIOUS file, taken just before each write, so
  a recovery landed one save behind. A book whose last save was a close came
  back with that position open (open 0 -> 1, the close gone from history, the
  margin deducted again).
* Every save after the recovery was refused as a CONFLICT, because the
  damaged primary reads as "unreadable" and the stale-write guard refuses an
  unreadable file. The state was parked in `portfolio_<user>.conflict-<pid>
  .json`, and the next restart recovered the same backup: everything since
  the recovery was lost, on every restart.
* And the next restart loaded that parked sidecar as a USER. `portfolio_*.json`
  matches it, so `portfolio_777.conflict-4242.json` became a phantom book
  `777conflict-4242`, whose open positions the stop sweep then closed, writing
  into the file kept so that nothing was lost.

The backup holds the same state as the primary now (written after it), the
damaged file a recovery was made past is copied aside and then replaced, and a
dotted name is not restored as a book.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import bot.risk.multi_portfolio as mp
import bot.risk.portfolio as portfolio_mod
from bot.risk.portfolio import PortfolioTracker
from bot.utils.logger import trade_log
from bot.utils.models import Direction, TradeIdea
from bot.utils.state_lock import REVISION_KEY, conflict_path

DAMAGED = '{\n  "schema_version": 1,\n  "bal'


def _idea(asset="BTC/USDT"):
    return TradeIdea(asset=asset, direction=Direction.LONG, entry_price=100.0,
                     stop_loss=95.0, take_profit=120.0, confidence=0.7,
                     reasoning="practice", source="scan")


def _open(path: Path, asset="BTC/USDT") -> PortfolioTracker:
    t = PortfolioTracker(initial_balance=1000.0, state_file=str(path))
    t.open_position(_idea(asset), 100.0, leverage=1)
    return t


def _restart(path: Path) -> PortfolioTracker:
    return PortfolioTracker(initial_balance=None, state_file=str(path))


def _bak(path: Path) -> Path:
    return path.with_suffix(".json.bak")


def _kept(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.unreadable-*"))


@pytest.fixture
def records():
    """trade_log does not propagate, so caplog cannot see it."""
    seen: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            seen.append(record)

    h = _H(level=logging.DEBUG)
    trade_log.addHandler(h)
    try:
        yield seen
    finally:
        trade_log.removeHandler(h)


def _with_an_older_backup(path: Path) -> None:
    """A book whose backup is one save behind its damaged primary: what the
    old code always left, and what the new one leaves when a backup write
    did not land. The backup holds the position open; the primary had it
    closed, and is damaged."""
    t = _open(path)                               # auto-saves: open
    older = path.read_text()
    t.close_position(next(iter(t._positions)), 110.0)
    _bak(path).write_text(older)
    path.write_text(DAMAGED)


# ── the backup is the book ────────────────────────────────────────────

class TestTheBackupHoldsTheLastSave:
    def test_a_close_stays_closed_after_a_recovery(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        t = _open(path)
        t.close_position(next(iter(t._positions)), 110.0)   # the last save
        before = (len(t.open_positions), round(t.balance, 2), len(t._history))
        path.write_text(DAMAGED)

        r = _restart(path)
        assert (len(r.open_positions), round(r.balance, 2), len(r._history)) == before, (
            "the recovery landed one save behind and reopened the close")

    def test_the_backup_is_the_same_state_as_the_primary(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        t = _open(path)
        t.close_position(next(iter(t._positions)), 110.0)
        assert json.loads(_bak(path).read_text()) == json.loads(path.read_text())

    def test_a_backup_that_did_not_land_is_said_at_warning(
            self, tmp_path, monkeypatch, records):
        path = tmp_path / "portfolio_777.json"
        real = portfolio_mod.atomic_write_json

        def _refuse_backup(p, *a, **k):
            if str(p).endswith(".bak"):
                raise OSError("disk full")
            return real(p, *a, **k)

        monkeypatch.setattr(portfolio_mod, "atomic_write_json", _refuse_backup)
        _open(path)
        assert json.loads(path.read_text())["positions"], "the primary must still land"
        said = [r for r in records if "backup not written" in r.getMessage()]
        assert said and said[0].levelno >= logging.WARNING


# ── the first save after a recovery lands ─────────────────────────────

class TestTheSaveAfterARecovery:
    def test_it_is_written_and_nothing_is_parked(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        r = _restart(path)
        assert len(r.open_positions) == 1, "fixture: the older backup holds it open"
        r.close_position(next(iter(r._positions)), 105.0)
        on_disk = json.loads(path.read_text())
        assert on_disk["positions"] == {} and len(on_disk["history"]) == 1
        assert not conflict_path(path).exists(), "the save was refused as a conflict"

    def test_the_damaged_file_is_kept_byte_for_byte_first(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        _restart(path).save_state()
        kept = _kept(path)
        assert len(kept) == 1 and kept[0].read_text() == DAMAGED

    def test_the_kept_file_is_not_named_like_a_book(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        _restart(path).save_state()
        assert not _kept(path)[0].name.endswith(".json")

    def test_the_next_restart_reads_the_saved_book(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        r = _restart(path)
        r.close_position(next(iter(r._positions)), 105.0)
        again = _restart(path)
        assert len(again.open_positions) == 0 and len(again._history) == 1

    def test_the_file_is_kept_once(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        r = _restart(path)
        r.save_state()
        r.save_state()
        assert len(_kept(path)) == 1

    def test_the_revision_is_past_the_backups(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        backup_rev = json.loads(_bak(path).read_text())[REVISION_KEY]
        _restart(path).save_state()
        assert json.loads(path.read_text())[REVISION_KEY] == backup_rev + 1

    def test_the_revision_is_past_a_damaged_files_own(self, tmp_path):
        """A primary that parses and has no balance still carries a revision;
        the new one is past both."""
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        path.write_text(json.dumps({REVISION_KEY: 9}))
        _restart(path).save_state()
        assert json.loads(path.read_text())[REVISION_KEY] == 10

    def test_a_readable_book_is_never_copied_aside(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        t = _open(path)
        t.close_position(next(iter(t._positions)), 110.0)
        _restart(path).save_state()
        assert _kept(path) == []

    def test_the_recovery_says_what_it_kept(self, tmp_path, records):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        _restart(path)
        said = [r for r in records if "Recovered portfolio state" in r.getMessage()]
        assert said and said[0].levelno >= logging.WARNING
        assert path.name in said[0].getMessage()
        assert "copied aside before it is replaced" in said[0].getMessage()


# ── what is still refused ─────────────────────────────────────────────

class TestWhatIsStillRefused:
    def test_a_damaged_file_that_changed_since_is_not_replaced(self, tmp_path):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        r = _restart(path)
        path.write_text("{ somebody else's damage")
        r.save_state()
        assert path.read_text() == "{ somebody else's damage"
        assert conflict_path(path).exists()
        assert _kept(path) == []

    def test_a_file_that_cannot_be_kept_is_not_replaced(
            self, tmp_path, monkeypatch, records):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        r = _restart(path)

        def _no(*a, **k):
            raise OSError("read-only")

        monkeypatch.setattr(portfolio_mod.shutil, "copy2", _no)
        r.save_state()
        assert path.read_text() == DAMAGED, "a damaged file was replaced before it was kept"
        assert conflict_path(path).exists()
        said = [x for x in records if "could not be copied aside" in x.getMessage()]
        assert said and said[0].levelno >= logging.WARNING

    def test_the_licence_is_spent_by_the_save_that_uses_it(self, tmp_path):
        """The one file a recovery may write over is the one it recovered
        past, ONCE. The same damaged bytes back on disk after the save that
        replaced them are a new damage, refused like any other."""
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        r = _restart(path)
        r.save_state()                          # replaces the damaged file
        path.write_text(DAMAGED)                # the same bytes, again
        r.save_state()
        assert path.read_text() == DAMAGED
        assert conflict_path(path).exists()
        assert len(_kept(path)) == 1

    def test_a_damage_after_a_clean_load_is_still_refused(self, tmp_path):
        """The clobber guard's own case, unchanged: nothing was recovered."""
        path = tmp_path / "portfolio_777.json"
        t = _open(path)
        path.write_text("{ not json at all")
        t.save_state()
        assert path.read_text() == "{ not json at all"
        assert conflict_path(path).exists()


# ── a parked sidecar is not a user ────────────────────────────────────

def _registry(data_dir: Path, monkeypatch) -> mp.MultiUserPortfolio:
    monkeypatch.setattr(mp, "DATA_DIR", str(data_dir))
    import bot.core.venue_key as vk
    monkeypatch.setattr(vk, "venue_root", lambda: str(data_dir / "venue"))
    return mp.MultiUserPortfolio(default_balance=1000.0)


class TestAParkedFileIsNotABook:
    def test_a_conflict_sidecar_is_not_restored(self, tmp_path, monkeypatch):
        real = tmp_path / "portfolio_777.json"
        _open(real)
        side = tmp_path / "portfolio_777.conflict-4242.json"
        side.write_text(real.read_text())
        multi = _registry(tmp_path, monkeypatch)
        assert sorted(multi.all_portfolios()) == ["777"]

    def test_the_stop_sweep_does_not_write_into_it(self, tmp_path, monkeypatch):
        real = tmp_path / "portfolio_777.json"
        _open(real)
        side = tmp_path / "portfolio_777.conflict-4242.json"
        side.write_text(real.read_text())
        kept = side.read_text()
        multi = _registry(tmp_path, monkeypatch)
        closed = multi.check_stops_all({"BTC/USDT": 90.0})
        assert sorted(closed) == ["777"]
        assert side.read_text() == kept

    def test_a_split_venue_sidecar_is_not_restored(self, tmp_path, monkeypatch):
        venue = tmp_path / "venue" / "bybit"
        venue.mkdir(parents=True)
        real = venue / "portfolio_777.json"
        _open(real)
        (venue / "portfolio_777.conflict-9.json").write_text(real.read_text())
        multi = _registry(tmp_path, monkeypatch)
        assert sorted(multi._venue_portfolios) == [("777", "bybit")]

    def test_a_kept_damaged_file_is_not_restored(self, tmp_path, monkeypatch):
        path = tmp_path / "portfolio_777.json"
        _with_an_older_backup(path)
        _restart(path).save_state()
        assert _kept(path)
        multi = _registry(tmp_path, monkeypatch)
        assert sorted(multi.all_portfolios()) == ["777"]

    def test_real_books_still_load(self, tmp_path, monkeypatch):
        for name in ("777", "web5", "a-b_c"):
            _open(tmp_path / f"portfolio_{name}.json")
        multi = _registry(tmp_path, monkeypatch)
        assert sorted(multi.all_portfolios()) == ["777", "a-b_c", "web5"]

    def test_the_skip_is_said(self, tmp_path, monkeypatch, caplog):
        real = tmp_path / "portfolio_777.json"
        _open(real)
        (tmp_path / "portfolio_777.conflict-4242.json").write_text(real.read_text())
        with caplog.at_level(logging.WARNING, logger=mp.log.name):
            _registry(tmp_path, monkeypatch)
        said = [r for r in caplog.records if "Not restoring" in r.getMessage()]
        assert said and "conflict-4242" in said[0].getMessage()
