"""The risk sentry says what it could not price.

`risk_sentry.assess` handled `positions is None` correctly — its own docstring
calls it "the sibling that was missed" for the escape plan's flat-book defect —
and then dropped rows inside the walk with no trace:

    if not base or n is None or n <= 0:
        continue

Gross, concentration, crowding and book leverage were all computed over what
was left, and `book_read: True` described the CALL (the list arrived), not the
ROWS. Driven, three open positions whose notional the book could not state
returned `worst_level: "clear"`, `gross_usd: 0` and

    🟢 Risk sentry: nothing flagged in your current posture.

byte-identical to a FLAT book — while the same three rows readable raised a
concentration warning.

HOW IT IS REACHED TODAY is narrower than the escape plan's, and it is driven
below rather than carried over. The sentry's one production caller
(`/gateway/sentry`) reads the PAPER book, which refuses a non-positive entry at
open, so an adopted position's unread entry never arrives here. What does is a
dust position: `open_quantity` rounds to eight decimals, so a $0.0001 margin
at a BTC price opens a quantity of `0.0`, and the gateway's
`entry_price * quantity` hands the sentry a notional of `0.0`. The rest of the
fix is the function's contract, for the day a live book is wired to it.
"""

from __future__ import annotations

import ast
import inspect

from bot.guardian import book_read
from bot.guardian import risk_sentry as rs

UNREAD = [
    {"symbol": "PENDLE/USDT", "side": "long", "notional_usd": None},
    {"symbol": "SUI/USDT", "side": "long", "notional_usd": 0.0},
    {"symbol": "ARB/USDT", "side": "short", "notional_usd": "n/a"},
]
READ = [dict(p, notional_usd=n) for p, n in zip(UNREAD, (1000.0, 520.0, 310.0))]
PARTIAL = [READ[0], READ[1], UNREAD[2]]


class TestAnUnpriceableBookIsNotClear:
    """The whole book unpriceable — the case that used to read as flat."""

    def test_the_verdict_is_unknown_not_clear(self):
        r = rs.assess(UNREAD, equity_usd=2000.0)
        assert r["worst_level"] == "unknown"
        assert rs.assess([], equity_usd=2000.0)["worst_level"] == "clear"

    def test_the_text_says_it_is_not_an_all_clear(self):
        out = rs.human_readable(rs.assess(UNREAD, equity_usd=2000.0))
        # The POSITIVE claim first: a case-sensitive absence check is the
        # assertion this repo keeps watching misfire, so what the text must
        # SAY is what is checked, and the absence is the folded second half.
        assert "This is not an all-clear" in out
        assert "None of your 3 open position(s) could be priced" in out
        for sym in ("PENDLE/USDT", "SUI/USDT", "ARB/USDT"):
            assert sym in out
        assert "nothing flagged" not in out.lower()
        assert "🟢" not in out

    def test_gross_is_no_figure_rather_than_a_measured_zero(self):
        r = rs.assess(UNREAD, equity_usd=2000.0)
        assert r["gross_usd"] is None
        # ...and a genuinely flat book keeps its measured zero.
        assert rs.assess([], equity_usd=2000.0)["gross_usd"] == 0

    def test_the_count_is_a_reading(self):
        cov = rs.assess(UNREAD, equity_usd=2000.0)["book_coverage"]
        assert cov["counted_positions"] == 3
        assert cov["scored_positions"] == 0
        assert set(cov["unpriced_symbols"]) == {"PENDLE/USDT", "SUI/USDT", "ARB/USDT"}


class TestTheSuppressedWarningWasReal:
    """The readable book is the measurement the unreadable one was hiding."""

    def test_the_same_rows_readable_raise_a_concentration_flag(self):
        r = rs.assess(READ, equity_usd=2000.0)
        assert r["worst_level"] == "caution"
        assert [a["category"] for a in r["alerts"]] == ["concentration"]

    def test_the_unreadable_book_no_longer_claims_what_the_readable_one_denies(self):
        assert rs.assess(UNREAD, equity_usd=2000.0)["worst_level"] != "clear"


class TestAPartialBookSaysSo:
    """Two of three priced — the flags are real and cover part of the book."""

    def test_the_real_flag_is_kept_and_the_note_rides_beside_it(self):
        r = rs.assess(PARTIAL, equity_usd=2000.0)
        cats = [a["category"] for a in r["alerts"]]
        assert "concentration" in cats and "book_partial" in cats
        note = next(a for a in r["alerts"] if a["category"] == "book_partial")
        assert note["level"] == "unknown"
        assert "ARB/USDT could not" in note["msg"]
        assert "2 of your 3 open position(s) could be priced" in note["msg"]

    def test_the_measured_word_is_kept(self):
        # `book_read.verdict_over`'s ruling, one reader over: a partial book
        # keeps what the priced rows really said. A real finding outranks a
        # gap, so the note never lifts or lowers the verdict on its own.
        assert rs.assess(PARTIAL, equity_usd=2000.0)["worst_level"] == "caution"

    def test_the_note_says_the_left_out_rows_could_change_the_flags(self):
        # Driven: over the priced subset PENDLE is 66% of gross, over the
        # whole book 55%. The flag is right about what it saw; the note is
        # what stops it being read as a statement about the whole book.
        part = rs.assess(PARTIAL, equity_usd=2000.0)
        whole = rs.assess(READ, equity_usd=2000.0)
        flag = next(a for a in part["alerts"] if a["category"] == "concentration")
        assert "66%" in flag["msg"]
        assert "55%" in whole["alerts"][0]["msg"]
        note = next(a for a in part["alerts"] if a["category"] == "book_partial")
        assert "could change them" in note["msg"]

    def test_gross_is_the_partial_figure_with_its_coverage_beside_it(self):
        r = rs.assess(PARTIAL, equity_usd=2000.0)
        assert r["gross_usd"] == 1520.0
        assert r["book_coverage"]["scored_positions"] == 2
        assert r["book_coverage"]["counted_positions"] == 3

    def test_a_long_list_of_unpriced_rows_says_how_many_it_did_not_name(self):
        # A bounded list printed without its total reads as the total. Six
        # unpriceable rows name four and COUNT the rest, in both sentences.
        six = [{"symbol": f"C{i}/USDT", "side": "long", "notional_usd": None}
               for i in range(6)]
        dead = rs.assess(six, equity_usd=2000.0)["alerts"][0]["msg"]
        assert "and 2 more" in dead
        part = rs.assess(READ + six, equity_usd=2000.0)
        note = next(a for a in part["alerts"] if a["category"] == "book_partial")
        assert "and 2 more" in note["msg"]

    def test_a_complete_book_carries_no_note(self):
        # Printed only when it bites: a permanent "3 of 3" under every
        # healthy card is the row that trains a reader to skip the line.
        cats = [a["category"] for a in rs.assess(READ, equity_usd=2000.0)["alerts"]]
        assert "book_partial" not in cats


class TestTheOtherTwoFactsAreUnchanged:
    def test_a_flat_book_is_clear_and_covers_nothing(self):
        r = rs.assess([], equity_usd=2000.0)
        assert r["worst_level"] == "clear" and r["alerts"] == []
        assert r["book_coverage"] == {"scored_positions": 0, "counted_positions": 0,
                                      "unpriced_symbols": []}
        assert "nothing flagged" in rs.human_readable(r)

    def test_a_failed_read_claims_no_coverage_it_never_measured(self):
        r = rs.assess(None, equity_usd=2000.0)
        assert r["book_read"] is False
        assert r["book_coverage"] is None


class TestTheWireKeepsBookReadABoolean:
    """`book_read` is a claim about the CALL and stays one.

    The coverage rides BESIDE it, the shape `escape_agent` uses. Repurposing
    the word into a three-valued reading would be a wire change for nobody's
    benefit — the list WAS read, which is exactly what True says.
    """

    def test_a_read_partial_book_is_still_book_read_true(self):
        for book in (UNREAD, PARTIAL, READ, []):
            assert rs.assess(book, equity_usd=2000.0)["book_read"] is True


class TestThePredicateStaysLocal:
    """`book_read` owns the COUNT and refuses to own what "priced" means."""

    def test_the_count_is_taken_once_against_this_modules_predicate(self):
        src = inspect.getsource(rs.assess)
        calls = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "coverage_of"]
        assert len(calls) == 1
        assert ast.unparse(calls[0].args[1]) == "_priced"

    def test_the_count_and_the_filter_agree_on_every_failure_shape(self):
        # Two readings of one predicate are two answers. Every way a row can
        # fail to be assessed, in one book: no symbol, no notional, a zero,
        # junk, a NaN, a boolean (which `_f` refuses rather than reading as 1).
        book = READ + [
            {"symbol": "", "notional_usd": 50.0},
            {"symbol": "X/USDT"},
            {"symbol": "Y/USDT", "notional_usd": 0},
            {"symbol": "Z/USDT", "notional_usd": "junk"},
            {"symbol": "W/USDT", "notional_usd": float("nan")},
            {"symbol": "V/USDT", "notional_usd": True},
        ]
        cov = book_read.coverage_of(book, rs._priced)
        r = rs.assess(book, equity_usd=2000.0)
        assert r["book_coverage"]["scored_positions"] == cov.scored == 3
        assert r["book_coverage"]["counted_positions"] == len(book)
        # The gross is the sum over exactly the rows the count scored.
        assert r["gross_usd"] == 1830.0

    def test_a_row_the_count_calls_unpriced_is_weighed_by_no_check(self):
        # THE OBSERVABLE HALF OF "ONE PREDICATE". A zero row adds nothing to
        # gross, so asserting the gross could not see a loop that kept its
        # own copy of the predicate and let that row through -- the mutation
        # round survived on exactly that. What such a row DOES change is the
        # set the other checks walk: one priced BTC long beside a zero-sized
        # ETH long makes the concentration check see two symbols and the
        # crowding check see two correlated majors, and both fire off a row
        # that is not a position anyone could size. Correctly, the ETH row is
        # unpriced, the note names it, and nothing else is flagged.
        book = [{"symbol": "BTC/USDT", "side": "long", "notional_usd": 1000.0},
                {"symbol": "ETH/USDT", "side": "long", "notional_usd": 0.0}]
        r = rs.assess(book, equity_usd=100000.0)
        assert [a["category"] for a in r["alerts"]] == ["book_partial"]
        assert "ETH/USDT" in r["alerts"][0]["msg"]

    def test_a_negative_notional_is_not_priced(self):
        # A notional is a magnitude; a negative one is a row the book spelled
        # wrong, not a position worth less than nothing.
        assert rs._priced({"symbol": "BTC/USDT", "notional_usd": -100.0}) is False
        assert rs._priced({"symbol": "BTC/USDT", "notional_usd": 100.0}) is True


class TestTheReachablePathIsADustPosition:
    """Driven, not assumed: the one input today's caller can hand the walk."""

    def test_the_paper_book_opens_a_dust_position_at_quantity_zero(self):
        from bot.utils.paper_money import open_quantity
        # $0.0001 of margin at a BTC price rounds to nothing at 8dp — and
        # `open_quantity` does not refuse it, so the paper book holds it.
        assert open_quantity(0.0001, 1, 63000.0) == 0.0

    def test_the_gateways_notional_for_it_reaches_the_partial_branch(self):
        from bot.utils.paper_money import open_quantity
        entry = 63000.0
        qty = open_quantity(0.0001, 1, entry)
        # Built the way `handle_sentry` builds every row.
        dust = {"symbol": "BTC/USDT", "side": "long",
                "notional_usd": float(entry or 0) * float(qty or 0)}
        r = rs.assess([READ[0], dust], equity_usd=100000.0)
        assert r["book_coverage"] == {"scored_positions": 1,
                                      "counted_positions": 2,
                                      "unpriced_symbols": ["BTC/USDT"]}
        assert [a["category"] for a in r["alerts"]] == ["book_partial"]


class TestEveryLevelTheSentryEmitsHasAnIcon:
    """The browser rendered `unknown` as a bare bullet while the Python card
    rendered it ⚪ — two renderers of one report, one of them a level short.
    The levels are read off the PRODUCER, so a level added tomorrow must be
    mapped in both places or this fails."""

    @staticmethod
    def _emitted_levels() -> set:
        tree = ast.parse(inspect.getsource(rs))
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "level"
                            and isinstance(v, ast.Constant)):
                        out.add(v.value)
        return out

    def test_the_producer_emits_the_levels_this_guard_expects(self):
        # A walk that found nothing would acquit every renderer.
        assert {"warn", "caution", "unknown"} <= self._emitted_levels()

    def test_the_text_card_maps_every_level(self):
        r = {"alerts": [{"level": lvl, "msg": "m"}
                        for lvl in sorted(self._emitted_levels())],
             "count": 1, "book_read": True}
        out = rs.human_readable(r)
        assert "•" not in out

    def test_the_dashboard_panel_maps_every_level(self):
        import pathlib
        import re
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "app/public/js/dashboard.js").read_text()
        start = src.index("renderPanel(C('sentry')")
        end = src.index("renderPanel(", start + 1)
        block = src[start:end]
        m = re.search(r"const icon = \{([^}]*)\}", block)
        assert m, "the sentry panel no longer builds an icon map"
        keys = set(re.findall(r"(\w+)\s*:", m.group(1)))
        missing = self._emitted_levels() - keys
        assert not missing, f"sentry levels with no icon on the dashboard: {missing}"
