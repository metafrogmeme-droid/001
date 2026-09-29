"""
Deep-scan patterns card.

render_patterns_card() turns the deep-scan pattern observations (per-symbol
chart + candle patterns) into a PNG, mirroring the text readout — so the scan
can be shown as an image like every other scan.

This file also pinned `_true_range_atr`, the ATR the deep scan computed for
its stop/target setups. Those setups are gone: a deep scan decides no
direction, so it has no trade to draw (see
`tests/test_the_deepscan_push_publishes_only_what_it_read.py`), and a helper
with no caller is a claim that something still needs it.
"""

from bot.formatters.signal_card import render_patterns_card


_HITS = [
    {
        "symbol": "ETH/USDT", "price": 1582.52, "chg": -0.04, "rsi": 51,
        "vol_spike": False,
        "chart_patterns": [
            {"name": "S/R Flip (Support -> Resistance)", "signal": "bearish", "confidence": 0.70},
            {"name": "Elliott ABC Expanded Flat", "signal": "bullish", "confidence": 0.70},
            {"name": "Wyckoff Accumulation", "signal": "bullish", "confidence": 0.70},
        ],
        "candle_patterns": {"doji": "neutral", "spinning_top": "neutral"},
    },
    {
        "symbol": "OPENAI/USDT", "price": 1336.06, "chg": -0.1, "rsi": 26,
        "vol_spike": True,
        "chart_patterns": [
            {"name": "Double Top", "signal": "bearish", "confidence": 0.71},
            {"name": "Wyckoff Accumulation", "signal": "bullish", "confidence": 0.70},
        ],
        "candle_patterns": {},
    },
]


def _is_png(b: bytes) -> bool:
    return isinstance(b, bytes) and b[:4] == b"\x89PNG"


class TestPatternsCard:
    def test_renders_png(self):
        png = render_patterns_card(_HITS, scan_label="DEEP SCAN 4H",
                                   timestamp="15:31 UTC", subtitle="2 hits · 4h")
        assert _is_png(png)
        assert len(png) > 1000

    def test_empty_is_safe(self):
        png = render_patterns_card([])
        assert _is_png(png)  # still a valid image, not a crash

    def test_tolerates_missing_fields(self):
        # Only a symbol — no price/rsi/patterns. Must not raise.
        png = render_patterns_card([{"symbol": "BTC/USDT"}])
        assert _is_png(png)

    def test_respects_max_symbols(self):
        many = [dict(_HITS[0], symbol=f"S{i}/USDT") for i in range(20)]
        # Should render without error and cap internally.
        png = render_patterns_card(many, max_symbols=5)
        assert _is_png(png)

    def test_handles_high_and_low_confidence(self):
        hits = [{
            "symbol": "X/USDT", "price": 1.0, "chg": 0.0, "rsi": 50,
            "chart_patterns": [
                {"name": "A", "signal": "bullish", "confidence": 0.0},
                {"name": "B", "signal": "bearish", "confidence": 1.0},
                {"name": "C", "signal": "neutral", "confidence": 1.5},  # clamps
            ],
            "candle_patterns": {"hammer": "bullish"},
        }]
        assert _is_png(render_patterns_card(hits))

