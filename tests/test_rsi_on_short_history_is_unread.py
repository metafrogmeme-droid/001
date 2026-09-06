"""RSI 50 and ATR 0 on short history are readings never taken.

compute_rsi answers 50.0 when there are fewer than fifteen closes, and
compute_atr answers 0.0. The scanner relies on the numbers (its regime
arithmetic compares them), but the same values flowed into every card
through fetch_analysis_data: a freshly listed symbol with a dozen bars
showed "RSI 50.0" on the analysis card, "50.0" in the comparison table,
and "RSI 50 (neutral)" on the position-details line -- a verdict about a
number nobody measured. The absent case was worse: `adata.get('rsi', 0)`
printed "RSI 0 (oversold)".

The display path reads through rsi_or_none now and renders a dash; the
label says "unread"; the scanner keeps its own default untouched.
"""
from __future__ import annotations

import io
import tokenize
from pathlib import Path

import numpy as np

from bot.formatters.rich_cards import (
    _fmt_rsi,
    compute_rsi,
    market_context_line,
    render_analysis_card,
    render_comparison_table,
    rsi_label,
    rsi_or_none,
)

ROOT = Path(__file__).resolve().parent.parent
SHORT = np.array([100.0, 101.0, 102.0, 101.5, 103.0, 102.0, 104.0, 103.5, 105.0, 104.0])
LONG = np.array([100.0 + (i % 5) * 0.7 - (i % 3) * 0.4 for i in range(60)])


def test_short_history_is_none_not_fifty():
    assert rsi_or_none(SHORT) is None
    assert compute_rsi(SHORT) == 50.0, "the scanner's default is deliberately untouched"


def test_enough_history_is_the_same_number_the_scanner_sees():
    assert rsi_or_none(LONG) == compute_rsi(LONG)
    assert 0.0 <= rsi_or_none(LONG) <= 100.0


def test_the_label_never_calls_an_unread_neutral():
    assert rsi_label(None) == "unread"
    assert rsi_label(75.0) == "overbought"
    assert rsi_label(25.0) == "oversold"
    assert rsi_label(50.0) == "neutral"


def test_the_formatter_renders_a_dash_for_none():
    assert _fmt_rsi(None) == "—"
    assert _fmt_rsi(63.26) == "63.3"


def test_the_market_context_line_says_unread():
    line = market_context_line({"rsi": None, "structure": "Range-bound $1 – $2"})
    assert line.startswith("RSI — (unread)")
    assert "Range-bound" in line, "the structure is still known and still said"
    assert "neutral" not in line and "oversold" not in line
    assert market_context_line({"rsi": 72.4, "structure": "Uptrend"}) == "RSI 72.4 (overbought) | Uptrend"
    assert market_context_line(None) == ""


def _analysis(rsi, atr):
    return {
        "symbol": "NEW/USDT:USDT", "pair": "NEW/USDT", "price": 1.25, "high_24h": 1.4, "low_24h": 1.1,
        "change_pct": 3.2, "volume_24h_usd": 2.5e6, "vol_spike": 1.1, "vwap": 1.2, "vwap_pct": 4.1,
        "rsi": rsi, "atr": atr, "sma9": 1.24, "sma20": 1.2, "sma50": 1.15,
        "bid_depth": 1e5, "ask_depth": 8e4, "supports": [(1.05, 1.1)], "resistances": [(1.4, 1.45)],
        "structure": "Uptrend", "ohlcv": {}, "ohlcv_raw": [],
    }


def test_the_analysis_card_prints_a_dash_not_a_neutral_reading():
    out = render_analysis_card(_analysis(None, None), None)
    assert "RSI: —" in out
    assert "ATR: —" in out
    assert "50.0" not in out
    assert "$0.00" not in out
    with_numbers = render_analysis_card(_analysis(41.2, 0.031), None)
    assert "RSI: 41.2" in with_numbers


def test_the_comparison_table_prints_a_dash_for_the_unread_asset():
    out = render_comparison_table([_analysis(None, None), _analysis(58.0, 0.02)])
    assert "—" in out
    assert "58.0" in out
    assert "50.0" not in out


def _code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    return " ".join(tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                    if tok.type != tokenize.COMMENT)


def test_the_position_details_card_reads_through_the_seam():
    from tests.source_scan import handler_sources
    # Every file the handler class is made of: _handle_callback lives in the
    # callback mixin since the handler split.
    code = "\n".join(_code_only(p) for p in handler_sources())
    i = code.find("async def _handle_callback")
    assert i >= 0, "the dispatcher is defined in no file of the handler class"
    body = code[i:]
    assert "market_context_line ( adata )" in body, "the inline RSI line is back"
    assert "rsi_label ( adata . get ( \"rsi\" ) )" in body
    assert "adata . get ( 'rsi' , 0 )" not in body and 'adata . get ( "rsi" , 0 )' not in body, (
        "an absent RSI defaulted to 0 renders as oversold")
