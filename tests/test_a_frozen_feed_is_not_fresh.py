"""A frozen feed is not a fresh one.

Every freshness stamp on the signal path used to be the wall clock at the
moment of analysis: the analyzer wrote ``TradeIdea.timestamp=datetime.now``,
the scanner wrote ``MarketSignal.timestamp=datetime.now``, and the risk gate's
STALE_DATA check measured the first of those. A venue that stopped printing
bars an hour ago therefore produced ideas that were "0 s old" at the gate. The
only time that belongs to the MARKET is the newest row's own open.

These tests plant every clock (`as_of`, `now_ms`) rather than reading the wall
clock, except the two that exist to prove the live path reads it.
"""
from __future__ import annotations

import ast
import asyncio
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot.compat import UTC
from bot.core import bar_freshness
from bot.core.analyzer import Analyzer
from bot.core.engine import _mtf_ttl
from bot.core.market_scanner import MarketScanner
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.candles import SUPPORTED_TIMEFRAMES, bar_freshness_verdict, newest_closed_bar_age, timeframe_to_ms
from bot.utils.models import ANALYZER_SOURCE, Direction, MarketSignal, TradeIdea
from tests.candle_clock import anchored

ROOT = Path(__file__).resolve().parents[1]
H = 3_600_000
#: A planted "now": 2026-06-01 12:00:00 UTC, on an hour boundary.
NOW_MS = int(datetime(2026, 6, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)


def _run(coro):
    return asyncio.run(coro)


def _rows(n: int = 150, *, newest_close_ms: int = NOW_MS, tf_ms: int = H) -> list:
    """A deterministic series whose newest bar closed at ``newest_close_ms``."""
    t0 = newest_close_ms - n * tf_ms
    out, p = [], 100.0
    for i in range(n):
        o = p
        p = p + 0.15 + 0.4 * math.sin(i / 3.0)
        out.append([t0 + i * tf_ms, o, max(o, p) + 0.3, min(o, p) - 0.3, p, 1000.0 + 10 * i])
    return out


# ── the reading ──────────────────────────────────────────────────────────────

class TestTheAge:
    def test_a_bar_that_closed_at_now_is_zero_seconds_old(self):
        assert newest_closed_bar_age(_rows(), "1h", NOW_MS) == 0.0

    def test_a_bar_that_closed_three_hours_ago_is_three_hours_old(self):
        assert newest_closed_bar_age(_rows(newest_close_ms=NOW_MS - 3 * H), "1h", NOW_MS) == 10_800.0

    def test_a_forming_bar_reads_negative_not_zero(self):
        # The newest row opened at now: its period ends an hour from now.
        rows = _rows(newest_close_ms=NOW_MS + H)
        assert newest_closed_bar_age(rows, "1h", NOW_MS) == -3600.0

    @pytest.mark.parametrize("rows,tf,now", [
        ([], "1h", NOW_MS),                                   # no rows
        ([[float("nan"), 1, 1, 1, 1, 1]], "1h", NOW_MS),      # unreadable open
        ([[True, 1, 1, 1, 1, 1]], "1h", NOW_MS),              # a bool is not a time
        ([["x", 1, 1, 1, 1, 1]], "1h", NOW_MS),               # nor a string
        (_rows(3), "zz", NOW_MS),                             # no period
        (_rows(3), "1h", float("inf")),                       # no clock
    ])
    def test_unreadable_is_none_never_zero(self, rows, tf, now):
        assert newest_closed_bar_age(rows, tf, now) is None

    def test_a_month_is_read_on_the_calendar(self):
        # A 1M bar opened 2026-01-01 closes 2026-02-01, 31 days later.
        open_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        close_ms = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert newest_closed_bar_age([[open_ms, 1, 1, 1, 1, 1]], "1M", close_ms + H) == 3600.0


class TestTheVerdict:
    def test_within_k_periods_is_fresh(self):
        ok, line = bar_freshness_verdict(7200.0, "1h", 2.0)
        assert ok and "7200s old" in line

    def test_beyond_k_periods_is_refused_and_says_by_how_much(self):
        ok, line = bar_freshness_verdict(7201.0, "1h", 2.0)
        assert not ok
        assert "7201s old > 2 periods (7200s)" in line

    def test_unread_is_refused(self):
        assert bar_freshness_verdict(None, "1h", 2.0) == (False, "newest bar age unread")

    def test_a_forming_bar_is_fresh_and_says_it_is_forming(self):
        ok, line = bar_freshness_verdict(-3600.0, "1h", 2.0)
        assert ok and "forming" in line

    def test_a_bar_from_the_future_is_refused(self):
        # More than one period plus the 30 s skew ahead is a clock fault.
        assert bar_freshness_verdict(-3631.0, "1h", 2.0)[0] is False
        assert bar_freshness_verdict(-3629.0, "1h", 2.0)[0] is True

    def test_a_timeframe_with_no_fixed_period_is_refused(self):
        assert bar_freshness_verdict(10.0, "1M", 2.0)[0] is False


# ── the threshold, and why its floor is where it is ──────────────────────────

class TestTheThreshold:
    def test_default_is_two_periods(self, monkeypatch):
        monkeypatch.delenv("BAR_FRESHNESS_MAX_PERIODS", raising=False)
        assert bar_freshness.bar_freshness_max_periods() == 2.0

    def test_an_override_is_clamped_to_the_floor(self, monkeypatch):
        monkeypatch.setenv("BAR_FRESHNESS_MAX_PERIODS", "1")
        assert bar_freshness.bar_freshness_max_periods() == 1.75

    def test_an_unparseable_override_is_the_default(self, monkeypatch):
        monkeypatch.setenv("BAR_FRESHNESS_MAX_PERIODS", "two")
        assert bar_freshness.bar_freshness_max_periods() == 2.0

    @pytest.mark.parametrize("tf", SUPPORTED_TIMEFRAMES)
    def test_a_healthy_cache_hit_is_fresh_at_the_floor(self, tf):
        """The engine's own cache must never read as a frozen feed.

        `_cached_ohlcv` drops the forming bar before it stores and serves the
        series for `_mtf_ttl(tf)`: the newest closed bar can have ended one
        period before the fetch plus the TTL before the read. That sum must
        sit under the LOWEST threshold an operator can set, not only the
        default.
        """
        period_s = timeframe_to_ms(tf) / 1000.0
        worst_s = period_s + _mtf_ttl(tf)
        assert worst_s < 1.75 * period_s, (tf, worst_s, period_s)
        assert bar_freshness_verdict(worst_s, tf, 1.75)[0] is True


# ── the analyzer refuses a stale series, and carries the age of a fresh one ──

async def _no_fetch(*_a, **_k):
    return None


def _thesis():
    async def thesis(*_a, **_k):
        return {"direction": "LONG", "confidence": 0.95, "reasoning": "planted",
                "source": "LLM_TEST"}
    return thesis


def _analyzer() -> Analyzer:
    a = Analyzer()
    a._sentiment.refresh_fear_greed = _no_fetch    # the analyzer's one network read
    a._llm_thesis = _thesis()
    a._regime_hard_gate_reason = lambda *_x: None
    a._direction_guard_action = lambda *_x: None
    return a


def _signal(rows) -> MarketSignal:
    return MarketSignal(symbol="BTC/USDT", price=rows[-1][4], change_pct_24h=1.0,
                        volume_usd_24h=1e8)


def _as_of(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


class TestTheAnalyzer:
    def test_a_series_whose_newest_bar_closed_three_periods_ago_is_refused(self):
        rows = _rows(newest_close_ms=NOW_MS - 3 * H)
        a = _analyzer()
        assert _run(a.analyze(_signal(rows), rows, as_of=_as_of(NOW_MS), timeframe="1h")) is None
        why = a.get_no_trade_reason("BTC/USDT")
        assert why["stage"] == "stale_feed"
        assert "10800s old > 2 periods" in why["reason"]

    def test_the_same_series_read_when_it_closed_is_analysed_and_carries_its_age(self):
        rows = _rows(newest_close_ms=NOW_MS)
        a = _analyzer()
        idea = _run(a.analyze(_signal(rows), rows, as_of=_as_of(NOW_MS + 600_000), timeframe="1h"))
        assert idea is not None, a.get_no_trade_reason("BTC/USDT")
        assert idea.newest_bar_close_ms == float(NOW_MS)
        assert idea.bar_age_at_analysis_s == 600.0

    def test_a_replay_pinned_to_the_newest_bars_open_is_not_refused(self):
        # The backtest passes `as_of=bar.timestamp`, the OPEN of the newest
        # bar in the window: the age reads minus one period, which is fresh.
        rows = _rows(newest_close_ms=NOW_MS)
        a = _analyzer()
        idea = _run(a.analyze(_signal(rows), rows, as_of=_as_of(int(rows[-1][0])), timeframe="1h"))
        assert idea is not None, a.get_no_trade_reason("BTC/USDT")
        assert idea.bar_age_at_analysis_s == -3600.0

    def test_the_period_is_the_one_analysed_not_the_default(self):
        # 4h bars whose newest closed 5 h ago: fresh on 4h (limit 8 h), and
        # would be refused if read against the 1h default (limit 2 h).
        rows = _rows(newest_close_ms=NOW_MS - 5 * H, tf_ms=4 * H)
        a = _analyzer()
        assert _run(a.analyze(_signal(rows), rows, as_of=_as_of(NOW_MS), timeframe="4h")) is not None
        b = _analyzer()
        assert _run(b.analyze(_signal(rows), rows, as_of=_as_of(NOW_MS), timeframe="1h")) is None
        assert b.get_no_trade_reason("BTC/USDT")["stage"] == "stale_feed"

    def test_live_reads_the_wall_clock(self):
        stale = _rows(newest_close_ms=NOW_MS)           # June 2026, read in November
        a = _analyzer()
        assert _run(a.analyze(_signal(stale), stale, timeframe="1h")) is None
        assert a.get_no_trade_reason("BTC/USDT")["stage"] == "stale_feed"
        fresh = anchored(stale, "1h")
        b = _analyzer()
        assert _run(b.analyze(_signal(fresh), fresh, timeframe="1h")) is not None


# ── the risk gate re-reads the carried age ───────────────────────────────────

def _engine() -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-bar-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea(**kw) -> TradeIdea:
    base = dict(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                stop_loss=97.0, take_profit=109.0, confidence=0.72, reasoning="bar",
                source=ANALYZER_SOURCE, timeframe="1h", timestamp=datetime.now(UTC))
    base.update(kw)
    return TradeIdea(**base)


def _line(check, prefix="BAR_FRESHNESS"):
    hits = [ln for ln in check.checks_passed + check.checks_failed if ln.startswith(prefix)]
    assert len(hits) == 1, hits
    return hits[0], hits[0] in check.checks_passed


class TestTheRiskGate:
    def test_an_idea_whose_bar_was_three_periods_old_is_refused(self):
        line, ok = _line(_engine().evaluate(_idea(bar_age_at_analysis_s=10_800.0), atr=2.0))
        assert not ok and "10800s old > 2 periods" in line

    def test_an_idea_whose_bar_was_fresh_passes_this_check(self):
        line, ok = _line(_engine().evaluate(_idea(bar_age_at_analysis_s=600.0), atr=2.0))
        assert ok and "600s old" in line

    def test_an_analyzer_idea_with_no_reading_is_refused(self):
        line, ok = _line(_engine().evaluate(_idea(), atr=2.0))
        assert not ok
        assert line == "BAR_FRESHNESS: analyzer idea carries no bar reading"

    def test_a_typed_ticket_is_said_not_measured_rather_than_fresh(self):
        line, ok = _line(_engine().evaluate(_idea(source="manual"), atr=2.0))
        assert ok
        assert "not measured" in line and "manual" in line
        assert "old" not in line

    def test_it_reads_the_carried_fact_not_the_replay_clock(self):
        # A replay pinned months back must not re-judge the carried age.
        eng = _engine()
        eng.set_sim_time(datetime(2025, 1, 1, tzinfo=timezone.utc))
        _, ok = _line(eng.evaluate(_idea(bar_age_at_analysis_s=600.0), atr=2.0))
        assert ok

    def test_an_error_names_the_class(self, monkeypatch):
        import bot.utils.candles as candles

        def _boom(*_a, **_k):
            raise RuntimeError("apiKey=SECRET")
        monkeypatch.setattr(candles, "bar_freshness_verdict", _boom)
        check = _engine().evaluate(_idea(bar_age_at_analysis_s=600.0), atr=2.0)
        line, ok = _line(check)
        assert not ok and line == "BAR_FRESHNESS: evaluation error (RuntimeError)"
        assert "SECRET" not in " ".join(check.checks_failed + check.checks_passed)


# ── the scanner carries the venue's time ─────────────────────────────────────

class TestTheTicker:
    def _tick(self, **kw):
        base = {"last": 100.0, "quoteVolume": 5_000_000.0, "percentage": 1.0}
        base.update(kw)
        return base

    def test_the_venues_ticker_time_is_carried(self):
        sig = MarketScanner()._process_ticker("BTC/USDT", self._tick(timestamp=NOW_MS))
        assert sig is not None
        assert sig.ticker_ts == _as_of(NOW_MS)

    @pytest.mark.parametrize("ts", [None, True, "soon", float("nan"), 0, -5])
    def test_a_ticker_with_no_readable_time_carries_none(self, ts):
        sig = MarketScanner()._process_ticker("BTC/USDT", self._tick(timestamp=ts))
        assert sig is not None and sig.ticker_ts is None

    def test_a_ticker_with_no_time_field_carries_none(self):
        sig = MarketScanner()._process_ticker("BTC/USDT", self._tick())
        assert sig is not None and sig.ticker_ts is None
        # ...and the scan's own clock is still stamped beside it.
        assert abs(sig.timestamp - datetime.now(UTC)) < timedelta(minutes=5)


# ── the website's analyze route analyses the timeframe it fetched ────────────

def test_the_api_bridge_analyses_the_timeframe_it_fetched():
    """The route fetched ``req.timeframe`` and analysed it as the default
    "1h", which a period-keyed freshness check would misjudge on 4h bars.
    The route needs a running engine and a venue, so this reads the call."""
    tree = ast.parse((ROOT / "api_bridge.py").read_text(encoding="utf-8"))
    route = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "analyze")
    calls = [n for n in ast.walk(route) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "analyze"
             and ast.unparse(n.func.value) == "engine.analyzer"]
    assert len(calls) == 1
    kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert kw.get("timeframe") == "req.timeframe"
