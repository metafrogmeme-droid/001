"""An open position the venue did not price is not a break-even one.

`_fetch_live_exchange_data` summed `float(p.get("unrealizedPnl", 0) or 0)`
over every open position and shipped the result inside `net_pnl` -- a partial
total, printed as whole, on the payload the website's portfolio summary is
built from. CLAUDE.md's table names this shape exactly.

It is not hypothetical. `bot/formatters/orphan_position.py` records that the
venue omits `unrealizedPnl` more often than it reports a real 0.00 and treats
the missing field as unknown; `telegram_handler`'s /portfolio counts `_marked`
and prints "unknown" when nothing carried a mark. Same measurement, honest on
two surfaces and a confident $0 on the third -- and the third is the one that
reaches the website, which already renders a null `net_pnl` as "--" and was
simply never sent one.

A failed positions fetch was the same shape one level up: `positions = []`
rendered "0 open positions" and let the total sum to $0 over rows nobody read.
"""
from __future__ import annotations

import dataclasses
import sys
import types

import pytest

import bot.skills.scan_skill as ss
from bot.core.live_executor import closed_trade_row
from tests.test_scan_reads_the_executors_record import _pos, record

# `record` is a pytest fixture defined beside the sibling test and reused here.
# Naming it in __all__ is a module-level USE, which is what keeps ruff's F811
# ("redefinition of unused") from firing on every test that takes it as a
# parameter -- without a noqa on each signature.
__all__ = ["record"]


class _FakeExchange:
    def __init__(self, positions=None, raise_positions=False):
        self._positions = positions if positions is not None else []
        self._raise = raise_positions

    def set_sandbox_mode(self, _flag):
        return None

    def fetch_balance(self, *_a, **_k):
        return {"USDT": {"total": 1000.0}, "total": {"USDT": 1000.0}}

    def fetch_positions(self, *_a, **_k):
        if self._raise:
            raise RuntimeError("ER_VENUE_TIMEOUT")
        return self._positions


@pytest.fixture
def exchange(monkeypatch):
    """Plant a fake `ccxt` (the import is local to the function) and a bitget
    venue, so the live readout runs against positions of our choosing."""
    holder: dict[str, _FakeExchange] = {}

    def bitget(_cfg):
        return holder["ex"]

    fake_ccxt = types.SimpleNamespace(bitget=bitget)
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)
    import bot.core.venues as venues
    monkeypatch.setattr(venues, "get_venue",
                        lambda: types.SimpleNamespace(id="bitget"))

    def plant(**kw):
        holder["ex"] = _FakeExchange(**kw)
        return holder["ex"]
    return plant


def _open(symbol, upnl, contracts=1.0):
    d = {"symbol": symbol, "side": "long", "contracts": contracts,
         "entryPrice": 100.0, "notional": 100.0, "initialMargin": 10.0,
         "leverage": 10}
    if upnl is not None:
        d["unrealizedPnl"] = upnl
    return d


# ── the defect ──────────────────────────────────────────────────────

def test_an_unpriced_open_position_is_not_summed_as_zero(record, exchange):
    _path, write = record
    write([closed_trade_row(_pos(100.0, tid="a"))])
    exchange(positions=[_open("BTC/USDT:USDT", 25.0),
                        _open("ETH/USDT:USDT", None)])       # venue omitted it
    data = ss._fetch_live_exchange_data()
    assert data is not None, 'the live readout produced nothing'
    assert data["net_pnl"] is None, \
        "one unmarked position and the total was still printed as whole"
    assert data["open_positions_unread"] is True
    rows = {r["symbol"]: r["unrealized_pnl"] for r in data["open_positions"]}
    assert rows["ETHUSDT"] is None, "the missing mark was published as $0.00"
    assert rows["BTCUSDT"] == 25.0, "a real mark must still be a real number"


def test_every_position_priced_keeps_the_real_total(record, exchange):
    """The honest sum must survive: this is the case the code was written for."""
    _path, write = record
    write([closed_trade_row(_pos(100.0, tid="a"))])
    exchange(positions=[_open("BTC/USDT:USDT", 25.0),
                        _open("ETH/USDT:USDT", -5.0)])
    data = ss._fetch_live_exchange_data()
    assert data is not None, 'the live readout produced nothing'
    assert data["net_pnl"] == 120.0
    assert data["open_positions_unread"] is False


def test_a_genuine_zero_from_the_venue_is_still_a_zero(record, exchange):
    """0.0 is a measured break-even. Only an ABSENT field is unknown."""
    _path, write = record
    write([closed_trade_row(_pos(100.0, tid="a"))])
    exchange(positions=[_open("BTC/USDT:USDT", 0.0)])
    data = ss._fetch_live_exchange_data()
    assert data is not None, 'the live readout produced nothing'
    assert data["net_pnl"] == 100.0
    assert data["open_positions_unread"] is False


def test_a_failed_positions_fetch_is_not_an_empty_book(record, exchange):
    _path, write = record
    write([closed_trade_row(_pos(100.0, tid="a"))])
    exchange(raise_positions=True)
    data = ss._fetch_live_exchange_data()
    assert data is not None, 'the live readout produced nothing'
    assert data["net_pnl"] is None, "a fetch that threw was summed as $0 open"
    assert data["open_positions_unread"] is True


def test_no_open_positions_is_a_measured_flat_book(record, exchange):
    """An empty list from a fetch that ANSWERED is a real zero-position book."""
    _path, write = record
    write([closed_trade_row(_pos(100.0, tid="a"))])
    exchange(positions=[])
    data = ss._fetch_live_exchange_data()
    assert data is not None, 'the live readout produced nothing'
    assert data["net_pnl"] == 100.0
    assert data["open_positions_unread"] is False


# ── it reaches the payload ──────────────────────────────────────────

def test_the_payload_flag_folds_in_the_open_book(record, exchange, monkeypatch):
    """`record_unreadable` was only ever true for the CLOSED record. An open
    book with an unmarked row must raise the same flag, or the dashboard
    renders "--" for net_pnl with nothing explaining why."""
    _path, write = record
    write([closed_trade_row(_pos(100.0, tid="a"))])
    exchange(positions=[_open("BTC/USDT:USDT", None)])
    # AppConfig is frozen, and scan_skill imports CONFIG locally per function,
    # so patch the MODULE attribute with a frozen-safe copy -- setattr on the
    # instance raises FrozenInstanceError, and setattr on ss.CONFIG would miss
    # the local import entirely.
    import bot.config as cfg_mod
    live = dataclasses.replace(cfg_mod.CONFIG, simulation_mode=False,
                               live_trading_enabled=True)
    monkeypatch.setattr(cfg_mod, "CONFIG", live)
    payload = ss._build_scan_payload([], engine=None)
    cb = payload["circuit_breaker"]
    assert cb["net_pnl"] is None
    assert cb["record_unreadable"] is True
