"""One failed order fetch reported every orphan position as unprotected.

`_cmd_open_positions` has two branches. The locally-tracked one was made
honest in an earlier pass — `price_unavailable`, `None if _unread`, and a
comment saying why. The ORPHAN branch, which lists positions the bot did not
open and is discovering on the exchange, was not.

    sl_tp_map = {}
    try:
        open_orders = await exchange.fetch_open_orders()
        ...
    except Exception:
        pass  # Orders fetch not critical

The map is then read per symbol with `.get("sl", 0)`, and the card renders

    sl_str = _fmt_price(sl) if sl and sl > 0 else _none      # -> "None"

So a single failed `fetch_open_orders` left every symbol at 0 and every row
saying **SL None / TP None** — a confident statement that the position has no
stop-loss. "Not critical" is true of the listing and false of every claim
built on it.

WHY THIS ONE RANKS. CLAUDE.md says to rank candidates by what a wrong claim
costs, and this is the most expensive claim the product makes: whether live
money is protected. It is shown on the orphan list specifically, which the
operator is reading *because they do not know what is out there*.

THE RED HERRING, planted below: a position that genuinely has no protective
order. It must still say "None" — that is a real finding and the reason the
line exists. A fix that renders every unread stop as "unknown" is only correct
if it leaves the true negative alone.

Three states, three sentences:

    SL $49,000.00 ... on exchange   a stop exists, here it is
    SL None                         the venue answered, and there is no stop
    SL unknown                      the order book could not be read
"""

from __future__ import annotations

import re

import pytest

from bot.formatters.rich_cards import _fmt_price, render_open_positions

ROW = dict(
    pair="BTCUSDT", direction="LONG", entry=50000.0, current=51000.0,
    pnl_pct=2.0, pnl_usd=20.0, sl=49000.0, tp=53000.0, sl_dist_pct=3.9,
    tp_dist_pct=3.9, size_usd=100.0, notional_usd=1000.0, leverage=10.0,
    rr_live=1.0, quantity=0.02, comm_pct=0.06, hold_hours=5.0,
    sl_order="exchange", tp_order="exchange", trade_id="t1", status="open",
)


def card(**over) -> str:
    return re.sub(r"<[^>]+>", "", render_open_positions([dict(ROW, **over)]))


# ── the three states are three different sentences ──────────────────────────

class TestAStopThatCouldNotBeReadIsNotAMissingStop:
    def test_unreadable_orders_say_unknown(self):
        out = card(sl=None, tp=None, sl_order="unknown", tp_order="unknown",
                   untracked=True)
        assert "SL unknown" in out, out
        assert "TP unknown" in out
        assert "SL None" not in out, (
            "an unreadable order book was reported as an unprotected position")

    def test_a_genuinely_unprotected_position_still_says_none(self):
        """THE RED HERRING. This is a real finding and must survive the fix."""
        out = card(sl=0, tp=0, sl_order="none", tp_order="none", untracked=True)
        assert "SL None" in out, out
        assert "unknown" not in out, (
            "a true 'no stop' finding was softened into 'unknown', which is "
            "the opposite error and hides a real exposure")

    def test_a_real_stop_still_prints_its_price(self):
        out = card()
        assert "SL $49,000.00" in out
        assert "on exchange" in out

    def test_the_three_states_are_mutually_distinguishable(self):
        seen = {
            card(),
            card(sl=0, tp=0, sl_order="none", tp_order="none"),
            card(sl=None, tp=None, sl_order="unknown", tp_order="unknown"),
        }
        assert len(seen) == 3, "two of the three states render identically"


# ── the mark price ──────────────────────────────────────────────────────────

class TestNoPriceIsInvented:
    def test_an_unread_mark_is_not_zero(self):
        # The orphan branch fell back to `0` after deliberately refusing to
        # fall back to the entry price — the same assertion with a different
        # wrong number. It escaped "$0.00" only when the venue ALSO omitted
        # unrealizedPnl, which is what set the renderer's `_unread`.
        out = card(current=None, price_unavailable=True, pnl_pct=None,
                   pnl_usd=None)
        assert "$0.00" not in out, out
        assert "price unavailable" in out

    def test_the_pnl_cell_carries_no_colour_when_the_mark_is_unread(self):
        """Colour is a claim: a green accent beside an unknown says "in profit"
        as loudly as a number would.

        Scoped to the P&L cell on purpose. The row also opens with a 🟢, and
        the first draft of this test asserted no green anywhere and failed on
        it — but that glyph is `d_icon`, which encodes DIRECTION (🟢 long /
        🔴 short), and the direction of a position whose mark we could not read
        is still perfectly well known. Not every match is a defect; asserting
        it away would have removed a true statement to satisfy a rule about
        false ones.
        """
        out = card(current=None, price_unavailable=True, pnl_pct=None,
                   pnl_usd=None)
        pnl_cell = out.split("|")[1]
        assert "🟢" not in pnl_cell and "🔴" not in pnl_cell, pnl_cell
        assert "⚪" in pnl_cell, pnl_cell
        # And the header total, which aggregates the same unknown.
        assert "P&L unknown" in out.splitlines()[0]

    @pytest.mark.parametrize("bad", [None, float("nan"), "", "abc"])
    def test_the_price_formatter_never_invents_a_number(self, bad):
        assert _fmt_price(bad) == "—", f"{bad!r} rendered as a price"

    def test_the_price_formatter_still_formats_prices(self):
        assert _fmt_price(50000) == "$50,000.00"
        assert _fmt_price(0.5) == "$0.50000"

    def test_a_real_zero_price_is_still_formatted(self):
        # Not reachable for a live mark, but the formatter must not start
        # treating a genuine 0 as absent — that is the mirror-image defect.
        assert _fmt_price(0) == "$0.000000"


# ── age ─────────────────────────────────────────────────────────────────────

def test_an_orphan_with_no_timestamp_is_not_just_opened():
    out = card(hold_hours=None)
    assert "0m" not in out, f'an unknown age rendered as "just opened":\n{out}'
    assert "?" in out


def test_a_real_age_still_renders():
    assert "5.0h" in card(hold_hours=5.0)


# ── the producer keeps the two facts apart ──────────────────────────────────

class TestTheHandlerStillPassesTheDistinctionIn:
    """`orphan_position_row` can only keep "unread" apart from "no stop" if the
    caller tells it which happened, and that wiring stays in the async handler
    around live exchange calls.

    Source-scanned deliberately. CLAUDE.md's rule is that a scan is for shapes
    a unit test cannot reach — naming "a guard being *reached* at every call
    site" as the example — and this is that: the behaviour is covered above,
    what is locked here is that the flag exists and is threaded through. A
    mutation reverting either line passes every behavioural test in this file,
    which is how these two came to be written.
    """

    def _src(self) -> str:
        from tests.source_scan import code_only
        return code_only(open("bot/skills/telegram_handler.py",
                              encoding="utf-8").read())

    def test_the_orders_fetch_failure_is_recorded(self):
        assert "_orders_read = False" in self._src(), (
            "the orders fetch fails silently again, so an empty map is "
            "indistinguishable from a book that genuinely holds no stops")

    def test_the_map_is_only_trusted_when_it_was_read(self):
        src = self._src()
        assert 'sl_price=(sym_orders.get("sl", 0) if _orders_read else None)' in src
        assert 'tp_price=(sym_orders.get("tp", 0) if _orders_read else None)' in src


class TestTheRowBuilderItself:
    """The row builder was inline in an async handler wrapped around live
    exchange calls, so nothing could plant a venue response and assert what the
    row said. The first thing extracting it found was that a mutation reverting
    the mark price to `0` passed the entire suite — the renderer was well
    covered and the producer was covered only by grep.
    """

    VENUE = {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.02,
             "entryPrice": 50000.0, "notional": 1000.0, "initialMargin": 100.0,
             "leverage": 10, "unrealizedPnl": 20.0, "timestamp": 1755000000000}

    def _row(self, **kw):
        from bot.formatters.orphan_position import orphan_position_row
        kw.setdefault("mark", 51000.0)
        kw.setdefault("sl_price", 49000.0)
        kw.setdefault("tp_price", 53000.0)
        kw.setdefault("commission_pct", 0.06)
        pos = kw.pop("pos", self.VENUE)
        return orphan_position_row(pos, **kw)

    def test_an_unread_order_book_is_unknown_not_none(self):
        r = self._row(sl_price=None, tp_price=None)
        assert r["sl"] is None and r["sl_order"] == "unknown"
        assert r["tp"] is None and r["tp_order"] == "unknown"

    def test_a_read_book_with_no_stops_is_none(self):
        """THE RED HERRING at the producer level."""
        r = self._row(sl_price=0.0, tp_price=0.0)
        assert r["sl_order"] == "none", "a real finding was softened to unknown"
        assert r["sl"] == 0

    def test_a_found_stop_is_reported_with_its_price(self):
        r = self._row()
        assert r["sl"] == 49000.0 and r["sl_order"] == "exchange"

    def test_an_unread_mark_is_none_not_zero(self):
        r = self._row(mark=None)
        assert r["current"] is None, "an unread mark became a $0.00 price"
        assert r["price_unavailable"] is True

    def test_a_read_mark_is_carried(self):
        r = self._row(mark=51000.0)
        assert r["current"] == 51000.0 and r["price_unavailable"] is False

    def test_a_zero_mark_is_treated_as_unread(self):
        # A venue quoting 0 for a live perp is not a price.
        assert self._row(mark=0.0)["price_unavailable"] is True

    def test_an_omitted_unrealized_pnl_is_unknown(self):
        pos = dict(self.VENUE)
        pos.pop("unrealizedPnl")
        r = self._row(pos=pos)
        assert r["pnl_usd"] is None and r["pnl_pct"] is None

    def test_a_reported_zero_pnl_is_a_real_break_even(self):
        r = self._row(pos=dict(self.VENUE, unrealizedPnl=0.0))
        assert r["pnl_usd"] == 0.0, "a measured break-even was discarded"
        assert r["pnl_pct"] == 0.0

    def test_a_missing_timestamp_leaves_the_age_unknown(self):
        pos = dict(self.VENUE)
        pos.pop("timestamp")
        assert self._row(pos=pos)["hold_hours"] is None

    @pytest.mark.parametrize("field", ["entryPrice", "notional",
                                       "initialMargin", "leverage"])
    def test_no_omitted_venue_field_becomes_a_number(self, field):
        pos = dict(self.VENUE)
        pos.pop(field)
        r = self._row(pos=pos)
        key = {"entryPrice": "entry", "notional": "notional_usd",
               "initialMargin": "size_usd", "leverage": "leverage"}[field]
        assert r[key] is None, f"{field} absent became {r[key]!r}"

    def test_an_orphan_has_no_risk_reward(self):
        # It carries no thesis, so there is no reward target to measure
        # against. 0 is a ratio; this is the absence of one.
        assert self._row()["rr_live"] is None

    def test_the_row_survives_a_wholly_empty_venue_response(self):
        r = self._row(pos={}, mark=None, sl_price=None, tp_price=None)
        assert r["sl_order"] == "unknown"
        assert r["entry"] is None and r["current"] is None

    def test_the_row_renders_without_inventing_anything(self):
        out = re.sub(r"<[^>]+>", "", render_open_positions([
            self._row(pos={}, mark=None, sl_price=None, tp_price=None)]))
        assert "$0.00" not in out, out
        assert "SL unknown" in out
        assert "0m" not in out
