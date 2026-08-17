"""A symbol that hangs gets a bounded rest, and is never reported as analysed.

THE INCIDENT, 2026-08-17. /status showed:

    Slowest tick phase: ⚠ analyze 292s peak of 300s (97%)

Three tokenized-equity symbols — MCD, AMD, HOOD — were each timing out at
ANALYSIS_TIMEOUT_SEC (90s). 3 x 90 = 270 of those 292 seconds, on every sweep,
indefinitely. The engine already NAMED them via `_last_analysis_timeout`, which
was the previous fix and a good one — a quiet tick hiding a hanging dependency
is how it stayed invisible for a day. But naming is not resting: nothing ever
skipped them, so the same three burned 92% of the analyze phase forever while
sixty-odd others shared what was left.

WHERE THE ARMING GOES, AND WHY IT IS NOT WHERE IT LOOKS LIKE IT SHOULD

The obvious site is `_last_analysis_timeout` — the existing record of which
symbols timed out. It is the WRONG site, and the reason is the incident itself:
that block runs after `await asyncio.gather(...)` RETURNS, and in this incident
it does not return, because the analyze phase hits its 300s cap and is
cancelled first. A fix placed there is present, greppable, passes any source
scan, and never executes in the exact case it was written for — the #999 shape.

So arming happens inside `_one`'s `except asyncio.TimeoutError`, per symbol, as
the timeout occurs.

WHY THESE TESTS DRIVE THE REAL BATCH

Everything else covering `_analyze_signals_batched` is a source scan
(test_analyze_phase_progress.py, test_analysis_timeout.py). That was enough for
the shapes they check and it was NOT enough here: the first draft of this fix
read `CONFIG.analyzer.analysis_timeout_rest_sec`, but those fields live on
AppConfig — the CONFIG root — so it raised AttributeError, was swallowed by the
instrumentation-grade `except Exception: pass`, and armed NOTHING. Every source
scan passed. Driving the batch is what caught it, on the first run.

THE HALF THAT IS NOT PERFORMANCE

A rested symbol was not analysed and found nothing — it was not looked at. The
batch's progress counter increments for a timeout ("counts finished work of ANY
outcome"), which is the right answer to "how far did the batch get" and the
wrong one to "how many symbols were analysed". Once symbols can rest those
diverge, and "Read 62 of 67" starts meaning two different things in one
sentence. `skipped_resting` is counted separately so no reader has to guess.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core.engine import RuneClawEngine, normalize_symbol
from bot.core.symbol_rest import coverage_sentence, rest_note, rest_seconds
from tests.source_scan import code_only

SRC = Path("bot/core/engine.py").read_text(encoding="utf-8")
BATCH = code_only(SRC[SRC.index("async def _analyze_signals_batched"):
                      SRC.index("async def _analyze_signal(self")])


@pytest.fixture(autouse=True)
def _restore_cap():
    cap = getattr(CONFIG, "analysis_timeout_sec", 90.0)
    yield
    object.__setattr__(CONFIG, "analysis_timeout_sec", cap)


def _stub():
    return SimpleNamespace(
        _analyze_batch_seq=0, _analyze_progress=None,
        _stage_totals={}, _stage_profiles={}, _stage_inflight={},
        _symbol_cooldowns={}, _analysis_rest_strikes={}, _analysis_rest_until={},
        _stage_exit=lambda s: None, _stage_report=None,
        _record_analyze_throughput=None, _last_analysis_timeout=None,
    )


def _run_batch(stub, symbols, *, hang=True, cap=0.05):
    async def _hang(sig, **kw):
        await asyncio.sleep(10)

    async def _ok(sig, **kw):
        return None

    stub._analyze_signal = _hang if hang else _ok
    object.__setattr__(CONFIG, "analysis_timeout_sec", cap)
    sigs = [SimpleNamespace(symbol=s) for s in symbols]
    return asyncio.run(RuneClawEngine._analyze_signals_batched(stub, sigs))


# ── the arming actually happens ──────────────────────────────────────────────

def test_a_timed_out_symbol_is_armed_for_rest():
    """The whole point, driven rather than grepped."""
    stub = _stub()
    _run_batch(stub, ["MCD/USDT:USDT", "AMD/USDT:USDT"])
    assert sorted(stub._symbol_cooldowns) == ["AMD", "MCD"], (
        f"nothing was rested: {stub._symbol_cooldowns}")


def test_the_armed_key_is_the_one_the_pre_analysis_guard_reads():
    """The guard does `self._symbol_cooldowns.get(normalize_symbol(signal.symbol))`.
    Arming under any other spelling writes a cooldown nothing consults — a
    cooldown that exists and does nothing, which is worse than none because it
    looks fixed."""
    stub = _stub()
    _run_batch(stub, ["MCD/USDT:USDT"])
    assert normalize_symbol("MCD/USDT:USDT") in stub._symbol_cooldowns
    assert "MCD/USDT:USDT" not in stub._symbol_cooldowns, (
        "armed under the raw symbol; the guard normalizes and would never "
        "find it")


def test_the_rest_expiry_is_in_the_future_and_bounded():
    import time
    stub = _stub()
    _run_batch(stub, ["MCD/USDT:USDT"])
    until = stub._symbol_cooldowns["MCD"]
    left = until - time.monotonic()
    assert 0 < left <= CONFIG.analysis_timeout_rest_sec + 1, left


def test_repeated_timeouts_escalate_the_strike_count():
    stub = _stub()
    _run_batch(stub, ["MCD/USDT:USDT"])
    assert stub._analysis_rest_strikes["MCD"] == 1
    _run_batch(stub, ["MCD/USDT:USDT"])
    assert stub._analysis_rest_strikes["MCD"] == 2, (
        "a symbol that keeps hanging must rest longer each time, or the "
        "reliably-unanalysable case is asked several times an hour forever")


def test_a_completed_analysis_clears_the_strike():
    """Escalation is for a symbol that KEEPS hanging. Without clearing, a
    healthy symbol accumulates strikes across a session and eventually rests
    four hours on its first blip."""
    stub = _stub()
    _run_batch(stub, ["MCD/USDT:USDT"])
    assert stub._analysis_rest_strikes.get("MCD") == 1
    _run_batch(stub, ["MCD/USDT:USDT"], hang=False)
    assert "MCD" not in stub._analysis_rest_strikes, (
        "a clean analysis must reset the escalation")


def test_resting_can_be_disabled_entirely():
    """0 is the documented escape hatch, matching ANALYSIS_TIMEOUT_SEC's own
    convention — an operator who wants the old behaviour must be able to have
    it without editing code."""
    prev = CONFIG.analysis_timeout_rest_sec
    try:
        object.__setattr__(CONFIG, "analysis_timeout_rest_sec", 0.0)
        stub = _stub()
        _run_batch(stub, ["MCD/USDT:USDT"])
        assert stub._symbol_cooldowns == {}, stub._symbol_cooldowns
        assert stub._analysis_rest_strikes == {}, (
            "a disabled rest must not accumulate strikes either — they would "
            "escalate the moment somebody re-enabled it")
    finally:
        object.__setattr__(CONFIG, "analysis_timeout_rest_sec", prev)


def test_arming_never_breaks_the_batch():
    """Instrumentation-grade: several suites drive this batch with
    SimpleNamespace stubs, and resting must never be why an analysis cannot
    finish. A stub with no rest attributes at all must still complete."""
    bare = SimpleNamespace(
        _analyze_batch_seq=0, _analyze_progress=None,
        _stage_totals={}, _stage_profiles={}, _stage_inflight={},
        _symbol_cooldowns={}, _stage_exit=lambda s: None, _stage_report=None,
        _record_analyze_throughput=None, _last_analysis_timeout=None,
    )
    out = _run_batch(bare, ["MCD/USDT:USDT"])
    assert out == [None]


# ── a rested symbol is not a scanned symbol ──────────────────────────────────

def test_a_rested_symbol_is_counted_separately_from_an_analysed_one():
    """`done` counts finished work of any outcome. Once symbols can rest, that
    is no longer the same number as "symbols analysed", and publishing one as
    the other is absence presented as coverage."""
    stub = _stub()
    _run_batch(stub, ["MCD/USDT:USDT"])
    assert "skipped_resting" in stub._analyze_progress, (
        "the batch must track resting separately from done")


def test_the_progress_record_starts_the_rest_counter_at_zero():
    stub = _stub()
    _run_batch(stub, ["MCD/USDT:USDT"], hang=False)
    assert stub._analyze_progress["skipped_resting"] == 0


# ── the arithmetic (the seam) ────────────────────────────────────────────────

def test_escalation_doubles_and_caps():
    assert rest_seconds(1, 900, 14400) == 900
    assert rest_seconds(2, 900, 14400) == 1800
    assert rest_seconds(3, 900, 14400) == 3600
    assert rest_seconds(5, 900, 14400) == 14400
    assert rest_seconds(9, 900, 14400) == 14400, "must stay capped"


def test_rest_seconds_is_total_and_never_returns_a_useless_zero_expiry():
    """0.0 means DO NOT ARM. Returning 0 as a duration would write
    monotonic()+0, expire on the very next comparison, and silently restore the
    old behaviour while looking like a working cooldown."""
    assert rest_seconds(0, 900, 14400) == 0.0
    assert rest_seconds(-1, 900, 14400) == 0.0
    assert rest_seconds(1, 0, 14400) == 0.0
    for bad in (None, "x", float("nan"), float("inf")):
        assert rest_seconds(bad, 900, 14400) == 0.0, bad
        assert rest_seconds(1, bad, 14400) in (0.0, 900.0), bad


def test_a_huge_strike_count_cannot_overflow():
    """The counter only ever increments, so this is where a shift overflow
    arrives — inside a timeout handler, in a live scan."""
    assert rest_seconds(10 ** 9, 900, 14400) == 14400


# ── the sentence ─────────────────────────────────────────────────────────────

def test_nothing_resting_says_nothing():
    """A coverage line that always carries a resting clause is one people stop
    reading. It must appear exactly when the count means something different."""
    assert rest_note(0, 67) == ""
    assert coverage_sentence(67, 0, 67) == "Analysed 67 of 67 symbols"


def test_a_resting_symbol_never_reads_as_scanned():
    note = rest_note(3, 67)
    assert "resting" in note
    assert "not analysed this pass" in note, (
        "the reader must be told these were never looked at, not that they "
        "were looked at and found clear")
    line = coverage_sentence(62, 3, 67)
    assert "62 of 67" in line and "3 resting" in line


def test_everything_resting_is_stated_as_such():
    """The all-missing case. `if total != 0` guarding a display is the shape
    that hides all-missing and genuinely-flat alike."""
    note = rest_note(67, 67)
    assert "all 67" in note and "nothing was analysed" in note


# ── the reachability property a unit test cannot reach ───────────────────────

def test_arming_is_in_the_timeout_handler_not_the_batch_tail():
    """THE #999 PROPERTY.

    `_last_analysis_timeout` is written after `await asyncio.gather(...)`
    returns. In the incident this fix exists for, it does not return — the
    analyze phase hits its 300s cap and is cancelled. Arming there would be
    code that is present, greppable, and never reached.
    """
    handler = BATCH[BATCH.index("except asyncio.TimeoutError:"):
                    BATCH.index("except Exception as exc:")]
    assert "_symbol_cooldowns[" in handler, (
        "the rest is no longer armed inside the per-symbol timeout handler — "
        "if it moved to the batch tail it will not fire on a cancelled phase, "
        "which is the only case that matters")
    gather = BATCH.index("await asyncio.gather(")
    arm = BATCH.index("_symbol_cooldowns[")
    assert arm < gather, (
        "arming must happen inside the per-analysis coroutine, before the "
        "gather it is nested in")


def test_the_rest_reuses_the_existing_cooldown_dict():
    """A second parallel mechanism would need its own guard, its own expiry
    handling and its own bugs. The pre-analysis check already works."""
    assert "_symbol_cooldowns" in BATCH
    assert "self._analysis_rest_until" in BATCH, (
        "provenance is what lets the guard tell a rest from a post-SL cooldown")


def test_monotonic_expiries_are_never_persisted():
    """A monotonic expiry written to disk is meaningless after a restart, and
    a restart is also the operator's chance to have fixed the hang."""
    save = code_only(SRC)
    for line in save.splitlines():
        if "_analysis_rest_until" in line or "_analysis_rest_strikes" in line:
            assert "json" not in line and "dump" not in line, line
