"""Where do the ~40 seconds in one analysis actually go?

Today's analyze-phase timeout was resolved by halving the universe — 161
symbols down to 70 — because 161 x ~3.3s exceeded the 300s phase cap. That
worked, and it cost 56% of the candidate pool on the same day high-conviction
sizing went on to trade the >=70% ideas harder. The two pull against each
other.

Getting the universe back means making one analysis cheaper, and nothing in
the engine measured where its time went. That is the position the PHASE was in
this morning: two fixes shipped against an inferred cause, both wrong, until a
number settled it. Same discipline, one level down.

Stages are summed ACROSS the batch, so the total exceeds wall-clock under
concurrency. That is deliberate — the SHARE is what says which stage to
attack, and wall-clock alone never could.
"""

from types import SimpleNamespace

import pytest

from bot.core.engine import RuneClawEngine
from tests.source_scan import code_only

SRC = code_only(open("bot/core/engine.py", encoding="utf-8").read())


class _Eng:
    ANALYSIS_STAGES = RuneClawEngine.ANALYSIS_STAGES
    _stage_add = RuneClawEngine._stage_add
    _stage_report = RuneClawEngine._stage_report

    def __init__(self, totals=None):
        self._stage_totals = totals


@pytest.fixture
def eng():
    return _Eng({k: 0.0 for k in RuneClawEngine.ANALYSIS_STAGES})


# ── accumulating ──────────────────────────────────────────────────────────

def test_each_stage_accumulates(eng):
    eng._stage_add("fetch", 1.5)
    eng._stage_add("fetch", 2.5)
    eng._stage_add("analyze", 10.0)
    assert eng._stage_totals["fetch"] == 4.0
    assert eng._stage_totals["analyze"] == 10.0


def test_an_unknown_stage_is_ignored_not_created(eng):
    """A typo must not invent a category that then shows up in the report as
    if it were measured."""
    eng._stage_add("teleport", 99.0)
    assert "teleport" not in eng._stage_totals


def test_it_never_raises(eng):
    _Eng(None)._stage_add("fetch", 1.0)          # no accumulator yet
    eng._stage_add("fetch", float("nan"))        # nonsense value
    _Eng({}) ._stage_add("fetch", 1.0)


def test_the_stage_list_is_the_single_source_of_truth():
    """Accumulator, report and tests all read ANALYSIS_STAGES, so they cannot
    drift into disagreeing about what a stage is."""
    assert RuneClawEngine.ANALYSIS_STAGES == ("fetch", "mtf", "analyze", "refine")
    for stage in RuneClawEngine.ANALYSIS_STAGES:
        assert f'_stage_add("{stage}"' in SRC, f"{stage} is declared but never timed"


# ── reporting ─────────────────────────────────────────────────────────────

def test_the_report_gives_shares_not_just_seconds(eng):
    eng._stage_totals = {"fetch": 40.0, "mtf": 40.0, "analyze": 120.0, "refine": 0.0}
    out = eng._stage_report(231.0)
    assert "analyze 120s (60%)" in out
    assert "fetch 40s (20%)" in out


def test_a_stage_that_never_ran_is_omitted(eng):
    eng._stage_totals = {"fetch": 10.0, "mtf": 0.0, "analyze": 5.0, "refine": 0.0}
    out = eng._stage_report(12.0)
    assert "mtf" not in out and "refine" not in out


def test_nothing_measured_produces_no_line(eng):
    """A breakdown of zero is not a breakdown, and 0s across the board would
    read as a measurement that was taken."""
    assert _Eng({k: 0.0 for k in RuneClawEngine.ANALYSIS_STAGES})._stage_report(9.0) == ""
    assert _Eng(None)._stage_report(9.0) == ""


def test_it_states_the_wall_clock_and_the_concurrency(eng):
    """Summed stage time exceeding wall-clock is expected under concurrency.
    Printing the sum WITHOUT both numbers invites reading it as elapsed."""
    eng._stage_totals = {"fetch": 100.0, "mtf": 0.0, "analyze": 200.0, "refine": 0.0}
    out = eng._stage_report(231.0)
    assert "300s of work across 231s wall-clock" in out
    assert "concurrent" in out


def test_the_report_never_raises(eng):
    assert isinstance(_Eng({"fetch": "junk"})._stage_report(1.0), str)


# ── it is emitted where it is needed ──────────────────────────────────────

def test_a_finished_batch_reports_its_shape():
    """The healthy baseline has to exist, or an incident breakdown has
    nothing to be compared against."""
    assert 'action="analyze_stages"' in SRC
    assert "Analyze batch complete:" in SRC


def test_a_cancelled_batch_reports_too():
    """It never reaches its own report — and it is the batch whose breakdown
    anyone actually wants."""
    block = SRC[SRC.index("async def _phase(self, coro"):
                SRC.index("def _record_phase_duration")]
    assert "_stage_report(_elapsed)" in block
    assert '_pdata["stages"]' in block


def test_the_accumulator_resets_per_batch():
    block = SRC[SRC.index("async def _analyze_signals_batched"):
                SRC.index("async def _analyze_signal(self")]
    assert "self._stage_totals = {k: 0.0 for k in ANALYSIS_STAGES}" in block, (
        "carrying totals between batches would report a sum nobody asked for")


def test_the_engine_declares_it_up_front():
    assert "self._stage_totals: dict[str, float] | None = None" in SRC


# ── the arithmetic it exists to support ───────────────────────────────────

def test_the_share_identifies_the_lever(eng):
    """The whole point: if the LLM is 60% of the work, cutting fetch time in
    half buys 20% and cutting LLM time in half buys 30%."""
    eng._stage_totals = {"fetch": 42.0, "mtf": 38.0, "analyze": 118.0, "refine": 12.0}
    out = eng._stage_report(231.0)
    total = 42 + 38 + 118 + 12
    assert f"analyze 118s ({118 / total:.0%})" in out
    _ = SimpleNamespace


def test_the_batch_does_not_require_its_caller_to_grow_attributes():
    """Twice now, adding a `self.` lookup to this path broke suites that
    exercise it with lightweight stubs — for reasons unrelated to what they
    test. The stage list is a module constant and the report call is guarded,
    so a stub standing in for the engine stays a valid stand-in."""
    block = SRC[SRC.index("async def _analyze_signals_batched"):
                SRC.index("async def _analyze_signal(self")]
    assert "self.ANALYSIS_STAGES" not in block, (
        "a class-attribute lookup for a constant is needless fragility here")
    assert 'getattr(self, "_stage_report", None)' in block


def test_the_constant_has_one_definition():
    """Module level, with the class attribute bound to it — not two tuples
    that can disagree."""
    assert SRC.count('("fetch", "mtf", "analyze", "refine")') == 1
    assert "ANALYSIS_STAGES = ANALYSIS_STAGES" in SRC
    from bot.core import engine as _e
    assert _e.ANALYSIS_STAGES is _e.RuneClawEngine.ANALYSIS_STAGES
