"""The pre-trade gates, DRIVEN — each one used to be unreachable by a test.

``LiveExecutor.execute`` was a single 1,864-line method. Nine of its phases —
market-hours/weekend rules, the two advisory funding checks, the stale-ticker
and spread gate, the order-book wall, SAFEGUARD 1 plus sizing, the pre-trade
slippage estimate, the exchange-minimum round-up and the notional ceiling —
are methods now, extracted verbatim. Twenty-one source scans across sixteen
files pinned their TEXT; not one could plant a stale ticker and read what the
executor said about it.

CLAUDE.md: when there is no seam, make one, and the fixing is most of the work
— here the fixing was the seam itself. Each test below plants a state, calls
the gate, and asserts the verdict AND the audit, which is the pair an operator
actually sees. The pure decisions (bot/core/entry_quality.py,
bot/core/order_rules.py, resolve_exchange_min_quantity) keep their own tests;
what this file pins is the executor's half — the refetch, the audit, the
refusal, and which locals hand back to the order path.

The end-to-end harness in tests/test_pretrade_slippage_gate.py drives the
REAL execute() through all nine, so the extraction is also checked as a
whole, not only gate by gate.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor
from bot.utils.models import Direction

SYM = "BTC/USDT:USDT"


def _idea(direction=Direction.LONG, entry=100.0, sl=95.0, tp=110.0, asset="BTC/USDT",
          **extra):
    """The attributes the gates read. A namespace rather than a TradeIdea so the
    test states exactly what each gate consumes and nothing else."""
    ns = SimpleNamespace(id="t1", asset=asset, direction=direction, entry_price=entry,
                         stop_loss=sl, take_profit=tp)
    for k, v in extra.items():
        setattr(ns, k, v)
    return ns


class FakeExchange:
    """Answers exactly as scripted; every method records that it was called."""

    def __init__(self, tickers=None, book=None, funding=None, precision=None):
        self.tickers = list(tickers or [])
        self.book = book
        self.funding = funding
        self.precision = precision          # value, exception class, or "raise"
        self.calls: list[str] = []

    async def fetch_ticker(self, symbol):
        self.calls.append("fetch_ticker")
        if not self.tickers:
            raise RuntimeError("no scripted ticker")
        t = self.tickers.pop(0)
        if isinstance(t, Exception):
            raise t
        return t

    async def fetch_order_book(self, symbol, limit=25):
        self.calls.append("fetch_order_book")
        if isinstance(self.book, Exception):
            raise self.book
        return self.book

    async def fetch_funding_rate(self, symbol):
        self.calls.append("fetch_funding_rate")
        if isinstance(self.funding, Exception):
            raise self.funding
        return self.funding

    def amount_to_precision(self, symbol, amount):
        self.calls.append("amount_to_precision")
        if isinstance(self.precision, Exception):
            raise self.precision
        if self.precision is None:
            return None
        return str(amount)


@pytest.fixture
def ex(tmp_path):
    return LiveExecutor(state_dir=str(tmp_path))


@pytest.fixture
def audits(monkeypatch):
    seen: list[dict] = []

    def _spy(log, message, **kw):
        seen.append({"message": message, **kw})

    monkeypatch.setattr(le, "audit", _spy)
    return seen


def _by_action(audits, action):
    return [a for a in audits if a.get("action") == action]


NOW_MS = lambda: int(time.time() * 1000)  # noqa: E731 — ccxt tickers carry ms


# ── 1. order rules ───────────────────────────────────────────────────────


def test_a_closed_market_forces_a_market_order_to_limit(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "_classify_symbol", lambda a: "Metal")
    monkeypatch.setattr(le, "is_market_open", lambda ac: (False, "COMEX closed"))
    monkeypatch.setattr(le, "is_weekend_queued", lambda ac: False)
    monkeypatch.setattr(le, "should_defer_tp_sl", lambda ac, wk, ot: False)
    order_type, size, ac, defer = ex._apply_order_rules(_idea(asset="XAU/USDT"), 100.0, "market")
    assert order_type == "limit"
    assert size == 100.0 and ac == "Metal" and defer is False
    assert _by_action(audits, "market_hours")[0]["result"] == "QUEUED"
    assert _by_action(audits, "order_type_override")[0]["result"] == "LIMIT"


def test_weekend_adjustments_change_size_and_stop_and_say_so(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "_classify_symbol", lambda a: "Metal")
    monkeypatch.setattr(le, "is_market_open", lambda ac: (True, ""))
    monkeypatch.setattr(le, "is_weekend_queued", lambda ac: True)
    monkeypatch.setattr(le, "adjust_size_for_weekend", lambda s, ac, wk: s * 0.7)
    monkeypatch.setattr(le, "adjust_sl_for_gap_risk", lambda sl, e, d, ac, wk: sl - 1.0)
    monkeypatch.setattr(le, "should_defer_tp_sl", lambda ac, wk, ot: True)
    idea = _idea(asset="XAU/USDT", sl=95.0)
    order_type, size, ac, defer = ex._apply_order_rules(idea, 100.0, "limit")
    assert size == pytest.approx(70.0)
    assert idea.stop_loss == 94.0, "the widened stop must land on the idea the order is built from"
    assert defer is True
    assert _by_action(audits, "weekend_size_adjust")[0]["result"] == "REDUCED"
    assert _by_action(audits, "weekend_sl_widen")[0]["result"] == "WIDENED"


def test_an_ordinary_crypto_entry_is_untouched(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "_classify_symbol", lambda a: "Crypto")
    monkeypatch.setattr(le, "is_market_open", lambda ac: (True, ""))
    monkeypatch.setattr(le, "is_weekend_queued", lambda ac: False)
    monkeypatch.setattr(le, "should_defer_tp_sl", lambda ac, wk, ot: False)
    idea = _idea()
    assert ex._apply_order_rules(idea, 100.0, "market") == ("market", 100.0, "Crypto", False)
    assert idea.stop_loss == 95.0
    assert audits == []


# ── 2. funding rate: advisory, never blocking ────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_funding_fetch_is_audited_not_swallowed_and_never_raises(ex, audits):
    ex._exchange = FakeExchange(funding=RuntimeError("venue funding endpoint down"))
    assert await ex._note_funding_rate(_idea()) is None
    assert _by_action(audits, "funding_check")[0]["result"] == "FETCH_FAILED"


@pytest.mark.asyncio
async def test_an_unreadable_rate_is_not_a_neutral_market(ex, audits):
    # `float(x or 0)` used to turn this into "0%: market closed". It is not.
    ex._exchange = FakeExchange(funding={"fundingRate": None})
    await ex._note_funding_rate(_idea())
    assert _by_action(audits, "funding_check")[0]["result"] == "UNREADABLE"


@pytest.mark.asyncio
async def test_funding_against_the_direction_warns(ex, audits):
    # Positive funding = longs pay; 0.2% is past the 0.1% warning line.
    ex._exchange = FakeExchange(funding={"fundingRate": 0.002})
    await ex._note_funding_rate(_idea(Direction.LONG))
    assert _by_action(audits, "funding_check")[0]["result"] == "WARN"


@pytest.mark.asyncio
async def test_funding_in_favour_is_silent(ex, audits):
    ex._exchange = FakeExchange(funding={"fundingRate": -0.002})
    await ex._note_funding_rate(_idea(Direction.LONG))
    assert _by_action(audits, "funding_check") == []


# ── 3. settlement clock: advisory, through the shared clock ─────────────


def test_an_entry_minutes_before_settlement_warns(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "seconds_to_settlement", lambda ts: 180.0)
    assert ex._note_settlement_clock(_idea()) is None
    w = _by_action(audits, "funding_clock")
    assert w and w[0]["result"] == "WARN"
    assert w[0]["data"]["mins_until_settlement"] == 3.0


def test_an_entry_hours_before_settlement_is_silent(ex, audits, monkeypatch):
    monkeypatch.setattr(le, "seconds_to_settlement", lambda ts: 3600.0)
    ex._note_settlement_clock(_idea())
    assert _by_action(audits, "funding_clock") == []


def test_an_unreadable_clock_is_recorded_as_unreadable(ex, audits, monkeypatch):
    def _boom(ts):
        raise RuntimeError("clock unavailable")
    monkeypatch.setattr(le, "seconds_to_settlement", _boom)
    ex._note_settlement_clock(_idea())   # must not raise
    assert _by_action(audits, "funding_clock")[0]["result"] == "UNREADABLE"


# ── 4. stale ticker / wide spread ────────────────────────────────────────

FRESH = lambda last=100.0, bid=99.99, ask=100.01: {  # noqa: E731
    "timestamp": NOW_MS(), "last": last, "bid": bid, "ask": ask}
STALE = {"timestamp": 1_000_000, "last": 100.0, "bid": 99.99, "ask": 100.01}


@pytest.mark.asyncio
async def test_a_stale_ticker_that_stays_stale_after_one_refetch_blocks(ex, audits):
    fx = FakeExchange(tickers=[STALE])   # the refetch also comes back stale
    blk, ticker, price = await ex._entry_market_gate(fx, SYM, STALE, 100.0)
    assert blk and "nothing was placed" in blk
    assert fx.calls.count("fetch_ticker") == 1, "exactly one refetch, as before"
    assert _by_action(audits, "live_execute")[0]["result"] == "BLOCKED_STALE_TICKER"


@pytest.mark.asyncio
async def test_a_refetch_that_comes_back_fresh_proceeds_with_the_new_price(ex, audits):
    fx = FakeExchange(tickers=[FRESH(last=101.0)])
    blk, ticker, price = await ex._entry_market_gate(fx, SYM, STALE, 100.0)
    assert blk is None
    assert price == 101.0, "the refetched last must replace the stale price"
    assert ticker["last"] == 101.0
    assert _by_action(audits, "live_execute") == []


@pytest.mark.asyncio
async def test_a_fresh_tight_market_proceeds_untouched(ex, audits):
    fx = FakeExchange()
    t = FRESH()
    blk, ticker, price = await ex._entry_market_gate(fx, SYM, t, 100.0)
    assert (blk, ticker, price) == (None, t, 100.0)
    assert fx.calls == [], "a fresh ticker must not be refetched"


@pytest.mark.asyncio
async def test_a_wide_book_blocks(ex, audits):
    # 3% wide against a 1.0% ceiling (2 x OF_MAX_SPREAD_BPS default 50).
    blk, _, _ = await ex._entry_market_gate(FakeExchange(), SYM, FRESH(bid=100.0, ask=103.0), 100.0)
    assert blk and "nothing was placed" in blk
    assert _by_action(audits, "live_execute")[0]["result"] == "BLOCKED_WIDE_SPREAD"


@pytest.mark.asyncio
async def test_an_unreadable_timestamp_warns_by_default_and_blocks_in_block_mode(ex, audits, monkeypatch):
    unreadable = {"timestamp": None, "last": 100.0, "bid": 99.99, "ask": 100.01}
    monkeypatch.delenv("ENTRY_UNREADABLE_MARKET_GATE", raising=False)
    blk, _, _ = await ex._entry_market_gate(FakeExchange(), SYM, unreadable, 100.0)
    assert blk is None, "observe-first: an unreadable reading must not start refusing trades"
    assert _by_action(audits, "entry_market_unreadable")[0]["result"] == "WARN"
    audits.clear()
    monkeypatch.setenv("ENTRY_UNREADABLE_MARKET_GATE", "block")
    blk, _, _ = await ex._entry_market_gate(FakeExchange(), SYM, unreadable, 100.0)
    assert blk and "could not be read" in blk
    assert _by_action(audits, "entry_market_unreadable")[0]["result"] == "BLOCKED"


# ── 5. order-book wall ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_both_gates_off_means_no_fetch_at_all(ex, audits, monkeypatch):
    monkeypatch.setenv("ENTRY_BOOK_WALL_GATE", "off")
    monkeypatch.setenv("ENTRY_SLIPPAGE_GATE", "off")
    fx = FakeExchange(book={"bids": [], "asks": []})
    blk, book, slip_mode = await ex._book_wall_gate(fx, SYM, _idea(), 100.0)
    assert (blk, book, slip_mode) == (None, None, "off")
    assert fx.calls == [], "no gate on, no call spent on the live entry path"


@pytest.mark.asyncio
async def test_one_fetch_serves_both_gates(ex, audits, monkeypatch):
    # The wall gate is off but the slippage gate wants the book: fetched once
    # here and handed back, so the slippage gate need not fetch its own.
    monkeypatch.setenv("ENTRY_BOOK_WALL_GATE", "off")
    monkeypatch.setenv("ENTRY_SLIPPAGE_GATE", "warn")
    book = {"bids": [[99.0, 1.0]], "asks": [[101.0, 1.0]]}
    fx = FakeExchange(book=book)
    blk, got, slip_mode = await ex._book_wall_gate(fx, SYM, _idea(), 100.0)
    assert (blk, got, slip_mode) == (None, book, "warn")
    assert fx.calls == ["fetch_order_book"]


@pytest.mark.asyncio
async def test_a_flagged_wall_blocks_only_in_block_mode(ex, audits, monkeypatch):
    fx = FakeExchange(book={"bids": [[99.0, 1.0]], "asks": [[101.0, 1.0]]})
    flagged = {"flag": True, "reason": "planted wall", "metrics": {"ratio": 9.0}}
    with patch("bot.core.entry_quality.book_wall_verdict", return_value=flagged):
        monkeypatch.setenv("ENTRY_BOOK_WALL_GATE", "warn")
        blk, _, _ = await ex._book_wall_gate(fx, SYM, _idea(), 100.0)
        assert blk is None
        assert _by_action(audits, "entry_book_wall")[0]["result"] == "WARN"
        audits.clear()
        monkeypatch.setenv("ENTRY_BOOK_WALL_GATE", "block")
        blk, _, _ = await ex._book_wall_gate(fx, SYM, _idea(), 100.0)
        assert blk and "planted wall" in blk and "nothing was placed" in blk
        assert _by_action(audits, "entry_book_wall")[0]["result"] == "BLOCKED"


@pytest.mark.asyncio
async def test_a_book_that_cannot_be_read_never_blocks(ex, audits, monkeypatch):
    monkeypatch.setenv("ENTRY_BOOK_WALL_GATE", "block")
    fx = FakeExchange(book=RuntimeError("book endpoint down"))
    blk, book, _ = await ex._book_wall_gate(fx, SYM, _idea(), 100.0)
    assert blk is None and book is None, "fail-open: a book read must never take a trade down"


# ── 6. SAFEGUARD 1 + sizing ──────────────────────────────────────────────


def test_a_long_already_at_its_stop_is_refused(ex, audits):
    blk, lev, qty = ex._size_or_block(_idea(Direction.LONG, sl=100.0), SYM, 99.0, 100.0)
    assert blk and "instantly stopped out" in blk
    assert _by_action(audits, "live_execute")[0]["result"] == "BLOCKED_PRICE_PAST_SL"


def test_a_short_already_at_its_stop_is_refused(ex, audits):
    blk, _, _ = ex._size_or_block(_idea(Direction.SHORT, sl=100.0), SYM, 101.0, 100.0)
    assert blk and "at/above SL" in blk


def test_sizing_honours_the_risk_engines_reduce_only_clamp(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_compute_target_leverage", lambda s: 10)
    blk, lev, qty = ex._size_or_block(_idea(_adjusted_leverage=3), SYM, 100.0, 100.0)
    assert blk is None
    assert lev == 3, "the engine's cap must LOWER the sized leverage"
    assert qty == pytest.approx(100.0 * 3 / 100.0)


def test_the_clamp_never_raises_leverage(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_compute_target_leverage", lambda s: 5)
    _, lev, qty = ex._size_or_block(_idea(_adjusted_leverage=20), SYM, 100.0, 100.0)
    assert lev == 5 and qty == pytest.approx(5.0)


def test_a_junk_clamp_value_is_ignored(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_compute_target_leverage", lambda s: 5)
    _, lev, _ = ex._size_or_block(_idea(_adjusted_leverage="x"), SYM, 100.0, 100.0)
    assert lev == 5


# ── 7. pre-trade slippage (the end-to-end harness covers the rest) ──────


def test_slippage_gate_off_is_silent(ex, audits):
    assert ex._pre_trade_slippage_gate(SYM, _idea(), 1.0, 100.0, None, "off") is None
    assert audits == []


# ── 8. exchange minimum + precision ─────────────────────────────────────

MARKET = {"limits": {"amount": {"min": 0.01}, "cost": {"min": 0}},
          "precision": {"amount": 0.001}}


def _roundup(monkeypatch, enabled=True, max_mult=1.5):
    monkeypatch.setattr(le, "CONFIG", SimpleNamespace(exchange=SimpleNamespace(
        exchange_min_roundup_enabled=enabled, exchange_min_roundup_max_mult=max_mult)))


def test_a_small_overshoot_is_rounded_up_to_the_venue_minimum(ex, audits, monkeypatch):
    _roundup(monkeypatch)
    fx = FakeExchange(precision="ok")
    blk, qty = ex._exchange_minimum_gate(fx, MARKET, SYM, 0.008, 100.0, 5, 100.0)
    assert blk is None
    assert qty >= 0.01, f"quantity was not raised to the minimum: {qty}"
    assert _by_action(audits, "live_execute")[0]["result"] == "ROUNDED_TO_MIN"


def test_a_large_overshoot_is_refused_with_the_reason(ex, audits, monkeypatch):
    _roundup(monkeypatch)
    blk, qty = ex._exchange_minimum_gate(FakeExchange(precision="ok"), MARKET, SYM, 0.004, 100.0, 5, 100.0)
    assert blk and "too small for the exchange" in blk and "exceeds 1.5x cap" in blk
    assert _by_action(audits, "live_execute")[0]["result"] == "BELOW_EXCHANGE_MIN"


def test_round_up_disabled_is_refused_and_says_why(ex, audits, monkeypatch):
    _roundup(monkeypatch, enabled=False)
    blk, _ = ex._exchange_minimum_gate(FakeExchange(precision="ok"), MARKET, SYM, 0.008, 100.0, 5, 100.0)
    assert blk and "round-up disabled" in blk


def test_a_precision_rejection_is_a_clean_skip_not_a_raw_venue_error(ex, audits, monkeypatch):
    _roundup(monkeypatch)
    fx = FakeExchange(precision=ValueError("amount must be greater than minimum amount precision of 0.001"))
    blk, _ = ex._exchange_minimum_gate(fx, MARKET, SYM, 0.05, 100.0, 5, 100.0)
    assert blk and "precision rules" in blk
    assert _by_action(audits, "live_execute")[0]["result"] == "BELOW_EXCHANGE_MIN"


def test_no_precision_data_is_a_failure_not_a_zero_quantity(ex, audits, monkeypatch):
    _roundup(monkeypatch)
    blk, _ = ex._exchange_minimum_gate(FakeExchange(precision=None), MARKET, SYM, 0.05, 100.0, 5, 100.0)
    assert blk and "returned no precision data" in blk


def test_a_zero_quantity_is_refused_even_without_a_market(ex, audits, monkeypatch):
    _roundup(monkeypatch)
    blk, _ = ex._exchange_minimum_gate(FakeExchange(), None, SYM, 0.0, 100.0, 5, 100.0)
    assert blk and "too small after precision" in blk
    assert _by_action(audits, "live_execute")[0]["result"] == "QUANTITY_TOO_SMALL"


def test_a_normal_quantity_passes_through_at_venue_precision(ex, audits, monkeypatch):
    _roundup(monkeypatch)
    blk, qty = ex._exchange_minimum_gate(FakeExchange(precision="ok"), MARKET, SYM, 0.05, 100.0, 5, 100.0)
    assert (blk, qty) == (None, 0.05)
    assert audits == []


# ── 9. notional ceiling + venue limits ──────────────────────────────────


def test_a_notional_inside_the_envelope_is_audited_and_allowed(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_validate_order_limits", lambda m, q, n: None)
    blk = ex._notional_boundary_gate(SYM, 5.0, 100.0, 100.0, 5, MARKET)
    assert blk is None
    assert _by_action(audits, "notional_boundary")[0]["result"] == "OK"


def test_a_notional_past_the_design_ceiling_is_hard_blocked(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_validate_order_limits", lambda m, q, n: None)
    blk = ex._notional_boundary_gate(SYM, 1_000_000.0, 100.0, 100.0, 5, MARKET)
    assert blk and "exceeds the design" in blk
    assert [a["result"] for a in _by_action(audits, "notional_boundary")] == ["OK", "EXCEEDS_CEILING"]


def test_a_venue_limit_rejection_is_blocked_cleanly(ex, audits, monkeypatch):
    monkeypatch.setattr(ex, "_validate_order_limits", lambda m, q, n: "min notional $5 not met")
    blk = ex._notional_boundary_gate(SYM, 5.0, 100.0, 100.0, 5, MARKET)
    assert blk == "BLOCKED: min notional $5 not met"
    assert _by_action(audits, "live_execute")[0]["result"] == "BELOW_EXCHANGE_MIN"


# ── the wiring: execute() consults every gate, in order ─────────────────


def test_execute_calls_every_gate_in_the_order_the_money_path_requires():
    import inspect

    src = inspect.getsource(LiveExecutor.execute)
    order = [
        "self._apply_order_rules(",
        "await self._note_funding_rate(",
        "self._note_settlement_clock(",
        "_preflight_check(",
        "await self._entry_market_gate(",
        "await self._book_wall_gate(",
        "self._size_or_block(",
        "self._pre_trade_slippage_gate(",
        "self._exchange_minimum_gate(",
        "self._notional_boundary_gate(",
        "_create_order_idempotent(",
    ]
    positions = [src.find(needle) for needle in order]
    missing = [n for n, p in zip(order, positions) if p < 0]
    assert not missing, f"execute() no longer consults: {missing}"
    assert positions == sorted(positions), (
        "the gates run out of order — every one of them must precede the order "
        f"submission, and the market gate must precede sizing: {list(zip(order, positions))}")
