"""A market entry booked at the PRE-ORDER TICKER is an estimate, and it says so.

`execute()` reads the fill price off the order response (``average`` or
``price``), else off the fills it produced, else off the order read back,
else off the position row read after the fill -- and when none of those
states one it falls back to ``current_price``, the ticker read BEFORE the
order went out. Driven through the real ``execute`` with a Bitget-shaped
venue (its create endpoint answers ids only, so ``average``, ``price`` and
``filled`` parse as None): fills empty, order read-back with no average,
position row with ``entryPrice: None``. The recorded entry was the ticker,
the card said ``Fill: $4,000.00``, the record carried no trace, the slippage
guard measured the ticker against the idea, and every reader took it as the
fill. A latency blip is enough to reach it.

The entry's SOURCE travels with the figure now (``entry_source``), the
estimate is the one value readers act on: the card marks it, the audit and
warning-rate stream say it, the slippage record and guard are skipped for
it, the chat prompt's row tells the model, the web row carries a
three-valued flag, a restart keeps it, the periodic position sync corrects
it from the venue's own ``avgPrice`` and only ever an estimate, and a close
whose P&L was computed locally off it says so in its ``fill_source``.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import ccxt
import pytest

from bot.config import CONFIG, RUNTIME
from bot.core import bounds_shadow
from bot.core import live_executor as le
from bot.core.live_executor import (
    ENTRY_ESTIMATED,
    ENTRY_ESTIMATED_SUFFIX,
    ENTRY_UNREAD,
    LiveExecutor,
    LivePosition,
    entry_close_suffix,
    entry_estimated_state,
    entry_is_estimated,
    venue_avg_price,
)
from bot.core.venues import get_venue
from bot.utils.models import Direction, TradeIdea
from tests.test_an_unread_entry_is_an_unpriced_close import (
    _executor as _close_executor,
)
from tests.test_an_unread_entry_is_an_unpriced_close import (  # noqa: F401
    _no_sleep,  # a fixture, taken by pytest.mark.usefixtures rather than by a shadowing parameter
    _pos100,
)

MARKET = {
    "id": "ETHUSDT", "symbol": "ETH/USDT:USDT", "base": "ETH", "quote": "USDT",
    "settle": "USDT", "baseId": "ETH", "quoteId": "USDT", "settleId": "USDT",
    "type": "swap", "spot": False, "margin": False, "swap": True, "future": False,
    "option": False, "active": True, "contract": True, "linear": True,
    "inverse": False, "contractSize": 1.0,
    "precision": {"amount": 0.01, "price": 0.01},
    "limits": {"amount": {"min": 0.01, "max": None}, "price": {"min": None, "max": None},
               "cost": {"min": 5.0, "max": None}, "leverage": {"min": 1, "max": 125}},
    "info": {},
}
TICKER = 4000.0


class Venue:
    """A Bitget stand-in whose order response states what it is told to."""

    def __init__(self, price=TICKER, *, row_entry=None, order_average=None,
                 trades=(), fetched_average=None):
        self.bg = ccxt.bitget()
        self.bg.set_markets([MARKET])
        self.markets = self.bg.markets
        self.price = price
        self.row_entry = row_entry
        self.order_average = order_average
        self.trades = list(trades)
        self.fetched_average = fetched_average
        self.orders: list[dict] = []
        self.lev = 5

    async def load_markets(self, *a, **k):
        return self.bg.markets

    async def fetch_ticker(self, symbol, *a, **k):
        return {"symbol": symbol, "last": self.price, "bid": self.price * 0.9999,
                "ask": self.price * 1.0001, "timestamp": int(time.time() * 1000)}

    async def fetch_order_book(self, symbol, limit=25, *a, **k):
        p = self.price
        return {"bids": [[p * (1 - 0.0001 * i), 1e6] for i in range(1, 26)],
                "asks": [[p * (1 + 0.0001 * i), 1e6] for i in range(1, 26)]}

    async def fetch_funding_rate(self, *a, **k):
        return {"fundingRate": 0.0001}

    async def set_margin_mode(self, *a, **k):
        return None

    async def set_leverage(self, leverage=None, symbol=None, params=None, **k):
        self.lev = leverage
        return {}

    async def fetch_leverage(self, *a, **k):
        v = self.lev
        return {"longLeverage": v, "shortLeverage": v, "leverage": v,
                "marginMode": "isolated",
                "info": {"marginMode": "isolated", "longLeverage": str(v),
                         "shortLeverage": str(v)}}

    async def fetch_balance(self, *a, **k):
        return {"USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0},
                "free": {"USDT": 10000.0}, "total": {"USDT": 10000.0}}

    async def fetch_positions(self, syms=None, params=None, *a, **k):
        return [{"symbol": "ETH/USDT:USDT", "side": "long", "contracts": 0.05,
                 "entryPrice": self.row_entry, "markPrice": self.price,
                 "unrealizedPnl": 0.0, "initialMargin": 40.0, "leverage": self.lev,
                 "info": {}}]

    async def fetch_open_orders(self, *a, **k):
        return []

    async def fetch_ohlcv(self, *a, **k):
        return []

    async def fetch_account_configuration(self, *a, **k):
        return {}

    def amount_to_precision(self, symbol, amount):
        return self.bg.amount_to_precision(symbol, amount)

    def price_to_precision(self, symbol, price):
        return self.bg.price_to_precision(symbol, price)

    async def create_order(self, symbol=None, type=None, side=None, amount=None,
                           price=None, params=None, **k):
        self.orders.append({"amount": float(amount)})
        return {"id": f"o{len(self.orders)}", "status": None, "average": self.order_average,
                "price": None, "filled": None, "cost": None, "info": {}}

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None, **k):
        return self.trades

    async def fetch_order(self, oid, symbol=None, params=None, **k):
        return {"id": oid, "status": "closed", "filled": 0.0,
                "average": self.fetched_average, "info": {}}

    async def close(self):
        return None


def _idea():
    return TradeIdea(id="TI-EST", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=TICKER, stop_loss=TICKER * 0.98,
                     take_profit=TICKER * 1.06, confidence=0.8, reasoning="fixture")


class _Slippage:
    def __init__(self):
        self.recorded = []

    def record(self, **kw):
        self.recorded.append(kw)


def _drive(venue, tmp_path):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._exchange = venue
    ex._slippage_tracker = _Slippage()
    RUNTIME.leverage_override = None
    audits: list[dict] = []
    warnings: list[str] = []
    ex._record_warning = lambda key: warnings.append(key)

    async def _sl_tp(*a, **k):
        return ("sl1", "tp1")

    async def _sync(*a, **k):
        return None

    guard = AsyncMock(return_value=(None, ""))
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})), \
         patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
         patch.object(type(CONFIG), "is_live", return_value=True), \
         patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
         patch.object(LiveExecutor, "sync_positions_from_exchange", _sync), \
         patch.object(LiveExecutor, "_post_fill_slippage_guard", guard):
        card = asyncio.run(ex.execute(_idea(), size_usd=200.0, order_type="market"))
    return ex, ex._positions["TI-EST"], card, audits, warnings, guard


@pytest.fixture(autouse=True)
def _no_override():
    RUNTIME.leverage_override = None
    yield
    RUNTIME.leverage_override = None


def _fill_line(card: str) -> str:
    return next(ln for ln in card.splitlines() if ln.startswith("- Fill:"))


# ── the estimate is marked, said, and not measured as slippage ─────────────

def test_nothing_stated_books_the_ticker_as_an_estimate_and_says_so(tmp_path):
    ex, pos, card, audits, warnings, guard = _drive(Venue(), tmp_path)
    assert pos.entry_price == TICKER
    assert getattr(pos, "entry_source", None) == ENTRY_ESTIMATED
    assert entry_is_estimated(pos)
    line = _fill_line(card)
    assert line.startswith("- Fill: <code>~$4,000.0000</code> ESTIMATED from the pre-order ticker")
    assert "the venue stated no fill price" in line
    est = [a for a in audits if a.get("action") == "fill_price_estimated"]
    assert len(est) == 1 and est[0]["result"] == "ESTIMATED"
    assert est[0]["data"]["ticker"] == TICKER
    assert "entry_price_estimated" in warnings
    # Nothing measured: the "fill" is the ticker the idea was priced from.
    assert ex._slippage_tracker.recorded == []
    assert guard.await_count == 0


@pytest.mark.parametrize("venue,source,entry", [
    (Venue(row_entry=4012.5), "venue_row", 4012.5),
    (Venue(order_average=4008.0), "order", 4008.0),
    (Venue(trades=[{"order": "o1", "amount": 0.05, "price": 4010.0}]), "fills", 4010.0),
    (Venue(fetched_average=4009.0), "fetched_order", 4009.0),
])
def test_a_stated_fill_records_its_source_and_is_not_an_estimate(tmp_path, venue, source, entry):
    ex, pos, card, audits, warnings, guard = _drive(venue, tmp_path)
    assert pos.entry_price == entry
    assert getattr(pos, "entry_source", None) == source
    assert not entry_is_estimated(pos)
    line = _fill_line(card)
    assert "ESTIMATED" not in line and "~" not in line
    assert f"${entry:,.4f}" in line
    assert not [a for a in audits if a.get("action") == "fill_price_estimated"]
    assert "entry_price_estimated" not in warnings
    assert guard.await_count == 1
    assert len(ex._slippage_tracker.recorded) == 1


def test_the_venue_row_is_the_entry_on_record_when_both_are_stated(tmp_path):
    # Both stated: the row read AFTER the fill is the position's own figure.
    ex, pos, card, *_ = _drive(Venue(order_average=4008.0, row_entry=4012.5), tmp_path)
    assert (pos.entry_price, pos.entry_source) == (4012.5, "venue_row")


# ── the marker survives a restart ──────────────────────────────────────────

def test_the_source_is_saved_and_restored(tmp_path):
    ex, pos, *_ = _drive(Venue(), tmp_path)
    ex._save_positions()
    again = LiveExecutor(state_dir=str(tmp_path))
    back = again._positions["TI-EST"]
    assert getattr(back, "entry_source", None) == ENTRY_ESTIMATED
    assert entry_is_estimated(back)


def test_a_record_written_before_the_marker_restores_without_one(tmp_path):
    ex, pos, *_ = _drive(Venue(), tmp_path)
    ex._save_positions()
    files = list(pathlib.Path(tmp_path).rglob("live_positions*.json"))
    assert files, "the executor saved no positions file"
    for f in files:
        data = json.loads(f.read_text())
        rows = data if isinstance(data, list) else data.get("positions", data)
        for row in (rows.values() if isinstance(rows, dict) else rows):
            row.pop("entry_source", None)
        f.write_text(json.dumps(data))
    again = LiveExecutor(state_dir=str(tmp_path))
    back = again._positions["TI-EST"]
    assert not hasattr(back, "entry_source")
    assert entry_estimated_state(back) is None


# ── the periodic sync corrects an estimate, and only an estimate ───────────

class _V3:
    rows: list[dict] = []

    def __init__(self, creds):
        self.has_credentials = True

    def get(self, path):
        return {"code": "00000", "data": {"list": list(_V3.rows)}}


@pytest.fixture
def v3(monkeypatch):
    import bot.core.bitget_v3_client as v3mod
    monkeypatch.setattr(v3mod.BitgetV3Client, "for_account",
                        classmethod(lambda cls, creds: _V3(creds)))
    return _V3


def _synced(tmp_path, monkeypatch, rows, *, source, entry=TICKER):
    _V3.rows = rows
    audits: list = []
    monkeypatch.setattr(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw}))
    ex = LiveExecutor(state_dir=str(tmp_path),
                      credentials={"api_key": "k", "api_secret": "s", "passphrase": "p"})
    ex._venue = get_venue("bitget")
    pos = LivePosition(trade_id="T1", symbol="ETH/USDT", direction="LONG",
                       entry_price=entry, quantity=0.05, cost_usd=entry * 0.05 / 5,
                       stop_loss=entry * 0.98, take_profit=entry * 1.06, leverage=5,
                       status="open")
    if source is not None:
        setattr(pos, "entry_source", source)
    ex._positions["T1"] = pos
    asyncio.run(ex.sync_positions_from_exchange())
    return pos, audits


def test_the_sync_corrects_an_estimate_from_the_venues_average(tmp_path, monkeypatch, v3):
    pos, audits = _synced(tmp_path, monkeypatch,
                          [{"symbol": "ETHUSDT", "leverage": "5", "total": "0.05",
                            "avgPrice": "4012.5"}], source=ENTRY_ESTIMATED)
    assert pos.entry_price == 4012.5
    assert pos.entry_source == "venue_sync"
    assert pos.cost_usd == pytest.approx(4012.5 * 0.05 / 5)
    assert not entry_is_estimated(pos)
    upd = [a for a in audits if a.get("action") == "entry_sync"]
    assert len(upd) == 1 and upd[0]["result"] == "UPDATED"
    assert upd[0]["data"] == {"trade_id": "T1", "old": TICKER, "new": 4012.5}


@pytest.mark.parametrize("source", ["order", "fills", "venue_row", "fetched_order", None])
def test_the_sync_leaves_a_stated_fill_alone(tmp_path, monkeypatch, v3, source):
    pos, audits = _synced(tmp_path, monkeypatch,
                          [{"symbol": "ETHUSDT", "leverage": "5", "total": "0.05",
                            "avgPrice": "4012.5"}], source=source)
    assert pos.entry_price == TICKER
    assert getattr(pos, "entry_source", None) == source
    assert not [a for a in audits if a.get("action") == "entry_sync"]


@pytest.mark.parametrize("avg", ["0", "", None, "junk", "-1"])
def test_a_row_stating_no_usable_average_corrects_nothing(tmp_path, monkeypatch, v3, avg):
    row = {"symbol": "ETHUSDT", "leverage": "5", "total": "0.05"}
    if avg is not None:
        row["avgPrice"] = avg
    pos, audits = _synced(tmp_path, monkeypatch, [row], source=ENTRY_ESTIMATED)
    assert pos.entry_price == TICKER and entry_is_estimated(pos)
    assert not [a for a in audits if a.get("action") == "entry_sync"]


def test_venue_avg_price_reads_three_spellings_and_refuses_the_rest():
    assert venue_avg_price({"avgPrice": "4012.5"}) == 4012.5
    assert venue_avg_price({"openPriceAvg": 4010.0}) == 4010.0
    assert venue_avg_price({"entryPrice": "4009"}) == 4009.0
    assert venue_avg_price({"avgPrice": "0", "openPriceAvg": "4010"}) == 4010.0
    for bad in ({}, {"avgPrice": None}, {"avgPrice": ""}, {"avgPrice": "n/a"},
                {"avgPrice": "-5"}, {"avgPrice": "nan"}, {"avgPrice": "inf"}, "row"):
        assert venue_avg_price(bad) is None, bad


# ── the readers say it ─────────────────────────────────────────────────────

def _open(source):
    p = LivePosition(trade_id="T1", symbol="ETH/USDT", direction="LONG",
                     entry_price=TICKER, quantity=0.05, cost_usd=40.0,
                     stop_loss=TICKER * 0.98, take_profit=TICKER * 1.06, leverage=5,
                     status="open")
    if source is not None:
        setattr(p, "entry_source", source)
    return p


def test_the_chat_prompt_row_tells_the_model_the_entry_is_an_estimate():
    from bot.skills.telegram_handler import _live_position_row
    row = _live_position_row(_open(ENTRY_ESTIMATED), 4050.0)
    assert "the entry is an ESTIMATE (the pre-order ticker; the venue stated no fill price)" in row
    assert "approximate" in row
    for src in ("order", "venue_row", None):
        assert "ESTIMATE" not in _live_position_row(_open(src), 4050.0)


def test_the_web_row_carries_a_three_valued_flag():
    from bot.web.user_gateway import _live_position_row as web_row
    assert web_row(_open(ENTRY_ESTIMATED))["entry_estimated"] is True
    assert web_row(_open("order"))["entry_estimated"] is False
    assert web_row(_open(None))["entry_estimated"] is None


def test_the_three_valued_state():
    assert entry_estimated_state(_open(ENTRY_ESTIMATED)) is True
    assert entry_estimated_state(_open("venue_sync")) is False
    assert entry_estimated_state(_open(None)) is None
    assert entry_estimated_state(_open("")) is None


# ── a close priced locally off an estimate says so ─────────────────────────

def test_the_close_suffix_is_only_for_a_local_pnl_off_an_estimate():
    est, stated = _open(ENTRY_ESTIMATED), _open("order")
    assert entry_close_suffix(est, entry_unread=False, local_pnl=True) == ENTRY_ESTIMATED_SUFFIX
    assert entry_close_suffix(est, entry_unread=False, local_pnl=False) == ""
    assert entry_close_suffix(stated, entry_unread=False, local_pnl=True) == ""
    assert entry_close_suffix(est, entry_unread=True, local_pnl=True) == ENTRY_UNREAD


def _own_close(fills, *, source):
    ex, x = _close_executor()
    x.fetch_my_trades = AsyncMock(return_value=list(fills))
    p = _pos100()
    if source is not None:
        setattr(p, "entry_source", source)
    ex._positions = {p.trade_id: p}
    ex._verify_position_closed = AsyncMock(return_value={
        "confirmed": True, "fill_price": 95.0, "fill_qty": 10.0, "fees": 0.0,
        "remaining_qty": 0.0, "failure_stage": ""})
    ex._fetch_bitget_close_data = AsyncMock(return_value=None)
    asyncio.run(ex.close_position(p.trade_id, reason="SL"))
    return p


def _fill(profit):
    return {"order": "CL-1", "price": 95.0, "side": "sell",
            "info": {"profit": profit, "feeDetail": {"totalFee": "-0.2"}}}


@pytest.mark.usefixtures("_no_sleep")
def test_a_close_priced_locally_off_an_estimate_carries_the_suffix():
    p = _own_close([_fill("0")], source=ENTRY_ESTIMATED)
    assert p.pnl_usd is not None
    assert p.fill_source.endswith(ENTRY_ESTIMATED_SUFFIX), p.fill_source


@pytest.mark.usefixtures("_no_sleep")
def test_a_close_the_venue_priced_carries_no_suffix():
    p = _own_close([_fill("-49")], source=ENTRY_ESTIMATED)
    assert p.gross_pnl == pytest.approx(-49.0)
    assert ENTRY_ESTIMATED_SUFFIX not in p.fill_source, p.fill_source


@pytest.mark.usefixtures("_no_sleep")
def test_a_close_off_a_stated_entry_carries_no_suffix():
    p = _own_close([_fill("0")], source="order")
    assert ENTRY_ESTIMATED_SUFFIX not in p.fill_source, p.fill_source


# ── the drift fallback's market order takes the same reading ───────────────

def _drift(order):
    ex = LiveExecutor.__new__(LiveExecutor)
    ex.user_id = None  # the fallback's cap reading asks whose account this is
    ex._standard_leverage = lambda symbol: 5
    ex._venue = get_venue("bitget")
    ex._positions = {}
    saved: list[int] = []
    ex._save_positions = lambda: saved.append(1)
    ex._record_warning = lambda key: None
    ex._place_sl_tp = AsyncMock(return_value=("SL1", "TP1"))
    ex._reattempt_post_fill_sl = AsyncMock(return_value=("SL1", "TP1", None))
    ex._guard_fill_leverage = AsyncMock(return_value=None)
    pos = LivePosition(trade_id="T", symbol="ETH/USDT", direction="LONG",
                       # 1 ETH: $28 of margin at 5x, inside the flat per-trade bound
                       # the fallback asks at the market price (10 ETH was $280,
                       # a size the bot's own caps would never have approved).
                       entry_price=140.0, quantity=1.0, cost_usd=28.0,
                       stop_loss=137.0, take_profit=150.0, leverage=5,
                       status="pending_fill", limit_order_id="OID1", order_type="limit")
    ex._positions["T"] = pos
    exchange = MagicMock()
    exchange.cancel_order = AsyncMock()
    exchange.fetch_order = AsyncMock(return_value={"status": "canceled", "filled": 0.0})
    exchange.create_order = AsyncMock(return_value=order)
    audits: list = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})):
        msg = asyncio.run(ex._execute_drift_market_fallback(exchange, "T", pos, 141.0))
    return pos, msg, audits


def test_the_drift_fallback_marks_an_unstated_fill_as_an_estimate():
    pos, msg, audits = _drift({"average": None, "price": None, "filled": 1.0})
    assert pos.entry_price == 141.0
    assert getattr(pos, "entry_source", None) == ENTRY_ESTIMATED
    assert "Market fill: ~$141.0000 (ESTIMATED from the pre-order ticker" in (msg or "")
    assert [a for a in audits if a.get("action") == "fill_price_estimated"]


def test_the_drift_fallback_records_a_stated_fill_as_the_orders():
    pos, msg, audits = _drift({"average": 141.3, "filled": 1.0})
    assert (pos.entry_price, pos.entry_source) == (141.3, "order")
    assert "Market fill: $141.3000" in (msg or "") and "ESTIMATED" not in (msg or "")
    assert not [a for a in audits if a.get("action") == "fill_price_estimated"]
