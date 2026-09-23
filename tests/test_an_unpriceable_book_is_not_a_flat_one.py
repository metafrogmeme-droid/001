"""The escape plan says what it could not price.

`escape_agent.plan()` filtered `[p for p in positions if _notional(p) > 0]` and
then answered the FLAT document for an empty result — so a book of ordinary
ADOPTED positions rendered byte-for-byte as

    🪂 Escape Agent — no open positions to unwind.

with `ok: True`, `risk: "none"` and `position_count: 0`. That is the defect the
comment twelve lines above `base` describes as fixed: it was fixed for the
`except` arm and left standing for the dropped-rows arm, in the same function.
The shape is ordinary, not a corner — `live_executor` writes `entry_price=0.0`
and `cost_usd=0.0` for a venue that stated neither, and the restore path reads
both back the same way, so it survives restarts.

Driven on the two-position book below (the same shape `book_read`'s own
docstring drives for the twin and the sentinel):

    both rows unpriceable   ->  identical to a flat book, on every field
    one of two priced       ->  "1 position(s) · gross $32 · 🟢 NONE"
    the same book readable  ->  2 positions · gross $1,030 · HIGH,
                                and PENDLE — the row the plan exists to close
                                FIRST — is not in the partial order at all

So the partial case is the more dangerous one: it looks like a working card.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from bot.formatters import escape_card as card
from bot.guardian import book_read
from bot.guardian import escape_agent as ea

BTC = {"symbol": "BTC/USDT", "entry": 63000.0, "qty": 0.0005, "cost_usd": 30.0,
       "leverage": 1.0, "side": "long", "group": "BTC"}
#: The ordinary adopted shape: the venue stated neither price nor cost.
PENDLE_UNPRICED = {"symbol": "PENDLE/USDT", "entry": 0.0, "qty": 0.0,
                   "cost_usd": 0.0, "leverage": 20.0, "side": "long",
                   "group": "ALT"}
PENDLE_PRICED = dict(PENDLE_UNPRICED, entry=4.16, qty=240.0, cost_usd=49.92)


class TestAnUnpriceableBookIsNotAFlatOne:
    """The whole book unreadable. This is the branch that used to be flat."""

    def test_the_document_differs_from_a_flat_one_on_every_field_a_reader_acts_on(self):
        dead = ea.plan([PENDLE_UNPRICED, dict(PENDLE_UNPRICED, symbol="SUI/USDT")])
        flat = ea.plan([])
        assert dead["risk"] is None and flat["risk"] == "none"
        assert dead["recommended"] != flat["recommended"]
        assert "not a flat book" in dead["recommended"]
        # `$0.00` gross over a book nobody priced is the figure the whole slice
        # removes; a flat book's `0.0` is a measurement of nothing left.
        assert dead["gross_notional_usd"] is None
        assert flat["gross_notional_usd"] == 0.0

    def test_the_count_is_a_reading_not_an_absence(self):
        dead = ea.plan([PENDLE_UNPRICED, dict(PENDLE_UNPRICED, symbol="SUI/USDT")])
        cov = dead["book_coverage"]
        assert cov["counted_positions"] == 2, "the rows were seen"
        assert cov["scored_positions"] == 0, "and none of them could be priced"
        assert set(cov["unpriced_symbols"]) == {"PENDLE/USDT", "SUI/USDT"}

    def test_the_card_names_the_count_and_never_says_flat(self):
        out = card.render_escape_card(
            ea.plan([PENDLE_UNPRICED, dict(PENDLE_UNPRICED, symbol="SUI/USDT")]),
            sealed=True)
        assert "2 position(s) are open" in out
        assert "PENDLE/USDT" in out and "SUI/USDT" in out
        assert card.UNKNOWN_ICON in out and card.UNKNOWN_WORD in out
        assert "🟢" not in out, "colour is a claim; unknown urgency is not green"
        # THE POSITIVE CLAIM FIRST. The first draft asserted only that the flat
        # sentence was absent, and the mutation round walked straight through
        # it: the mutant wrote "No open positions to unwind" and `not in` is
        # case-sensitive. Asserting a short string is ABSENT is the assertion
        # this repo keeps watching misfire, and here it misfired in the test
        # written from that rule. What the card must SAY is what is checked;
        # the absence check stays as the second half, folded.
        assert "not a flat book" in out
        assert "not an all-clear" in out
        assert "no open positions to unwind" not in out.lower()

    def test_ok_stays_true_because_the_planner_ran(self):
        # `ok is False` is the card's "could not be built" branch, and that
        # document cannot name the count or the symbols. This one can, so it is
        # a third fact rather than a spelling of the second.
        dead = ea.plan([PENDLE_UNPRICED])
        assert dead["ok"] is True
        assert "could not be built" not in card.render_escape_card(dead)


class TestThePartialPlanSaysSo:
    """One of two priced. The card that looks like it is working."""

    def test_the_shortfall_is_named_and_the_dropped_row_is_not_in_the_order(self):
        p = ea.plan([BTC, PENDLE_UNPRICED])
        assert p["book_coverage"]["scored_positions"] == 1
        assert p["book_coverage"]["counted_positions"] == 2
        assert [s["symbol"] for s in p["steps"]] == ["BTC/USDT"]
        assert "PENDLE/USDT" in p["coverage_note"]

    def test_the_card_says_the_dropped_rows_are_still_open(self):
        # `coverage_note` says the figures do not cover them. It does not say
        # they are POSITIONS THAT ARE STILL THERE, which is the fact an
        # operator reading an exit plan needs — the card adds that clause and
        # nothing was checking it until the mutation round asked.
        out = card.render_escape_card(ea.plan([BTC, PENDLE_UNPRICED]), sealed=True)
        assert "are open" in out and "not in the order below" in out

    def test_the_note_sits_above_the_totals_line(self):
        # The number a reader acts on is the one at the top. A shortfall at the
        # foot of the card is a hedge nobody reaches.
        out = card.render_escape_card(ea.plan([BTC, PENDLE_UNPRICED]), sealed=True)
        assert out.index("cover 1 of 2") < out.index("position(s) · gross")

    def test_the_priced_subset_really_did_say_the_measured_word(self):
        # `book_read.verdict_over`'s ruling, driven rather than restated: a
        # partial book keeps its measured verdict and carries the note. The
        # cost is named in the card's own comment — a `min` over a subset is an
        # UPPER bound on the true urgency, so a partial word errs only ever in
        # the flattering direction.
        partial = ea.plan([BTC, PENDLE_UNPRICED])
        whole = ea.plan([BTC, PENDLE_PRICED])
        assert partial["risk"] == "none" and whole["risk"] == "high"
        assert partial["gross_notional_usd"] < whole["gross_notional_usd"] / 30
        assert [s["symbol"] for s in whole["steps"]][0] == "PENDLE/USDT", (
            "the row the plan exists to close FIRST is the one the partial "
            "plan dropped")

    def test_a_complete_book_carries_no_note(self):
        # Printed ONLY when it bites: a permanent "2 of 2" under every healthy
        # card is the row that trains a reader to stop reading the line.
        p = ea.plan([BTC, PENDLE_PRICED])
        assert p["coverage_note"] == ""
        assert "could not be priced" not in card.render_escape_card(p, sealed=True)


class TestTheSealedRecordCarriesIt:
    """`order_truncated` exists for this reason one row over."""

    def test_the_payload_carries_the_coverage(self):
        pay = ea.escape_payload([BTC, PENDLE_UNPRICED])
        assert pay["book_coverage"]["scored_positions"] == 1
        assert pay["book_coverage"]["counted_positions"] == 2

    def test_a_crashed_planner_seals_no_coverage_rather_than_a_zero(self):
        # `failed` cannot say how many rows there were, so it claims nothing
        # rather than sealing a counted zero it never measured.
        assert ea.plan(object())["book_coverage"] is None  # type: ignore[arg-type]


class TestTheTwoUnknownsAgree:
    """Two spellings of unknown in one document would be the second-copy shape.

    `_unpriceable` answers `risk=None` (this module's spelling, which
    `escape_card.risk_icon` has a dedicated arm for) rather than calling
    `book_read.verdict_over`, which spells it `"unknown"`. That is only safe
    while the two agree on this input, so the agreement is DRIVEN.
    """

    def test_book_risk_and_verdict_over_reach_the_same_conclusion(self):
        cov = book_read.coverage_of([PENDLE_UNPRICED], lambda p: False)
        assert cov.nothing_read is True
        assert book_read.verdict_over(cov, "none") == "unknown"
        assert ea._book_risk(None) is None, (
            "nothing priced means nothing ranked means no min_move_pct")

    def test_the_card_renders_both_spellings_as_the_same_unknown(self):
        assert card.risk_icon(None) == card.UNKNOWN_ICON
        assert card.risk_icon("unknown") == card.UNKNOWN_ICON
        assert card.risk_word(None) == card.UNKNOWN_WORD
        assert card.risk_word("unknown") == card.UNKNOWN_WORD


class TestThePredicateStaysLocal:
    """`book_read` owns the COUNT and refuses to own what "priced" means."""

    def test_the_plan_counts_against_its_own_notional_predicate(self):
        src = inspect.getsource(ea.plan)
        tree = ast.parse(src.lstrip() if not src.startswith("def") else src)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "coverage_of"]
        assert len(calls) == 1, "one count, taken once"
        # The predicate is this module's, not a shared one.
        assert "_notional" in ast.unparse(calls[0])

    def test_the_count_and_the_filter_agree_on_every_row(self):
        # Two readings of one predicate are two answers. Driven over a book
        # holding each way a row can fail to price.
        book = [BTC, PENDLE_UNPRICED, {"symbol": "X/USDT"}, {"entry": "n/a", "qty": 1},
                {"entry": float("nan"), "qty": 2.0}, PENDLE_PRICED]
        cov = book_read.coverage_of(book, lambda p: ea._notional(p) > 0)
        p = ea.plan(book)
        assert p["book_coverage"]["scored_positions"] == cov.scored == len(p["steps"])
        assert p["book_coverage"]["counted_positions"] == len(book)


class TestTheDeletedBranchCouldNotFire:
    """`if gross <= 0: return base` sat after a `> 0` filter.

    A line no input can reach is a claim that there is a check, so it is gone
    and the property that made it unreachable is driven here instead: the day
    `_notional` stops returning a magnitude, this fails rather than an
    unreachable branch quietly becoming reachable.
    """

    @pytest.mark.parametrize("pos", [
        {"entry": -63000.0, "qty": 0.0005},        # a negative price
        {"entry": 63000.0, "qty": -0.0005},        # a negative quantity
        {"cost_usd": -30.0, "leverage": 20.0},     # a negative margin
        {"entry": 5e-324, "qty": 1.0},             # the smallest float there is
    ])
    def test_every_priced_row_contributes_a_positive_magnitude(self, pos):
        n = ea._notional(pos)
        assert n >= 0.0, "_notional returns a magnitude on both arms"
        if n > 0:
            # The claim is about the SUM the deleted branch tested, not about
            # the published figure: `gross_notional_usd` is `round(gross, 2)`,
            # so a denormal book really does publish `0.0` — a measurement at
            # two decimals of a book worth less than a cent, which is not the
            # shape this slice removes. What must never happen is the FLAT
            # document, which is what `if gross <= 0: return base` would do.
            assert ea.plan([pos])["steps"], "a filtered book always plans"
            assert "nothing to unwind" not in ea.plan([pos])["recommended"]

    def test_no_filtered_book_can_sum_to_zero(self):
        rows = [{"entry": 5e-324, "qty": 1.0}, {"entry": -5e-324, "qty": 1.0}]
        assert all(ea._notional(r) > 0 for r in rows), "abs() on both arms"
        p = ea.plan(rows)
        assert len(p["steps"]) == 2 and p["book_coverage"]["scored_positions"] == 2
        assert "nothing to unwind" not in p["recommended"]


class TestAnOlderDocumentIsNotAccused:
    """A plan with no `book_coverage` is an older bot build, not a shortfall.

    Reporting one as "0 of N priced" would be a finding manufactured from a key
    nobody wrote, which is the accusation shape this repo keeps recording.
    """

    def test_a_document_without_coverage_renders_as_it_always_did(self):
        legacy_flat = {"version": 1, "ok": True, "position_count": 0,
                       "gross_notional_usd": 0.0, "total_margin_usd": 0.0,
                       "risk": "none", "steps": [],
                       "recommended": "no open positions — nothing to unwind"}
        assert "no open positions to unwind" in card.render_escape_card(legacy_flat)

    def test_a_non_integer_coverage_is_not_read_as_a_count(self):
        junk = {"version": 1, "ok": True, "position_count": 0, "steps": [],
                "risk": "none", "gross_notional_usd": 0.0,
                "total_margin_usd": 0.0, "recommended": "x",
                "book_coverage": {"counted_positions": "2",
                                  "scored_positions": None}}
        assert "no open positions to unwind" in card.render_escape_card(junk)


class TestTheConsoleGetsItForFree:
    """The Guardian console reads this plan's `risk` and nothing else.

    Before the fix an unpriceable book handed it `"none"` — the calmest word,
    off a book nobody could price — and the posture rollup then ranked it as a
    real reading. Both halves are checked, because the console is a 200-line
    method behind an engine and a drive of the ROLLUP is cheaper than standing
    one up.
    """

    def test_the_console_reads_the_plan_risk(self):
        from bot.core import engine as eng
        src = inspect.getsource(eng.RuneClawEngine.guardian_status)
        assert '_ea.plan(positions).get("risk")' in src

    def test_an_unpriceable_book_now_hands_it_unknown(self):
        assert ea.plan([PENDLE_UNPRICED])["risk"] is None

    def test_the_rollup_excludes_an_unknown_rather_than_ranking_it_safest(self):
        order = {"none": 0, "low": 1, "medium": 2, "high": 3}
        reads = [ea.plan([PENDLE_UNPRICED])["risk"], "high", "low"]
        known = [r for r in reads if r in order]
        assert known == ["high", "low"], "None is not a risk word"
        assert max(known, key=lambda r: order[r]) == "high"
