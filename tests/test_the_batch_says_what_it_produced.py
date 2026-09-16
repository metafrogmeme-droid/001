"""
`37 attempted — 16 gave up` and a reader subtracts to 21.

The phase-timeout line prints two counts and lets the reader infer a third.
Slice #161 proved that inference wrong: `analysed = attempts - gave_up` is the
`losses = len(all) - wins` shape from CLAUDE.md's own table, because the batch
has FOUR exits and `gave_up` counts one.

  * the analysis ran                      -> counted on the success path
  * it hit `ANALYSIS_TIMEOUT_SEC`         -> `gave_up`
  * it RAISED                             -> `except Exception`, counted by nobody
  * the PHASE cap cancelled the gather    -> `CancelledError` is a BaseException,
                                             so neither handler saw it, and the
                                             `finally` still counted the attempt

Driven before this slice, the note read `gave_up` ALONE, so:

    {done 37, gave_up 16, analysed  9}  and
    {done 37, gave_up 16, analysed 21}  rendered IDENTICALLY, and

    {done 37, gave_up  0, analysed  9}  rendered as ''

— 28 symbols produced nothing and the line said nothing at all, which is the
worst case rendering as the quietest one.

The buckets are kept APART rather than summed because their levers differ: the
per-symbol cap, the venue, the phase cap. That is the line the scan-partial
slice already draws between "not reached (time budget)" and "errors".
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot.utils.i18n as i18n_mod
from bot.config import CONFIG
from bot.core import engine as eng_mod
from bot.core.engine import RuneClawEngine
from bot.formatters.rich_cards import _batch_outcome_note

BUCKETS = ("analysed", "gave_up", "errored", "cancelled")


def _plain(html: str) -> str:
    return re.sub("<[^>]+>", "", html or "")


# ── 1. every exit is counted, and the taxonomy CLOSES ──────────────────────
#
# Driven through the REAL batch method. A mirror of the coroutine's structure
# would be a second answer about what the structure does.

async def _run(kinds, per_cap, phase_cap=None):
    async def analyze(sig, *a, **k):
        head = sig.symbol.split("/")[0]
        if head.startswith("OK"):
            return None
        if head.startswith("BAD"):
            raise RuntimeError("venue said no")
        await asyncio.sleep(30)

    host = SimpleNamespace(_analyze_signal=analyze, _symbol_cooldowns={},
                           _record_analyze_throughput=lambda *a, **k: None)
    host._analyze_signals_batched = (
        RuneClawEngine._analyze_signals_batched.__get__(host))
    sigs = [SimpleNamespace(symbol=f"{n}/USDT:USDT") for n in kinds]
    with patch.object(eng_mod, "CONFIG",
                      dataclasses.replace(CONFIG, analysis_timeout_sec=per_cap)):
        coro = host._analyze_signals_batched(sigs)
        if phase_cap:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(coro, timeout=phase_cap)
        else:
            await coro
    return host._analyze_progress


class TestTheTaxonomyCloses:
    @pytest.mark.asyncio
    async def test_a_raise_is_its_own_bucket(self):
        p = await _run(["OK1", "BAD1", "HANG1", "OK2", "BAD2", "HANG2"], 0.2)
        assert (p["analysed"], p["gave_up"], p["errored"], p["cancelled"]) \
            == (2, 2, 2, 0)
        assert sum(p[b] for b in BUCKETS) == p["done"] == 6

    @pytest.mark.asyncio
    async def test_a_phase_cancel_is_its_own_bucket(self):
        """THE LIVE INCIDENT'S SHAPE. `gave_up` is 0 here and the old note
        printed nothing, on the batch that delivered two of six."""
        p = await _run(["OK1", "BAD1", "HANG1", "HANG2", "HANG3", "OK2"],
                       9.0, phase_cap=0.25)
        assert p["cancelled"] == 3
        assert p["gave_up"] == 0, "no symbol reached its OWN cap"
        assert (p["analysed"], p["errored"]) == (2, 1)
        assert sum(p[b] for b in BUCKETS) == p["done"] == 6

    @pytest.mark.asyncio
    async def test_a_clean_sweep_is_all_analysed(self):
        p = await _run(["OK1", "OK2", "OK3"], 5.0)
        assert (p["analysed"], p["gave_up"], p["errored"], p["cancelled"]) \
            == (3, 0, 0, 0)
        assert sum(p[b] for b in BUCKETS) == p["done"] == 3

    @pytest.mark.asyncio
    async def test_a_cancelled_batch_never_reaches_its_own_recorder(self):
        """The batch is GENUINELY cancelled, not merely counted as such.

        THE `raise` ITSELF IS NOT OBSERVABLE HERE, and that is recorded
        rather than dressed up. `wait_for` cancels the OUTER coroutine, so
        `await asyncio.gather(...)` raises whatever the children do —
        swallowing `CancelledError` in the child and re-raising it produce
        byte-identical behaviour from every input this product has. The
        mutation round drove both and got the same answer: an EQUIVALENT
        MUTANT. The `raise` stays because swallowing a cancellation tells
        asyncio it did not take, which is wrong by convention and a hung
        phase under any topology that cancels one child alone — but an
        assertion claiming to measure it would be claiming a check this
        suite does not make.

        What IS measurable: the post-gather recorder never runs, so the
        phase-timeout path is the only thing that records this batch.
        """
        seen = []

        async def analyze(sig, *a, **k):
            await asyncio.sleep(30)

        host = SimpleNamespace(
            _analyze_signal=analyze, _symbol_cooldowns={},
            _record_analyze_throughput=lambda *a, **k: seen.append(1))
        host._analyze_signals_batched = (
            RuneClawEngine._analyze_signals_batched.__get__(host))
        sigs = [SimpleNamespace(symbol=f"H{n}/USDT:USDT") for n in range(2)]
        with patch.object(eng_mod, "CONFIG",
                          dataclasses.replace(CONFIG, analysis_timeout_sec=9.0)):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    host._analyze_signals_batched(sigs), timeout=0.2)

        assert host._analyze_progress["cancelled"] == 2
        assert seen == [], (
            "the batch reached its post-gather recorder, so it completed "
            "rather than being cancelled")


# ── 2. the note names each bucket it counted, and only those ───────────────

class TestTheNoteNamesEachBucket:
    def test_the_live_incident_names_all_four(self):
        out = _plain(_batch_outcome_note(
            {"of": 40, "done": 37, "analysed": 9, "gave_up": 16,
             "errored": 1, "cancelled": 11}))
        assert "9 analysed" in out
        assert "16 gave up at the per-symbol cap" in out
        assert "1 failed with an error" in out
        assert "11 still running when the phase was cancelled" in out

    def test_the_case_that_used_to_print_nothing(self):
        """`gave_up` 0 with 28 symbols producing nothing. The old note read
        `gave_up` alone and returned '' — the worst batch, silent."""
        out = _plain(_batch_outcome_note(
            {"of": 40, "done": 37, "analysed": 9, "gave_up": 0,
             "errored": 0, "cancelled": 28}))
        assert out, "the loudest batch must not render as the quietest"
        assert "9 analysed" in out and "28 still running" in out

    def test_zero_analysed_is_printed(self):
        """A counted zero is the loudest reading this note can carry, so it
        is the one bucket printed at zero."""
        out = _plain(_batch_outcome_note(
            {"of": 40, "done": 37, "analysed": 0, "gave_up": 37,
             "errored": 0, "cancelled": 0}))
        assert "0 analysed" in out

    def test_a_clean_batch_gets_no_zero_rows(self):
        """A permanent '0 cancelled' trains the reader to stop reading the
        line — the argument the scan card's 'Not reached' row already makes."""
        out = _plain(_batch_outcome_note(
            {"of": 40, "done": 40, "analysed": 40, "gave_up": 0,
             "errored": 0, "cancelled": 0}))
        assert "40 analysed" in out
        for word in ("gave up", "failed with an error", "still running"):
            assert word not in out, "a counted zero is not a row"

    def test_an_uncounted_bucket_is_omitted_not_zeroed(self):
        """ABSENT IS NOT ZERO, per bucket. An older record says LESS and
        never says something false."""
        out = _plain(_batch_outcome_note(
            {"of": 40, "done": 37, "gave_up": 16}))
        assert out == " — 16 gave up at the per-symbol cap"
        assert "analysed" not in out, "a bucket nobody counted is not 0"

    def test_a_record_that_counted_nothing_says_nothing(self):
        assert _batch_outcome_note({"of": 40, "done": 37}) == ""
        assert _batch_outcome_note(None) == ""
        assert _batch_outcome_note("nope") == ""

    @pytest.mark.parametrize("junk, why", [
        ("nine", "a string is not a count"),
        (1.5, "int() would quietly make this 1"),
        (True, "a bool is an int in Python and is not a count"),
        (-1, "negative"),
        (None, "nobody counted"),
    ])
    def test_junk_in_one_bucket_leaves_the_others_readable(self, junk, why):
        """Each bucket is validated FOR ITSELF: dropping a real reading
        because a sibling is unreadable is the failed-read-as-empty shape one
        field over."""
        out = _plain(_batch_outcome_note(
            {"of": 40, "done": 37, "analysed": junk, "gave_up": 16}))
        assert "16 gave up" in out, why
        assert "analysed" not in out, why

    def test_the_order_is_what_ran_then_why_the_rest_did_not(self):
        out = _plain(_batch_outcome_note(
            {"of": 40, "done": 37, "analysed": 9, "gave_up": 16,
             "errored": 1, "cancelled": 11}))
        i = [out.index(w) for w in
             ("analysed", "gave up", "failed with", "still running")]
        assert i == sorted(i), "the delivered count leads; the causes follow"


# ── 3. the buckets are kept apart because their LEVERS differ ──────────────

class TestTheBucketsAreNotSummed:
    def test_a_give_up_and_a_cancellation_read_differently(self):
        """Folding them would send an operator to lower a per-symbol timeout
        that was never reached."""
        gave = _plain(_batch_outcome_note(
            {"done": 10, "analysed": 5, "gave_up": 5,
             "errored": 0, "cancelled": 0}))
        canc = _plain(_batch_outcome_note(
            {"done": 10, "analysed": 5, "gave_up": 0,
             "errored": 0, "cancelled": 5}))
        assert gave != canc
        assert "per-symbol cap" in gave and "per-symbol cap" not in canc
        assert "phase was cancelled" in canc

    def test_an_error_is_not_a_give_up(self):
        err = _plain(_batch_outcome_note(
            {"done": 10, "analysed": 5, "gave_up": 0,
             "errored": 5, "cancelled": 0}))
        assert "failed with an error" in err
        assert "per-symbol cap" not in err


# ── 4. every language ──────────────────────────────────────────────────────

class TestItSpeaksEveryLanguage:
    KEYS = ("val_analysed", "val_gave_up_short", "val_errored", "val_cancelled")

    def test_all_fourteen(self):
        """Read `_STRINGS` directly. `translate()` falls back to English, so a
        guard written as 'translate() is non-empty' detects only a WHOLLY
        missing key — the trap CLAUDE.md records about the deck study."""
        langs = list(i18n_mod.SUPPORTED_LANGS)
        assert len(langs) == 14
        for key in self.KEYS:
            row = i18n_mod._STRINGS.get(key) or {}
            missing = [c for c in langs if not row.get(c)]
            assert not missing, f"{key} missing: {missing}"

    def test_each_one_renders_without_leaking_a_key(self):
        prog = {"done": 37, "analysed": 9, "gave_up": 16,
                "errored": 1, "cancelled": 11}
        for code in i18n_mod.SUPPORTED_LANGS:
            out = _plain(_batch_outcome_note(prog, code))
            assert out.strip(), code
            for key in self.KEYS:
                assert key not in out, f"untranslated key leaked in {code}"
            for n in ("9", "16", "1", "11"):
                assert n in out, f"{code} dropped a count"


# ── 5. the renderer is reached ─────────────────────────────────────────────

class TestItIsWiredIn:
    def test_the_phase_line_calls_it(self):
        import io

        from tests.source_scan import code_only
        src = code_only(io.open("bot/formatters/rich_cards.py",
                                encoding="utf-8").read())
        assert "_batch_outcome_note((phase_timeout or {}).get('progress')" in src
        assert "_gave_up_note(" not in src, (
            "the old one-bucket note still has a caller")

    def test_both_new_counters_are_written_by_the_batch(self):
        import io

        from tests.source_scan import code_only
        src = code_only(io.open("bot/core/engine.py", encoding="utf-8").read())
        assert 'except asyncio.CancelledError:' in src
        assert '_p["cancelled"] = int(_p.get("cancelled") or 0) + 1' in src
        assert '_p["errored"] = int(_p.get("errored") or 0) + 1' in src
        # The progress dict must SEED them, or the first increment on an
        # older dict would start a count nobody can compare.
        assert '"analysed": 0, "errored": 0, "cancelled": 0,' in src
