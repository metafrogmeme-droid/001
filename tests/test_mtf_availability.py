"""A timeframe the venue will not serve was re-requested every tick, forever.

The mtf leg fetches four extra timeframes per symbol with `ttl=180`. That
cache holds SUCCESSES. A failure caches nothing, the leg is fail-open, and
the failure is logged at debug — so a symbol whose extra timeframes the venue
does not serve fails all four and is asked again on the very next tick.
Every tick. Each attempt costs up to the per-call timeout, and none of it is
ever visible as an error.

2026-08-01 production: mtf overtook fetch as the dominant stage (~59-64% vs
~36-41%) on an 85-symbol universe, 22-27 signals skipped per cycle, and the
operator reported the stall was "almost all R* stocks" — tokenized equities.
One tick hit the 300s phase cap at 315s having finished 60/60: the work was
done and it was cancelled at the tape.

    A CACHE THAT ONLY REMEMBERS SUCCESSES ASKS THE SAME FAILING QUESTION
    FOREVER.

Not an R* exclusion: that fixes the instance, not the class. `R` is a
venue-specific prefix that can change, it says nothing about WHY those
symbols are slow, and the next family with thin coverage would rediscover the
whole problem.

The asymmetry that shapes every threshold here is the same one as the
venue-auth classifier in #1029:

    blip treated as unavailable   -> a timeframe silently missing from a
                                     real-money decision
    unavailable treated as a blip -> some wasted latency

so suppression takes CONSECUTIVE failures, expires on its own, and clears on
any success. When in doubt, fetch.
"""
from __future__ import annotations

import pytest

from bot.core import mtf_availability as ma
from tests.source_scan import code_only

SPECS = (("15m", 200), ("1h", 200), ("4h", 200), ("1d", 200))
SYM = "RNVDA/USDT"


def _fail(st, n, symbol=SYM, tf="4h", now=0.0):
    out = False
    for _ in range(n):
        out = ma.record_failure(st, symbol, tf, now=now)
    return out


class TestOneBadResponseCannotLatchAnything:
    def test_a_single_failure_suppresses_nothing(self):
        st = ma.new_state()
        assert _fail(st, 1) is False
        assert not ma.is_suppressed(st, SYM, "4h", now=0.0)

    def test_it_takes_the_full_run(self):
        st = ma.new_state()
        assert _fail(st, ma.CONSECUTIVE_FAILURES - 1) is False
        assert not ma.is_suppressed(st, SYM, "4h", now=0.0)
        assert _fail(st, 1) is True
        assert ma.is_suppressed(st, SYM, "4h", now=0.0)

    def test_a_success_resets_the_run(self):
        # Two failures, a success, two more failures — never three in a row.
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES - 1)
        ma.record_success(st, SYM, "4h")
        _fail(st, ma.CONSECUTIVE_FAILURES - 1)
        assert not ma.is_suppressed(st, SYM, "4h", now=0.0), (
            "an intermittent timeframe must not be suppressed"
        )

    def test_a_success_clears_an_active_suppression(self):
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES)
        assert ma.is_suppressed(st, SYM, "4h", now=0.0)
        ma.record_success(st, SYM, "4h")
        assert not ma.is_suppressed(st, SYM, "4h", now=0.0)


class TestSuppressionIsPerPairAndTemporary:
    def test_only_the_failing_timeframe_is_dropped(self):
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES, tf="4h")
        kept = ma.filter_specs(st, SYM, SPECS, now=0.0)
        assert ("4h", 200) not in kept
        assert len(kept) == len(SPECS) - 1, (
            "one thin timeframe must not cost the other three"
        )

    def test_only_the_failing_symbol_is_affected(self):
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES, symbol="RNVDA/USDT")
        assert ma.filter_specs(st, "BTC/USDT", SPECS, now=0.0) == list(SPECS)

    def test_it_expires(self):
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES, now=0.0)
        assert ma.is_suppressed(st, SYM, "4h", now=ma.SUPPRESS_SECONDS - 1)
        assert not ma.is_suppressed(st, SYM, "4h", now=ma.SUPPRESS_SECONDS + 1), (
            "a venue that starts serving a timeframe must be picked back up "
            "without a restart"
        )

    def test_expiry_restores_a_clean_count(self):
        # Otherwise the pair sits one failure away from re-suppression
        # forever, and a single blip after the window re-latches it.
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES, now=0.0)
        t = ma.SUPPRESS_SECONDS + 1
        assert not ma.is_suppressed(st, SYM, "4h", now=t)
        assert _fail(st, 1, now=t) is False
        assert not ma.is_suppressed(st, SYM, "4h", now=t)

    def test_all_four_can_be_suppressed_independently(self):
        st = ma.new_state()
        for tf, _ in SPECS:
            _fail(st, ma.CONSECUTIVE_FAILURES, tf=tf)
        assert ma.filter_specs(st, SYM, SPECS, now=0.0) == []


class TestTheFailureModeIsAlwaysToFetch:
    """Fetching needlessly costs latency. Not fetching costs a decision."""

    @pytest.mark.parametrize("bad", [None, 7, "state", []])
    def test_junk_state_never_drops_a_timeframe(self, bad):
        assert ma.filter_specs(bad, SYM, SPECS) == list(SPECS)  # type: ignore[arg-type]
        assert ma.is_suppressed(bad, SYM, "4h") is False        # type: ignore[arg-type]
        assert ma.record_failure(bad, SYM, "4h") is False       # type: ignore[arg-type]
        ma.record_success(bad, SYM, "4h")                       # type: ignore[arg-type]

    def test_a_corrupt_expiry_does_not_suppress(self):
        st = ma.new_state()
        st["until"][f"{SYM}|4h"] = "not-a-number"
        assert ma.is_suppressed(st, SYM, "4h", now=0.0) is False
        assert ma.filter_specs(st, SYM, SPECS, now=0.0) == list(SPECS)

    def test_a_hostile_spec_list_is_returned_unchanged(self):
        # The first fallback was `return list(specs)`, which re-ran the very
        # iteration that had just failed — a recovery path that raises the
        # error it is recovering from. It must hand back the caller's own
        # object untouched.
        st = ma.new_state()

        class Hostile:
            def __iter__(self):
                raise RuntimeError("nope")

        h = Hostile()
        assert ma.filter_specs(st, SYM, h) is h

    def test_a_missing_symbol_or_timeframe_is_handled(self):
        st = ma.new_state()
        for _ in range(ma.CONSECUTIVE_FAILURES):
            ma.record_failure(st, None, None, now=0.0)  # type: ignore[arg-type]
        assert ma.is_suppressed(st, None, None, now=0.0)  # type: ignore[arg-type]


class TestItIsVisible:
    """Suppression nobody can see is the same defect as the silent fail-open."""

    def test_nothing_suppressed_says_nothing(self):
        assert ma.summarize(ma.new_state()) == {}
        assert ma.render(ma.new_state()) == ""

    def test_it_reports_counts_and_names(self):
        st = ma.new_state()
        for sym in ("RNVDA/USDT", "RTSLA/USDT"):
            for tf, _ in SPECS:
                _fail(st, ma.CONSECUTIVE_FAILURES, symbol=sym, tf=tf)
        s = ma.summarize(st, now=0.0)
        assert s["pairs"] == 8 and s["symbols"] == 2
        out = ma.render(st, now=0.0)
        assert "RNVDA/USDT" in out

    def test_it_says_the_suppression_is_temporary(self):
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES)
        out = ma.render(st, now=0.0)
        assert "re-probed" in out, (
            "a skip that reads as permanent invites a fix nobody needs"
        )

    def test_it_never_says_why(self):
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES)
        out = ma.render(st, now=0.0).lower()
        for banned in ("delist", "not supported", "bad symbol", "drop ",
                       "the venue does not"):
            assert banned not in out, f"named a cause it cannot see: {banned!r}"

    def test_expired_entries_drop_out_of_the_summary(self):
        st = ma.new_state()
        _fail(st, ma.CONSECUTIVE_FAILURES, now=0.0)
        assert ma.summarize(st, now=ma.SUPPRESS_SECONDS + 1) == {}


class TestTheEngineUsesIt:
    def _src(self) -> str:
        return code_only(open("bot/core/engine.py", encoding="utf-8").read())

    def test_the_specs_are_filtered_before_the_gather(self):
        src = self._src()
        i = src.index("_mtf_filter_specs(self, signal.symbol, _tf_specs)")
        j = src.index("asyncio.gather(", i)
        assert j > i, "filtering after the fetch would save nothing"

    def test_both_outcomes_are_recorded(self):
        src = self._src()
        assert "_mtf_note(self, signal.symbol, _tf, ok=False)" in src
        assert "_mtf_note(self, signal.symbol, _tf, ok=True)" in src, (
            "without the success path a transient failure run would latch "
            "permanently — nothing would ever clear it"
        )

    def test_an_empty_result_counts_as_a_failure(self):
        # Not an exception, but not data either. The expensive case — a venue
        # that answers "no candles" only after a timeout — lands here.
        src = self._src()
        i = src.index("_mtf_note(self, signal.symbol, _tf, ok=True)")
        assert "ok=False" in src[i:i + 700]

    def test_the_helpers_fail_open(self):
        src = self._src()
        i = src.index("def _mtf_filter_specs(")
        body = src[i:src.index("def _mtf_note(", i)]
        assert "return specs" in body, (
            "on error it must fetch everything, never silently fetch nothing"
        )

    def test_the_helpers_are_guarded_like_every_other_mark(self):
        src = self._src()
        for fn in ("def _mtf_filter_specs(", "def _mtf_note("):
            i = src.index(fn)
            assert "except Exception:" in src[i:i + 1400], fn

    def test_the_suppression_actually_reaches_a_surface(self):
        """render() existed, was tested, and NOTHING called it.

        So the suppression it reports was invisible — the precise defect this
        module's own docstring is written against. Third time this session a
        mechanism landed with no surface: live_auth_healthy in #1030, the
        halt in #1031, and this. Writing the renderer is not wiring it.
        """
        src = self._src()
        assert "_mtf_suppression_note" in src, (
            "a skip nobody can see is the silent fail-open it replaced"
        )
        i = src.index("def _stage_report(")
        j = src.index("\n    def ", i + 10)
        assert "_mtf_suppression_note" in src[i:j], (
            "it must be reached from the line the operator actually reads"
        )

    def test_the_note_survives_a_lightweight_stub(self):
        # Same hazard that made _stage_report return "" once already: a
        # diagnostic that requires its caller to grow an attribute deletes
        # the line it was meant to enrich.
        from bot.core.engine import RuneClawEngine

        class Stub:
            _stage_totals = {"mtf": 90.0, "fetch": 10.0}
            _stage_report = RuneClawEngine._stage_report

        out = Stub()._stage_report(120.0)
        assert out and "mtf 90s" in out

    def test_the_note_appears_when_something_is_suppressed(self):
        from bot.core.engine import RuneClawEngine

        # Real clock: the note calls summarize() with no `now`, so failures
        # stamped at 0.0 would already have expired against time.monotonic().
        import time as _t
        st = ma.new_state()
        for tf, _ in SPECS:
            _fail(st, ma.CONSECUTIVE_FAILURES, symbol="RNVDA/USDT", tf=tf,
                  now=_t.monotonic())

        class Stub:
            _stage_totals = {"mtf": 90.0, "fetch": 10.0}
            _mtf_availability = st
            _stage_report = RuneClawEngine._stage_report
            _mtf_suppression_note = RuneClawEngine._mtf_suppression_note

        out = Stub()._stage_report(120.0)
        assert "mtf skipping 4 timeframe(s)" in out
        assert "RNVDA/USDT" in out

    def test_the_note_is_silent_when_nothing_is_suppressed(self):
        from bot.core.engine import RuneClawEngine

        class Stub:
            _stage_totals = {"mtf": 90.0}
            _mtf_availability = ma.new_state()
            _mtf_suppression_note = RuneClawEngine._mtf_suppression_note

        assert Stub()._mtf_suppression_note() == "", (
            "a healthy run must not grow a diagnostic it does not need"
        )

    def test_the_leg_is_still_fail_open_per_timeframe(self):
        # The pre-existing guarantee this must not have cost: one bad
        # timeframe degrades that leg to absent, never the rest.
        src = self._src()
        i = src.index("Elliott MTF fetch %s failed")
        assert "continue" in src[i:i + 200]
