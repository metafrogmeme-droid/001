"""Every unpriced close pushed the displayed win rate down.

`LivePosition.pnl_usd` is `Optional[float] = None`. Six surfaces counted wins
like this:

    wins = sum(1 for t in live_closed if (t.pnl_usd or 0) > 0)
    win_rate = wins / len(live_closed)

`None or 0` is 0 and `0 > 0` is false, so a close the record cannot price was
filed as a DEFEAT — while `len(live_closed)` kept it in the denominator. One
unpriced close in five drops a true 50% to 40%, on the number the operator
reads first.

Three of the six carried the defect in a second form:

    losses = len(closed) - wins

which prints an unpriced close as an L on a W/L line.

The same bug shipped on the web in the Arena discipline read (#1019) and the
edge metrics (#1017), and the lesson from those is why this is a shared
module rather than six edits: fixing the LINE leaves the surface half-cured.
The fix has to follow the VALUE.

    A CLOSE THE RECORD CANNOT PRICE IS NOT A LOSS, AND NOT A WIN.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from bot.utils.win_rate import coverage_note, win_rate, win_stats


def _pos(pnl):
    return NS(pnl_usd=pnl)


class TestUnpricedIsScoredNeitherWay:
    def test_it_leaves_both_numerator_and_denominator(self):
        st = win_stats([_pos(5), _pos(None), _pos(-2), _pos(0.0), _pos(3)])
        assert st["wins"] == 2
        assert st["scored"] == 4
        assert st["unscored"] == 1
        assert st["rate"] == 0.5

    def test_the_old_arithmetic_understated_it(self):
        rows = [_pos(5), _pos(None), _pos(-2), _pos(0.0), _pos(3)]
        old = sum(1 for t in rows if (t.pnl_usd or 0) > 0) / len(rows)
        assert old == 0.4
        assert win_rate(rows) == 0.5, (
            "one unpriced close in five cost ten points of win rate"
        )

    def test_losses_are_scored_non_wins_not_everything_else(self):
        st = win_stats([_pos(1), _pos(None), _pos(-1)])
        assert st["scored"] - st["wins"] == 1, (
            "len(closed) - wins would show the unpriced close as an L"
        )

    def test_nothing_scorable_yields_no_rate_rather_than_zero(self):
        # 0.0 claims everything lost. None says we could not price any of it.
        st = win_stats([_pos(None), _pos(None)])
        assert st["rate"] is None
        assert st["unscored"] == 2
        assert win_rate([_pos(None)]) is None

    def test_an_empty_set_has_no_rate(self):
        assert win_stats([])["rate"] is None
        assert win_rate(None) is None


class TestARecordedZeroIsAMeasurement:
    def test_break_even_stays_scored(self):
        # 0.0 is falsy and 0.0 is a real, recorded break-even. It must not be
        # mistaken for a missing value — the corollary this codebase has now
        # met on the drawdown peak, the position mark, and the edge metrics.
        st = win_stats([_pos(0.0)])
        assert st["scored"] == 1 and st["unscored"] == 0

    def test_break_even_is_not_a_win(self):
        assert win_stats([_pos(0.0)])["wins"] == 0

    def test_zero_and_missing_produce_different_readings(self):
        assert win_stats([_pos(0.0)]) != win_stats([_pos(None)])


class TestJunkIsUnscorableNotZero:
    def test_nan_and_inf_are_refused(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            assert win_stats([_pos(bad)])["unscored"] == 1, bad

    def test_unparseable_values_are_refused(self):
        for bad in ("n/a", "", object(), [], {}):
            assert win_stats([_pos(bad)])["unscored"] == 1, bad

    def test_numeric_strings_are_accepted(self):
        # The website rows arrive as strings; refusing them would discard
        # real measurements.
        assert win_stats([_pos("2.5")])["wins"] == 1


class TestItReadsTheShapesItsCallersHave:
    def test_live_positions_by_attribute(self):
        assert win_stats([_pos(1)])["wins"] == 1

    def test_journal_dicts_by_pnl(self):
        assert win_stats([{"pnl": 4}])["wins"] == 1

    def test_website_rows_prefer_net_pnl(self):
        # net_pnl is after fees; when both are present it is the truer figure
        # and the key order in _pnl puts it first.
        assert win_stats([{"net_pnl": -1, "pnl": 5}])["wins"] == 0

    def test_a_dict_without_any_pnl_key_is_unscorable(self):
        assert win_stats([{"symbol": "BTC"}])["unscored"] == 1


class TestTheNoteIsSilentWhenNothingWasDropped:
    def test_a_complete_set_says_nothing(self):
        assert coverage_note(win_stats([_pos(1), _pos(-1)])) == ""

    def test_a_partial_set_names_both_counts(self):
        note = coverage_note(win_stats([_pos(1), _pos(None), _pos(-1)]))
        assert "2 of 3" in note and "1 carry no recorded" in note

    def test_junk_stats_do_not_raise(self):
        assert coverage_note({}) == ""
        assert coverage_note({"unscored": "x"}) == ""


class TestEverySurfaceUsesTheSharedRule:
    """Six sites shared the mistake; they must share the fix."""

    def _src(self, path):
        from tests.source_scan import code_only
        return code_only(open(path, encoding="utf-8").read())

    def test_no_surface_still_coerces_pnl_to_decide_a_win(self):
        for path in ("bot/skills/telegram_handler.py",
                     "bot/skills/skill_registry.py",
                     "bot/skills/scan_skill.py",
                     "bot/formatters/rich_cards.py"):
            src = self._src(path)
            assert "(t.pnl_usd or 0) > 0" not in src, path
            assert "(p.pnl_usd or 0) > 0" not in src, path
            assert 't.get("pnl", 0) > 0' not in src, path

    def test_the_four_handler_sites_all_route_through_it(self):
        src = self._src("bot/skills/telegram_handler.py")
        assert src.count("_win_stats(") >= 5, (
            "four separate win counts lived here; one converted site means "
            "the rate depends on which command was typed. The fifth is the "
            "PAPER half of the session-tally card, which kept the remainder "
            "form for a year after the LIVE half beside it was cured"
        )

    def test_the_session_tally_has_no_remainder_form_left(self):
        """`losses = <total> - wins`, in the fourth spelling it took here.

        The docstring above names three: `len(closed) - wins`. This one read
        `state.total_trades - wins` — a different expression, the same claim,
        and unreachable by any grep written for the other three. It sat in the
        same function as an already-fixed site, sixty lines apart.
        """
        src = self._src("bot/skills/telegram_handler.py")
        for banned in ("state.total_trades - wins", "total_trades - wins"):
            assert banned not in src, (
                f"`{banned}` files every break-even and every unpriced close "
                f"as a defeat on the session W/L line"
            )

    def test_the_other_three_modules_route_through_it_too(self):
        for path in ("bot/skills/skill_registry.py",
                     "bot/skills/scan_skill.py",
                     "bot/formatters/rich_cards.py"):
            assert "win_stats" in self._src(path), path

    def test_no_surface_computes_losses_from_the_full_count(self):
        for path in ("bot/skills/telegram_handler.py",
                     "bot/formatters/rich_cards.py"):
            src = self._src(path)
            assert "losses = len(closed_trades) - wins" not in src, path
            assert "losses = len(live_closed) - wins" not in src, path
            assert "losses = today_trades - wins" not in src, path


class TestTheScopeIsStatedNotImplied:
    """What was fixed, and what was checked and deliberately left alone."""

    def _src(self, path):
        from tests.source_scan import code_only
        return code_only(open(path, encoding="utf-8").read())

    def test_the_live_shape_is_converted_everywhere(self):
        # pnl_usd is Optional[float], so `or 0` genuinely mis-scores. Nine
        # sites carried it across six modules; all nine route through the
        # shared rule now.
        for path in ("bot/skills/telegram_handler.py",
                     "bot/skills/skill_registry.py",
                     "bot/skills/scan_skill.py",
                     "bot/skills/live_stats.py",
                     "bot/formatters/rich_cards.py"):
            src = self._src(path)
            assert 'getattr(t, "pnl_usd", 0) or 0' not in src, path
            assert "(t.pnl_usd or 0) > 0" not in src, path

    def test_the_paper_model_is_left_alone_on_purpose(self):
        # bot/utils/models.py declares `pnl: float = 0.0` — NOT Optional — so
        # `t.pnl > 0` on the paper path cannot meet a None and is safe as
        # written. The residual issue is the DEFAULT: a Trade that never
        # recorded a result reads as a measured break-even, which is the same
        # conflation one level down. Changing a dataclass default touches
        # every consumer of the paper portfolio, so it is named here rather
        # than done quietly as part of a win-rate fix.
        from tests.source_scan import code_only
        src = code_only(open("bot/utils/models.py", encoding="utf-8").read())
        assert "pnl: float = 0.0" in src, (
            "if this becomes Optional, the paper-path win counts in "
            "metrics.py, engine.py and telegram_handler.py need the same "
            "treatment the live sites just got"
        )
