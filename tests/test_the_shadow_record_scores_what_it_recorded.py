"""The POC-retest shadow record: what a confirmed setup PAID.

`poc_retest` says a sequence completed and that its geometry clears the spec's
two rejections. Neither is a claim that the setup WORKS, and until this record
nothing could become one -- the detector had a single reader, the `/pocretest`
card, and nothing scored what it found.

THE ASSERTIONS HERE ARE DRIVES. Every outcome is produced by walking real bars
through `score_setup` rather than by constructing a `SetupOutcome` and reading
it back, because the walk is the thing that can be wrong: its first draft's
`triggered` was a bare membership test that answered False for a read which had
already triggered, and only driving the six outcomes said so.
"""
from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from bot.core.poc_retest_record import (  # noqa: E402
    MIN_SCORED_SETUPS,
    OUTCOMES,
    SCORED,
    TRIGGERED,
    VERDICTS,
    RecordedSetup,
    SetupOutcome,
    ShadowVerdict,
    load_outcomes,
    load_rows,
    load_setups,
    record_confirmed,
    record_setup_outcome,
    score_setup,
    setup_key,
    shadow_card,
    shadow_reading,
    shadow_verdict,
)

LONG = ("long", 100.0, 95.0, 115.0, 2.4)     # side, entry, stop, target, R
SHORT = ("short", 100.0, 105.0, 85.0, 2.4)


def walk(levels, highs, lows):
    return score_setup(*levels, highs, lows)


# --------------------------------------------------------------------------
class TestEveryOutcomeIsReachedByRealBars:
    """A taxonomy with an unreachable member is a claim, not a reading."""

    CASES = [
        ("target",        [101, 108, 116], [99, 104, 110]),
        ("stop",          [101, 99, 96],   [99, 97, 94]),
        ("ambiguous",     [101, 116],      [99, 94]),
        ("open",          [101, 105],      [99, 103]),
        ("not_triggered", [99, 98, 97],    [96, 96, 96]),
        ("unscored",      [101, float("nan")], [99, 98]),
    ]

    @pytest.mark.parametrize("expect,highs,lows", CASES)
    def test_a_long_reaches_it(self, expect, highs, lows):
        assert walk(LONG, highs, lows).outcome == expect

    def test_the_six_are_the_whole_vocabulary(self):
        assert set(o for o, _, _ in self.CASES) == set(OUTCOMES)

    @pytest.mark.parametrize("expect,highs,lows", [
        ("target",    [101, 99, 96],   [100, 95, 84]),
        ("stop",      [101, 104, 106], [100, 99, 98]),
        ("ambiguous", [101, 106],      [100, 84]),
    ])
    def test_the_short_is_the_inverse(self, expect, highs, lows):
        assert walk(SHORT, highs, lows).outcome == expect


class TestTheLevelsFillWhenPriceTRADESAtThem:
    """A stop order at a level fills when the market trades AT it.

    So the comparisons are `>=` and `<=`, not `>` and `<` -- `setup_verdict`'s
    own docstring names the entry as "a BREAK of a candle extreme, which is a
    stop/market order". Both mutations SURVIVED the first round because no
    fixture put a bar exactly ON a level: a fixture positioned either side of
    a boundary measures nothing about the comparison that decides it.
    """

    def test_a_high_exactly_at_the_entry_triggers(self):
        out = walk(LONG, [100.0, 116.0], [99.0, 101.0])
        assert out.outcome == "target" and out.trigger_index == 0

    def test_a_high_a_hair_under_the_entry_does_not(self):
        assert walk(LONG, [99.999], [99.0]).outcome == "not_triggered"

    def test_a_low_exactly_at_the_stop_is_a_stop(self):
        out = walk(LONG, [101.0, 99.0], [99.0, 95.0])
        assert out.outcome == "stop" and out.r == pytest.approx(-1.0)

    def test_a_low_a_hair_above_the_stop_is_not(self):
        assert walk(LONG, [101.0, 99.0], [99.0, 95.001]).outcome == "open"

    def test_a_high_exactly_at_the_target_is_a_target(self):
        assert walk(LONG, [101.0, 115.0], [99.0, 110.0]).outcome == "target"

    def test_the_short_fills_at_its_levels_too(self):
        assert walk(SHORT, [101.0, 99.0], [100.0, 85.0]).outcome == "target"
        assert walk(SHORT, [101.0, 105.0], [100.0, 99.0]).outcome == "stop"


class TestOrderInsideOneBarIsNotKnowable:
    """The whole reason `ambiguous` exists.

    A bar whose range spans the stop AND the target says both were reached and
    says nothing about which came first. Assuming stop-first is conservative
    and false; assuming target-first is flattering and false.
    """

    def test_a_bar_that_spans_both_is_neither_a_win_nor_a_loss(self):
        out = walk(LONG, [101, 116], [99, 94])
        assert out.outcome == "ambiguous"
        assert out.outcome not in SCORED

    def test_it_carries_no_r_in_either_direction(self):
        out = walk(LONG, [101, 116], [99, 94])
        assert out.r is None, "an unorderable bar must not be assigned an R"
        assert not out.scored

    def test_the_spanning_bar_can_be_the_trigger_bar_itself(self):
        # One bar takes out the entry, the stop and the target. Still
        # ambiguous -- the entry is necessarily first, the other two are not.
        out = walk(LONG, [116], [94])
        assert out.outcome == "ambiguous"
        assert out.trigger_index == 0 and out.resolve_index == 0

    def test_a_trigger_bar_that_only_reaches_the_target_is_not_ambiguous(self):
        # 99 -> 116 passes the entry on the way up and never nears the stop.
        out = walk(LONG, [116], [99])
        assert out.outcome == "target" and out.r == pytest.approx(2.4)

    def test_it_is_kept_out_of_the_mean(self):
        rows = ([SetupOutcome("target", "d", r=2.0, trigger_index=0)] * 12
                + [SetupOutcome("ambiguous", "d", trigger_index=0)] * 8)
        v = shadow_verdict(rows)
        assert v.n_scored == 12 and v.n_ambiguous == 8
        assert v.mean_r == pytest.approx(2.0)


class TestNotTriggeredIsNotALoss:
    """`losses = len(all) - wins` is the shapes table's own row."""

    def test_price_that_never_broke_the_entry_is_its_own_outcome(self):
        out = walk(LONG, [99, 98, 97], [96, 96, 96])
        assert out.outcome == "not_triggered"
        assert out.r is None and out.triggered is False

    def test_a_stop_touched_before_the_entry_is_not_a_loss(self):
        # Bar 0 dips through the stop while the entry is still untouched:
        # nothing was in the market to lose. Bar 1 then triggers.
        out = walk(LONG, [98, 101, 99], [94, 99, 96])
        assert out.outcome == "open"
        assert out.trigger_index == 1

    def test_it_is_not_in_the_mean_and_not_in_the_losses(self):
        rows = ([SetupOutcome("target", "d", r=2.0, trigger_index=0)] * 10
                + [SetupOutcome("not_triggered", "d")] * 30)
        v = shadow_verdict(rows)
        assert v.n_stop == 0 and v.n_not_triggered == 30
        assert v.mean_r == pytest.approx(2.0)


class TestTriggeredIsThreeValued:
    """An `unscored` row may or may not have reached the market.

    The first draft answered `self.outcome in TRIGGERED`, which is False for a
    read that failed at bar 7 having triggered at bar 3 -- a confident negative
    inside the module written to keep those apart.
    """

    def test_a_read_that_failed_after_triggering_says_it_triggered(self):
        out = walk(LONG, [101, float("nan")], [99, 98])
        assert out.outcome == "unscored"
        assert out.trigger_index == 0
        assert out.triggered is True

    def test_a_read_that_failed_before_triggering_cannot_say(self):
        out = walk(LONG, [float("nan"), 101], [98, 99])
        assert out.outcome == "unscored"
        assert out.trigger_index is None
        assert out.triggered is None, "unknown is not False"

    def test_unscored_is_not_in_the_triggered_tuple(self):
        assert "unscored" not in TRIGGERED


class TestAStopIsExactlyMinusOneR:
    """`net_reward_risk`'s denominator IS the stopped-out loss, fees inside.

    So a stop-out is -1.0R by construction and charging fees again here would
    charge them twice. The target pays the ARM-TIME R and nothing is recomputed
    at scoring time.
    """

    def test_a_stop_pays_minus_one(self):
        assert walk(LONG, [101, 96], [99, 94]).r == pytest.approx(-1.0)

    def test_it_is_minus_one_whatever_the_setup_s_own_r_was(self):
        for r in (2.0, 3.5, 9.9):
            levels = ("long", 100.0, 95.0, 115.0, r)
            assert walk(levels, [101, 96], [99, 94]).r == pytest.approx(-1.0)

    def test_a_target_pays_the_arm_time_r_and_not_a_recomputed_one(self):
        levels = ("long", 100.0, 95.0, 115.0, 7.77)
        assert walk(levels, [101, 116], [99, 110]).r == pytest.approx(7.77)

    def test_the_denominator_really_is_the_fee_inclusive_loss(self):
        """Read from `net_reward_risk` rather than restated here."""
        from bot.core.trade_costs import net_reward_risk
        got = net_reward_risk(100.0, 95.0, 115.0)
        assert got is not None and got.net is not None
        # The fee-aware net is strictly below the price-only gross, which is
        # what makes -1R the fee-inclusive unit rather than a price distance.
        assert got.net < got.gross


class TestTheRecordRoundTrips:

    def test_a_setup_written_is_a_setup_read_back(self, tmp_path):
        """The discriminator is stamped on write and filtered on read.

        The first draft wrote a bare `asdict(setup)`: every row landed on disk
        and `load_setups` filtered all of them out, so the record accepted
        writes and reported an empty history forever. Both halves read correct
        alone; only the round trip says so.
        """
        rec = tmp_path / "poc.jsonl"
        s = RecordedSetup("SOL/USDT", "long", 100.0, 95.0, 115.0, 2.4,
                          1_758_000_000_000, "1h", "2026-09-18T09:00:00Z",
                          1_758_000_000_000)
        assert record_confirmed(s, rec) is True
        assert len(load_rows(rec)) == 1
        assert len(load_setups(rec)) == 1, "written but invisible to the read"

    def test_the_same_retest_candle_is_not_recorded_twice(self, tmp_path):
        rec = tmp_path / "poc.jsonl"
        s = RecordedSetup("SOL/USDT", "long", 100.0, 95.0, 115.0, 2.4,
                          1_758_000_000_000, "1h", "2026-09-18T09:00:00Z",
                          1_758_000_000_000)
        assert record_confirmed(s, rec) is True
        assert record_confirmed(s, rec) is False
        assert len(load_setups(rec)) == 1

    def test_a_setup_with_no_outcome_is_unscored_not_dropped(self, tmp_path):
        rec = tmp_path / "poc.jsonl"
        record_confirmed(RecordedSetup("SOL/USDT", "long", 100.0, 95.0, 115.0,
                                       2.4, 1, "1h", "t", 1), rec)
        _rows, v = shadow_reading(rec)
        assert v.n_total == 1 and v.n_unscored == 1

    def test_the_last_outcome_wins(self, tmp_path):
        """An open setup resolving later is a better reading of the same
        setup, not a second setup."""
        rec = tmp_path / "poc.jsonl"
        record_confirmed(RecordedSetup("SOL/USDT", "long", 100.0, 95.0, 115.0,
                                       2.4, 1, "1h", "t", 1), rec)
        key = setup_key("SOL/USDT", "long", 1)
        record_setup_outcome(key, walk(LONG, [101, 105], [99, 103]), rec)
        _r, v = shadow_reading(rec)
        assert v.n_open == 1 and v.n_target == 0
        record_setup_outcome(key, walk(LONG, [101, 105, 116], [99, 103, 110]), rec)
        _r, v = shadow_reading(rec)
        assert v.n_open == 0 and v.n_target == 1

    def test_the_setup_row_survives_its_own_scoring(self, tmp_path):
        """Append only. Rewriting the file in place is how
        `secrets_vault._load_vault` destroyed what it could not read."""
        rec = tmp_path / "poc.jsonl"
        record_confirmed(RecordedSetup("SOL/USDT", "long", 100.0, 95.0, 115.0,
                                       2.4, 1, "1h", "t", 1), rec)
        record_setup_outcome(setup_key("SOL/USDT", "long", 1),
                       walk(LONG, [101, 116], [99, 110]), rec)
        assert len(load_setups(rec)) == 1
        assert len(load_outcomes(rec)) == 1
        assert len(load_rows(rec)) == 2

    def test_a_malformed_line_does_not_take_the_file_down(self, tmp_path):
        rec = tmp_path / "poc.jsonl"
        record_confirmed(RecordedSetup("SOL/USDT", "long", 100.0, 95.0, 115.0,
                                       2.4, 1, "1h", "t", 1), rec)
        with rec.open("a") as fh:
            fh.write("{not json\n")
        assert len(load_setups(rec)) == 1


class TestNoHistoryIsNotAnUnreadableRecord:
    """`arb_reading` draws the same line one tracker over."""

    def test_an_absent_file_is_no_history(self, tmp_path):
        _rows, v = shadow_reading(tmp_path / "nothing.jsonl")
        assert v.verdict == "too_thin" and v.n_total == 0
        assert "no setups have been recorded yet" in v.why

    def test_an_unreadable_record_says_so(self, tmp_path):
        bad = tmp_path / "bad"
        bad.mkdir()
        _rows, v = shadow_reading(bad)
        assert v.verdict == "unread"
        assert "could not be read" in v.why

    def test_an_empty_record_is_not_reported_as_a_thin_sample(self):
        why = shadow_verdict([]).why
        assert "under the" not in why, (
            "an empty record is a fact about the record; a thin one is a fact "
            "about the sample, and '0 of 10' reads as the second")


class TestTheVerdictNeedsTheWholeIntervalClear:

    def _n(self, wins, losses, win_r=2.4):
        return ([SetupOutcome("target", "d", r=win_r, trigger_index=0)] * wins
                + [SetupOutcome("stop", "d", r=-1.0, trigger_index=0)] * losses)

    def test_a_clear_edge_survives(self):
        v = shadow_verdict(self._n(12, 4))
        assert v.verdict == "survives"
        assert v.interval and v.interval[0] > 0

    def test_a_clear_loss_does_not(self):
        v = shadow_verdict(self._n(2, 16, win_r=0.4))
        assert v.verdict == "does_not"
        assert v.interval and v.interval[1] < 0

    def test_a_losing_mean_too_thin_to_call_is_not_a_verdict(self):
        """The input that separates `hi < 0` from `mean < 0`.

        The first `does_not` fixture had a mean AND an upper bound below zero,
        so the point-estimate mutant changed no verdict. A losing mean whose
        interval still reaches above zero is thin, not a finding.
        """
        rows = ([SetupOutcome("target", "d", r=2.4, trigger_index=0)] * 4
                + [SetupOutcome("stop", "d", r=-1.0, trigger_index=0)] * 11)
        v = shadow_verdict(rows)
        assert v.mean_r is not None and v.mean_r < 0, "the mean really loses"
        assert v.interval and v.interval[1] > 0, "the interval still reaches up"
        assert v.verdict == "too_thin", "a point estimate is not a verdict"

    def test_a_straddling_interval_is_not_a_verdict(self):
        v = shadow_verdict(self._n(6, 6))
        assert v.verdict == "too_thin"
        assert v.interval and v.interval[0] < 0 < v.interval[1]
        assert "straddles zero" in v.why

    def test_the_floor_refuses_a_degenerate_sample(self):
        """Three identical outcomes have a sample sd of zero and therefore a
        lower bound at their own mean -- certainty from three trades."""
        ident = [SetupOutcome("target", "d", r=1.8, trigger_index=0)] * 3
        assert shadow_verdict(ident).verdict == "too_thin"
        loose = shadow_verdict(ident, min_scored=2)
        assert loose.verdict == "survives"
        assert loose.interval == (1.8, 1.8), "the false certainty the floor is for"

    def test_the_floor_is_the_shared_one(self):
        from bot.core.shadow_book import MIN_GATE_TRADES
        assert MIN_SCORED_SETUPS is MIN_GATE_TRADES

    def test_the_four_verdicts_are_the_whole_vocabulary(self):
        seen = {shadow_verdict([]).verdict,
                shadow_verdict(self._n(12, 4)).verdict,
                shadow_verdict(self._n(2, 16, win_r=0.4)).verdict,
                ShadowVerdict("unread", "x").verdict}
        assert seen == set(VERDICTS)


class TestTheCountsClose:

    def test_every_outcome_lands_in_exactly_one_bucket(self):
        rows = ([SetupOutcome("target", "d", r=2.0, trigger_index=0)] * 3
                + [SetupOutcome("stop", "d", r=-1.0, trigger_index=0)] * 2
                + [SetupOutcome("ambiguous", "d", trigger_index=0)] * 4
                + [SetupOutcome("open", "d", trigger_index=0)] * 5
                + [SetupOutcome("not_triggered", "d")] * 6
                + [SetupOutcome("unscored", "d")] * 7)
        v = shadow_verdict(rows)
        assert (v.n_target + v.n_stop + v.n_ambiguous + v.n_open
                + v.n_not_triggered + v.n_unscored) == v.n_total == 27


class TestTheMeanSaysWhatItIsNotOver:
    """A bar wide enough to span both levels is a VOLATILE bar, so dropping
    those silently reports the mean over the calm half as the mean over all."""

    def _v(self, ambiguous=0, unscored=0):
        return shadow_verdict(
            [SetupOutcome("target", "d", r=2.4, trigger_index=0)] * 11
            + [SetupOutcome("stop", "d", r=-1.0, trigger_index=0)] * 4
            + [SetupOutcome("ambiguous", "d", trigger_index=0)] * ambiguous
            + [SetupOutcome("unscored", "d")] * unscored)

    def test_the_ambiguous_count_rides_the_sentence(self):
        assert "could not be ordered from OHLC" in self._v(ambiguous=9).why

    def test_the_unscored_count_rides_it_too(self):
        assert "could not be read" in self._v(unscored=3).why

    def test_a_clean_record_gets_no_clause(self):
        why = self._v().why
        assert "could not be" not in why, (
            "a permanent zero clause trains a reader to stop reading the line")

    def test_it_says_they_are_not_in_that_mean(self):
        assert "not in that mean" in self._v(ambiguous=2).why


class TestTheCard:

    def _card(self, v):
        return re.sub(r"<[^>]+>", "", shadow_card(v))

    def test_it_renders_at_every_verdict(self):
        for v in (shadow_verdict([]),
                  shadow_verdict([SetupOutcome("target", "d", r=2.4,
                                               trigger_index=0)] * 12
                                 + [SetupOutcome("stop", "d", r=-1.0,
                                                 trigger_index=0)] * 4),
                  ShadowVerdict("unread", "the record could not be read")):
            assert "POC-RETEST SHADOW RECORD" in self._card(v)

    def test_a_verdict_word_it_cannot_place_is_not_an_all_clear(self):
        text = self._card(ShadowVerdict("something_new", "x"))
        assert "cannot place that verdict" in text
        assert "✅" not in text

    def test_an_unread_record_is_not_green(self):
        assert "✅" not in self._card(ShadowVerdict("unread", "x"))

    def test_zero_rows_are_omitted(self):
        v = shadow_verdict([SetupOutcome("target", "d", r=2.4,
                                         trigger_index=0)] * 12
                           + [SetupOutcome("stop", "d", r=-1.0,
                                           trigger_index=0)] * 4)
        text = self._card(v)
        assert "reached target" in text
        assert "spanned both" not in text, "a permanent 0 row is noise"

    def test_no_dollar_amount_can_appear(self):
        v = shadow_verdict([SetupOutcome("target", "d", r=2.4,
                                         trigger_index=0)] * 12
                           + [SetupOutcome("stop", "d", r=-1.0,
                                           trigger_index=0)] * 4)
        assert "$" not in self._card(v), (
            "an outcome is denominated in its own risk, which is what makes "
            "the record comparable across symbols")

    def test_it_says_nothing_was_placed(self):
        text = self._card(shadow_verdict([]))
        assert "Nothing here was placed" in text
        assert "nothing is armed" in text

    def test_it_names_the_r_convention(self):
        assert "-1.00R" in self._card(shadow_verdict([]))


class TestTheDoors:

    def test_the_shadow_command_is_registered(self):
        src = (ROOT / "bot" / "skills" / "telegram_handler.py").read_text(
            encoding="utf-8")
        assert '("pocshadow", self._cmd_pocshadow)' in src

    def test_it_is_in_the_guarded_baseline(self):
        base = (ROOT / "tests" / "guarded_commands_baseline.txt").read_text()
        assert "_cmd_pocshadow analyze" in base

    def test_both_commands_are_in_the_catalogue(self):
        from bot.skills.command_catalog import all_entries
        entries = all_entries()
        assert "pocshadow" in entries and "pocretest" in entries

    def test_every_locale_describes_it(self):
        locales = ROOT / "bot" / "skills" / "command_catalog_locales"
        missing = []
        for fp in sorted(locales.glob("*.json")):
            d = json.loads(fp.read_text(encoding="utf-8"))
            if "pocshadow" not in d.get("desc", {}):
                missing.append(fp.name)
        assert not missing, missing

    def test_the_shadow_handler_builds_no_card_of_its_own(self):
        from bot.skills.scan_commands import ScanCommands
        from tests.source_scan import code_only
        body = code_only(inspect.getsource(ScanCommands._cmd_pocshadow))
        assert "shadow_card" in body and "shadow_reading" in body
        for own in ("✅", "❌", "Nothing here was placed", "95% interval"):
            assert own not in body, own

    def test_the_read_command_records_through_the_observer(self):
        import ast

        from bot.skills.scan_commands import ScanCommands
        from tests.source_scan import code_only
        body = code_only(inspect.getsource(ScanCommands._cmd_pocretest))
        # THE CALL, not the name. `"observe_setup" in body` is satisfied by
        # the import line, so the handler could stop calling it and the pin
        # would stay green -- a false acquittal the mutation round found.
        import textwrap
        tree = ast.parse(textwrap.dedent(body))
        awaited = {ast.unparse(n.value.func)
                   for n in ast.walk(tree)
                   if isinstance(n, ast.Await)
                   and isinstance(n.value, ast.Call)}
        assert "observe_setup" in awaited, (
            f"the read must ARM the record, not just import the seam: "
            f"{sorted(awaited)}")


class TestTheRecordNoteIsHonest:

    def _note(self, armed=None, scored=0, err=None):
        from bot.skills.scan_commands import _shadow_note

        class Seen:
            pass
        s = Seen()
        s.armed, s.scored, s.record_error = armed, scored, err
        return _shadow_note(s)

    def test_nothing_happened_says_nothing(self):
        assert self._note() == ""

    def test_an_arming_is_named(self):
        assert "added to the shadow record" in self._note(armed=True)

    def test_already_recorded_is_not_silence(self):
        """A caller who runs it twice must not read the second silence as a
        failure to record."""
        assert "already on the shadow record" in self._note(armed=False)

    def test_a_record_that_could_not_be_written_says_so(self):
        note = self._note(err="the shadow record could not be updated (OSError)")
        assert "could not be updated" in note
        assert "added to the shadow record" not in note, (
            "a card must not imply an arming it never made")

    def test_a_rescore_is_counted(self):
        assert "2 recorded setup(s) re-scored" in self._note(armed=False, scored=2)


class TestTheObserverArmsOnlyWhatTheStrategyWouldTake:
    """Driving `observe_setup`, not reading it.

    Both of these SURVIVED the first mutation round: nothing drove the
    observer's arming rule or its record-fault branch at all, so the guard
    could not see either being removed.
    """

    def _confirmed(self):
        from test_the_poc_retest_is_a_sequence_not_a_distance import htf, ltf
        from test_the_poc_retest_reads_two_timeframes import FakeExchange, _rows

        from bot.core.poc_retest import leg_poc, swing_leg
        h4, l4, c4, v4 = htf()
        leg = swing_leg(h4, l4)
        poc = leg_poc(h4, l4, c4, v4, leg)
        h1, l1, c1 = ltf(poc, side="long", hold=1)
        return FakeExchange({"4h": _rows(h4, l4, c4, v4),
                             "1h": _rows(h1, l1, c1)})

    async def test_a_confirmed_and_ok_setup_is_armed(self, tmp_path):
        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        seen = await observe_setup(self._confirmed(), "SOL/USDT", path=rec)
        assert seen.setup.read is not None
        assert seen.setup.read.state == "confirmed"
        assert seen.setup.verdict is not None
        assert seen.setup.verdict.verdict == "ok"
        assert seen.armed is True
        assert len(load_setups(rec)) == 1

    async def test_a_setup_the_verdict_REJECTS_is_not_armed(self, tmp_path):
        """Recording a rejected setup would measure a strategy nobody
        proposed, and the verdict read off it would be about that one."""
        from bot.core.poc_retest import PocRetestParams
        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        # Same sequence, an unreachable R floor: confirmed, and refused.
        params = PocRetestParams(min_net_r=99.0)
        seen = await observe_setup(self._confirmed(), "SOL/USDT",
                                   params=params, path=rec)
        assert seen.setup.read is not None
        assert seen.setup.read.state == "confirmed", "still a real sequence"
        assert seen.setup.verdict is not None
        assert seen.setup.verdict.verdict == "below_min_r"
        assert seen.armed is None, "nothing to arm, and not a failed arming"
        assert load_setups(rec) == []

    async def test_a_record_fault_does_not_take_the_card_down(self, tmp_path):
        """The READ still stands. A shadow record that cannot be written must
        not cost the caller the answer they asked for."""
        from bot.core.poc_retest_scan import observe_setup
        bad = tmp_path / "bad"
        bad.mkdir()
        seen = await observe_setup(self._confirmed(), "SOL/USDT", path=bad)
        assert seen.record_error is not None
        assert "could not be updated" in seen.record_error
        assert seen.setup.read is not None, "the read survived the fault"
        assert seen.setup.read.state == "confirmed"
        assert seen.armed is None

    async def test_the_second_look_rescores_without_rearming(self, tmp_path):
        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        ex = self._confirmed()
        first = await observe_setup(ex, "SOL/USDT", path=rec)
        again = await observe_setup(ex, "SOL/USDT", path=rec)
        assert first.armed is True and again.armed is False
        assert again.scored >= 1, "the pending row is scored again"
        assert len(load_setups(rec)) == 1

    async def test_the_pure_read_writes_nothing(self, tmp_path):
        from bot.core.poc_retest_scan import read_setup
        rec = tmp_path / "poc.jsonl"
        await read_setup(self._confirmed(), "SOL/USDT")
        assert not rec.exists()

    def test_a_setup_not_armed_says_why(self):
        from bot.skills.scan_commands import _shadow_note

        class Seen:
            armed, scored, record_error = None, 0, None
            not_armed = "its entry has already traded since the retest <candle>"
        note = _shadow_note(Seen())
        assert "not recorded: its entry has already traded" in note
        assert "&lt;candle&gt;" in note, "the reason is escaped at the boundary"


# --------------------------------------------------------------------------
class TestOnlyATakeableSetupIsArmed:
    """A read confirmed about a retest candle that closed hours ago is armed
    only if its entry has not traded since. Replayed over the frozen
    snapshots, the rule as first written read "survives, +2.04R" from a
    once-a-day observer for a setup whose bar-by-bar record is +0.03R, and 125
    of its 228 setups had resolved before the read that armed them."""

    END = 1_000_000_000_000
    H1 = 3_600_000

    def _stale(self, *, traded: bool, extra=()):
        """A confirmed long, then three bars that stay under the POC (so the
        read stays confirmed) and, when `traded`, one of them reaching the
        entry. `extra` appends later bars without moving the earlier ones."""
        from test_the_poc_retest_is_a_sequence_not_a_distance import htf, ltf
        from test_the_poc_retest_reads_two_timeframes import FakeExchange, _rows

        from bot.core.poc_retest import leg_poc, swing_leg
        h4, l4, c4, v4 = htf()
        leg = swing_leg(h4, l4)
        poc = leg_poc(h4, l4, c4, v4, leg)
        h1, l1, c1 = ltf(poc, side="long", hold=1)
        entry = h1[-1]
        for k in range(3):
            c = poc - 0.6
            h1.append(entry + 0.05 if (traded and k == 1) else c + 0.15)
            l1.append(c - 0.15)
            c1.append(c)
        for hi, lo, cl in extra:
            h1.append(hi)
            l1.append(lo)
            c1.append(cl)
        return FakeExchange({
            "4h": _rows(h4, l4, c4, v4),
            "1h": _rows(h1, l1, c1, last_open_ms=self.END + len(extra) * self.H1)}), entry

    async def test_a_stale_read_whose_entry_traded_is_not_armed(self, tmp_path):
        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        ex, _entry = self._stale(traded=True)
        seen = await observe_setup(ex, "SOL/USDT", path=rec)
        assert seen.setup.read.state == "confirmed", "still a real sequence"
        assert seen.setup.verdict.verdict == "ok", "and the verdict takes it"
        assert seen.armed is None
        assert "already traded since the retest candle" in seen.not_armed
        assert load_setups(rec) == []

    async def test_a_stale_read_still_takeable_is_armed_on_the_bar_it_was_read(self, tmp_path):
        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        ex, _entry = self._stale(traded=False)
        seen = await observe_setup(ex, "SOL/USDT", path=rec)
        assert seen.armed is True and seen.not_armed is None
        row = load_setups(rec)[0]
        assert row["armed_ms"] == self.END                    # the last closed bar
        assert row["retest_ms"] == self.END - 3 * self.H1     # three bars earlier

    async def test_it_is_scored_from_the_bar_after_the_one_it_was_armed_on(self, tmp_path):
        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        ex, entry = self._stale(traded=False)
        await observe_setup(ex, "SOL/USDT", path=rec)
        row = load_setups(rec)[0]
        # the next bar trades the entry and the one after reaches the target
        ex2, _ = self._stale(traded=False, extra=[
            (entry + 0.1, entry - 0.2, entry), (row["target"] + 1.0, entry, row["target"])])
        seen = await observe_setup(ex2, "SOL/USDT", path=rec)
        # the rally is a fresh breakout, so this read is no longer confirmed
        # and arms nothing; the setup already on record is scored all the same
        assert seen.setup.read.state == "awaiting_retest"
        assert seen.armed is None and seen.scored == 1
        got = load_outcomes(rec)[setup_key("SOL/USDT", "long", row["retest_ms"])]
        assert got["outcome"] == "target"
        # counted from the arming bar: the trigger is the first bar after it,
        # not the fourth bar after the retest candle
        assert got["trigger_index"] == 0

    async def test_a_setup_on_record_whose_entry_since_traded_is_still_on_record(self, tmp_path):
        """Armed fresh, then read again after its entry traded: the card must
        say it is on the record (it is, and it is being scored), not that it
        was refused."""
        from test_the_poc_retest_reads_two_timeframes import FakeExchange

        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        later, _entry = self._stale(traded=True)
        h4 = later.by_tf["4h"]
        h1 = later.by_tf["1h"][:-3]                 # up to the retest candle
        fresh = FakeExchange({"4h": h4, "1h": h1})
        first = await observe_setup(fresh, "SOL/USDT", path=rec)
        assert first.armed is True
        again = await observe_setup(later, "SOL/USDT", path=rec)
        assert again.setup.read.state == "confirmed"
        assert again.armed is False and again.not_armed is None

    async def test_an_unreadable_bar_never_confirms_in_the_first_place(self, tmp_path):
        """An unreadable bar makes ATR unreadable, so the read is `atr_unread`
        and nothing reaches the arming check. That is why the next test has to
        plant the reading to reach its branch."""
        from bot.core.poc_retest_scan import observe_setup
        ex, _entry = self._stale(traded=False)
        ex.by_tf["1h"][-2][2] = float("nan")
        seen = await observe_setup(ex, "SOL/USDT", path=tmp_path / "poc.jsonl")
        assert seen.setup.read.state == "atr_unread"
        assert seen.armed is None and seen.not_armed is None

    async def test_an_entry_that_cannot_be_told_is_not_armed(self, tmp_path, monkeypatch):
        import bot.core.poc_retest_record as rr
        from bot.core.poc_retest_scan import observe_setup
        monkeypatch.setattr(rr, "entry_traded", lambda *_a: None)
        ex, _entry = self._stale(traded=False)
        rec = tmp_path / "poc.jsonl"
        seen = await observe_setup(ex, "SOL/USDT", path=rec)
        assert seen.armed is None
        assert "whether its entry already traded is unknown" in seen.not_armed
        assert load_setups(rec) == []

    async def test_a_fresh_read_is_armed_on_its_own_retest_candle(self, tmp_path):
        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        ex = TestTheObserverArmsOnlyWhatTheStrategyWouldTake()._confirmed()
        seen = await observe_setup(ex, "SOL/USDT", path=rec)
        assert seen.armed is True
        row = load_setups(rec)[0]
        assert row["armed_ms"] == row["retest_ms"]


class TestTheTriggerIsOneReading:

    @pytest.mark.parametrize("side,highs,lows,want", [
        ("long", [99.0, 100.0], [98.0, 99.0], True),     # exactly at the entry
        ("long", [99.99], [98.0], False),
        ("short", [101.0, 100.5], [100.01, 100.0], True),
        ("short", [101.0], [100.01], False),
        ("long", [float("nan"), 101.0], [98.0, 99.0], None),   # unknown before a take
        ("long", [101.0, float("nan")], [99.0, 98.0], True),   # took before it
        ("sideways", [101.0], [99.0], None),
    ])
    def test_entry_traded(self, side, highs, lows, want):
        from bot.core.poc_retest_record import entry_traded
        assert entry_traded(side, 100.0, highs, lows) is want

    def test_the_scorer_and_the_arming_check_ask_the_same_rule(self, monkeypatch):
        """A byte-identical copy agrees on every fixture, so the rule is
        planted and both readers must follow it."""
        import bot.core.poc_retest_record as rr
        monkeypatch.setattr(rr, "_takes", lambda long, e, hi, lo: False)
        assert rr.entry_traded("long", 100.0, [200.0], [150.0]) is False
        assert rr.score_setup(*LONG, [200.0], [150.0]).outcome == "not_triggered"


class TestOlderRowsAreLeftOutByName:

    def _legacy(self, rec, retest_ms=1, outcome=("target", 2.4)):
        with rec.open("a") as fh:
            fh.write(json.dumps({"kind": "setup", "symbol": "SOL/USDT", "side": "long",
                                 "entry": 100.0, "stop": 95.0, "target": 115.0,
                                 "target_r": 2.4, "retest_ms": retest_ms,
                                 "entry_tf": "1h", "recorded_at": "t"}) + "\n")
            fh.write(json.dumps({"kind": "outcome",
                                 "key": setup_key("SOL/USDT", "long", retest_ms),
                                 "outcome": outcome[0], "r": outcome[1]}) + "\n")

    def test_a_row_with_no_arming_bar_is_not_in_the_verdict(self, tmp_path):
        rec = tmp_path / "poc.jsonl"
        self._legacy(rec)
        record_confirmed(RecordedSetup("SOL/USDT", "long", 100.0, 95.0, 115.0,
                                       2.4, 2, "1h", "t", 2), rec)
        record_setup_outcome(setup_key("SOL/USDT", "long", 2),
                             walk(LONG, [101, 96], [99, 94]), rec)
        _rows, v = shadow_reading(rec)
        assert (v.n_total, v.n_stop, v.n_target, v.n_legacy) == (1, 1, 0, 1)
        assert "1 setup(s) recorded before a setup had to be takeable" in v.why

    def test_a_record_of_only_older_rows_does_not_say_nothing_was_recorded(self, tmp_path):
        rec = tmp_path / "poc.jsonl"
        self._legacy(rec)
        _rows, v = shadow_reading(rec)
        assert v.n_total == 0 and v.n_legacy == 1
        assert "no setups have been recorded yet" not in v.why
        assert v.why.startswith("no setup has been recorded under the current arming rule")

    def test_the_card_names_them(self, tmp_path):
        rec = tmp_path / "poc.jsonl"
        self._legacy(rec)
        _rows, v = shadow_reading(rec)
        assert "1</b> older setup(s) left out" in shadow_card(v)

    def test_a_current_record_carries_no_such_line(self, tmp_path):
        rec = tmp_path / "poc.jsonl"
        record_confirmed(RecordedSetup("SOL/USDT", "long", 100.0, 95.0, 115.0,
                                       2.4, 2, "1h", "t", 2), rec)
        _rows, v = shadow_reading(rec)
        assert v.n_legacy == 0 and "left out" not in shadow_card(v)

    async def test_an_older_row_is_not_scored_again(self, tmp_path):
        from bot.core.poc_retest_scan import observe_setup
        rec = tmp_path / "poc.jsonl"
        ex = TestTheObserverArmsOnlyWhatTheStrategyWouldTake()._confirmed()
        ms = int(ex.by_tf["1h"][-2][0])          # a bar inside the fetch
        # still open, so only the arming-bar rule keeps it from a re-score
        self._legacy(rec, retest_ms=ms, outcome=("open", None))
        before = len(load_rows(rec))
        seen = await observe_setup(ex, "SOL/USDT", path=rec)
        # the fresh setup is armed and scored; the older row is not touched
        assert seen.armed is True and seen.scored == 1
        assert len(load_rows(rec)) == before + 2

    def test_a_bool_is_not_an_arming_time(self):
        from bot.core.poc_retest_record import armed_bar_ms
        assert armed_bar_ms({"armed_ms": True}) is None
        assert armed_bar_ms({}) is None
        assert armed_bar_ms({"armed_ms": 5.0}) == 5
