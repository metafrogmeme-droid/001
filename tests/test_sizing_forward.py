"""scripts/sizing_forward.py: the window, the refusal, and the sealed rule."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _m():
    spec = importlib.util.spec_from_file_location(
        "sizing_forward", ROOT / "scripts" / "sizing_forward.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _close(day: int, net: float, notional: float = 1000.0, **kw) -> dict:
    t = datetime(2026, 11, 2, tzinfo=UTC) + timedelta(days=day)
    row = {"closed_at": t.isoformat(), "pnl_usd": net, "entry_price": 100.0,
           "quantity": notional / 100.0}
    row.update(kw)
    return row


def test_the_window_is_twelve_weeks_after_the_seal():
    m = _m()
    assert (m.END - m.START).days == 84
    sealed = (ROOT / "docs/research/prereg/equal_sizing_forward_2026-10/"
              "PREREGISTRATION.sealed_at").read_text().strip()
    assert datetime.fromisoformat(sealed.replace("Z", "+00:00")) < m.START


def test_unreadable_rows_are_counted_not_scored_as_zero():
    m = _m()
    rows = [_close(0, 5.0), _close(1, None), _close(2, 3.0, quantity=None),
            {"pnl_usd": 1.0}, _close(-30, 9.0)]
    scored, left = m.score(rows)
    assert len(scored) == 1
    assert left == {"no_close_time": 1, "no_net": 1, "no_notional": 1}


def test_equal_size_wins_where_the_big_positions_lose():
    m = _m()
    rows = []
    for d in range(60):
        rows.append(_close(d, 10.0, notional=1000.0))     # small, +1.0%
        rows.append(_close(d, -40.0, notional=8000.0))    # large, -0.5%
    j = m.judge(m.score(rows)[0])
    assert j["E"] > 0 > j["W"]
    assert j["q1_equal_size_makes_money"] is True and j["q2_equal_beats_sized"] is True


def test_the_same_sizes_make_neither_claim_when_nothing_wins():
    m = _m()
    rows = [_close(d, (-5.0 if d % 2 else 4.0)) for d in range(80)]
    j = m.judge(m.score(rows)[0])
    assert j["q1_equal_size_makes_money"] is False and j["q2_equal_beats_sized"] is False


def test_too_few_closes_is_no_verdict():
    m = _m()
    j = m.judge(m.score([_close(d, 5.0) for d in range(39)])[0])
    assert j["q1_equal_size_makes_money"] is None and "no verdict" in j["verdict"]


def test_evaluate_refuses_before_the_window_closes_and_status_shows_no_return(tmp_path, capsys):
    m = _m()
    f = tmp_path / "closed_trades.json"
    f.write_text(json.dumps([_close(d, 5.0) for d in range(50)]))
    assert m.main(["evaluate", "--trades", str(f)], now=datetime(2027, 1, 22, tzinfo=UTC)) == 2
    assert m.main(["status", "--trades", str(f)], now=datetime(2026, 12, 1, tzinfo=UTC)) == 0
    out = capsys.readouterr().out
    assert "scored 50" in out and "%" not in out and '"E"' not in out
    assert m.main(["evaluate", "--trades", str(f)], now=datetime(2027, 1, 23, tzinfo=UTC)) == 0
    assert '"q1_equal_size_makes_money": true' in capsys.readouterr().out


def test_a_positive_mean_on_noise_is_not_money():
    m = _m()
    rows = [_close(d, (60.0 if d % 2 else -59.0)) for d in range(84)]
    j = m.judge(m.score(rows)[0])
    assert j["E"] > 0 and j["E_lower"] < 0 and j["q1_equal_size_makes_money"] is False
