"""scripts/weekend_reversal_forward.py: the window, the refusal, and the rules."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _m():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "weekend_reversal_forward", ROOT / "scripts" / "weekend_reversal_forward.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_window_is_sixteen_weekends_all_after_the_seal():
    m = _m()
    fr = m.fridays()
    assert len(fr) == 16
    assert fr[0] == date(2026, 10, 30) and fr[-1] + timedelta(days=3) == date(2027, 2, 15)
    assert all(f.weekday() == 4 for f in fr)


def test_fetch_refuses_until_the_window_has_closed(tmp_path, monkeypatch):
    m = _m()
    monkeypatch.setattr(m, "DATA", tmp_path / "data")
    assert m.cmd_fetch(today=date(2027, 2, 15)) == 2
    assert not (tmp_path / "data").exists()


def test_reversal_rules_score_a_planted_reversal_at_maker_costs():
    m = _m()
    ws = m.ws
    fri = m.FIRST_FRIDAY
    times = (ws.at_et(fri, 16), ws.at_et(fri + timedelta(days=2), 20),
             ws.at_et(fri + timedelta(days=3), 9))

    def bars(prices):
        return {"candles": [[t - ws.HOUR_MS, p, p, p, p, 1.0, p] for t, p in zip(times, prices)],
                "funding": []}
    # Up 2% over SPY into Sunday, then gives back 1% by Monday 09:00.
    series = {"SPYUSDT": bars((100, 100, 100)), "AAA": bars((100, 102, 100.98))}
    res = m.evaluate(series, ["AAA"], m.COST_PER_SIDE)
    r1 = res["rows"]["R1"][0]["pnl"]
    r2 = res["rows"]["R2"][0]["pnl"]
    assert r1 == pytest.approx(0.01 - ws.legs_cost(2, m.COST_PER_SIDE))
    assert r2 == pytest.approx(0.01 - ws.legs_cost(1, m.COST_PER_SIDE))
    # Only the one planted weekend is scored; the other fifteen have no bars.
    assert res["rules"]["R1"]["n"] == 1


def test_judge_needs_a_positive_mean_and_lower_bound():
    m = _m()
    assert m.judge([{"pnl": 0.01}] * 16)["passed"] is True
    assert m.judge([{"pnl": v} for v in (0.03, -0.028) * 8])["passed"] is False
    assert m.judge([{"pnl": -0.01}] * 16)["passed"] is False
