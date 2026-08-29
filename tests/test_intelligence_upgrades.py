"""Tests for the three intelligence-layer upgrades:
  12. SentimentAnalyzer   (sentiment.py)
  13. On-chain flow methods on SmartMoneyEngine (smart_money.py)
"""

import pytest

from bot.core.sentiment import SentimentAnalyzer
from bot.core.smart_money import SmartMoneyEngine


# ======================================================================
# 11. Trade Outcome Feedback Loop
# ======================================================================

def _make_features(**overrides):
    base = {"adx": 25.0, "momentum": 0.5, "regime": "trending",
            "confidence": 0.7, "hurst": 0.55, "volume_ratio": 1.2}
    base.update(overrides)
    return base


def _make_outcome(won=True, pnl_pct=2.0, r_multiple=1.5, hold_bars=10):
    return {"pnl_pct": pnl_pct, "r_multiple": r_multiple,
            "won": won, "hold_bars": hold_bars}


class TestSentimentFunding:
    def test_extreme_positive_funding_bearish(self):
        result = SentimentAnalyzer.analyze_funding_rate(0.05)
        assert result["bias"] == "BEARISH"
        assert result["signal_strength"] == pytest.approx(0.5, abs=0.01)

    def test_extreme_negative_funding_bullish(self):
        result = SentimentAnalyzer.analyze_funding_rate(-0.08)
        assert result["bias"] == "BULLISH"
        assert result["signal_strength"] == pytest.approx(0.8, abs=0.01)

    def test_neutral_funding(self):
        result = SentimentAnalyzer.analyze_funding_rate(0.01)
        assert result["bias"] == "NEUTRAL"

    def test_boundary_positive(self):
        result = SentimentAnalyzer.analyze_funding_rate(0.03)
        assert result["bias"] == "NEUTRAL"

    def test_boundary_negative(self):
        result = SentimentAnalyzer.analyze_funding_rate(-0.03)
        assert result["bias"] == "NEUTRAL"


class TestSentimentLongShort:
    def test_extreme_long_ratio(self):
        result = SentimentAnalyzer.analyze_long_short_ratio(3.0)
        assert result["bias"] == "BEARISH"

    def test_extreme_short_ratio(self):
        result = SentimentAnalyzer.analyze_long_short_ratio(0.2)
        assert result["bias"] == "BULLISH"

    def test_balanced_ratio(self):
        result = SentimentAnalyzer.analyze_long_short_ratio(1.0)
        assert result["bias"] == "NEUTRAL"


class TestSentimentComposite:
    def test_contrarian_alert_extreme_funding(self):
        result = SentimentAnalyzer.composite_sentiment(
            funding_rate=0.08, long_short_ratio=1.0, fear_greed=50,
        )
        assert result["contrarian_alert"] is True

    def test_no_contrarian_calm_market(self):
        result = SentimentAnalyzer.composite_sentiment(
            funding_rate=0.01, long_short_ratio=1.0, fear_greed=50,
        )
        assert result["contrarian_alert"] is False

    def test_extreme_greed_contrarian(self):
        result = SentimentAnalyzer.composite_sentiment(
            funding_rate=0.05, long_short_ratio=2.5, fear_greed=90,
        )
        assert result["overall_bias"] == "BEARISH"
        assert result["contrarian_alert"] is True

    def test_telegram_format(self):
        result = SentimentAnalyzer.composite_sentiment(0.05, 2.5, 90)
        html = SentimentAnalyzer.format_for_telegram(result)
        assert "<b>SENTIMENT WAR ROOM</b>" in html
        assert "CONTRARIAN ALERT" in html


# ======================================================================
# 13. On-Chain Flow Signals (SmartMoneyEngine static methods)
# ======================================================================

class TestExchangeFlow:
    def test_large_outflow_bullish(self):
        result = SmartMoneyEngine.analyze_exchange_flow(
            net_flow_btc=-2000.0, avg_daily_flow=1000.0,
        )
        assert result["flow_signal"] == "BULLISH"
        assert "accumulation" in result["interpretation"].lower()

    def test_large_inflow_bearish(self):
        result = SmartMoneyEngine.analyze_exchange_flow(
            net_flow_btc=2000.0, avg_daily_flow=1000.0,
        )
        assert result["flow_signal"] == "BEARISH"
        assert "distribution" in result["interpretation"].lower()

    def test_normal_flow_neutral(self):
        result = SmartMoneyEngine.analyze_exchange_flow(
            net_flow_btc=500.0, avg_daily_flow=1000.0,
        )
        assert result["flow_signal"] == "NEUTRAL"

    def test_zero_avg_flow(self):
        result = SmartMoneyEngine.analyze_exchange_flow(
            net_flow_btc=-100.0, avg_daily_flow=0.0,
        )
        assert result["flow_signal"] == "NEUTRAL"


class TestWhaleActivity:
    def test_elevated_activity(self):
        result = SmartMoneyEngine.analyze_whale_activity(
            large_tx_count=30, avg_large_tx=15,
        )
        assert result["signal"] == "ACTIVE"
        assert result["activity_ratio"] == pytest.approx(2.0)

    def test_quiet_activity(self):
        result = SmartMoneyEngine.analyze_whale_activity(
            large_tx_count=5, avg_large_tx=15,
        )
        assert result["signal"] == "QUIET"

    def test_zero_baseline(self):
        result = SmartMoneyEngine.analyze_whale_activity(
            large_tx_count=10, avg_large_tx=0,
        )
        assert result["signal"] == "NEUTRAL"


class TestCompositeFlowSignal:
    def test_bullish_composite(self):
        flow = SmartMoneyEngine.analyze_exchange_flow(-2000.0, 1000.0)
        whale = SmartMoneyEngine.analyze_whale_activity(30, 15)
        result = SmartMoneyEngine.composite_flow_signal(flow, whale, oi_change_pct=5.0)
        assert result["bias"] == "BULLISH"
        assert result["confidence"] > 0

    def test_bearish_composite(self):
        flow = SmartMoneyEngine.analyze_exchange_flow(2000.0, 1000.0)
        whale = SmartMoneyEngine.analyze_whale_activity(25, 15)
        result = SmartMoneyEngine.composite_flow_signal(flow, whale, oi_change_pct=-3.0)
        assert result["bias"] == "BEARISH"

    def test_neutral_composite(self):
        flow = SmartMoneyEngine.analyze_exchange_flow(100.0, 1000.0)
        whale = SmartMoneyEngine.analyze_whale_activity(10, 15)
        result = SmartMoneyEngine.composite_flow_signal(flow, whale, oi_change_pct=0.0)
        assert result["bias"] == "NEUTRAL"
        assert "factors" in result
