"""A real finding, reached by a method that lies to whoever looks once.

On 2026-07-30 the analyze phase was blowing its budget and the operator
surface named the symbols that hung: FET, RENDER, LINK. Read it again a few
ticks later and it named TRUMP, ENA, RENDER. The names ROTATED — which means
capacity pressure, not three bad symbols, and the two call for opposite
responses (drop the symbols, or shrink the universe / lengthen the budget).

That conclusion was correct and the method was not. `_last_analysis_timeout`
holds ONE batch and is overwritten by the next, so it answers "what timed out
most recently" while the question being asked is "what keeps timing out".
Anyone who happened to look only once would have concluded the opposite with
exactly as much evidence.

This module keeps the distribution instead of the last sample, and then stops
one step short of the conclusion:

    A TALLY DESCRIBES A SHAPE. IT DOES NOT NAME A CAUSE.

A symbol can dominate because its venue feed is slow, because it is always
scheduled last, or because a bounded gather is unlucky in symbol order. §4:
heuristic flags are never verdicts. So the summary reports counts and names
the shape, and never says "the exchange is throttling" or "drop these".

And a shape is not claimable from two events — below MIN_SAMPLE it says so,
the same refusal the coverage note, the position mark, and the edge metrics
now make.
"""
from __future__ import annotations

from bot.core import analysis_timeout_tally as att


def _tally(*events):
    t = att.new_tally()
    for batch in events:
        att.record(t, batch)
    return t


class TestRecording:
    def test_an_empty_tally_summarizes_to_nothing(self):
        assert att.summarize(att.new_tally()) == {}
        assert att.render(att.new_tally()) == ""

    def test_events_accumulate_across_batches(self):
        t = _tally([("FET/USDT", "mtf")], [("FET/USDT", "mtf")])
        s = att.summarize(t, min_sample=1)
        assert s["total"] == 2
        assert s["batches"] == 2
        assert s["top_symbols"][0] == ("FET/USDT", 2)
        # The raw counters live on the tally itself; summarize() reports the
        # ranked view. Assert both, so neither can drift from the other.
        assert t["by_symbol"]["FET/USDT"] == 2
        assert t["by_stage"]["mtf"] == 2

    def test_an_empty_batch_is_not_a_batch(self):
        # A tick with no timeouts must not inflate the denominator; "2 of 40
        # batches had timeouts" and "2 of 400" are different facts.
        t = _tally([("A", "fetch")], [], [])
        assert att.summarize(t, min_sample=1)["batches"] == 1

    def test_an_unmarked_stage_is_recorded_as_unknown_not_guessed(self):
        t = _tally([("A", "")])
        s = att.summarize(t, min_sample=1)
        assert s["top_stages"][0][0] == "unmarked"

    def test_a_bare_symbol_without_a_stage_does_not_raise(self):
        t = att.new_tally()
        att.record(t, ["SOLO/USDT"])
        assert att.summarize(t, min_sample=1)["total"] == 1

    def test_recording_never_raises(self):
        # Instrumentation must never be the reason an analysis fails.
        att.record(None, [("A", "b")])            # type: ignore[arg-type]
        att.record(att.new_tally(), None)         # type: ignore[arg-type]
        att.record({}, [object()])
        assert att.summarize({"total": "x"}) == {}


class TestTheShapeIsNotClaimedFromTooFewEvents:
    def test_below_the_sample_floor_no_pattern_is_claimed(self):
        t = _tally([("FET/USDT", "mtf")], [("FET/USDT", "mtf")])
        s = att.summarize(t)
        assert s["shape"] == "insufficient", (
            "two events look concentrated or spread purely by chance"
        )

    def test_the_line_says_so_in_words(self):
        t = _tally([("FET/USDT", "mtf")])
        assert "Too few to characterise" in att.render(t)

    def test_the_counts_are_still_shown(self):
        # Refusing to characterise is not refusing to report.
        t = _tally([("FET/USDT", "mtf")])
        line = att.render(t)
        assert "FET/USDT" in line and "1 analysis timeout" in line


class TestConcentratedVersusSpread:
    def test_one_dominant_symbol_reads_concentrated(self):
        t = _tally([("FET/USDT", "mtf")] * 5 + [("A", "mtf")])
        assert att.summarize(t)["shape"] == "concentrated"

    def test_rotating_symbols_read_spread(self):
        # The live shape: different names every batch, none dominant.
        t = _tally(
            [("FET/USDT", "mtf"), ("RENDER/USDT", "mtf"), ("LINK/USDT", "fetch")],
            [("TRUMP/USDT", "mtf"), ("ENA/USDT", "analyze"), ("RENDER/USDT", "mtf")],
        )
        assert att.summarize(t)["shape"] == "spread"

    def test_the_two_shapes_read_differently(self):
        conc = att.render(_tally([("A", "mtf")] * 6))
        spread = att.render(_tally([(c, "mtf") for c in "ABCDEF"]))
        assert conc != spread
        assert "One symbol holds most of them" in conc
        assert "No symbol dominates" in spread

    def test_distinct_symbol_count_is_reported(self):
        t = _tally([(c, "mtf") for c in "ABCDEF"])
        assert att.summarize(t)["distinct_symbols"] == 6


class TestItNamesAShapeAndNeverACause:
    def test_neither_shape_blames_the_exchange(self):
        for line in (att.render(_tally([("A", "mtf")] * 6)),
                     att.render(_tally([(c, "mtf") for c in "ABCDEF"]))):
            low = line.lower()
            assert "throttl" not in low
            assert "exchange is" not in low
            assert "drop these" not in low

    def test_each_shape_states_what_it_cannot_distinguish(self):
        conc = att.render(_tally([("A", "mtf")] * 6))
        spread = att.render(_tally([(c, "mtf") for c in "ABCDEF"]))
        assert "a tally cannot tell that apart" in conc
        assert "a tally cannot tell that apart" in spread

    def test_the_shape_is_hedged_with_what_it_looks_like(self):
        # "is" would be a verdict; "looks like" is the flag §4 permits.
        assert "looks like" in att.render(_tally([("A", "mtf")] * 6))


class TestItIsWiredIntoTheEngine:
    def _src(self, path):
        from tests.source_scan import code_only
        return code_only(open(path, encoding="utf-8").read())

    def test_the_batch_records_structured_pairs(self):
        src = self._src("bot/core/engine.py")
        assert "_timed_out_pairs.append((_sym, _stg))" in src, (
            "parsing 'FET/USDT (mtf)' back apart would make the tally depend "
            "on a display string's format"
        )
        assert "_att.record(_t, _timed_out_pairs)" in src

    def test_the_tally_call_is_guarded(self):
        # Several suites drive this batch with SimpleNamespace stubs, and this
        # exact hazard has bitten instrumentation added here before.
        src = self._src("bot/core/engine.py")
        i = src.index("_att.record(_t, _timed_out_pairs)")
        window = src[i - 500:i + 200]
        assert "getattr(self, \"_analysis_timeout_tally\", None)" in window
        assert "except Exception:" in window

    def test_the_operator_hint_shows_the_distribution(self):
        src = self._src("bot/skills/telegram_handler.py")
        assert "_analysis_timeout_tally" in src, (
            "a tally nothing reads is a tally that answers nothing"
        )
        assert "Across this run" in src
