"""1086s of fetch across 70 symbols is the same number either way.

From the operator's 2026-08-01 work report: a tick stall, fetch stage
dominating, ~1086s cumulative and 192s wall-clock. The line they were reading
is engine._stage_report, and it stops exactly one step short of the answer:

    fetch 1086s (62%) — 1740s of work across 192s wall-clock at 12 concurrent

A percentage says fetch owns the tick. It cannot say whether that is

    all 70 symbols at ~15.5s each          -> systemic
    three symbols at 300s and 67 at 2s     -> three bad symbols

and those call for opposite fixes. #1018's tally cannot see this case at all,
because it only fires on TIMEOUTS and these analyses completed.

WHY THE MEAN IS THE WRONG STATISTIC

A symbol's "fetch" is ~9 rate-limited calls (config.py says so, and caps the
INTERACTIVE path for exactly that reason) issued against ONE cached ccxt
instance with enableRateLimit=True. So two very different situations produce
a similar mean, and only the SHAPE separates them:

    calls hitting the per-call ceiling  ->  durations PILE UP at the ceiling
    rate-limit queueing                 ->  durations SPREAD smoothly

    A PROFILE DESCRIBES A SHAPE. IT DOES NOT NAME A CAUSE.

The near-ceiling count is the one number here that reads as a diagnosis if it
is not framed as a measurement, because queueing behind a full bucket can
park a fetch near the ceiling too. These tests pin that framing.
"""
from __future__ import annotations

import pytest

from bot.core import stage_profile as fp
from tests.source_scan import code_only

CEILING = 15.0


def _prof(*durations, symbol="BTC/USDT"):
    p = fp.new_profile()
    for i, d in enumerate(durations):
        fp.record(p, f"{symbol}{i}" if symbol == "SPREAD" else symbol, d)
    return p


def _spread(n, d):
    p = fp.new_profile()
    for i in range(n):
        fp.record(p, f"SYM{i}/USDT", d)
    return p


class TestTheTwoShapesTheOperatorNeededToTellApart:
    def test_seventy_symbols_at_the_ceiling_reads_as_at_ceiling(self):
        # The systemic case: nothing is a "bad symbol", every fetch is slow.
        p = _spread(70, 15.2)
        s = fp.summarize(p, ceiling_s=CEILING)
        assert s["shape"] == "at_ceiling"
        assert s["near_ceiling"] == 70

    def test_a_handful_of_bad_symbols_reads_as_concentrated(self):
        """The operator's actual shape: "almost all R* stocks".

        Split across three symbols, none reaches half the total on its own —
        so a top-1 concentration rule reports "spread" and points at capacity
        for the textbook few-bad-symbols case.
        """
        p = fp.new_profile()
        for i in range(67):
            fp.record(p, f"SYM{i}/USDT", 2.0)
        for sym in ("FET/USDT", "RENDER/USDT", "LINK/USDT"):
            fp.record(p, sym, 300.0)
        s = fp.summarize(p, ceiling_s=CEILING)
        assert s["shape"] == "concentrated"
        assert s["top_symbols"][0][0] in {"FET/USDT", "RENDER/USDT",
                                          "LINK/USDT"}

    def test_broad_load_reads_as_spread(self):
        # Slow, but nowhere near the per-call limit and nobody dominating.
        s = fp.summarize(_spread(70, 4.0), ceiling_s=CEILING)
        assert s["shape"] == "spread"

    def test_the_two_cases_have_a_similar_mean_and_different_shapes(self):
        """The point of the whole module, asserted directly."""
        systemic = _spread(70, 15.2)
        bad = fp.new_profile()
        for i in range(67):
            fp.record(bad, f"SYM{i}/USDT", 2.0)
        for sym in ("A/USDT", "B/USDT", "C/USDT"):
            fp.record(bad, sym, 310.0)
        a = fp.summarize(systemic, ceiling_s=CEILING)
        b = fp.summarize(bad, ceiling_s=CEILING)
        assert abs(a["mean_s"] - b["mean_s"]) < 3.0, (
            "these were chosen to be nearly indistinguishable by mean"
        )
        assert a["shape"] != b["shape"], (
            "the shape is what the aggregate could not tell apart"
        )


class TestTheOperatorsRealShape:
    """The case the first version got WRONG, kept as a regression.

    2026-08-01 production: ~85 symbols, the stall "almost all R* stocks"
    (RMSFT, RNVDA, RTSLA, RAAPL...) — roughly 25 tokenized equities pinned
    near the per-call limit while ~60 other symbols ran in a couple of
    seconds.

    That is 29% near the ceiling: under CEILING_SHARE, and far too many
    symbols for the top-few concentration test. Both checks missed and the
    module said "spread — broad load", which is the reading that argues for
    MORE CONCURRENCY. mtf and fetch are exchange I/O behind ONE rate-limited
    ccxt instance, so more concurrency adds no throughput — the tool would
    have pointed at the one fix that cannot work.
    """

    def _real(self):
        p = fp.new_profile()
        for i in range(60):
            fp.record(p, f"SYM{i}/USDT", 2.5)
        for t in ("RMSFT", "RNVDA", "RTSLA", "RAAPL", "RGOOGL", "RMETA",
                  "RAMZN", "RNFLX", "RCOIN", "RMSTR", "RAMD", "RPLTR",
                  "RBABA", "RUBER", "RSHOP", "RSNAP", "RSOFI", "RHOOD",
                  "RPYPL", "RSQ", "RTWLO", "RCRWD", "RNET", "RDDOG", "RZS"):
            fp.record(p, f"{t}/USDT", 14.8)
        return p

    def test_it_is_not_called_broad_load(self):
        s = fp.summarize(self._real(), ceiling_s=CEILING)
        assert s["shape"] != "spread", (
            "'broad load' is the reading that argues for more concurrency, "
            "which adds no throughput on a rate-limited single ccxt instance"
        )

    def test_it_is_named_as_a_slow_subset(self):
        s = fp.summarize(self._real(), ceiling_s=CEILING)
        assert s["shape"] == "slow_subset"

    def test_the_slow_group_is_named(self):
        # Which symbols IS the answer, and it is cheap to supply.
        out = fp.render(self._real(), ceiling_s=CEILING)
        assert "RMSFT/USDT" in out or "RAAPL/USDT" in out

    def test_the_fast_majority_is_not_named(self):
        s = fp.summarize(self._real(), ceiling_s=CEILING)
        assert all(x.startswith("R") for x in s["subset"]), (
            "naming fast symbols in the slow group makes the list useless"
        )

    def test_it_still_stops_short_of_saying_why(self):
        out = fp.render(self._real(), ceiling_s=CEILING)
        assert "cannot answer for you" in out, (
            "what the group has in common is exactly the judgement a profile "
            "is not entitled to make"
        )

    def test_a_subset_too_small_to_matter_is_not_promoted(self):
        p = fp.new_profile()
        for i in range(98):
            fp.record(p, f"SYM{i}/USDT", 2.0)
        fp.record(p, "ODD/USDT", 14.9)
        s = fp.summarize(p, ceiling_s=CEILING)
        assert s["shape"] != "slow_subset", (
            "one straggler in a hundred is not a group"
        )

    def test_the_boundary_is_the_constant(self):
        def build(n_slow):
            p = fp.new_profile()
            for i in range(100 - n_slow):
                fp.record(p, f"SYM{i}/USDT", 2.0)
            for i in range(n_slow):
                fp.record(p, f"SLOW{i}/USDT", 14.9)
            return p
        below = int(100 * fp.SUBSET_MIN_SHARE) - 1
        assert fp.summarize(build(below), ceiling_s=CEILING)["shape"] != "slow_subset"
        at = int(100 * fp.SUBSET_MIN_SHARE)
        assert fp.summarize(build(at), ceiling_s=CEILING)["shape"] == "slow_subset"


class TestItNamesTheWorstSymbol:
    """"max 74.94s" recurring across three cycles, and no symbol attached.

    The operator read that number in production, correctly inferred one
    symbol was serially responsible, and had to ask WHICH. The profile held
    the answer per-symbol the whole time and never printed it — and
    at_ceiling, the shape that fires under load, was the one branch that
    named no symbol at all.

    `top_symbols` does not answer this. It ranks by TOTAL time, so a symbol
    called often out-totals the one symbol that parks for 75 seconds once —
    which is precisely the case worth finding.
    """

    def _mixed(self):
        p = fp.new_profile()
        # BUSIEST by total: many moderate calls.
        for _ in range(40):
            fp.record(p, "BUSY/USDT", 5.0)          # 200s total, max 5s
        # WORST single call, seen once.
        fp.record(p, "CULPRIT/USDT", 74.94)         # 74.94s total, max 74.94s
        for i in range(30):
            fp.record(p, f"SYM{i}/USDT", 1.0)
        return p

    def test_it_names_the_symbol_holding_the_max(self):
        s = fp.summarize(self._mixed(), ceiling_s=CEILING)
        assert s["worst_symbol"] == "CULPRIT/USDT"
        assert s["worst_symbol_max_s"] == 74.94

    def test_that_is_not_the_same_as_the_biggest_total(self):
        # The reason a separate field is needed at all.
        s = fp.summarize(self._mixed(), ceiling_s=CEILING)
        assert s["top_symbols"][0][0] == "BUSY/USDT"
        assert s["worst_symbol"] != s["top_symbols"][0][0]

    def test_the_max_it_reports_is_the_profile_max(self):
        s = fp.summarize(self._mixed(), ceiling_s=CEILING)
        assert s["worst_symbol_max_s"] == s["max_s"]

    def test_the_at_ceiling_rendering_names_it(self):
        # at_ceiling is the shape that fires under load and named nothing.
        p = fp.new_profile()
        for i in range(70):
            fp.record(p, f"SYM{i}/USDT", 14.5)
        fp.record(p, "CULPRIT/USDT", 74.94)
        out = fp.render(p, ceiling_s=CEILING)
        assert fp.summarize(p, ceiling_s=CEILING)["shape"] == "at_ceiling"
        assert "CULPRIT/USDT" in out

    def test_the_engine_note_names_it_too(self):
        # The line the operator actually reads is _stage_report's suffix.
        src = code_only(open("bot/core/engine.py", encoding="utf-8").read())
        i = src.index('if s["shape"] == "at_ceiling":')
        window = src[i:i + 800]
        assert "worst_symbol" in window, (
            "the branch that fires under load must name the culprit"
        )

    def test_an_empty_profile_has_no_worst(self):
        assert fp.summarize(fp.new_profile(), ceiling_s=CEILING) == {}

    def test_a_single_sample_is_its_own_worst(self):
        p = fp.new_profile()
        fp.record(p, "ONLY/USDT", 3.0)
        s = fp.summarize(p, ceiling_s=CEILING)
        assert s["worst_symbol"] == "ONLY/USDT"

    def test_ties_are_broken_deterministically(self):
        # Two symbols at the same max must not make the report flap between
        # runs — a diagnostic that changes without the system changing is
        # read as movement that did not happen.
        def build(order):
            p = fp.new_profile()
            for sym in order:
                fp.record(p, sym, 10.0)
            return fp.summarize(p, ceiling_s=CEILING)["worst_symbol"]
        assert build(["B/USDT", "A/USDT"]) == build(["A/USDT", "B/USDT"])


class TestItRefusesToClaimAShapeFromTooLittle:
    def test_below_min_sample_says_insufficient(self):
        s = fp.summarize(_spread(3, 15.0), ceiling_s=CEILING)
        assert s["shape"] == "insufficient"

    def test_insufficient_still_reports_the_numbers(self):
        # Refusing a shape is not refusing the measurement.
        s = fp.summarize(_spread(3, 15.0), ceiling_s=CEILING)
        assert s["count"] == 3 and s["max_s"] == 15.0

    def test_the_boundary_is_the_constant(self):
        assert fp.summarize(_spread(fp.MIN_SAMPLE - 1, 4.0),
                            ceiling_s=CEILING)["shape"] == "insufficient"
        assert fp.summarize(_spread(fp.MIN_SAMPLE, 4.0),
                            ceiling_s=CEILING)["shape"] != "insufficient"

    def test_an_empty_profile_returns_nothing_at_all(self):
        assert fp.summarize(fp.new_profile(), ceiling_s=CEILING) == {}
        assert fp.render(fp.new_profile(), ceiling_s=CEILING) == ""


class TestNoCeilingMeansNoCeilingClaim:
    def test_a_zero_ceiling_disables_the_test(self):
        # Inventing a limit would manufacture the finding this measures.
        s = fp.summarize(_spread(70, 15.2), ceiling_s=0)
        assert s["shape"] != "at_ceiling"
        assert "near_ceiling" not in s

    @pytest.mark.parametrize("bad", [None, -1, "abc", float("nan"),
                                     float("inf")])
    def test_an_unusable_ceiling_does_not_crash_or_claim(self, bad):
        # Unusable = there is no limit to be near. Disable the test rather
        # than invent one.
        s = fp.summarize(_spread(70, 15.2), ceiling_s=bad)  # type: ignore[arg-type]
        assert s.get("shape") != "at_ceiling"
        assert "near_ceiling" not in s

    def test_a_coercible_ceiling_is_honoured_not_discarded(self):
        # "15" is not nonsense, it is a string that plainly means 15 seconds.
        # Discarding it would silently drop a real measurement — the failure
        # mode is a missing finding, which is exactly what this module exists
        # to prevent.
        s = fp.summarize(_spread(70, 15.2), ceiling_s="15")  # type: ignore[arg-type]
        assert s["shape"] == "at_ceiling"


class TestItNeverRaises:
    @pytest.mark.parametrize("bad", [None, 7, "profile", []])
    def test_junk_profile_is_absorbed(self, bad):
        fp.record(bad, "BTC/USDT", 1.0)          # type: ignore[arg-type]
        assert fp.summarize(bad, ceiling_s=CEILING) == {}   # type: ignore[arg-type]
        assert fp.render(bad, ceiling_s=CEILING) == ""      # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [None, "slow", float("nan"),
                                     float("inf"), -1.0])
    def test_a_junk_duration_is_dropped_not_accumulated(self, bad):
        p = fp.new_profile()
        fp.record(p, "BTC/USDT", bad)            # type: ignore[arg-type]
        assert p["count"] == 0, (
            "a clock that went backwards would drag the mean below every "
            "sample and make the profile disagree with its own maximum"
        )

    def test_a_missing_symbol_is_recorded_not_guessed(self):
        p = fp.new_profile()
        fp.record(p, None, 3.0)                  # type: ignore[arg-type]
        assert p["count"] == 1 and "?" in p["by_symbol"]

    def test_the_sample_list_is_bounded(self):
        # A long-running engine must not accumulate without limit.
        p = fp.new_profile()
        for _ in range(2500):
            fp.record(p, "BTC/USDT", 1.0)
        assert len(p["samples"]) <= 2000
        assert p["count"] == 2500, "the COUNT must stay exact"


class TestTheRenderingStopsShortOfACause:
    def test_it_never_names_a_cause(self):
        banned = ("throttl", "drop these", "the venue is", "rate limit is",
                  "you should", "caused by")
        for p, c in ((_spread(70, 15.2), CEILING), (_spread(70, 4.0), CEILING),
                     (_spread(3, 15.0), CEILING)):
            out = fp.render(p, ceiling_s=c).lower()
            for b in banned:
                assert b not in out, f"named a cause: {b!r}"

    def test_the_near_ceiling_line_is_framed_as_a_measurement(self):
        out = fp.render(_spread(70, 15.2), ceiling_s=CEILING)
        assert "at or above" in out, "the number must read as a count"
        assert "queueing behind a full rate-limit bucket can park a fetch" in out, (
            "the competing explanation must travel WITH the finding, or the "
            "count reads as a diagnosis"
        )

    def test_every_shape_carries_its_own_caveat(self):
        for p in (_spread(70, 15.2), _spread(70, 4.0)):
            assert "though" in fp.render(p, ceiling_s=CEILING)

    def test_it_names_the_slowest_symbols(self):
        p = fp.new_profile()
        for i in range(20):
            fp.record(p, f"SYM{i}/USDT", 1.0)
        fp.record(p, "FET/USDT", 200.0)
        out = fp.render(p, ceiling_s=CEILING)
        assert "FET/USDT" in out, (
            "telling someone their fetch is slow without saying which symbols "
            "sends them to the logs for the one fact that ends the search"
        )


class TestTheEngineRecordsAndReportsIt:
    def _src(self) -> str:
        return code_only(open("bot/core/engine.py", encoding="utf-8").read())

    def test_both_fetch_sites_record(self):
        src = self._src()
        assert src.count('_stage_profile_record(self, "fetch", signal.symbol') == 2, (
            "the lightweight path fetches too; instrumenting one of two "
            "sites is how a surface ends up disagreeing with itself"
        )

    def test_the_slow_stages_all_record(self):
        """The bottleneck MOVED from fetch to mtf inside one day.

        Instrumenting only the stage that was slow when this was written is
        how the profiler ends up pointed at the wrong one.
        """
        src = self._src()
        for stage in ("fetch", "mtf", "analyze"):
            assert f'_stage_profile_record(self, "{stage}"' in src, stage

    def test_the_recorder_is_getattr_guarded_like_the_stage_mark(self):
        src = self._src()
        i = src.index("def _stage_profile_record(")
        # Window measured, not guessed: code_only blanks the docstring but
        # PRESERVES offsets, so a ~700-char docstring pushes the body past a
        # 900-char window and the assertion fails on correct code.
        body = src[i:i + 2000]
        assert "except Exception:" in body, (
            "_analyze_signal runs under lightweight stubs; instrumentation "
            "that requires the caller to grow an attribute broke five suites "
            "once already"
        )

    def test_the_duration_is_measured_once_not_twice(self):
        # Calling time.monotonic() again for the profile would record a
        # different number than the stage total, and two instruments
        # disagreeing about the same event is worse than one.
        src = self._src()
        for stage, var in (("fetch", "_fetch_dt"), ("mtf", "_mtf_dt"),
                           ("analyze", "_an_dt")):
            assert f'self._stage_add("{stage}", {var})' in src, stage

    def test_the_note_follows_the_dominant_stage(self):
        src = self._src()
        assert "_top = max(acc.items(), key=lambda kv: kv[1]" in src, (
            "a note hardcoded to `fetch` was describing the wrong stage "
            "within a day of being written"
        )
        assert "_top[1] >= total * 0.5" in src, (
            "a healthy tick must not grow a diagnostic it does not need"
        )

    def test_the_note_never_costs_the_breakdown(self):
        """A stub with _stage_totals and nothing else must still get a report.

        Calling self._stage_shape_note directly made _stage_report return ""
        for every such stub: the outer except swallowed the AttributeError
        and took the WHOLE breakdown with it, so adding a diagnostic DELETED
        the line it was meant to enrich. Three tests in
        test_analysis_stage_breakdown.py caught it, and this file's own
        _stage_profile_record docstring warns about exactly this hazard.
        """
        from bot.core.engine import RuneClawEngine

        class Stub:
            _stage_totals = {"fetch": 10.0, "mtf": 90.0}
            _stage_report = RuneClawEngine._stage_report

        out = Stub()._stage_report(120.0)
        assert out, "the breakdown vanished because of its own instrumentation"
        assert "mtf 90s" in out

    def test_the_guard_is_present_at_the_call_site(self):
        src = self._src()
        i = src.index('_top = max(acc.items()')
        body = src[i:i + 500]
        assert 'getattr(self, "_stage_shape_note", None)' in body

    def test_the_note_stays_on_one_line(self):
        # Both callers embed _stage_report in a single-line audit string.
        src = self._src()
        i = src.index("def _stage_shape_note(")
        body = src[i:src.index("\n    def ", i + 10)]
        assert "\\n" not in body, "a wrapped note would break both audit lines"

    def test_the_ceiling_is_only_applied_to_the_exchange_stages(self):
        """analyze is an LLM call, not market data.

        Testing it against MARKET_DATA_TIMEOUT_MS would invent a finding — a
        near-ceiling count against a limit that never bounded those calls.
        """
        src = self._src()
        i = src.index("def _stage_shape_note(")
        body = src[i:i + 2000]
        assert 'if stage in ("fetch", "mtf"):' in body
        assert 'getattr(CONFIG, "market_data_timeout_ms", 0)' in body, (
            "a hardcoded 15s would silently stop matching MARKET_DATA_TIMEOUT_MS"
        )
        assert "/ 1000.0" in body, "the config is milliseconds"
