"""The Limit Entry Scanner in the portfolio backtest: one pass a bar across
the universe, its limit resting from the next bar, through the same gate.

The scanner decides at a bar's close from that bar's own close and VWAP, so a
touch inside the same bar's range happened before the order existed; the
house path's `_place_entry` counts that touch, and the scanner's
`_rest_limit` does not. The analyzer is not asked: the scanner is the book's
whole entry rule. Driven through the real `PortfolioBacktester`, risk engine
and tracker on hand-built bars; the pass's choice is fixed so the wiring is
what is measured (the rules have their own file).
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester
from bot.core import limit_scanner as ls

T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
SYMS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
N = 140
SIGNAL = 110          # the bar whose close the pass places from


def _bars(sym, lows=None):
    lows = lows or {}
    out = []
    for i in range(N):
        low = lows.get(i, 99.7)
        out.append(BacktestBar(timestamp=T0 + timedelta(hours=i), open=100.0, high=100.3,
                               low=low, close=100.0, volume=1_000.0, symbol=sym))
    return out


def _data(eth_lows):
    return {s: _bars(s, eth_lows if s.startswith("ETH") else None) for s in SYMS}


PLAN = ls.EntryPlan("ETH/USDT:USDT", ls.LONG, 99.5, 97.0, 104.5, 0.025, 1.0, 82.0)


@pytest.fixture
def one_plan(monkeypatch):
    """The pass places PLAN once, at SIGNAL's close; the real pass otherwise
    runs on the flat bars and places nothing. Records each call's held set."""
    real = ls.scan_pass
    calls: list = []

    def scan(snaps, params, held=frozenset()):
        calls.append(held)
        if len(calls) == SIGNAL - 99:            # passes start at the lookback (100)
            return ls.Pass(ls.Regime(ls.LONG, PLAN.size_factor, 2, True, "test"), (), PLAN, "")
        return real(snaps, params, held)

    monkeypatch.setattr(ls, "scan_pass", scan)
    scan.calls = calls
    return scan


def _run(data, **cfg):
    import asyncio
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0, scanner="limit_entry",
                                            **cfg), symbols=list(SYMS))
    try:
        result = asyncio.run(pb.run(data))
    finally:
        pb.cleanup()
    return pb, result


def _opened(result):
    return [t for t in result.trades if t.symbol.startswith("ETH")]


def test_the_limit_rests_from_the_next_bar(one_plan, monkeypatch):
    def _no_analyzer(*a, **k):
        raise AssertionError("the analyzer was asked in a scanner run")

    monkeypatch.setattr("bot.core.analyzer.Analyzer.analyze", _no_analyzer)
    # The signal bar's own low reaches 99.5; the next does not; the one after does.
    pb, result = _run(_data({SIGNAL: 99.0, SIGNAL + 1: 99.8, SIGNAL + 2: 99.4}))
    assert pb.scanner_passes.get("placed a limit") == 1
    (trade,) = {t.trade_id: t for t in _opened(result)}.values() or [None]
    assert trade is not None, result.trades
    assert trade.entry_time == T0 + timedelta(hours=SIGNAL + 2), \
        "filled inside the bar the order was decided at, or missed its touch"
    assert trade.entry_price == pytest.approx(99.5, rel=1e-3)
    # ETH's engine is not the first: the book's limit counts are every engine's.
    assert (result.total_limits_filled, result.total_limits_filled_same_bar) == (1, 0)


def test_an_untouched_limit_is_never_filled_and_the_symbol_is_held(one_plan):
    pb, result = _run(_data({SIGNAL: 99.0}))
    assert pb.scanner_passes.get("placed a limit") == 1
    assert _opened(result) == []
    assert (result.total_limits_filled, result.total_limits_expired) == (0, 1)
    # While it rests, the pass is told ETH is held. The limit expires four
    # hours after it was placed, at the start of that bar, before its pass.
    after = one_plan.calls[SIGNAL - 99:]
    assert all("ETH" in h for h in after[:3]), after[:4]
    assert "ETH" not in after[3]


def test_a_half_size_regime_halves_the_gates_size(monkeypatch):
    import asyncio
    sizes = {}
    for factor in (1.0, 0.5):
        plan = dataclasses.replace(PLAN, size_factor=factor)
        calls = []
        real = ls.scan_pass

        def scan(snaps, params, held=frozenset(), plan=plan, calls=calls, real=real):
            calls.append(1)
            if len(calls) == SIGNAL - 99:
                return ls.Pass(ls.Regime(ls.LONG, factor, 2, True, "t"), (), plan, "")
            return real(snaps, params, held)

        monkeypatch.setattr(ls, "scan_pass", scan)
        pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0, scanner="limit_entry"),
                                 symbols=list(SYMS))
        try:
            asyncio.run(pb.run(_data({SIGNAL + 1: 99.4})))
            eng = pb._engines["ETH/USDT:USDT"]
            sizes[factor] = None
            for t in eng._trades:
                sizes[factor] = t.size_usd
        finally:
            pb.cleanup()
    assert sizes[1.0] and sizes[0.5]
    assert sizes[0.5] == pytest.approx(sizes[1.0] / 2, rel=1e-6)


def test_numbers_that_do_not_read_refuse_the_run():
    with pytest.raises(ValueError):
        PortfolioBacktester(BacktestConfig(scanner="limit_entry",
                                           scanner_config={"min_score": "70"}),
                            symbols=list(SYMS))
    with pytest.raises(ValueError):
        PortfolioBacktester(BacktestConfig(scanner="another"), symbols=list(SYMS))
    # The other arm: the preset's numbers read, and the run is built.
    pb = PortfolioBacktester(BacktestConfig(scanner="limit_entry",
                                            scanner_config={"min_score": 75}),
                             symbols=list(SYMS))
    try:
        assert pb._scanner.min_score == 75
    finally:
        pb.cleanup()


def test_the_runner_refuses_a_scanner_run_on_one_symbol_and_bad_json(monkeypatch, capsys):
    from bot.backtest import runner
    monkeypatch.setattr("sys.argv", ["runner", "--scanner", "limit_entry"])
    with pytest.raises(SystemExit):
        runner.main()
    assert "ranks a universe" in capsys.readouterr().out
    args = runner.build_parser().parse_args(["--scanner-config", "[1, 2]"])
    with pytest.raises(SystemExit):
        runner._scanner_config(args)
    good = runner.build_parser().parse_args(["--scanner-config", '{"min_score": 75}'])
    assert runner._scanner_config(good) == {"min_score": 75}


def test_each_class_places_its_own_limit_and_names_its_outcomes(monkeypatch):
    """Two classes, two leaders: one pass a bar for each, over its own markets
    and floor, and one limit each on the same bar; each outcome names its class."""
    import asyncio
    syms = ("BTC/USDT:USDT", "ETH/USDT:USDT", "XAU/USDT:USDT", "XAG/USDT:USDT")
    plans = {"BTC": PLAN, "XAU": dataclasses.replace(PLAN, symbol="XAG/USDT:USDT")}
    real = ls.scan_pass
    passes: dict = {}
    boards: set = set()

    def scan(snaps, params, held=frozenset()):
        n = passes[params.leader] = passes.get(params.leader, 0) + 1
        boards.add((params.leader, params.min_quote_volume,
                    tuple(sorted(ls._base(s.symbol) for s in snaps))))
        if n == SIGNAL - 99:
            return ls.Pass(ls.Regime(ls.LONG, 1.0, 2, True, "test"), (), plans[params.leader], "")
        return real(snaps, params, held)

    monkeypatch.setattr(ls, "scan_pass", scan)
    classes = [{"name": "crypto", "leader": "BTCUSDT", "members": ["ETHUSDT"],
                "min_quote_volume": 10_000_000},
               {"name": "metals", "leader": "XAUUSDT", "members": ["XAGUSDT"],
                "min_quote_volume": 250_000}]
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0, scanner="limit_entry",
                                            scanner_config={"classes": classes}),
                             symbols=list(syms))
    data = {s: _bars(s, {SIGNAL + 1: 99.4} if s[:3] in ("ETH", "XAG") else None) for s in syms}
    try:
        result = asyncio.run(pb.run(data))
    finally:
        pb.cleanup()
    assert boards == {("BTC", 10_000_000, ("BTC", "ETH")), ("XAU", 250_000, ("XAG", "XAU"))}
    assert passes["BTC"] == passes["XAU"] >= SIGNAL - 99
    assert pb.scanner_passes.get("crypto: placed a limit") == 1
    assert pb.scanner_passes.get("metals: placed a limit") == 1
    assert "placed a limit" not in pb.scanner_passes
    filled = {t.symbol: t.entry_time for t in result.trades}
    assert filled == {"ETH/USDT:USDT": T0 + timedelta(hours=SIGNAL + 1),
                      "XAG/USDT:USDT": T0 + timedelta(hours=SIGNAL + 1)}, result.trades
