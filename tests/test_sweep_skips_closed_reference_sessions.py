"""The sweep held slots for stock perps whose market was closed.

2026-09-03 ~00:00 UTC, on the operator's screen:

    Analyses are hanging -- the last background sweep gave up on 4 of 85
    symbols after 90s each (OPEN, NFLX, NOKSTOCK, BBSTOCK ...).
    163 analysis timeout(s) across 43 symbol(s) in 36 batch(es).
    Stages: mtf x96, analyze x48. Most seen: BBSTOCK x12, NFLX x12, HOOD x11

and half an hour later, on /status: "Tick phase timed out: analyze (exceeded
its 300s, x33)". Thirty-three consecutive cancelled ticks is what trips the
warning-rate breaker that had been suppressing entries all evening.

LLM_BACKGROUND_SCANS=off was set, so the brain was not in the path, and the
tally said so: the stall was the multi-timeframe CANDLE fetch, and every
symbol it named was a US-stock perp, at midnight UTC, with Wall Street shut.
A stock perp trades around the clock; its candles only flow while its
reference market does. Overnight, each one stalled to the 90s cap and the
sweep burned minutes on symbols that could not produce a thesis. Worse than
merely including them: `_allocate_slots` RESERVES TradFi slots ahead of
crypto, so the closed-market symbols were guaranteed a place.

The clock already existed. M-21 built `order_rules.is_reference_session_open`
for exactly this question, and `black_swan` was its only caller -- the scanner
never asked. It asks now, through `reference_session_state`, which had to
grow a fourth answer on the way: the bool folded "the timezone database is
unreadable" into False, correct for a filter that wants to attenuate and
wrong for a sweep that would otherwise drop stock perps forever and log
"session closed" as the reason. Only "closed" drops. The drop is recorded so
/status can say the universe shrank and why.
"""
from __future__ import annotations

import builtins
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import bot.core.market_scanner as ms
import bot.core.order_rules as order_rules
from bot.core.market_scanner import MarketScanner
from bot.core.order_rules import is_reference_session_open, reference_session_state
from bot.utils.models import MarketSignal

# A Wednesday. 00:34 UTC is 20:34 in New York under EDT: the operator's card.
MIDNIGHT_UTC = datetime(2026, 9, 3, 0, 34, tzinfo=timezone.utc)
NOON_NY = datetime(2026, 9, 3, 16, 0, tzinfo=timezone.utc)      # 12:00 EDT
SUNDAY = datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)


# ── the clock ────────────────────────────────────────────────────────────────

def test_the_operators_card_time_is_a_closed_session():
    assert reference_session_state("Stock", MIDNIGHT_UTC) == "closed"
    assert reference_session_state("ETF", MIDNIGHT_UTC) == "closed"


def test_the_session_itself_is_open():
    assert reference_session_state("Stock", NOON_NY) == "open"
    assert is_reference_session_open("Stock", NOON_NY)


def test_a_weekend_is_closed_not_unknown():
    assert reference_session_state("Stock", SUNDAY) == "closed"


def test_crypto_has_no_session_to_be_in():
    assert reference_session_state("Crypto", NOON_NY) == "none"
    assert reference_session_state("Crypto", MIDNIGHT_UTC) == "none"


def test_an_unreadable_clock_is_unknown_and_the_bool_still_attenuates(monkeypatch):
    """The two callers want opposite defaults; only a fourth word serves both."""
    real_import = builtins.__import__

    def _no_zoneinfo(name, *a, **kw):
        if name == "zoneinfo":
            raise ModuleNotFoundError("No module named 'zoneinfo'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_zoneinfo)
    assert reference_session_state("Stock", NOON_NY) == "unknown", \
        "a missing tzdata must not be reported as a closed market"
    assert not is_reference_session_open("Stock", NOON_NY), \
        "the spread filter's default (attenuate) must survive the split"


# ── the sweep ────────────────────────────────────────────────────────────────

def _sig(symbol: str, cat: str) -> MarketSignal:
    return MarketSignal(symbol=symbol, price=100.0, volume_usd_24h=1_000_000.0,
                        asset_category=cat)


def _scanner():
    """Bare allocator -- the method must not need a live scanner."""
    s = SimpleNamespace()
    s._allocate_slots = MarketScanner._allocate_slots.__get__(s)
    return s


_CFG = SimpleNamespace(top_movers_count=50, scan_tradfi_full_coverage=True,
                       scan_min_per_category=1)


def _allocate(signals, clock):
    with patch.object(ms, "CONFIG", _CFG), \
         patch.object(order_rules, "reference_session_state", clock):
        sc = _scanner()
        out = sc._allocate_slots(signals)
        return out, getattr(sc, "_session_dropped", None)


def _clock(answer: str):
    """A clock that says `answer` for session-gated classes and none for the rest."""
    return lambda cat, now=None: answer if cat in ("Stock", "ETF") else "none"


STOCKS = [_sig("NFLX/USDT:USDT", "Stock"), _sig("HOOD/USDT:USDT", "Stock"),
          _sig("BBSTOCK/USDT:USDT", "Stock")]
CRYPTO = [_sig("BTC/USDT:USDT", "Crypto"), _sig("ETH/USDT:USDT", "Crypto")]


def test_closed_session_drops_the_stock_perps():
    out, dropped = _allocate(STOCKS + CRYPTO, _clock("closed"))
    assert all(s.asset_category != "Stock" for s in out), \
        "a stock perp reached the sweep with Wall Street closed"
    assert dropped == {"Stock": 3}, "the drop must be counted, not silent"


def test_open_session_keeps_them():
    out, dropped = _allocate(STOCKS + CRYPTO, _clock("open"))
    assert sum(1 for s in out if s.asset_category == "Stock") == 3
    assert dropped == {}, "an open market must drop nothing"


def test_crypto_is_never_session_gated():
    """The clock says 'closed' for the gated classes; crypto has none."""
    out, dropped = _allocate(CRYPTO, _clock("closed"))
    assert [s.symbol for s in out] == [s.symbol for s in CRYPTO]
    assert dropped == {}


def test_an_unknown_clock_keeps_the_whole_universe():
    """THE trap: 'unknown' folded into 'closed' drops stock perps forever on a
    container without tzdata, and logs the wrong reason while doing it."""
    out, dropped = _allocate(STOCKS + CRYPTO, _clock("unknown"))
    assert len(out) == 5, "a class that cannot be clocked must still be scanned"
    assert dropped == {}, "nothing was dropped, so nothing may be reported as dropped"


def test_a_raising_clock_costs_the_optimisation_not_the_sweep():
    def boom(cat, now=None):
        raise RuntimeError("clock exploded")
    out, dropped = _allocate(STOCKS + CRYPTO, boom)
    assert len(out) == 5, "a broken clock must fall back to the full universe"
    assert dropped == {}


def test_the_drop_is_recorded_on_the_scanner_for_status():
    """A surface that prints '4 of 60' must be able to add 'stocks skipped: 3'."""
    _out, dropped = _allocate(STOCKS + CRYPTO, _clock("closed"))
    assert isinstance(dropped, dict)
    assert dropped.get("Stock") == 3


def test_the_clock_is_asked_with_the_category_name_and_a_time():
    seen = []

    def spy(cat, now=None):
        seen.append((cat, now))
        return "none"
    _allocate(STOCKS + CRYPTO, spy)
    cats = [c for c, _ in seen]
    assert "Stock" in cats, "the session check must be asked about the Stock class"
    assert all(isinstance(t, datetime) and t.tzinfo is not None for _, t in seen), \
        "the sweep must pass an aware 'now', not let the clock guess"


def test_the_real_clock_drops_stocks_at_the_operators_card_time():
    """End to end through the real function: only `now` is planted."""
    with patch.object(ms, "CONFIG", _CFG), \
         patch.object(ms, "datetime") as fake_dt:
        fake_dt.now.return_value = MIDNIGHT_UTC
        sc = _scanner()
        out = sc._allocate_slots(STOCKS + CRYPTO)
    assert all(s.asset_category == "Crypto" for s in out)
    assert sc._session_dropped == {"Stock": 3}
