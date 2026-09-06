"""The freshness gate's input is actually WRITTEN, and the window can be met.

WHAT WAS WRONG, AND WHY A GREEN SUITE COULD NOT SEE IT.

`_last_scan_time` is the sole input to the interactive responsiveness gate —
the thing that lets a "Latest Signal" tap answer instantly from the background
sweep instead of running a live 45s re-scan. It was initialised to 0.0 in
`RuneClawEngine.__init__` and **assigned nowhere**. Two references in the whole
tree: that initialiser, and the handler that reads it.

`_background_scan_is_fresh` returns False for `last_scan_time <= 0`. So the
gate took its never-scanned branch on every tap, on every deploy, since the
feature shipped. `tests/test_interactive_scan_freshness.py` even names that
case — `test_never_scanned_is_not_fresh` — and it was the only branch
production ever reached.

Every test in that file passes `last_scan_time` in as a PARAMETER. The pure
function is correct and thoroughly covered; the wiring is what was missing, and
a test that supplies the input itself can never discover that nothing supplies
it in real life. Same lesson as the SIWF verifier, where seventeen tests
injected their own verifier and none could tell the URL was wrong: a test
double cannot vouch for the integration it replaces.

So this file drives the REAL `_tick()`. The engine's collaborators are stubbed,
but the method under test is the shipped one, and the assertion is on the field
Telegram actually reads.

AND WIRING ALONE WOULD HAVE CHANGED NOTHING. The window was `interval + grace`
= 90-120s, because `_compute_smart_scan_interval` derives the interval from ATR
volatility and clamps it to 60-90s. A sweep of ~200 symbols at the repo's own
recorded ~3.3s/symbol takes minutes. A window that a sweep can never fit inside
is a gate that never opens, so the window is sized off the measured sweep
duration now — which is the other half, and the reason both live in one file.
"""

from __future__ import annotations

import asyncio
import time
import types


from bot.core.engine import AgentState, RuneClawEngine
from bot.core.system_health import SystemHealthMonitor
from bot.skills.scan_hints import (
    _background_scan_is_fresh,
    _skipped_symbols_note,
    ANALYSIS_TIMEOUT_HINT_WINDOW_S,
)


def _signal(symbol: str = "BTCUSDT"):
    """The attributes the tick's scan-summary logging reads off a signal."""
    return types.SimpleNamespace(
        symbol=symbol, price=1.0, change_pct_24h=1.0,
        volume_usd_24h=1e6, volume_spike=False, momentum_score=0.5,
    )


class _Engine:
    """Enough engine to run the real `_tick`, and nothing more.

    Every collaborator here is a stub; `_tick` and `_record_sweep_complete` are
    the shipped implementations, bound to this object. That is the point — the
    question is whether the SHIPPED tick reaches the recorder, and a
    reimplementation of the tick could not answer it.
    """

    def __init__(self, scan_result):
        self._scan_result = scan_result
        self._last_tick_started_ts = 0.0
        self.state = AgentState.IDLE
        self._last_state_change = time.time()
        self._cooldown_until = None
        self._pending_ideas = {}
        self._scan_lock = asyncio.Lock()
        self._last_scan_signals = []
        self._last_analysis_timeout = None
        # The two fields under test, in their pre-sweep state.
        self._last_scan_time = 0.0
        self._last_sweep_duration_s = None
        # The REAL monitor, not a namespace of the two methods the tick
        # happened to call when this was written. That stub broke the day
        # `_record_sweep_complete` started stamping `record_scan()` --
        # a fixture failing on a wiring change it was not testing. The
        # monitor is pure and dependency-free, so standing in for it buys
        # nothing and costs the next feeder a red build here.
        self.health = SystemHealthMonitor()
        self.ws_feed = types.SimpleNamespace(is_connected=lambda: False)
        self.risk = types.SimpleNamespace(circuit_breaker_active=False)
        self.scanner = types.SimpleNamespace(scan=lambda: None)

    def _transition(self, state, why=""):
        self.state = state

    async def _phase(self, coro, what, fatal=True):
        # The real _phase awaits its coro; these stubs return canned results,
        # so close the un-awaited coroutine rather than leaking a warning.
        if hasattr(coro, "close"):
            try:
                coro.close()
            except Exception:
                pass
        if what == "scan":
            return self._scan_result
        if what == "analyze":
            return []
        return None

    async def _analyze_signals_batched(self, signals, background=False):
        return []

    async def _check_open_positions(self):
        return None

    def _push_scan_summary_to_website(self, signals):
        return None

    _record_sweep_complete = RuneClawEngine._record_sweep_complete


def _run_tick(scan_result) -> _Engine:
    eng = _Engine(scan_result)
    asyncio.run(RuneClawEngine._tick(eng))
    return eng


# ── the wiring, driven through the real tick ───────────────────────────────

def test_a_sweep_that_found_nothing_still_stamps_the_clock() -> None:
    """The case the gate exists for.

    An operator taps, nothing is queued, and the honest instant answer is "we
    swept 40s ago and the tape is quiet". An empty scan returns BEFORE the
    analyze phase, so a recorder placed only after analyze would miss exactly
    the sweep the feature was built to serve.
    """
    eng = _run_tick([])
    assert eng._last_scan_time > 0, (
        "_last_scan_time is still 0 after a completed sweep — the freshness "
        "gate will take its never-scanned branch, which is the whole bug"
    )
    assert eng._last_sweep_duration_s is not None


def test_a_sweep_that_found_signals_stamps_the_clock() -> None:
    eng = _run_tick([_signal()])
    assert eng._last_scan_time > 0
    assert eng._last_sweep_duration_s is not None


def test_a_FAILED_scan_does_not_stamp_the_clock() -> None:
    """`is None`, not falsiness — the difference between two opposite facts.

    `_phase(..., fatal=False)` returns None when the scan phase times out, and
    `if not signals:` cannot tell that from an empty list. Stamping it would
    make the gate answer a tap with "swept 30s ago, nothing found" on the
    strength of a read that never happened — a failed read rendered as an empty
    result, which is the one thing this codebase refuses to do.
    """
    eng = _run_tick(None)
    assert eng._last_scan_time == 0.0, (
        "a scan that TIMED OUT was recorded as a completed sweep; the gate "
        "will now serve its emptiness as a measurement of the market"
    )
    assert eng._last_sweep_duration_s is None


def test_a_measured_duration_of_nearly_zero_is_still_a_measurement() -> None:
    """0.0 is falsy and 0.0 is a real reading.

    A stubbed sweep completes in microseconds, and the duration is consumed by
    a caller that must use `is not None` rather than `or` — otherwise the one
    genuinely fast sweep is indistinguishable from never having measured.
    """
    eng = _run_tick([])
    assert isinstance(eng._last_sweep_duration_s, float)
    assert eng._last_sweep_duration_s >= 0.0


# ── the end-to-end claim: what the tick writes, the gate can read ──────────

def test_the_gate_opens_on_what_the_tick_actually_wrote() -> None:
    """The assertion that ties the two halves together.

    Reads the fields exactly as the handler does, feeds them to the real gate,
    and requires it to say fresh. Either half regressing — nothing written, or
    a window nothing can fit — fails here, which is what neither the pure
    function's tests nor a source scan could do.
    """
    eng = _run_tick([])
    last = float(getattr(eng, "_last_scan_time", 0.0) or 0.0)
    sweep = getattr(eng, "_last_sweep_duration_s", None)
    sweep = float(sweep) if sweep is not None else None

    fresh, _ = _background_scan_is_fresh(
        last, interval=60.0, grace=30.0, now=time.monotonic(), sweep_s=sweep)
    assert fresh is True, (
        "the tick recorded a sweep and the gate still calls it stale — a tap "
        "will run a live 45s re-scan instead of answering instantly"
    )


def test_a_real_sweep_duration_fits_the_window_a_real_interval_gives() -> None:
    """The sizing bug, in the numbers that produced it.

    ~200 symbols at the repo's recorded ~3.3s/symbol is a 660s sweep. The smart
    interval is clamped to 60-90s, so the OLD window of interval + grace was at
    most 120s and no sweep could ever land inside it: every tap fell through to
    the slow path the gate was written to avoid.
    """
    sweep_s = 660.0
    now = 10_000.0
    # A sweep that finished 30s ago, i.e. the loop is keeping up perfectly.
    last = now - 30.0

    old_window_fresh, _ = _background_scan_is_fresh(last, 60.0, 30.0, now)
    assert old_window_fresh is True, "sanity: 30s old is fresh under any window"

    # Now the realistic case: the sweep finished, the loop slept, the next one
    # is under way. The answer on file is ~5 minutes old and is still the
    # current answer, because that IS the cadence.
    last = now - 300.0
    without, _ = _background_scan_is_fresh(last, 60.0, 30.0, now)
    with_measure, _ = _background_scan_is_fresh(last, 60.0, 30.0, now, sweep_s)
    assert without is False, (
        "interval-only sizing called a 300s-old answer stale — this is the bug"
    )
    assert with_measure is True, (
        "measured sizing still calls it stale; the window is not using the "
        "sweep duration"
    )


def test_an_answer_older_than_the_cadence_is_genuinely_stale() -> None:
    """The window must still close — it detects a STALLED loop.

    Widening it to fit a real sweep must not widen it to fit anything, or the
    gate stops distinguishing "keeping up" from "the loop died an hour ago" and
    serves an hour-old all-clear as the current state of the market.
    """
    now = 10_000.0
    sweep_s = 300.0
    # Two full cadences late: a sweep was missed.
    last = now - (2 * (sweep_s + 60.0) + 60.0)
    fresh, _ = _background_scan_is_fresh(last, 60.0, 30.0, now, sweep_s)
    assert fresh is False


def test_no_measurement_yet_falls_back_rather_than_inventing_one() -> None:
    """Before the first sweep completes there is no duration, and a guessed
    rate on this path is a fabricated freshness claim. The fallback is the old
    arithmetic, which is wrong only for the first tap after a restart."""
    now = 10_000.0
    assert _background_scan_is_fresh(now - 10.0, 60.0, 30.0, now, None)[0] is True
    assert _background_scan_is_fresh(now - 200.0, 60.0, 30.0, now, None)[0] is False


def test_the_never_scanned_and_disabled_branches_are_unchanged() -> None:
    """The two refusals that must survive the new parameter."""
    now = 10_000.0
    assert _background_scan_is_fresh(0.0, 60.0, 30.0, now, 300.0)[0] is False
    assert _background_scan_is_fresh(now - 1.0, 60.0, 0.0, now, 300.0)[0] is False


# ── the all-clear does not overclaim coverage ──────────────────────────────

def test_a_sweep_that_skipped_symbols_says_so() -> None:
    """"No setups above the line" reads as "we looked and there is nothing".

    A sweep that gave up on symbols has not looked at those, so the same
    sentence widens from a measurement into a claim about markets nobody read —
    the partial-total shape, printed as a whole.
    """
    note = _skipped_symbols_note(
        {"skipped": 3, "of": 85, "at": 1000.0}, now=1010.0)
    assert "3 of 85" in note


def test_a_clean_sweep_says_nothing() -> None:
    """A caveat printed every time is a caveat nobody reads."""
    assert _skipped_symbols_note({"skipped": 0, "of": 85, "at": 1000.0}, 1010.0) == ""


def test_an_unreadable_or_stale_skip_record_is_omitted_not_guessed() -> None:
    """Absent is not zero, and a record about an older sweep does not describe
    this one."""
    for bad in (None, {}, [], "nope", {"skipped": "x", "of": 85, "at": 1000.0},
                {"skipped": 3, "of": 0, "at": 1000.0},
                {"skipped": 3, "of": 85, "at": 0.0}):
        assert _skipped_symbols_note(bad, 1010.0) == "", f"leaked on {bad!r}"
    # Older than the hint window: a real record, about a sweep that is gone.
    stale = {"skipped": 3, "of": 85, "at": 1000.0}
    assert _skipped_symbols_note(stale, 1000.0 + ANALYSIS_TIMEOUT_HINT_WINDOW_S + 1) == ""
