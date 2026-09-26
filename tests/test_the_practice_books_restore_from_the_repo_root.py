"""The practice books restore from the repo root, and only users' books do.

`MultiUserPortfolio._load_existing` globbed ``data/portfolio_*.json`` against
the WORKING DIRECTORY, while every book is written through `state_path` to the
repo root. Driven: a practice book holding a BTC position at $9,500, a
restart from another directory, and the restore found nobody; the next
practice fill created a fresh $10,000 book and saved it over the old one. The
BTC position and the balance were gone, with no conflict file, and the backup
held the same replacement. That is the 2026-08-19 DB_PATH incident
`bot/utils/paths.py` records, in a module the anchoring did not reach.

The same glob matched the operator's own paper book. ``PORTFOLIO_STATE_FILE``
defaults to ``data/portfolio_state.json``, so it was restored as a practice
user named ``state``, holding the operator's positions: stop-swept, published
as a trader, summed into every combined snapshot, and the sweep wrote into the
operator's file.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from bot.config import CONFIG
from bot.risk import multi_portfolio as mp
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import Direction, TradeIdea
from bot.utils.paths import state_path


def _idea(asset="BTC/USDT", tid="TI-A"):
    return TradeIdea(id=tid, asset=asset, direction=Direction.LONG,
                     entry_price=100.0, stop_loss=90.0, take_profit=130.0,
                     confidence=0.7, reasoning="r", signals_used=["s"],
                     timestamp=datetime.now(timezone.utc))


def _book_with_a_position(uid="777"):
    book = mp.MultiUserPortfolio().get(uid)
    book.open_position(_idea(), 500.0)
    return state_path(f"data/portfolio_{uid}.json")


class TestTheRestoreDoesNotDependOnTheWorkingDirectory:
    def test_a_restart_from_elsewhere_restores_the_book(self, tmp_path,
                                                         monkeypatch):
        _book_with_a_position("777")
        monkeypatch.chdir(tmp_path)
        restored = mp.MultiUserPortfolio().all_portfolios()
        assert "777" in restored, "the restore globbed the working directory"
        snap = restored["777"].snapshot()
        assert (snap.open_positions, round(snap.balance_usd, 2)) == (1, 9500.0)

    def test_the_first_fill_after_it_does_not_replace_the_book(self, tmp_path,
                                                               monkeypatch):
        path = _book_with_a_position("777")
        monkeypatch.chdir(tmp_path)
        book = mp.MultiUserPortfolio().get("777")
        book.open_position(_idea("ETH/USDT", "TI-B"), 100.0)
        on_disk = PortfolioTracker(initial_balance=None, state_file=str(path))
        assert {p.asset for p in on_disk.open_positions} == {"BTC/USDT", "ETH/USDT"}

    def test_a_new_book_is_written_where_the_restore_reads(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.chdir(tmp_path)
        mp.MultiUserPortfolio().get("888").open_position(_idea(), 100.0)
        assert state_path("data/portfolio_888.json").exists()
        assert not (tmp_path / "data" / "portfolio_888.json").exists()


class TestTheOperatorsBookIsNotAUser:
    def _operator_book(self, path=None):
        target = state_path(path or CONFIG.portfolio_state_file)
        op = PortfolioTracker(initial_balance=10000.0, state_file=str(target))
        op.open_position(_idea("SOL/USDT", "TI-OP"), 1000.0)
        return target

    def test_the_default_state_file_is_not_restored_as_a_user(self, caplog):
        self._operator_book()
        _book_with_a_position("777")
        with caplog.at_level(logging.INFO, logger=mp.log.name):
            restored = mp.MultiUserPortfolio().all_portfolios()
        assert "state" not in restored
        assert "777" in restored, "a user's book beside it must still restore"
        assert any("operator's own paper book" in r.getMessage()
                   for r in caplog.records)

    def test_a_stop_sweep_cannot_write_into_it(self):
        target = self._operator_book()
        kept = target.read_text()
        mp.MultiUserPortfolio().check_stops_all({"SOL/USDT": 50.0})
        assert target.read_text() == kept

    def test_the_rule_is_the_configured_path_not_the_name(self):
        """A PORTFOLIO_STATE_FILE pointed elsewhere is the operator's book;
        a file merely named like the default is then somebody's to restore."""
        was = CONFIG.portfolio_state_file
        object.__setattr__(CONFIG, "portfolio_state_file",
                           "data/portfolio_operator.json")
        try:
            self._operator_book("data/portfolio_operator.json")
            self._operator_book("data/portfolio_state.json")
            restored = mp.MultiUserPortfolio().all_portfolios()
        finally:
            object.__setattr__(CONFIG, "portfolio_state_file", was)
        assert "operator" not in restored
        assert "state" in restored


class TestOneReadingOfWhereTheBooksLive:
    def test_get_and_the_restore_read_one_directory(self, tmp_path,
                                                   monkeypatch):
        monkeypatch.setattr(mp, "DATA_DIR", str(tmp_path / "books"))
        (tmp_path / "books").mkdir()
        mp.MultiUserPortfolio().get("777").open_position(_idea(), 100.0)
        assert (tmp_path / "books" / "portfolio_777.json").exists()
        assert "777" in mp.MultiUserPortfolio().all_portfolios()

    def test_the_operators_book_is_recognised_through_a_symlinked_data_dir(
            self, tmp_path, monkeypatch):
        """`deploy.sh` symlinks ``data/`` to a persistent store, so the glob
        hands back a path through the link and the configured file resolves
        to the store. Both sides are resolved, or the operator's book reads as
        somebody else's again on exactly the deployed box."""
        real = tmp_path / "store"
        real.mkdir()
        link = tmp_path / "data"
        link.symlink_to(real)
        monkeypatch.setattr(mp, "DATA_DIR", str(link))
        was = CONFIG.portfolio_state_file
        object.__setattr__(CONFIG, "portfolio_state_file",
                           str(link / "portfolio_state.json"))
        try:
            op = PortfolioTracker(initial_balance=10000.0,
                                  state_file=str(link / "portfolio_state.json"))
            op.open_position(_idea("SOL/USDT", "TI-OP"), 1000.0)
            restored = mp.MultiUserPortfolio().all_portfolios()
        finally:
            object.__setattr__(CONFIG, "portfolio_state_file", was)
        assert "state" not in restored
