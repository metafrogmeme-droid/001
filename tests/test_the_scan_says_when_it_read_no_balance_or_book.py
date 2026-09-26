"""The scan payload says when it read no balance and no book.

Driven on 2026-09-26, live mode, the engine's balance cache stale:

* the venue readout RAISED (or was skipped, which every /venue switch away
  from Bitget does on every scan), `_file_only_result` returned the realized
  record with the defaults under it, and the payload published
  ``equity: 0`` and ``open_count: 0`` with ``live_unavailable: False``. The
  slot chip read ``Open Positions: 0/5`` in green. The docstring over that
  function said the caller "treats that as unknown"; it did so only while the
  cache was fresh.
* the balance was read and the POSITIONS fetch failed: ``open_count`` was
  ``len([])``, the same green ``0/5``.

`open_positions_rule` already had the words for this ("unread/5") and
`_slot_count` already refused a book nobody read, keyed on
``live_data_loaded``. That flag says the readout RETURNED, not that it read
anything, so both paths walked past it. Unread is unread on every field it
feeds: the count and the balance are None, and ``live_unavailable`` is keyed
on whether a balance was read.

The counted zero is the other half and is pinned too: a flat book the venue
answered for is 0, and a measured $0.00 balance is not "unavailable".
"""

import json
import logging
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import bot.skills.scan_skill as ss
from bot.core.live_executor import LivePosition, closed_trade_row


def _closed(pnl, tid):
    return LivePosition(
        trade_id=tid, symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=90.0, take_profit=120.0, leverage=1, is_spot=False,
        opened_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        close_price=110.0, pnl_usd=pnl, status="closed",
        strategy_type="breakout", close_reason="tp")


class _Venue:
    """A stand-in ccxt client: each read answers or raises as planted."""

    def __init__(self, balance=None, positions=None):
        self._balance, self._positions = balance, positions

    def set_sandbox_mode(self, *a, **k):
        pass

    def fetch_balance(self, *a, **k):
        if isinstance(self._balance, Exception):
            raise self._balance
        return self._balance

    def fetch_positions(self, *a, **k):
        if isinstance(self._positions, Exception):
            raise self._positions
        return self._positions


DOWN = RuntimeError("venue down")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two closes on disk (+10, -4), a planted venue, and live mode."""
    path = tmp_path / "closed_trades.json"
    path.write_text(json.dumps([closed_trade_row(_closed(v, f"t{i}"))
                                for i, v in enumerate([10.0, -4.0])], default=str))
    monkeypatch.setattr(ss, "_closed_trades_file", lambda: str(path))
    fake = types.ModuleType("ccxt")
    state = {"venue": _Venue(DOWN, DOWN)}
    fake.bitget = lambda *a, **k: state["venue"]
    monkeypatch.setitem(sys.modules, "ccxt", fake)
    monkeypatch.setattr("bot.core.venues.get_venue", lambda: SimpleNamespace(id="bitget"))
    return state


def _payload(cache=None, book=()):
    cfg = MagicMock()
    cfg.simulation_mode = False
    cfg.live_trading_enabled = True
    cfg.risk.max_open_positions = 5
    engine = MagicMock()
    engine.live_balance_cached = lambda: cache
    engine.live_executor.open_positions = list(book)
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_build_features_block", return_value={}):
        return ss._build_scan_payload([], engine)["circuit_breaker"]


def _slots(cb):
    rows = [r for r in cb["rules"] if str(r.get("label", "")).startswith("Open Positions")]
    assert len(rows) == 1, cb["rules"]
    return rows[0]


# ── the readout ──────────────────────────────────────────────────────

def test_a_readout_that_raised_publishes_no_balance_and_no_count(world):
    data = ss._fetch_live_exchange_data()
    assert data["equity"] is None, "a balance nobody read was published as a figure"
    assert data["open_count"] is None, "a book nobody read was counted"
    # What the trade file supports still arrives.
    assert data["total_trades"] == 2
    assert data["net_pnl"] == 6.0


def test_a_venue_switch_publishes_no_balance_and_no_count(world, monkeypatch):
    monkeypatch.setattr("bot.core.venues.get_venue", lambda: SimpleNamespace(id="bybit"))
    world["venue"] = _Venue(AssertionError("the Bitget readout ran for a Bybit operator"),
                            AssertionError("the Bitget readout ran for a Bybit operator"))
    data = ss._fetch_live_exchange_data()
    assert data["equity"] is None
    assert data["open_count"] is None
    assert data["total_trades"] == 2


def test_a_positions_fetch_that_failed_is_not_a_flat_book(world):
    world["venue"] = _Venue({"USDT": {"total": 250.0}}, DOWN)
    data = ss._fetch_live_exchange_data()
    assert data["equity"] == 250.0
    assert data["open_count"] is None


def test_a_flat_book_the_venue_answered_for_is_zero(world):
    world["venue"] = _Venue({"USDT": {"total": 250.0}}, [])
    data = ss._fetch_live_exchange_data()
    assert data["open_count"] == 0
    assert data["equity"] == 250.0


# ── the payload ──────────────────────────────────────────────────────

def test_the_payload_says_the_account_was_not_read(world):
    cb = _payload(cache=None, book=[MagicMock()])
    assert cb["equity"] is None
    assert cb["open_count"] is None
    assert cb["live_unavailable"] is True, "a balance nobody read was not flagged"
    slots = _slots(cb)
    assert slots["active"] is None, "the slot chip judged a book nobody read"
    assert slots["label"] == "Open Positions: unread/5"
    # The realized record the trade file supports is still published.
    assert cb["total_trades"] == 2
    assert cb["net_pnl"] == 6.0


def test_a_fresh_cache_and_the_executors_book_still_answer(world):
    book = [SimpleNamespace(symbol="BTC/USDT", direction="LONG",
                            entry_price=100.0, quantity=1.0)]
    cb = _payload(cache={"total": 500.0}, book=book)
    assert cb["equity"] == 500.0
    assert cb["open_count"] == 1
    assert cb["live_unavailable"] is False
    assert _slots(cb)["label"] == "Open Positions: 1/5"


def test_a_balance_the_venue_answered_without_a_figure_is_unavailable(world):
    world["venue"] = _Venue({"info": "no USDT row"}, [])
    cb = _payload(cache=None)
    assert cb["equity"] is None
    assert cb["live_unavailable"] is True
    # The venue did answer for the book, and it was flat.
    assert cb["open_count"] == 0


def test_a_measured_zero_balance_is_not_unavailable(world):
    world["venue"] = _Venue({"USDT": {"total": 0.0}}, [])
    cb = _payload(cache=None)
    assert cb["equity"] == 0.0
    assert cb["live_unavailable"] is False


def test_the_log_line_can_say_unread(world, caplog):
    with caplog.at_level(logging.INFO, logger=ss.log.name):
        _payload(cache=None)
    assert "Live exchange data loaded: equity=unread" in caplog.text
    assert "unread open" in caplog.text
