"""`--trades-out` writes one percent-only row per OOS position, fold-tagged."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.backtest.runner import build_parser, trade_rows

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _t(symbol="BTC/USDT:USDT", size=1000.0, net=25.0, hours=0):
    return SimpleNamespace(
        symbol=symbol, direction="LONG", entry_time=T0 + timedelta(hours=hours),
        exit_time=T0 + timedelta(hours=hours + 3), size_usd=size, net_pnl_usd=net,
        exit_reason="TP", entry_regime="TREND_UP", setup="swing",
        signal_type="regime_trend")


def test_rows_are_percent_only_and_fold_tagged():
    rows = trade_rows([{"_trades": [_t(), _t(net=-10.0, hours=5)]},
                       {"_trades": [_t("ETH/USDT:USDT")]}])
    assert [r["fold"] for r in rows] == [0, 0, 1]
    assert rows[0]["net_pnl_pct"] == 2.5 and rows[1]["net_pnl_pct"] == -1.0
    assert not any("usd" in k for r in rows for k in r)


def test_a_position_with_no_size_is_unreadable_not_zero():
    rows = trade_rows([{"_trades": [_t(size=0.0), _t(size=None), _t(net=None)]}])
    assert [r["net_pnl_pct"] for r in rows] == [None, None, None]


def test_a_fold_with_no_trades_adds_no_rows_and_the_flag_is_optional():
    assert trade_rows([{}, {"_trades": []}]) == []
    args = build_parser().parse_args([])
    assert args.trades_out is None
    assert build_parser().parse_args(["--trades-out", "x.json"]).trades_out == "x.json"
