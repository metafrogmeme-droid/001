"""The resting-fill model and the forward bar, before any post-cutoff result.

A knob that is off leaves the historical touch. A bar at the placement
instant is not a fill. The forward verdict refuses a trade on or before
2026-10-23, and an empty window is not a mean of zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from bot.backtest.limit_fill import decide_resting_fill
from bot.backtest.portfolio_engine import merge_rejections_by_gate
from bot.core.limit_scanner import LONG, ScannerParams
from bot.core.limit_shadow import REALISTIC, _Cfg, _mark_to_market, evaluate_forward, summarize


class _Bar:
    def __init__(self, ts, o, h, low, c):
        self.timestamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        self.open, self.high, self.low, self.close = o, h, low, c


class _Idea:
    direction = "LONG"
    asset = "ETH"


def _order(placed=1_000.0):
    return {"idea": _Idea(), "px": 100.0, "placed_ts": placed}


def _cfg(**kw):
    base = dict(limit_through_bp=None, limit_queue_p=None, limit_queue_sure_bp=None,
                limit_reclaim=False, limit_cancel_adverse_bp=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_off_is_the_old_touch_and_a_bare_touch_does_not_fill_when_through_is_on():
    bar = _Bar(2_000, 100, 101, 100, 100.5)
    assert decide_resting_fill(_cfg(), _order(), bar) == "legacy"
    assert decide_resting_fill(_cfg(limit_through_bp=5), _order(), bar) == "rest"
    deep = _Bar(2_000, 100, 101, 99.0, 100.2)
    assert decide_resting_fill(_cfg(limit_through_bp=5), _order(), deep) == "fill"
    same = _Bar(1_000, 100, 101, 99.0, 100.2)
    assert decide_resting_fill(_cfg(limit_through_bp=5), _order(), same) == "rest"


def test_the_queue_draw_ignores_the_close():
    first = _Bar(2_000, 100, 101, 99.9, 100.4)
    second = _Bar(2_000, 100, 101, 99.9, 90.0)
    cfg = _cfg(limit_through_bp=5, limit_queue_p=0.5, limit_queue_sure_bp=50)
    assert decide_resting_fill(cfg, _order(), first) == decide_resting_fill(cfg, _order(), second)


def test_gate_counts_sum_and_an_empty_book_stays_empty():
    a = SimpleNamespace(_rejections_by_gate={"LOSS_STREAK": 2, "note": True, "bad": -1})
    b = SimpleNamespace(_rejections_by_gate={"LOSS_STREAK": 3, "RISK_REWARD": 1})
    assert merge_rejections_by_gate([a, b]) == {"LOSS_STREAK": 5, "RISK_REWARD": 1}
    assert merge_rejections_by_gate([]) == {}


def test_the_shadow_does_not_fill_the_bar_that_decided_the_order():
    params = ScannerParams()
    spec = REALISTIC
    order = {
        "symbol": "ETH", "side": LONG, "px": 100.0, "stop": 90.0, "take_profit": 105.0,
        "atr": 1.0, "points": 80.0, "placed_ts": 1_700_000_000.0,
        "placed_ms": 1_700_000_000_000, "placed_close": 101.0, "class": "crypto",
        "bars_waited": 0,
    }
    resting = [dict(order)]
    same = (1_700_000_000_000, 101.0, 102.0, 99.0, 100.5, 1.0)
    _mark_to_market(resting, {}, {}, {"ETH": same}, params, spec, _Cfg(spec), [])
    assert resting, "the decision bar is not a fill"
    later = (1_700_003_600_000, 101.0, 102.0, 99.0, 100.5, 1.0)
    positions, meta, trades = {}, {}, []
    _mark_to_market(resting, positions, meta, {"ETH": later}, params, spec, _Cfg(spec), trades)
    assert "ETH" in positions
    assert positions["ETH"].opened_ms > order["placed_ms"]


def test_the_forward_bar_refuses_a_spent_trade_and_an_empty_window():
    old = {"fill_ms": 1, "decision_ms": 0, "net_bp": 10.0, "gross_bp": 12.0,
           "symbol": "ETH", "funding": "unpriced"}
    refused = evaluate_forward([old], funding_priced=True)
    assert refused["verdict"] == "refused" and refused["mean_net_bp"] is None
    empty = evaluate_forward([], funding_priced=False)
    assert empty["evaluable"] is False and empty["mean_net_bp"] is None
    assert "2026-10-23" in empty["reason"]
    assert summarize([])["mean_net_bp"] is None
