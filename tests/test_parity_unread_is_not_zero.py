"""The parity report read a missing fee as a free trade and a missing PnL as a flat one.

The report the operator pasted tonight ends its fee line with a verdict:

    Fees: realized 0.092%/round-trip vs modeled 0.200% -> 0.46x (better than model)

`_fees` answered 0.0 for a close whose `commission` was None, and `_net`
answered 0.0 for one whose `pnl_usd` was. Both are real, loadable shapes:
LivePosition declares both Optional with a None default, and the persisted-
record loader restores a null faithfully. A close with no fee record then
lowered the realized fee rate, and enough of them turn "WORSE than model"
into "better than model" -- a confident positive assembled from absent data,
on the line whose whole purpose is to say whether live fills are as good as
the backtest assumes. A close with no PnL record counted as a non-win at $0:
win rate down, PF untouched, net unchanged, nobody told.

Unreadable is never zero. The report now measures fees only over closes
that carry a fee record, says how many that is, and WITHHOLDS the verdict
when it is not all of them; closes with no PnL record are excluded from
win rate, PF and net, and counted in the header, the way never-filled
records already are. A genuine 0.0 in either field still counts as 0.0.
"""
from __future__ import annotations

from bot.backtest.parity import format_report, parity_summary


def _t(net=1.0, fees=0.12, **kw):
    t = {"symbol": "BTC/USDT:USDT", "entry_price": 100.0, "quantity": 1.0,
         "leverage": 10, "pnl_usd": net, "gross_pnl": None if net is None else net + (fees or 0.0),
         "commission": fees, "signal_type": "regime_trend", "strategy_type": "swing",
         "close_reason": "TP HIT", "fill_source": "exchange"}
    t.update(kw)
    return t


MODEL = 0.1   # per-side %, so 0.2% per round trip


# ── fees ─────────────────────────────────────────────────────────────────────

def test_every_close_with_a_fee_record_still_yields_a_verdict():
    s = parity_summary([_t(fees=0.2), _t(fees=0.2)], MODEL)
    assert s["fees_read"] == 2 and s["trades"] == 2
    assert isinstance(s["fee_vs_model"], float)
    out = format_report(s)
    assert "matches model" in out or "than model" in out


def test_a_missing_fee_record_withholds_the_verdict_and_says_how_many():
    trades = [_t(fees=0.2), _t(fees=0.2), _t(fees=None), _t(fees=None)]
    s = parity_summary(trades, MODEL)
    assert s["fees_read"] == 2
    assert s["fee_vs_model"] is None, "a ratio over half the closes is not the ratio"
    out = format_report(s)
    assert "fees recorded on 2 of 4 closes" in out
    for verdict in ("better than model", "matches model", "WORSE than model"):
        assert verdict not in out, f"verdict {verdict!r} printed from a partial read"


def test_the_fee_rate_is_measured_over_the_closes_that_carry_one():
    """Two closes at 0.2 fee on 100 notional each, two with no record: the
    rate is 0.2%, not 0.1% diluted by notional nobody charged for."""
    trades = [_t(fees=0.2), _t(fees=0.2), _t(fees=None), _t(fees=None)]
    s = parity_summary(trades, MODEL)
    assert abs(s["realized_fee_rate"] - 0.002) < 1e-9
    assert s["total_fees"] == 0.4


def test_no_fee_records_at_all_cannot_be_measured():
    s = parity_summary([_t(fees=None), _t(fees=None)], MODEL)
    assert s["fees_read"] == 0
    assert s["fee_vs_model"] is None
    assert s["realized_fee_rate"] is None, "no notional was charged for -- there is no rate"
    out = format_report(s)
    assert "no fee record on any close" in out
    assert "than model" not in out


def test_a_genuine_zero_fee_is_a_fee_record():
    """A maker rebate or a fee-free promo is a real 0.0. `is None`, not falsiness."""
    s = parity_summary([_t(fees=0.0), _t(fees=0.0)], MODEL)
    assert s["fees_read"] == 2
    assert s["realized_fee_rate"] == 0.0
    assert s["fee_vs_model"] == 0.0
    assert "better than model" in format_report(s)


# ── pnl ──────────────────────────────────────────────────────────────────────

def test_a_close_with_no_pnl_record_is_excluded_and_counted():
    trades = [_t(net=3.0), _t(net=-1.0), _t(net=None)]
    s = parity_summary(trades, MODEL)
    assert s["trades"] == 2, "the unscored close is not one of the scored trades"
    assert s["unscored_pnl"] == 1
    assert s["win_rate"] == 0.5, "THE DEFECT: it used to count as a third, losing, trade"
    assert s["net_pnl"] == 2.0
    out = format_report(s)
    assert "1 close(s) with no PnL record excluded" in out


def test_an_unscored_close_reaches_no_bucket():
    trades = [_t(net=3.0, signal_type="a"), _t(net=None, signal_type="b")]
    s = parity_summary(trades, MODEL)
    assert "b" not in s["by_signal_type"]
    assert s["by_signal_type"]["a"]["trades"] == 1


def test_the_bucket_helper_refuses_an_unscored_close_on_its_own():
    """parity_summary filters before it buckets, so a mutation of _group's
    own guard survives every test that goes through the front door. The
    helper is module-level and must hold on its own."""
    from bot.backtest.parity import _group
    out = _group([_t(net=None, signal_type="b"), _t(net=1.0, signal_type="a")], "signal_type")
    assert "b" not in out, "an unscored close must not become a $0 bucket entry"
    assert out["a"]["trades"] == 1


def test_a_real_break_even_close_still_counts():
    s = parity_summary([_t(net=0.0), _t(net=2.0)], MODEL)
    assert s["trades"] == 2 and s["unscored_pnl"] == 0
    assert s["win_rate"] == 0.5


def test_a_recorded_zero_gross_is_not_replaced_by_the_net():
    """`gross_pnl or net` swapped a real 0.0 gross for the (negative) net."""
    s = parity_summary([_t(net=-0.12, gross_pnl=0.0, fees=0.12)], MODEL)
    assert s["gross_pnl"] == 0.0


def test_no_unscored_line_when_every_close_is_scored():
    out = format_report(parity_summary([_t(), _t()], MODEL))
    assert "no PnL record" not in out


# ── the weekly digest, which used to crash into silence on a withheld ratio ──

def test_the_weekly_digest_survives_a_withheld_fee_ratio(monkeypatch, tmp_path):
    import json
    import types
    from datetime import datetime as _dt

    import bot.core.proactive_monitor as pm

    now = _dt.now(pm.UTC)
    monkeypatch.setenv("PARITY_DIGEST_DOW", str(now.weekday()))
    monkeypatch.setenv("PARITY_DIGEST_HOUR_UTC", "0")
    f = tmp_path / "closed.json"
    f.write_text(json.dumps([_t(fees=0.2), _t(fees=None)]))
    eng = types.SimpleNamespace(
        live_executor=types.SimpleNamespace(_closed_trades_file=str(f)))
    mon = pm.ProactiveMonitor(eng)
    alerts = mon._check_parity_digest()
    assert len(alerts) == 1, "a withheld ratio must not swallow the digest"
    body = alerts[0].body
    assert "fee record on 1 of 2 closes" in body and "withheld" in body
    assert "the modeled rate" not in body
