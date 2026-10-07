"""Ten quiet windows after a whale do not drop the whale voter.

The consistency amplifier divides by the windows in the last ten where a
whale traded. One whale window followed by ten read-but-quiet ones left that
list empty: ZeroDivisionError, swallowed by the caller's broad handler, and
the whale voter left the score without a trace while the rolling bias over
the older window was a real reading.
"""
from types import SimpleNamespace

from bot.core.smart_money import WhaleFlowTracker


def _sig(buy, sell):
    return SimpleNamespace(symbol="BTC/USDT", whale_buy_usd=buy, whale_sell_usd=sell,
                           components_ok=["trades"])


def test_a_whale_then_ten_quiet_windows_is_still_a_reading():
    t = WhaleFlowTracker()
    t.evaluate(_sig(5_000_000.0, 1_000_000.0))
    out = None
    for _ in range(10):
        out = t.evaluate(_sig(0.0, 0.0))
    # (5 - 1) / 6, with no recent window to call consistent: not amplified.
    assert out == round(4 / 6, 4)


def test_consistent_recent_buying_is_still_amplified():
    t = WhaleFlowTracker()
    out = None
    for _ in range(10):
        out = t.evaluate(_sig(3_000_000.0, 1_000_000.0))
    assert out == round(min(1.0, 0.5 * 1.3), 4)
