"""The close CARD is the other surface making the same claim.

Making `_close_position_inner` publish a null PnL is half a fix. The PNG
close card read it with `.get("pnl_usd", 0)`, which defaults a MISSING key
and passes a PRESENT None straight through — into `None >= 0`, a TypeError
in the caption path and a swallowed exception in the card path. Had it not
raised, `is_win` would have been the truthiness of a value nobody read, and
the accent stripe, the hero row, the EXIT cell and the NET PnL cell would
all have been GREEN.

The POSITION card in the same file already carries this conversion, with a
comment describing the identical bug. The close card never got it. That is
the corollary in the standard: ask which OTHER surface makes the same claim,
before calling the fix done.
"""
import pytest

from bot.formatters.signal_card import (
    _GREEN,
    _MUTED,
    _RED,
    humanize_close_reason,
    render_close_card,
)

BASE = {
    "symbol": "TRUMP/USDT", "direction": "SHORT", "entry": 2.2390,
    "size_usd": 175.0, "leverage": 20, "hold_time": "0m",
}
UNPRICED = dict(BASE, reason="CLOSED (unknown)", exit=None, pnl_pct=None,
                pnl_pct_margin=None, pnl_usd=None, fees=None, confirmed=False)
PRICED = dict(BASE, reason="sl", exit=2.20, pnl_pct=-1.7, pnl_pct_margin=-34.0,
              pnl_usd=-5.9, fees=0.21, confirmed=True)


class TestTheVerdictEmoji:
    def test_a_win_is_a_win(self):
        assert humanize_close_reason("CLOSED (unknown)", 5.0)[0] == "✅"

    def test_a_loss_is_a_loss(self):
        assert humanize_close_reason("CLOSED (unknown)", -5.0)[0] == "❌"

    def test_a_measured_break_even_still_scores(self):
        # 0.0 is a reading. It is not a gain, and ✅/❌ is a two-way switch —
        # what matters is that it does NOT fall to the unknown branch.
        assert humanize_close_reason("CLOSED (unknown)", 0.0)[0] == "✅"

    def test_an_unread_pnl_asserts_neither(self):
        assert humanize_close_reason("CLOSED (unknown)", None)[0] == "⚪"

    def test_a_named_mechanism_is_unaffected_by_a_null_pnl(self):
        # The reason is known even when the money is not.
        assert humanize_close_reason("sl", None)[1] == "Stop-Loss Hit"


class TestTheCardRenders:
    def test_the_unpriced_card_does_not_raise(self):
        # It used to: `None >= 0`, swallowed by the caller's bare except, so
        # the card silently vanished and the text fell through instead.
        assert len(render_close_card(UNPRICED)) > 0

    def test_the_priced_card_still_renders(self):
        assert len(render_close_card(PRICED)) > 0

    def test_the_two_differ(self):
        assert render_close_card(UNPRICED) != render_close_card(PRICED)

    @pytest.mark.parametrize("field", ["pnl_usd", "pnl_pct", "pnl_pct_margin",
                                       "exit", "fees"])
    def test_any_single_null_field_is_survivable(self, field):
        assert len(render_close_card(dict(PRICED, **{field: None}))) > 0

    def test_a_missing_key_is_survivable_too(self):
        d = dict(PRICED)
        d.pop("pnl_usd")
        assert len(render_close_card(d)) > 0


class TestColourIsAClaim:
    """Read the accent stripe out of the rendered pixels, not out of source."""

    def _stripe(self, data):
        from io import BytesIO

        from PIL import Image
        img = Image.open(BytesIO(render_close_card(data))).convert("RGB")
        return img.getpixel((img.width // 2, 1))

    def test_a_loss_is_red(self):
        assert self._stripe(PRICED) == _RED

    def test_a_win_is_green(self):
        assert self._stripe(dict(PRICED, pnl_usd=5.9, pnl_pct=1.7,
                                 pnl_pct_margin=34.0)) == _GREEN

    def test_an_unread_close_is_neither(self):
        stripe = self._stripe(UNPRICED)
        assert stripe == _MUTED
        assert stripe not in (_GREEN, _RED)
