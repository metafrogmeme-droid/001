"""The scan payload keeps the closed record and the open book apart.

`record_unreadable` was `closed_record_unreadable OR open_positions_unread`,
and the engine card renders that flag as "The closed-trade record could not be
read". So a record that read fine was blamed whenever a position fetch failed
or the venue omitted a position's mark. `open_book_unread` is the open book's
own flag now, set on every branch that did not read the book:

- the venue readout ran and the positions fetch failed, or a mark was missing;
- the exchange leg failed and only the trade file was read;
- the balance-cache fallback built rows from the executor's own book, which
  knows the positions and none of their marks;
- nothing returned and nothing stood in, where the open count's initial ``0``
  used to be published as a flat book.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import bot.skills.scan_skill as ss


def _payload(live_data, engine=None):
    cfg = MagicMock()
    cfg.simulation_mode = False
    cfg.live_trading_enabled = True
    cfg.risk.max_open_positions = 5
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_fetch_live_exchange_data", return_value=live_data), \
         patch.object(ss, "_build_features_block", return_value={}):
        return ss._build_scan_payload([], engine or MagicMock())["circuit_breaker"]


def _readout(**kw):
    base = {"equity": 900.0, "net_pnl": 12.0, "win_rate": 50.0,
            "total_trades": 4, "open_count": 1,
            "open_positions": [{"symbol": "BTCUSDT", "unrealized_pnl": 2.0}],
            "closed_trades": [], "closed_record_unreadable": False,
            "open_positions_unread": False}
    base.update(kw)
    return base


class TestTheTwoFlagsAreTwoFlags:
    def test_an_unread_book_is_not_an_unread_record(self):
        cb = _payload(_readout(net_pnl=None, open_positions_unread=True))
        assert cb["open_book_unread"] is True
        assert cb["record_unreadable"] is False

    def test_an_unread_record_is_not_an_unread_book(self):
        cb = _payload(_readout(closed_record_unreadable=True))
        assert cb["record_unreadable"] is True
        assert cb["open_book_unread"] is False

    def test_a_readout_that_read_both_raises_neither(self):
        cb = _payload(_readout())
        assert cb["record_unreadable"] is False
        assert cb["open_book_unread"] is False
        assert cb["open_count"] == 1


class TestEveryBranchThatDidNotReadTheBookSaysSo:
    def test_the_file_only_result(self, monkeypatch):
        """The exchange leg failed: the realized record is published, the book
        is not a reading."""
        out = ss._file_only_result(
            {"closed_record_unreadable": False, "open_positions": []},
            total=3, realized_pnl=7.5, win_rate=66.7)
        assert out["open_positions_unread"] is True
        assert out["open_count"] is None
        assert out["net_pnl"] == 7.5

    def test_nothing_returned_and_no_cache_is_not_a_flat_book(self):
        """A readout that failed on a bot with no closed trades returns None,
        and with the balance cache stale the open count's initial 0 stood."""
        engine = MagicMock()
        engine.live_balance_cached.return_value = None
        cb = _payload(None, engine)
        assert cb["live_unavailable"] is True
        assert cb["open_count"] is None, "a book nobody read was published as 0 open"
        assert cb["open_book_unread"] is True

    def test_the_cache_fallback_knows_positions_and_no_marks(self):
        engine = MagicMock()
        engine.live_balance_cached.return_value = {"total": 500.0}
        engine.live_executor.open_positions = [types.SimpleNamespace(
            symbol="ETH/USDT", direction="LONG", entry_price=2000.0, quantity=0.1)]
        cb = _payload(None, engine)
        assert cb["equity"] == 500.0
        assert cb["open_count"] == 1
        assert cb["open_positions"][0]["unrealized_pnl"] is None
        assert cb["open_book_unread"] is True

    def test_an_empty_executor_book_behind_a_fresh_cache_is_a_reading(self):
        engine = MagicMock()
        engine.live_balance_cached.return_value = {"total": 500.0}
        engine.live_executor.open_positions = []
        cb = _payload(None, engine)
        assert cb["open_count"] == 0
        assert cb["open_book_unread"] is False

    def test_a_readout_with_no_balance_keeps_the_book_it_read(self):
        """The unread-count branch is for a book nothing looked at. A readout
        that returned read the positions even when it read no balance."""
        engine = MagicMock()
        engine.live_balance_cached.return_value = None
        cb = _payload(_readout(equity=None), engine)
        assert cb["live_unavailable"] is True
        assert cb["open_count"] == 1
        assert cb["open_book_unread"] is False
