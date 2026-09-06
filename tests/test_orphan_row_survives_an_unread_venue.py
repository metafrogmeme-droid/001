"""`/open_positions` died on the positions it exists to show.

`orphan_position_row` returns an explicit ``None`` for every field the venue
did not report — margin, leverage, R:R, age, the stop distances. The consumer
read them with ``pos.get("hold_hours", 0)``.

**`.get(key, default)` does not substitute for a stored None.** Every default
was dead, the None flowed on, and ``if hold_h < 1:`` raised TypeError. The
loop has no try/except, so one such position killed the WHOLE listing — on
the command an operator runs precisely because they do not know what is out
there.

The `pnl_pct`/`pnl_usd` reads two lines above had already been fixed for this
exact reason, with a comment saying so. Fixing the two lines left the surface
half-cured. CLAUDE.md's own note about this function — "the mutation that
reverted the mark price to 0 had passed the ENTIRE suite before it existed" —
was about the producer; this is the consumer.
"""
import pytest

from bot.formatters.orphan_position import orphan_position_row
from bot.formatters.signal_card import render_position_card

VENUE_SAYS_NOTHING = {"symbol": "TRUMP/USDT", "side": "short", "contracts": 78.0}


@pytest.fixture
def unread_row():
    return orphan_position_row(VENUE_SAYS_NOTHING, mark=None, sl_price=None,
                               tp_price=None, commission_pct=0.06)


class TestTheProducerStillSaysNothing:
    """If these change, the consumer fix below is aimed at the wrong shape."""

    @pytest.mark.parametrize("field", ["size_usd", "leverage", "rr_live",
                                       "hold_hours", "sl", "tp"])
    def test_an_unreadable_field_is_none_not_zero(self, unread_row, field):
        assert unread_row[field] is None

    def test_get_with_a_default_does_not_rescue_it(self, unread_row):
        # The whole trap, in one line.
        assert unread_row.get("hold_hours", 0) is None
        assert unread_row.get("leverage", 1) is None


class TestTheArithmeticThatKilledTheListing:
    def test_comparing_an_unread_age_raises(self, unread_row):
        hold_h = unread_row.get("hold_hours", 0)
        with pytest.raises(TypeError):
            _ = hold_h < 1

    def test_a_fee_off_an_unread_margin_raises(self, unread_row):
        size_usd = unread_row.get("size_usd", 0)
        with pytest.raises(TypeError):
            _ = size_usd * 0.0006


class TestTheCardRenders:
    def test_an_entirely_unread_orphan_renders(self, unread_row):
        png = render_position_card({
            "symbol": "TRUMP/USDT", "direction": "SHORT", "is_live": True,
            "entry": 2.239, "now": None, "pnl_pct": None, "pnl_usd": None,
            "net_pnl": None, "fees": None, "size_usd": None, "leverage": None,
            "hold_time": "unknown", "rr": None, "sl": None, "tp": None,
            "sl_pct": None, "tp_pct": None,
            "sl_status": "bot-managed", "tp_status": "bot-managed"})
        assert isinstance(png, bytes) and len(png) > 0

    def test_a_fully_read_position_still_renders(self):
        png = render_position_card({
            "symbol": "TRUMP/USDT", "direction": "SHORT", "is_live": True,
            "entry": 2.239, "now": 2.20, "pnl_pct": -1.7, "pnl_usd": -5.9,
            "net_pnl": -6.1, "fees": 0.21, "size_usd": 175.0, "leverage": 20,
            "hold_time": "1.5h", "rr": 1.8, "sl": 2.3, "tp": 2.1,
            "sl_pct": 2.7, "tp_pct": 6.2,
            "sl_status": "on exchange", "tp_status": "on exchange"})
        assert isinstance(png, bytes) and len(png) > 0

    @pytest.mark.parametrize("field", ["size_usd", "leverage", "fees", "rr",
                                       "sl", "tp", "sl_pct", "tp_pct", "now"])
    def test_any_single_unread_field_is_survivable(self, field):
        base = {"symbol": "X/USDT", "direction": "LONG", "is_live": True,
                "entry": 1.0, "now": 1.1, "pnl_pct": 10.0, "pnl_usd": 1.0,
                "net_pnl": 0.9, "fees": 0.1, "size_usd": 10.0, "leverage": 3,
                "hold_time": "2h", "rr": 2.0, "sl": 0.9, "tp": 1.3,
                "sl_pct": 10.0, "tp_pct": 30.0, "sl_status": "", "tp_status": ""}
        assert len(render_position_card(dict(base, **{field: None}))) > 0


class TestTheFormatterGuardsAtTheBoundary:
    """`_fmt(None)` raised in seven of the eight copies in signal_card."""

    def test_no_copy_still_tests_equality_against_zero(self):
        import io

        from tests.source_scan import code_only
        code = code_only(io.open("bot/formatters/signal_card.py",
                                 encoding="utf-8").read())
        # `None == 0` is False, so this shape falls through to `None >= 100`.
        assert "if price == 0:" not in code

    def test_every_copy_guards(self):
        import io

        from tests.source_scan import code_only
        code = code_only(io.open("bot/formatters/signal_card.py",
                                 encoding="utf-8").read())
        assert code.count("def _fmt(price)") == 8
        assert code.count("if not price:") >= 8


class TestTheConsumerWiring:
    def _block(self):
        # Every file the handler class is made of: /open_positions is leaving
        # for the trading mixin, and a scan of one file reads the move as the
        # consumer wiring vanishing.
        from tests.source_scan import code_only, handler_sources
        code = "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())
        i = code.index('sl_order = pos.get("sl_order", "")')
        return code[i - 900:i + 2000]

    @pytest.mark.parametrize("dead", ['pos.get("size_usd", 0)',
                                      'pos.get("leverage", 1)',
                                      'pos.get("rr_live", 0)',
                                      'pos.get("hold_hours", 0)'])
    def test_the_dead_defaults_are_gone(self, dead):
        assert dead not in self._block()

    def test_an_unknown_age_does_not_render_as_just_opened(self):
        # "0m" is a specific claim: this position opened moments ago.
        assert 'hold_str = "unknown"' in self._block()

    def test_an_unknown_fee_is_not_a_free_position(self):
        assert "entry_fee = exit_fee = total_fees = funding_paid = None" in self._block()
