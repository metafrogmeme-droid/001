"""Playbook guards the GetAgent card's conditions have to satisfy.

The chase percent is not a permission to cross. One trade's loss at the stop
is under the pause, and the concurrent book is inside the circuit stop. The
breakeven trigger is the percent alone. Tokenized equities are out unless
asked, and an unread book still skips them. US event times are New York wall
times, not the schedule's Shanghai clock.
"""
from __future__ import annotations

import ast
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot.config import US_STOCK_SYMBOLS
from bot.core import limit_scanner as ls
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
PB = ROOT / "playbooks" / "runeclaw-limit-scanner"


def _install_getagent():
    ga = types.ModuleType("getagent")
    trade = types.ModuleType("getagent.trade")
    data = types.ModuleType("getagent.data")
    runtime = types.ModuleType("getagent.runtime")
    trade.account = types.SimpleNamespace(
        subaccount_exists=lambda: True,
        total_value=lambda: {"usdtEquity": 100.0},
    )
    trade.contract = types.SimpleNamespace()
    trade.helpers = types.SimpleNamespace()
    trade.is_success = lambda result: True
    ga.trade = trade
    ga.data = data
    ga.runtime = runtime
    sys.modules["getagent"] = ga
    sys.modules["getagent.trade"] = trade
    sys.modules["getagent.data"] = data
    sys.modules["getagent.runtime"] = runtime
    return trade


@pytest.fixture(scope="module")
def playbook():
    _install_getagent()
    sys.path.insert(0, str(PB))
    from src import clock, execution, risk, scoring
    return types.SimpleNamespace(clock=clock, execution=execution, risk=risk, scoring=scoring)


def test_a_us_print_is_not_read_as_a_shanghai_hour(playbook):
    clock = playbook.clock
    winter = clock.us_wall_to_utc(2026, 1, 14, 8, 30)
    summer = clock.us_wall_to_utc(2026, 7, 14, 8, 30)
    assert winter == datetime(2026, 1, 14, 13, 30, tzinfo=timezone.utc)
    assert summer == datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
    assert clock.shanghai_clock(winter).hour == 21
    assert clock.shanghai_clock(summer).hour == 20
    # 13:15 UTC is inside the January window and outside the July window.
    probe = datetime(2026, 1, 14, 13, 15, tzinfo=timezone.utc)
    assert clock.covers(probe, winter, 30, 30) is True
    july_probe = datetime(2026, 7, 14, 13, 15, tzinfo=timezone.utc)
    assert clock.covers(july_probe, summer, 30, 30) is False
    # The same digits read as Shanghai are a different instant.
    as_shanghai = datetime(2026, 1, 14, 8, 30, tzinfo=clock.SHANGHAI).astimezone(timezone.utc)
    assert as_shanghai != winter
    cfg = {"event_blackouts": {
        "enabled": True, "before_min": 30, "after_min": 30,
        "events": [{"year": 2026, "month": 1, "day": 14, "hour": 8, "minute": 30}],
    }}
    assert clock.blackout_reason(cfg, probe) == "us_event_blackout"
    assert clock.blackout_reason(cfg, datetime(2026, 1, 14, 16, 0, tzinfo=timezone.utc)) == ""
    assert clock.blackout_reason({}, probe) == ""
    assert clock.blackout_reason({"event_blackouts": {"enabled": True}}, probe) == "blackout_unreadable"


def test_one_trade_stays_under_the_pause_and_the_book_under_the_stop(playbook):
    risk = playbook.risk
    cfg = {"circuit_pause_usdt": "30", "circuit_stop_usdt": "40", "max_concurrent": 5,
           "max_loss_usdt": "150", "margin_budget": "150", "leverage": 10,
           "sl_min_alt_pct": "2.5"}
    cap = risk.loss_cap(cfg)
    assert cap is not None and cap < 30 and cap * 5 <= 40
    from src.features import SymbolFeatures
    row = SymbolFeatures(symbol="ADAUSDT", ok=True, last=100.0, vwap=99.0, high=101.0, low=90.0)
    open_risk = 0.0
    risks = []
    for _ in range(5):
        plan = risk.build_plan(row, cfg, 1.0, side="long", open_risk=open_risk)
        assert plan is not None and plan.sizing_ok
        assert plan.risk_usdt < 30
        risks.append(plan.risk_usdt)
        open_risk += plan.risk_usdt
    assert sum(risks) <= 40 + 1e-6
    # The book is full. A sixth stop is refused.
    assert risk.build_plan(row, cfg, 1.0, side="long", open_risk=open_risk) is None
    # Twice the configured max loss binds when it is under the circuit stop.
    tight = dict(cfg, max_loss_usdt="10")
    tight_cap = risk.loss_cap(tight)
    assert tight_cap is not None and tight_cap * 5 <= 20 + 1e-9
    assert tight_cap * 5 <= 40
    # A buy at or above the last trade is not moved onto it.
    through = SymbolFeatures(symbol="ADAUSDT", ok=True, last=99.0, vwap=100.0, high=101.0, low=98.0)
    assert risk.build_plan(through, cfg, 1.0, side="long") is None


def test_equities_are_excluded_and_an_unread_book_still_skips(playbook):
    scoring = playbook.scoring
    bases = {s.replace("/", "") for s in US_STOCK_SYMBOLS}
    assert scoring._EQUITY_SYMBOLS == bases
    assert scoring.is_tokenized_equity("RAAPLUSDT") is True
    assert scoring.is_tokenized_equity("RENDERUSDT") is False
    from src.features import SymbolFeatures
    equity = SymbolFeatures(symbol="RAAPLUSDT", ok=True, last=100.0, vwap=99.0, high=110.0,
                            low=90.0, change_pct=3.0, quote_volume=50e6)
    other = SymbolFeatures(symbol="ADAUSDT", ok=True, last=100.0, vwap=99.0, high=110.0,
                           low=90.0, change_pct=1.0, quote_volume=20e6)
    btc = SymbolFeatures(symbol="BTCUSDT", ok=True, last=100.0, vwap=99.0, high=110.0,
                         low=90.0, change_pct=1.0, quote_volume=80e6)
    skipped = scoring.score_universe([equity, other], btc, {}, "long")
    row = next(s for s in skipped if s.symbol == "RAAPLUSDT")
    assert row.skip_reason == "equity_excluded"
    opted = scoring.score_universe([equity, other], btc, {"include_equities": True}, "long")
    row = next(s for s in opted if s.symbol == "RAAPLUSDT")
    assert row.skip_reason == "equity_book_unread"
    assert row.dims.get("orderbook") == 0.0
    # The missing 20 is a zero, not a reason to rescale the other 80 onto 100.
    raw = (row.dims["momentum"] + row.dims["vwap"] + row.dims["range"]
           + row.dims["orderbook"] + row.dims["volume"])
    assert row.dims["total"] == pytest.approx(round(raw, 2))
    assert row.score < raw / 80.0 * 100.0 - 1
    filled = SymbolFeatures(symbol="RAAPLUSDT", ok=True, last=100.0, vwap=99.0, high=110.0,
                            low=90.0, change_pct=3.0, quote_volume=50e6,
                            bid_volume=300.0, ask_volume=100.0)
    read = scoring.score_universe([filled, other], btc, {"include_equities": True}, "long")
    got = next(s for s in read if s.symbol == "RAAPLUSDT")
    assert got.skip_reason == ""
    assert got.dims["orderbook"] == 20.0


def test_the_engine_excludes_the_same_names_until_the_book_is_read():
    eth = ls.Snapshot(symbol="ETH", last=100.0, vwap=99.0, high=101.0, low=95.0,
                      change_pct=4.0, quote_volume=50e6, atr=1.0, bid_volume=300.0, ask_volume=100.0)
    stock = ls.Snapshot(symbol="RAAPL/USDT", last=100.0, vwap=99.0, high=101.0, low=95.0,
                        change_pct=6.0, quote_volume=90e6, atr=1.0)
    leader = ls.Snapshot(symbol="BTC", last=101.0, vwap=100.0, high=102.0, low=99.0,
                         change_pct=1.0, quote_volume=80e6, atr=1.0)
    board = ls.score_universe([stock, eth], leader, ls.LONG, ls.ScannerParams())
    assert next(r for r in board if r.symbol.startswith("RAAPL")).skip == "tokenized equity is excluded"
    opted = ls.ScannerParams(include_equities=True)
    board = ls.score_universe([stock, eth], leader, ls.LONG, opted)
    unread = next(r for r in board if r.symbol.startswith("RAAPL"))
    assert unread.skip == "the order book was not read"
    assert unread.dims["orderbook"] == 0.0
    assert ls.bar_score(unread.points, unread.dims) == unread.points
    assert ls.bar_score(unread.points, {"orderbook": None}) != pytest.approx(unread.points)
    render = ls.Snapshot(symbol="RENDER", last=100.0, vwap=99.0, high=101.0, low=95.0,
                         change_pct=2.0, quote_volume=30e6, atr=1.0, bid_volume=200.0, ask_volume=100.0)
    board = ls.score_universe([render, eth], leader, ls.LONG, ls.ScannerParams())
    assert next(r for r in board if r.symbol == "RENDER").skip != "tokenized equity is excluded"


def test_mutations_stay_in_the_callback_and_debug_is_not_a_close():
    live = (PB / "src" / "main_live.py").read_text(encoding="utf-8")
    scanned = code_only(live)
    assert 'action="close"' not in scanned and "action='close'" not in scanned
    tree = ast.parse(live)

    class Finder(ast.NodeVisitor):
        def __init__(self):
            self.stack = []
            self.sites = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if name in ("apply_management", "cancel_order", "close_position", "modify_stop_loss"):
                self.sites.append((name, tuple(self.stack)))
            self.generic_visit(node)

    found = Finder()
    found.visit(tree)
    managed = [site for site in found.sites if site[0] == "apply_management"]
    assert managed and all(site[1][-1] == "_execute" for site in managed)
    assert all(site[0] == "apply_management" for site in found.sites)
    execution = code_only((PB / "src" / "execution.py").read_text(encoding="utf-8"))
    assert "_runeclaw_sized" not in execution
    assert "breakeven_trigger_usdt" not in execution
    assert "_record_notional" not in execution
    manifest = (PB / "manifest.yaml").read_text(encoding="utf-8")
    assert 'tp2_pct: "7.0"' in manifest
    assert 'time_stop_hours: "4"' in manifest
    assert 'breakeven_pct: "2.0"' in manifest
    assert "breakeven_trigger_usdt" not in manifest
    assert 'tp2_pct: "20' not in manifest
    assert 'margin_budget: "100"' in manifest
    assert 'limit_chase_pct: "0.25"' in manifest


def test_a_chase_never_crosses_the_best_opposite_price(playbook):
    execution = playbook.execution
    room = execution.chase_room(99.0, 10.0, 3.0)
    assert room == pytest.approx(min(99.0 * 0.0025, 5.0))
    assert room < 99.0 * 0.03
    # A 3% lift of 99 is 101.97, through this ask. The cap keeps the maker price.
    px, why = execution.quote_entry(
        "long", 99.0, bid=99.1, ask=99.2, tick=0.01, atr=10.0, last=99.5,
        configured_pct=3.0)
    assert why == "" and px is not None
    assert px < 99.2 and px <= 99.1 - 0.01
    assert px - 99.0 <= room + 1e-9
    crossed, why = execution.quote_entry(
        "long", 99.5, bid=99.4, ask=99.45, tick=0.01, atr=1.0, last=99.6)
    assert crossed is None and why == "would_cross"
    short, why = execution.quote_entry(
        "short", 99.3, bid=99.4, ask=99.5, tick=0.01, atr=1.0, last=99.2)
    assert short is None and why == "would_cross"
    held, _ = execution.quote_entry(
        "short", 101.0, bid=99.9, ask=100.0, tick=0.01, atr=10.0, last=100.0,
        configured_pct=3.0)
    assert held is not None and held > 99.9
    assert 101.0 - held <= execution.chase_room(101.0, 10.0, 3.0) + 1e-9


def test_a_matching_notional_is_not_ownership(playbook):
    execution = playbook.execution
    symbols = {"ADAUSDT"}
    # 15 * 100 = 1500, the old "this size is ours" envelope.
    assert execution._is_ours(
        {"symbol": "ADAUSDT", "orderId": "someone", "total": "15", "price": "100"},
        {"ours"}, symbols) is False
    assert execution._is_ours(
        {"symbol": "ADAUSDT", "orderId": "ours", "total": "15", "price": "100"},
        {"ours"}, symbols) is True
    assert execution._is_ours(
        {"symbol": "ADAUSDT", "total": "15", "price": "100"},
        {"ours"}, symbols) is False


def test_an_open_loss_after_the_baseline_pauses_the_breaker(playbook, tmp_path, monkeypatch):
    import json
    execution = playbook.execution
    monkeypatch.setenv("RUNECLAW_PLAYBOOK_STATE_DIR", str(tmp_path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_path / "runeclaw_scanner.json").write_text(json.dumps(
        {"orders": [], "date": today, "day_start_equity": 1000.0}), encoding="utf-8")
    trade = sys.modules["getagent"].trade
    trade.account.subaccount_exists = lambda: True
    trade.contract.pending_orders = lambda *a, **k: []
    trade.helpers.contract_position_records = lambda positions: list(positions)
    cfg = {"circuit_pause_usdt": "30", "circuit_stop_usdt": "40",
           "trading_symbols": ["ADAUSDT"]}
    trade.account.total_value = lambda: {"totalAmount": 1000.0}
    trade.contract.current_position = lambda *a, **k: [
        {"symbol": "ADAUSDT", "unrealizedPL": -31.0, "holdSide": "long"}]
    status = execution.manage_open_state(cfg)
    assert status["open_pnl_counted"] is True
    assert status["today_pnl"] == pytest.approx(-31.0)
    assert status["circuit"] == "paused"
    # An inclusive equity already holds the open loss. Adding it again would
    # print -62 and trip the stop. The breaker pauses once.
    trade.account.total_value = lambda: {"usdtEquity": 969.0}
    status = execution.manage_open_state(cfg)
    assert status["today_pnl"] == pytest.approx(-31.0)
    assert status["circuit"] == "paused"
    trade.account.total_value = lambda: {"totalAmount": 1000.0}
    trade.contract.current_position = lambda *a, **k: [
        {"symbol": "ADAUSDT", "holdSide": "long"}]
    status = execution.manage_open_state(cfg)
    assert status["circuit"] == "unconfirmed"
    assert status["today_pnl"] is None


def test_earnings_and_macro_prints_follow_new_york_not_shanghai(playbook):
    clock = playbook.clock
    cfg = {"event_blackouts": {
        "enabled": True, "before_min": 15, "after_min": 15,
        "events": [{"year": 2026, "month": 1, "day": 14, "hour": 8, "minute": 30}],
        "earnings": [{"year": 2026, "month": 1, "day": 29, "hour": 16, "minute": 5}],
    }}
    shanghai_morning = datetime(2026, 1, 14, 8, 30, tzinfo=clock.SHANGHAI)
    assert clock.blackout_reason(cfg, shanghai_morning.astimezone(timezone.utc)) == ""
    print_utc = clock.us_wall_to_utc(2026, 1, 14, 8, 30)
    assert clock.shanghai_clock(print_utc).hour == 21
    assert clock.blackout_reason(cfg, print_utc) == "us_event_blackout"
    earn = clock.us_wall_to_utc(2026, 1, 29, 16, 5)
    assert earn == datetime(2026, 1, 29, 21, 5, tzinfo=timezone.utc)
    assert clock.shanghai_clock(earn).hour == 5
    assert clock.blackout_reason(cfg, earn) == "earnings_blackout"
    summer = clock.us_wall_to_utc(2026, 7, 29, 16, 5)
    assert summer == datetime(2026, 7, 29, 20, 5, tzinfo=timezone.utc)
    assert clock.shanghai_clock(summer).hour == 4
    as_shanghai = datetime(2026, 1, 29, 16, 5, tzinfo=clock.SHANGHAI).astimezone(timezone.utc)
    assert clock.blackout_reason(cfg, as_shanghai) == ""


def test_a_v3_refusal_is_reread_and_a_miss_stays_unread(playbook):
    from src.book import read_levels

    class Fetch:
        def __init__(self):
            self.params = []

        def __call__(self, symbol, params=None):
            self.params.append(params)
            if not params:
                raise RuntimeError(
                    '{"code":"40502","msg":"Please upgrade to access the V3 API."}')
            return {"bids": [[100.0, 2.0], [99.0, 1.0]], "asks": [[101.0, 4.0]]}

    fetch = Fetch()
    bid, ask = read_levels(fetch, "RAAPLUSDT")
    assert (bid, ask) == (3.0, 4.0)
    assert fetch.params[1] == {"uta": True}

    def timeout(symbol, params=None):
        raise RuntimeError("timed out")

    assert read_levels(timeout, "RAAPLUSDT") == (None, None)
