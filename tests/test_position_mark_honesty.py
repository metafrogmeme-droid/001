"""An unfetchable price shown as +0.00% beside a green stripe.

That sentence is in this repo's CLAUDE.md as a named past incident. It was
still live in `/open_positions`, on the surface where being wrong costs the
most:

    try:
        tk = await exchange.fetch_ticker(p.symbol)
        ...
    except Exception:
        pass
    ...
    last_price = prices.get(pos.symbol, pos.entry_price)

A failed ticker fetch fell back to the ENTRY price. Every number derived
from the mark then came out exactly zero — pnl_pct 0.0, pnl_usd 0.0 — and
the card printed "+0.0% ($0.00)" over a position whose current price had
never been read. An operator reading break-even on a position that is
actually deep red leaves it open.

The reason it survived so long is the corollary this file is really about:

    TEST `is None`, NOT FALSINESS.

0.0 is falsy and 0.0 is a real, measured, break-even position. A renderer
handed a zero cannot tell the two apart, so the distinction has to be made
where the fetch fails and carried through as None.

Four states, four different renderings:
  read and profitable   -> number, dollars, colour
  read and break-even   -> number, dollars, NO colour (it is neither)
  read and losing       -> number, dollars, colour
  never read            -> no number, no dollars, no colour, and it says so
"""
from __future__ import annotations

from bot.formatters.rich_cards import render_open_positions

_BASE = dict(pair="BTCUSDT", direction="LONG", entry=100.0, size_usd=50.0,
             leverage=10, sl=95.0, tp=110.0, hold_hours=2.0)


def _row(**over):
    return dict(_BASE, **over)


def _read(pnl, usd, cur):
    return _row(current=cur, pnl_pct=pnl, pnl_usd=usd)


def _unread(**over):
    return _row(price_unavailable=True, current=None, pnl_pct=None,
                pnl_usd=None, rr_live=None, **over)


class TestAnUnreadMarkIsNeverBreakEven:
    def test_it_prints_no_percentage(self):
        out = render_open_positions([_unread()])
        assert "0.0%" not in out and "0.00%" not in out, (
            "a price that was never fetched must not render as a measured "
            "break-even"
        )

    def test_it_prints_no_dollar_figure(self):
        assert "$+0.00" not in render_open_positions([_unread()])

    def test_it_says_the_pnl_is_unknown(self):
        assert "P&L unknown" in render_open_positions([_unread()])

    def test_it_says_the_price_could_not_be_read(self):
        assert "price unavailable" in render_open_positions([_unread()])

    def test_it_carries_no_colour(self):
        # Colour is a claim. A green accent says "in profit" as loudly as
        # the number does; unknown gets the muted one.
        out = render_open_positions([_unread()])
        body = out.split("\n\n", 1)[1]      # drop the header line
        assert "\U0001f7e2 " not in body.split("|", 1)[1]
        assert "\U0001f534" not in body.split("|", 1)[1]
        assert "⚪" in out


class TestARealZeroIsStillAMeasurement:
    def test_break_even_renders_as_a_number(self):
        out = render_open_positions([_read(0.0, 0.0, 100.0)])
        assert "+0.0%" in out, (
            "0.0 is falsy and 0.0 is a real break-even position; dropping it "
            "would trade one wrong claim for another"
        )
        assert "P&L unknown" not in out

    def test_break_even_is_distinguishable_from_unread(self):
        assert (render_open_positions([_read(0.0, 0.0, 100.0)])
                != render_open_positions([_unread()]))


class TestTheHeaderAndTheRowsCannotDisagree:
    """One card, one answer about whether a mark was read.

    `render_open_positions` derives a row's dollars from `pnl_pct * size_usd`
    when no `pnl_usd` is supplied; `open_book_return` used to require the
    dollars outright. A position carrying only a percentage therefore rendered

        <b>Open Positions (1)</b> ⚠️ P&L unknown
        🟢 <b>ETHUSDT</b> LONG | 🟢 +10.0% ($+50.00)

    — the warning and its own refutation, two lines apart. "Absent is never a
    measurement" is symmetric: calling a mark we read unknown is a false claim
    too, and it is the one that teaches an operator to ignore the warning.
    """

    def _pct_only(self, pnl_pct=10.0, size=500.0):
        return {"pair": "ETHUSDT", "direction": "LONG", "entry": 100.0,
                "current": 110.0, "pnl_pct": pnl_pct, "size_usd": size,
                "sl": 0, "tp": 0, "hold_hours": 2}

    def test_a_percentage_only_row_is_measured_not_unknown(self):
        out = render_open_positions([self._pct_only()])
        assert "P&L unknown" not in out
        assert "total" in out

    def test_the_derived_total_matches_the_row(self):
        from bot.utils.portfolio_return import open_book_return
        book = open_book_return([self._pct_only(pnl_pct=10.0, size=500.0)])
        assert book["pnl_usd"] == 50.0     # what the row prints
        assert round(book["pct"], 6) == 10.0
        assert book["measured"] == 1 and book["unmeasured"] == 0

    def test_an_explicitly_unavailable_price_is_still_unknown(self):
        # The derivation must not resurrect a row the caller flagged dead.
        p = self._pct_only()
        p["price_unavailable"] = True
        from bot.utils.portfolio_return import open_book_return
        assert open_book_return([p])["unmeasured"] == 1

    def test_a_row_with_neither_dollars_nor_percent_stays_unknown(self):
        from bot.utils.portfolio_return import open_book_return
        p = self._pct_only()
        p.pop("pnl_pct")
        assert open_book_return([p])["unmeasured"] == 1

    def test_explicit_dollars_still_win_over_the_derivation(self):
        from bot.utils.portfolio_return import open_book_return
        p = self._pct_only(pnl_pct=10.0, size=500.0)
        p["pnl_usd"] = 7.25          # the venue's own figure
        assert open_book_return([p])["pnl_usd"] == 7.25


class TestTheTotalDoesNotAbsorbTheUnknown:
    def test_an_unread_row_contributes_nothing_to_the_total(self):
        both = render_open_positions([_read(100.0, 50.0, 110.0), _unread(pair="ETHUSDT")])
        # 100 alone, not 100 averaged/summed against a fabricated 0.
        assert "+100.0% total" in both

    def test_a_partial_total_discloses_what_it_omitted(self):
        both = render_open_positions([_read(100.0, 50.0, 110.0), _unread(pair="ETHUSDT")])
        # Asserted against the ONE place the sentence lives, not a retyped
        # copy of it. This test asserted the literal "excludes 1 position"
        # and went red when the disclosure moved into the shared helper —
        # the card was correct throughout.
        from bot.utils.portfolio_return import coverage_note
        assert coverage_note({"measured": 1, "unmeasured": 1, "total": 2},
                             html=False).strip() in both

    def test_a_complete_total_says_nothing_extra(self):
        # A caveat on every healthy card is how a real one gets skipped.
        out = render_open_positions([_read(100.0, 50.0, 110.0)])
        # "excludes" was the OLD wording, so this assertion had become
        # vacuous — passing because the word no longer exists anywhere
        # rather than because the card is quiet. Match what a caveat says now.
        assert "Covers" not in out and "no readable mark" not in out

    def test_all_marks_unread_produces_no_total_at_all(self):
        out = render_open_positions([_unread(), _unread(pair="ETHUSDT")])
        assert "total" not in out.split("\n")[0]
        assert "P&L unknown" in out.split("\n")[0]
        assert "0.0%" not in out


class TestTheHandlerCarriesTheGapRatherThanPapering:
    """The renderer can only be honest about what it is handed."""

    def _src(self):
        from tests.source_scan import code_only
        return code_only(
            open("bot/skills/telegram_handler.py", encoding="utf-8").read())

    def test_the_entry_price_is_no_longer_a_silent_default(self):
        assert "prices.get(pos.symbol, pos.entry_price)" not in self._src(), (
            "defaulting the mark to the entry price is what manufactured the "
            "zero in the first place"
        )

    def test_the_derived_fields_go_absent_when_the_mark_is_absent(self):
        src = self._src()
        for field in ("pnl_pct", "pnl_usd", "sl_dist_pct",
                      "tp_dist_pct", "rr_live"):
            assert f'"{field}": None if _unread' in src, (
                f"{field} is derived from the mark; a zero here is the same "
                f"false claim one field over"
            )

    def test_the_row_is_flagged_for_the_renderer(self):
        assert '"price_unavailable": _unread' in self._src()
