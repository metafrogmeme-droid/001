"""The scan's closed-trade rows carry the exit the executor recorded.

Driven on 2026-09-26: `_fetch_live_exchange_data` built each recent closed
row with ``float(t.get("exit_price", t.get("exit", 0)) or 0)``. The
executor's record (`closed_trade_row`) names that field ``close_price``, so
the reader found neither of its two spellings and published an exit of
``0.0`` for EVERY row, on the scan payload served to anonymous callers. The
same ``or 0`` turned an adopted position's unread entry (recorded as
``0.0``) into a measured entry of ``0.0``.

A price of zero is a level nobody stated (`price_on_record`), so an unread
price is ``None`` here, never ``0.0``. Both vocabularies are read, the
executor's name winning when a row carries both, which is the rule the chat
prompt's `_closed_trade_line` already follows for the same record.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import bot.skills.scan_skill as ss
from bot.core.live_executor import LivePosition, closed_trade_row


def _closed(entry, close, tid="t1"):
    return LivePosition(
        trade_id=tid, symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=entry, quantity=1.0, cost_usd=100.0,
        stop_loss=90.0, take_profit=120.0, leverage=1, is_spot=False,
        opened_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        close_price=close, pnl_usd=10.0, status="closed",
        strategy_type="breakout", close_reason="tp")


@pytest.fixture
def record(tmp_path, monkeypatch):
    """Write rows to the closed-trade file and read them back through the scan.

    The venue is not Bitget, so the readout takes the file-only path and no
    client is built; the rows are formatted before that branch either way.
    """
    path = tmp_path / "closed_trades.json"
    monkeypatch.setattr(ss, "_closed_trades_file", lambda: str(path))
    monkeypatch.setattr("bot.core.venues.get_venue",
                        lambda: SimpleNamespace(id="bybit"))

    def read(rows):
        path.write_text(json.dumps(rows, default=str))
        return ss._fetch_live_exchange_data()["closed_trades"]
    return read


def _prices(row):
    return row["entry_price"], row["exit_price"]


def test_an_executor_row_publishes_its_close_price(record):
    rows = record([closed_trade_row(_closed(100.0, 110.0))])
    assert _prices(rows[0]) == (100.0, 110.0), (
        "the executor records `close_price`; publishing 0.0 is the field "
        "read under the paper book's name")


def test_an_unpriced_close_publishes_no_exit(record):
    # close_position books close_price=None when the exit could not be read.
    rows = record([closed_trade_row(_closed(100.0, None))])
    assert rows[0]["exit_price"] is None
    assert rows[0]["entry_price"] == 100.0


def test_an_adopted_entry_nobody_stated_is_not_zero(record):
    # Adoption records 0.0 for an entry the venue did not state.
    rows = record([closed_trade_row(_closed(0.0, 110.0))])
    assert rows[0]["entry_price"] is None, "a level nobody stated was published as $0"
    assert rows[0]["exit_price"] == 110.0


@pytest.mark.parametrize("row, expected", [
    # the paper book's vocabulary
    ({"symbol": "ETH/USDT", "entry_price": 100.0, "exit_price": 105.0}, (100.0, 105.0)),
    # the oldest spelling the reader has always taken
    ({"symbol": "ETH/USDT", "entry": 100.0, "exit": 104.0}, (100.0, 104.0)),
    # a numeric string is a spelling of the price, not junk
    ({"symbol": "ETH/USDT", "entry_price": "100.5", "close_price": "106.25"}, (100.5, 106.25)),
    # junk, a NaN and a negative are not prices
    ({"symbol": "ETH/USDT", "entry_price": "n/a", "close_price": float("nan")}, (None, None)),
    ({"symbol": "ETH/USDT", "entry_price": -3.0, "close_price": 0}, (None, None)),
    # a row with no price fields at all
    ({"symbol": "ETH/USDT"}, (None, None)),
])
def test_each_vocabulary_reads_and_an_unread_price_is_none(record, row, expected):
    assert _prices(record([row])[0]) == expected


def test_the_executors_name_wins_when_a_row_carries_both(record):
    rows = record([{"symbol": "ETH/USDT", "entry_price": 100.0,
                    "close_price": 107.0, "exit_price": 999.0}])
    assert rows[0]["exit_price"] == 107.0


def test_a_close_price_recorded_as_unread_is_not_replaced_by_another_field(record):
    # The first field PRESENT is the reading (`_first_attr`'s rule): a row
    # that says its close price was not read is not priced off a second field.
    rows = record([{"symbol": "ETH/USDT", "entry_price": 100.0,
                    "close_price": None, "exit_price": 105.0}])
    assert rows[0]["exit_price"] is None


def test_the_payload_carries_what_the_reader_read(record, monkeypatch):
    record([closed_trade_row(_closed(100.0, 110.0)),
            closed_trade_row(_closed(0.0, None, tid="t2"))])
    cfg = MagicMock()
    cfg.simulation_mode = False
    cfg.live_trading_enabled = True
    cfg.risk.max_open_positions = 5
    engine = MagicMock()
    engine.live_balance_cached = lambda: {"total": 500.0}
    engine.live_executor.open_positions = []
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_build_features_block", return_value={}):
        cb = ss._build_scan_payload([], engine)["circuit_breaker"]
    assert [_prices(r) for r in cb["closed_trades"]] == [(100.0, 110.0), (None, None)]
