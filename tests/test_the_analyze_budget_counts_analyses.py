"""
"at least 3 will not be analysed" on a tick that analysed 21 of 40.

From the live incident of 2026-09-16 — engine halted, LIVE, six consecutive
TimeoutErrors, the analyze phase hitting its 300s cap fifteen times over:

    Tick phase timed out: analyze (exceeded its 300s, x15)
      ↳ 37/40 signals attempted before it was cancelled — 16 of them gave up
        at the per-symbol cap and were not analysed
    📉 Analyze budget short: 40 signals at ≥8.2s each against a 300s cap —
       at least 4 will not be analysed. … Lower TOP_MOVERS_COUNT or raise
       SCAN_ANALYSIS_CONCURRENCY.

`_record_analyze_throughput` set `per_signal_s = elapsed / done`, and `done`
COUNTS ATTEMPTS. `tests/test_status_counts_attempts_not_analyses.py` says so
in its own docstring — *"the batch's `finally` increments it for a symbol
that timed out exactly as it does for one that finished. 'Analysed' was the
label's claim, not the counter's. THE LABEL SAYS 'ATTEMPTED' NOW"* — and that
fix reached the label and stopped. The rate one function over still divided by
the attempt count, with `gave_up` sitting in the same progress dict the
recorder was called from.

AND THE SECOND CALL SITE JUSTIFIED IT IN A COMMENT. The cancelled-batch path
read *"A cancelled batch measured a real rate for the analyses it DID
finish"* — about `_done`, which counts attempts. The exact confusion the guard
was written to end, restated as a reason 1500 lines from it, with a third
surface (the `result="TIMEOUT"` audit line) saying "It had finished {done} of
{of} signals".

Driven through the real recorder and the real forecast, that batch:

    attempted 37 · gave up 16 · ANALYSED 21   in ~300s
    8.1s per attempt   <- what the card quoted
    14.3s per analysis <- what "will not be analysed" means
    card: "at least 3"   honest: 19 of 40

AND THE REMEDY IS THE WRONG NOUN. Both knobs it names are THROUGHPUT knobs;
43% of attempts producing nothing is a LATENCY fact. 16 give-ups x the 90s
`ANALYSIS_TIMEOUT_SEC` at the default 12-way concurrency is 120s of a 300s
phase spent on symbols that produce nothing, and the lever for that is the cap
itself. The clause ADDS that rather than replacing the advice — no threshold
is invented, and each remedy is named beside the size of what it addresses.
"""

from __future__ import annotations

import ast
import io
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot.core.engine as engine_mod
import bot.utils.i18n as i18n_mod
from bot.core.engine import RuneClawEngine, give_up_cost_s
from bot.formatters.rich_cards import analyze_budget_line

#: The measuring batch off the live card.
ASKED, ATTEMPTED, GAVE_UP, ELAPSED, CAP = 40, 37, 16, 300.0, 300.0
#: What the batch really delivered. NOT `ATTEMPTED - GAVE_UP`: that
#: subtraction is the `losses = len(all) - wins` shape, and section 5 drives
#: the two reachable ways it overcounts. 21 is the value that subtraction
#: gives, kept as a named decoy so a test asserting it is obviously doing so.
ANALYSED = 21
SUBTRACTED = ATTEMPTED - GAVE_UP        # 21 -- the number, not the reading


def _eng():
    """A bare surface — the recorder and the forecaster need no live engine."""
    e = SimpleNamespace(_analyze_throughput=None)
    for name in ("_record_analyze_throughput", "_forecast_analyze_capacity"):
        setattr(e, name, getattr(RuneClawEngine, name).__get__(e))
    return e


def _forecast(gave_up, analysed=ANALYSED, of=ASKED, attempts=ATTEMPTED,
              elapsed=ELAPSED, cap=CAP, timeout=90.0, conc=12):
    """Record one batch and forecast the next.

    `analysed` is EXPLICIT and independent of `gave_up` -- deriving it here
    would rebuild in the fixture the subtraction the slice removed from the
    code, and every test would then agree with a defect.
    """
    e = _eng()
    stub = SimpleNamespace(
        monitoring=SimpleNamespace(tick_phase_timeout_sec=cap),
        analysis_timeout_sec=timeout, scan_analysis_concurrency=conc)
    with patch.object(engine_mod, "CONFIG", stub):
        e._record_analyze_throughput(attempts, of, elapsed,
                                     gave_up=gave_up, analysed=analysed)
        return e._forecast_analyze_capacity(of)


def _plain(html):
    return re.sub("<[^>]+>", "", html or "")


# ── 1. the rate is per ANALYSIS ───────────────────────────────────────────

class TestTheRateCountsAnalyses:
    def test_the_recorder_keeps_attempts_and_analyses_apart(self):
        e = _eng()
        e._record_analyze_throughput(ATTEMPTED, ASKED, ELAPSED,
                                     gave_up=GAVE_UP, analysed=ANALYSED)
        tp = e._analyze_throughput
        assert tp["attempts"] == ATTEMPTED
        assert tp["gave_up"] == GAVE_UP
        assert tp["analysed"] == ANALYSED
        assert tp["per_attempt_s"] == pytest.approx(ELAPSED / ATTEMPTED)
        assert tp["per_analysis_s"] == pytest.approx(ELAPSED / ANALYSED)

    def test_the_live_incidents_shortfall(self):
        """The whole slice in one assertion: 3 was the card's figure."""
        rec = _forecast(GAVE_UP)
        assert rec["rate_basis"] == "analysis"
        assert rec["per_signal_s"] == pytest.approx(14.29, abs=0.01)
        assert rec["fits"] == ANALYSED
        assert rec["shortfall"] == ASKED - ANALYSED == 19
        # What it WAS, so the regression is named rather than described.
        assert _forecast(None, analysed=None)["shortfall"] == 3

    def test_a_batch_with_no_give_ups_is_unchanged(self):
        """Attempts ARE analyses there, so the same numbers as before."""
        rec = _forecast(0, analysed=ATTEMPTED)
        assert rec["rate_basis"] == "analysis"
        assert rec["per_signal_s"] == pytest.approx(ELAPSED / ATTEMPTED, abs=.01)
        assert (rec["gave_up"], rec["analysed"]) == (0, ATTEMPTED)

    def test_an_absent_count_falls_back_and_says_so(self):
        """ABSENT IS NOT ZERO. An older build recorded no give-ups; the rate
        is then per ATTEMPT, which is a WIDER claim than the sentence makes,
        and `rate_basis` is how a reader is told rather than left to assume."""
        rec = _forecast(None, analysed=None)
        assert rec["rate_basis"] == "attempt"
        assert rec["gave_up"] is None and rec["analysed"] is None

    def test_a_counted_zero_abstains_rather_than_quoting_the_attempt_rate(self):
        """A COUNTED ZERO IS A MEASUREMENT, and the loudest one here.

        Every attempt gave up. `elapsed / 0` is no rate at all, and the
        attempt rate is not a stand-in for it: quoting 8.1s/attempt there
        prints "at least 3 of 40 will not be analysed" directly above the
        clause saying all 37 analysed nothing. Two numbers, opposite
        stories, and the wrong one is the reassuring one. The forecast
        abstains; `_analyze_progress` and the phase-timeout line carry the
        fact.
        """
        assert _forecast(ATTEMPTED, analysed=0) is None
        assert analyze_budget_line(None) == ""

    def test_an_uncounted_batch_is_not_a_batch_that_analysed_nothing(self):
        """The two reasons `per_analysis_s` is None, kept apart. `None` is an
        older record and still earns the attempt-rate fallback; `0` is a
        reading and earns silence."""
        assert _forecast(GAVE_UP, analysed=None)["rate_basis"] == "attempt"
        assert _forecast(GAVE_UP, analysed=0) is None

    JUNK = [
        (-1, "negative"),
        (ATTEMPTED + 1, "more than the batch attempted"),
        ("16", "a string is not a count"),
        (1.5, "int() would quietly make this 1"),
        (True, "a bool is an int in Python and is not a count"),
        (None, "nobody counted"),
    ]

    @pytest.mark.parametrize("junk, why", JUNK)
    def test_only_an_integer_count_within_the_batch_is_read(self, junk, why):
        """Each count is validated FOR ITSELF. The good one survives its
        neighbour being junk -- they are independent readings, and dropping
        a measurement because a sibling is unreadable is the failed-read-
        as-empty shape one field over."""
        e = _eng()
        e._record_analyze_throughput(ATTEMPTED, ASKED, ELAPSED,
                                     gave_up=junk, analysed=ANALYSED)
        tp = e._analyze_throughput
        assert tp["gave_up"] is None, why
        assert tp["analysed"] == ANALYSED, "the readable count still reads"
        assert tp["per_analysis_s"] == pytest.approx(ELAPSED / ANALYSED)

    @pytest.mark.parametrize("junk, why", JUNK)
    def test_a_junk_analysis_count_yields_no_analysis_rate(self, junk, why):
        """The other direction: an unreadable `analysed` leaves the rate
        unmeasured rather than deriving one from `done - gave_up`."""
        e = _eng()
        e._record_analyze_throughput(ATTEMPTED, ASKED, ELAPSED,
                                     gave_up=GAVE_UP, analysed=junk)
        tp = e._analyze_throughput
        assert tp["analysed"] is None, why
        assert tp["per_analysis_s"] is None, why
        assert tp["gave_up"] == GAVE_UP, "the readable count still reads"
        # The attempt rate is still a real measurement of the batch.
        assert tp["per_attempt_s"] == pytest.approx(ELAPSED / ATTEMPTED)

    def test_the_recorder_never_subtracts_one_count_from_the_other(self):
        """THE FIXTURE HAS TO MAKE THEM DIFFER. On the live incident's own
        numbers `attempts - gave_up` IS 21, so a recorder that subtracted
        would agree with every figure in this file -- which is what a
        derivation looks like from outside until you plant a batch the
        subtraction gets wrong."""
        e = _eng()
        e._record_analyze_throughput(ATTEMPTED, ASKED, ELAPSED,
                                     gave_up=GAVE_UP, analysed=9)
        tp = e._analyze_throughput
        assert tp["analysed"] == 9 != SUBTRACTED
        assert tp["per_analysis_s"] == pytest.approx(ELAPSED / 9)


# ── 2. what the give-ups cost the phase ───────────────────────────────────

class TestTheGiveUpCost:
    def _cost(self, n, timeout=90.0, conc=12):
        stub = SimpleNamespace(analysis_timeout_sec=timeout,
                               scan_analysis_concurrency=conc)
        with patch.object(engine_mod, "CONFIG", stub):
            return give_up_cost_s(n)

    def test_the_live_figure(self):
        assert self._cost(GAVE_UP) == 120.0      # 16 x 90 / 12, of a 300s cap

    @pytest.mark.parametrize("bad", [
        {"timeout": 0.0}, {"conc": 0}, {"timeout": -5.0}, {"conc": -1},
    ])
    def test_an_unread_input_is_none_not_zero(self, bad):
        """A cost derived from a guessed concurrency is the fabricated
        operator number this module refuses everywhere else."""
        assert self._cost(GAVE_UP, **bad) is None

    @pytest.mark.parametrize("n", [0, None, -3])
    def test_no_give_ups_costs_nothing_to_report(self, n):
        assert self._cost(n) is None

    def test_it_needs_no_engine(self):
        """MODULE-LEVEL. The forecaster's own guard builds a bare
        SimpleNamespace under the comment "the forecaster must not need a live
        engine"; hanging this off `self` would have made it need one — and
        did, until the guard said so."""
        import inspect
        assert not inspect.ismethod(give_up_cost_s)
        assert "self" not in inspect.signature(give_up_cost_s).parameters


# ── 3. the card ───────────────────────────────────────────────────────────

class TestTheCardNamesTheLeverForWhatItMeasured:
    def test_the_give_up_clause_names_the_per_symbol_cap(self):
        out = _plain(analyze_budget_line(_forecast(GAVE_UP, analysed=ANALYSED)))
        assert "at least 19 will not be analysed" in out
        assert "16 of those 37 attempts gave up" in out
        assert "120s of the phase" in out
        assert "ANALYSIS_TIMEOUT_SEC" in out, (
            "the only lever that shortens a give-up is unnamed")
        # ADDED, not swapped: both knobs really do raise throughput.
        assert "TOP_MOVERS_COUNT" in out and "SCAN_ANALYSIS_CONCURRENCY" in out

    def test_a_clean_batch_gets_no_caveat(self):
        out = _plain(analyze_budget_line(_forecast(0)))
        assert "gave up" not in out
        assert "counts ATTEMPTS" not in out, (
            "a caveat printed when there is nothing to caveat is one nobody "
            "reads")

    def test_an_absent_count_says_the_rate_counts_attempts(self):
        out = _plain(analyze_budget_line(_forecast(None, analysed=None)))
        assert "counts ATTEMPTS" in out
        assert "gave up at the per-symbol cap and analysed nothing" not in out

    def test_the_count_it_quotes_is_never_coerced(self):
        """The clause names `measured_from`. `or 0` would print "16 of those 0
        attempts"; the caller's rule for a malformed forecast is to say
        nothing rather than render a stray number, and this follows it.

        `partial` is cleared FIRST, and that is the whole test. The floor
        variant of the base sentence interpolates `measured_from` itself, so
        with it left on, `int(None)` raises inside the caller's own `except`
        and the line comes back "" before the clause is ever reached — the
        assertion then passes for a reason that has nothing to do with the
        guard it names. The mutation round found exactly that: replacing the
        guard with `attempts = attempts or 0` SURVIVED.
        """
        rec = _forecast(GAVE_UP)
        rec["partial"] = False
        assert "gave up" in _plain(analyze_budget_line(rec)), (
            "the fixture must reach the clause at all")
        rec["measured_from"] = None
        out = _plain(analyze_budget_line(rec))
        assert out, "the base sentence should still render"
        assert "gave up" not in out
        assert "those 0 attempts" not in out

    def test_an_unreadable_cost_still_reports_the_count(self):
        rec = _forecast(GAVE_UP)
        rec["gave_up_cost_s"] = None
        out = _plain(analyze_budget_line(rec))
        assert "16 of those 37 attempts gave up" in out
        assert "of the phase" not in out, "a cost nobody could read was quoted"

    def test_a_contradictory_row_gets_no_caveat(self):
        """`rate_basis: analysis` with no count is a pair the forecast never
        emits. Claiming the rate counts attempts would be the WRONG caveat
        rather than a missing one."""
        rec = _forecast(GAVE_UP)
        rec["gave_up"], rec["rate_basis"] = None, "analysis"
        assert "counts ATTEMPTS" not in _plain(analyze_budget_line(rec))

    def test_the_measured_batch_is_attempted_not_done(self):
        """'done' reads as 'analysed'. The status card was moved off that word
        by `test_status_counts_attempts_not_analyses`; this is the sibling
        surface quoting the same count."""
        out = _plain(analyze_budget_line(_forecast(GAVE_UP)))
        assert "37 of 40 attempted" in out
        assert "of 40 done" not in out


# ── 4. every language, and the writers ────────────────────────────────────

class TestItSpeaksEveryLanguage:
    KEYS = ("fmt_analyze_budget_gave_up_cost", "fmt_analyze_budget_gave_up",
            "fmt_analyze_budget_attempt_basis")

    def test_all_fourteen(self):
        for key in self.KEYS:
            langs = set(i18n_mod._STRINGS[key])
            assert len(langs) == 14, f"{key}: {sorted(langs)}"

    def test_each_one_formats(self):
        for key in self.KEYS:
            for lang in i18n_mod._STRINGS[key]:
                s = i18n_mod.t(key, lang).format(gave_up=16, attempts=37,
                                                 cost=120.0)
                assert s and "{" not in s, (key, lang, s)

    def test_the_cap_is_named_in_every_language(self):
        """The lever is an env var; it is not translated, and a sentence that
        drops it is a remedy that names nothing."""
        for lang in i18n_mod._STRINGS["fmt_analyze_budget_gave_up_cost"]:
            assert "ANALYSIS_TIMEOUT_SEC" in i18n_mod.t(
                "fmt_analyze_budget_gave_up_cost", lang)

    def test_no_language_still_says_done(self):
        stale = ("done)", "完成 {measured_from}", "fertig)",
                 "terminés)", "completadas)", "completati)",
                 "concluídos)", "klaar)", "tanesi bitti)",
                 "готово {measured_from}",
                 "{measured_from} 件完了", "{measured_from}개 완료",
                 "اكتملت)", "{measured_from} पूरे)")
        tbl = i18n_mod._STRINGS["fmt_analyze_budget_short_floor"]
        for lang, text in tbl.items():
            for word in stale:
                assert word not in text, f"{lang} still says {word!r}"


class TestBothWritersHandTheCountOn:
    """A scan, and it says so: both calls sit inside a branch a unit test
    cannot reach without an engine, a signal and an analyzer. What is DRIVEN
    is the reading — above, through the real recorder."""

    @staticmethod
    def _calls():
        tree = ast.parse(io.open("bot/core/engine.py", encoding="utf-8").read())
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id in ("_rt", "_rec_tp")]

    def test_every_writer_passes_gave_up(self):
        calls = self._calls()
        assert len(calls) == 2, f"expected 2 recorder calls, found {len(calls)}"
        for call in calls:
            kw = {k.arg for k in call.keywords}
            assert "gave_up" in kw, (
                f"engine.py:{call.lineno} records a rate without the give-up "
                "count, so it silently means per-ATTEMPT while the card "
                "claims analyses")

    def test_the_cancelled_batch_no_longer_claims_analyses(self):
        """Its comment used to read "a real rate for the analyses it DID
        finish" — about a count of attempts."""
        src = io.open("bot/core/engine.py", encoding="utf-8").read()
        assert "the analyses it DID\n                # finish" not in src
        assert "It had attempted {_done} of {_of} signals" in src, (
            "the TIMEOUT audit line still says 'finished'")

    def test_the_recorder_has_no_default_that_means_zero(self):
        """A caller that does not say leaves the row unclassifiable, which is
        the honest answer and never the flattering one."""
        import inspect
        sig = inspect.signature(RuneClawEngine._record_analyze_throughput)
        assert sig.parameters["gave_up"].default is None

    def test_every_writer_passes_the_analysis_count_too(self):
        """`gave_up` alone is half a taxonomy, and section 5 drives the
        half it misses."""
        for call in self._calls():
            kw = {k.arg for k in call.keywords}
            assert "analysed" in kw, (
                f"engine.py:{call.lineno} records a rate without the analysis "
                "count, so the recorder has to subtract one -- which section "
                "5 shows overcounts by the whole in-flight batch")

    def test_neither_count_has_a_default_that_means_zero(self):
        """A caller that does not say leaves the row unclassifiable, which is
        the honest answer and never the flattering one."""
        import inspect
        sig = inspect.signature(RuneClawEngine._record_analyze_throughput)
        assert sig.parameters["analysed"].default is None


# ── 5. ANALYSED IS COUNTED, NOT SUBTRACTED ────────────────────────────────
#
# `analysed = attempts - gave_up` is the `losses = len(all) - wins` shape
# from CLAUDE.md's own table: it assumes the taxonomy is complete. The batch
# has FOUR exits and `gave_up` counts one of them, so the subtraction calls
# the other two analyses. Both are driven here through the REAL batch method,
# because a mirror of the structure is a second answer about what the
# structure does.

class TestTheSubtractionOvercounts:
    @staticmethod
    def _host(analyze):
        host = SimpleNamespace(_analyze_signal=analyze, _symbol_cooldowns={},
                               _record_analyze_throughput=lambda *a, **k: None)
        host._analyze_signals_batched = (
            RuneClawEngine._analyze_signals_batched.__get__(host))
        return host

    @pytest.mark.asyncio
    async def test_an_analysis_that_raises_is_not_an_analysis(self):
        """`except Exception` returns None without touching `gave_up`, so the
        subtraction counts a venue error as a delivered analysis."""
        import dataclasses

        from bot.config import CONFIG

        async def analyze(sig, *a, **k):
            if sig.symbol.startswith("BAD"):
                raise RuntimeError("venue said no")
            return None

        host = self._host(analyze)
        sigs = [SimpleNamespace(symbol=f"{n}/USDT:USDT")
                for n in ("OK1", "BAD1", "OK2", "BAD2")]
        with patch.object(engine_mod, "CONFIG",
                          dataclasses.replace(CONFIG, analysis_timeout_sec=5.0)):
            await host._analyze_signals_batched(sigs)

        prog = host._analyze_progress
        assert prog["done"] == 4, "all four were ATTEMPTED"
        assert prog["gave_up"] == 0, "none hit the per-symbol cap"
        assert prog["analysed"] == 2, "only two analyses ran"
        assert prog["done"] - prog["gave_up"] == 4, (
            "the subtraction answers 4 where 2 ran -- this assertion IS the "
            "defect, pinned so a return to it is loud")

    @pytest.mark.asyncio
    async def test_a_batch_cancelled_at_the_phase_cap_analysed_none_of_it(self):
        """THE LIVE INCIDENT'S SHAPE. `asyncio.CancelledError` is a
        BaseException, so neither handler catches it -- but the `finally`
        still runs and still increments `done`. Every symbol in flight when
        the PHASE cap cancelled the gather is therefore an attempt the
        subtraction calls an analysis.
        """
        import asyncio
        import dataclasses

        from bot.config import CONFIG

        async def analyze(sig, *a, **k):
            await asyncio.sleep(30)          # still running when cancelled
            return None

        host = self._host(analyze)
        sigs = [SimpleNamespace(symbol=f"S{n}/USDT:USDT") for n in range(4)]
        with patch.object(engine_mod, "CONFIG",
                          # per-symbol cap far beyond the phase cap below, so
                          # nothing gives up: the phase cancel gets there first
                          dataclasses.replace(CONFIG, analysis_timeout_sec=60.0)):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    host._analyze_signals_batched(sigs), timeout=0.2)

        prog = host._analyze_progress
        assert prog["done"] == 4, "every finally ran -- CancelledError included"
        assert prog["gave_up"] == 0, "no symbol reached its own cap"
        assert prog["analysed"] == 0, "not one analysis completed"
        assert prog["done"] - prog["gave_up"] == 4, (
            "the subtraction calls a wholly-cancelled batch four analyses")

    @pytest.mark.asyncio
    async def test_a_clean_batch_counts_every_analysis(self):
        """The decoy: where the taxonomy IS complete the count and the
        subtraction agree, which is exactly why the defect read as working."""
        import dataclasses

        from bot.config import CONFIG

        async def analyze(sig, *a, **k):
            return None

        host = self._host(analyze)
        sigs = [SimpleNamespace(symbol=f"S{n}/USDT:USDT") for n in range(3)]
        with patch.object(engine_mod, "CONFIG",
                          dataclasses.replace(CONFIG, analysis_timeout_sec=5.0)):
            await host._analyze_signals_batched(sigs)

        prog = host._analyze_progress
        assert (prog["done"], prog["gave_up"], prog["analysed"]) == (3, 0, 3)

    @pytest.mark.asyncio
    async def test_the_count_reaches_the_recorder_from_the_real_batch(self):
        """Wiring, driven: whatever the batch counted is what the rate is
        built from. A counter nobody hands on is a counter."""
        import dataclasses

        from bot.config import CONFIG

        seen = {}

        async def analyze(sig, *a, **k):
            if sig.symbol.startswith("BAD"):
                raise RuntimeError("venue said no")
            return None

        host = self._host(analyze)
        host._record_analyze_throughput = (
            lambda *a, **k: seen.update(args=a, kw=k))
        sigs = [SimpleNamespace(symbol=n) for n in
                ("OK1/USDT:USDT", "BAD1/USDT:USDT", "OK2/USDT:USDT")]
        with patch.object(engine_mod, "CONFIG",
                          dataclasses.replace(CONFIG, analysis_timeout_sec=5.0)):
            await host._analyze_signals_batched(sigs)

        assert seen["kw"]["analysed"] == 2
        assert seen["kw"]["gave_up"] == 0
        assert seen["args"][0] == 3, "attempts still travel as the first arg"

    @pytest.mark.asyncio
    async def test_the_cancelled_phase_hands_the_count_on(self):
        """THE LIVE INCIDENT'S OWN WRITER, driven.

        `_phase`'s timeout handler is the recorder call that runs when the
        analyze phase hits its cap -- the exact path of 2026-09-16, and the
        one whose next tick the forecast is for. Mutating it to pass
        `analysed=None` survived every other guard in this file: the AST
        test sees the keyword and not its value, and nothing else reaches
        this branch. A batch recorded without its count falls back to the
        attempt rate, which is the whole defect, on the one path it matters
        most.
        """
        import asyncio
        import time

        seen = {}
        host = SimpleNamespace(
            _analyze_progress={"of": ASKED, "done": ATTEMPTED,
                               "gave_up": GAVE_UP, "analysed": 9,
                               "started": time.monotonic(), "seq": 1},
            _record_analyze_throughput=lambda *a, **k: seen.update(a=a, k=k),
            _record_phase_duration=lambda *a, **k: None,
            _stage_report=lambda _s: "",
            _stage_totals={},
        )
        host._phase = RuneClawEngine._phase.__get__(host)

        async def _hangs():
            await asyncio.sleep(30)

        stub = SimpleNamespace(
            monitoring=SimpleNamespace(tick_phase_timeout_sec=0.05))
        with patch.object(engine_mod, "CONFIG", stub):
            with pytest.raises(asyncio.TimeoutError):
                await host._phase(_hangs(), "analyze")

        assert seen["k"]["analysed"] == 9, (
            "the cancelled phase recorded a rate with no analysis count, so "
            "the next tick forecasts from the ATTEMPT rate")
        assert seen["k"]["gave_up"] == GAVE_UP
        assert seen["a"][0] == ATTEMPTED
