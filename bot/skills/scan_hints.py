"""What a slow or stale scan should say about itself — a leaf out of the handler.

Six module-level helpers the scan commands share, moved here because the
scan group left the handler as a mixin and a mixin must not import from the
handler, while `/latest_signal` (still on the handler) reads three of them:

  `_scan_timeout_hint`         one diagnostic line for a timed-out interactive
                               scan, built from MEASURED evidence only — the
                               brain state, the degraded-provider streak and
                               the engine's own analysis-timeout record — and
                               "" when nothing was measured;
  `_inflight_analysis_progress` and `_recent_analysis_timeout`, the two
                               engine reads it is assembled from;
  `_background_scan_is_fresh`  whether the cached background scan is recent
                               enough to answer from, three-valued at the
                               boundary (an unreadable timestamp is never
                               "fresh");
  `_skipped_symbols_note`      the "(N of M symbols were skipped)" caveat on a
                               scan that did not cover its universe;
  `ANALYSIS_TIMEOUT_HINT_WINDOW_S`, the window both of those hints look back.

A leaf, not a mixin: nothing here reads `self`. Pure functions over an
analyzer, an engine or a record, which is what let them be exercised in the
first place (`test_scan_timeout_hint`, `test_scan_freshness_is_wired`,
`test_interactive_scan_freshness`, `test_surface_scenarios`,
`test_brain_state_is_not_inferred`).
"""
from __future__ import annotations

import html
import time

from bot.formatters.brain_state import BRAIN_TEXT as _BRAIN_TEXT
from bot.formatters.brain_state import brain_state as _brain_state
from bot.formatters.brain_state import untested_confirmation as _untested_confirm


def _background_scan_is_fresh(
    last_scan_time: float, interval: float, grace: float, now: float,
    sweep_s: float | None = None,
) -> tuple[bool, int]:
    """Decide whether the continuous background sweep is recent enough that an
    interactive "Latest Signal" tap should serve its result instantly instead
    of triggering a slow, throttle-exposed re-scan.

    Returns ``(is_fresh, seconds_until_next_sweep)``. Pure — no I/O — so the
    responsiveness gate is unit-testable without the engine or Telegram.

    THE WINDOW IS THE CADENCE, NOT THE INTERVAL, and getting that wrong made
    this gate useless even once its input was finally being written.

    Consecutive answers arrive ``sweep_s + interval`` apart: the loop runs a
    sweep, then sleeps. So a result older than that means a sweep was MISSED —
    the loop stalled or is throttled — which is the genuine staleness this gate
    was built to detect. Sizing the window on ``interval`` alone asserts that a
    sweep is instantaneous.

    It is not. The repo's own recorded rate is ~3.3s per symbol against a
    universe of ~200, so a sweep is minutes; ``interval`` comes from
    ``_compute_smart_scan_interval``, which is derived from ATR volatility and
    clamped to 60-90s and has no relationship to how long a sweep takes. The
    old window of ``interval + grace`` was therefore 90-120s against a sweep of
    280-660s: every tap fell through to a live re-scan, which is exactly the
    slowness the gate was added to remove.

    ``sweep_s`` is None until one sweep has finished. That falls back to the
    old arithmetic rather than inventing a duration — a guessed rate on this
    path is a fabricated freshness claim, and the fallback is wrong only for
    the first tap after a restart.
    """
    if grace <= 0 or last_scan_time <= 0:
        return False, 0
    age = now - last_scan_time
    # `is not None` rather than `or`. Here the two happen to coincide, because
    # the fallback is 0.0 and a discarded 0.0 lands on 0.0 anyway — so this is
    # stated intent, not a load-bearing distinction, and saying otherwise would
    # be inventing a defect to look careful about. It stays spelled this way
    # because "absent" and "measured zero" stop coinciding the moment the
    # fallback becomes anything but zero, and a sweep really can measure ~0.
    cadence = interval + (sweep_s if sweep_s is not None else 0.0)
    if age <= (cadence + grace):
        # Time until the next sweep STARTS — the loop is sleeping `interval`
        # from the moment the last one finished.
        return True, max(0, int(interval - age))
    return False, 0


ANALYSIS_TIMEOUT_HINT_WINDOW_S = 900.0


def _skipped_symbols_note(record: dict | None, now: float) -> str:
    """" (N of M symbols were skipped)" for a sweep that did not cover them all.

    The all-clear this decorates — "no setups above the confidence line" —
    means "we looked and there is nothing" to whoever reads it. A sweep that
    gave up on some symbols has not looked at those, so the same sentence
    quietly widens from a measurement into a claim about markets nobody read.
    That is the partial-total shape: a number computed over a subset and
    printed as though it covered the whole.

    Silent on a clean sweep. A caveat printed every time is a caveat nobody
    reads, which is how the real one gets skipped.

    Pure, and returns "" for anything it cannot read: absent record, wrong
    shape, unusable counts, or a record too old to describe this sweep.
    """
    if not isinstance(record, dict):
        return ""
    try:
        skipped = int(record.get("skipped") or 0)
        of = int(record.get("of") or 0)
        at = float(record.get("at") or 0.0)
    except (TypeError, ValueError):
        return ""
    if skipped <= 0 or of <= 0 or at <= 0:
        return ""
    if (now - at) > ANALYSIS_TIMEOUT_HINT_WINDOW_S:
        return ""
    return f" <i>({skipped} of {of} symbols were skipped)</i>"


def _scan_timeout_hint(analyzer, engine=None) -> str:
    """One diagnostic line for the interactive-scan timeout message.

    The quick scan analyzes up to INTERACTIVE_SCAN_COUNT symbols inside a
    fixed deadline; when the LLM brain is degraded (every provider failing —
    e.g. a bad model id or exhausted quota), each analysis burns through the
    fallback chain and the deadline blows every time. Without this hint the
    operator sees only "taking longer than usual" and can't tell LLM failure
    from exchange throttling (live incident: two consecutive timeouts right
    after a model-id change). Best-effort — returns "" on any error.

    HONESTY: a green LLM health check rules ONE cause out; it does not name
    the cause. This line used to conclude "the slowness is likely
    exchange/data latency, not the AI" from that single negative — and on
    2026-07-29 it said exactly that while individual analyses were hanging
    long enough to blow the 300s tick cap thirty-seven times in a row,
    pointing the operator at the exchange for a problem that was not there.
    A heuristic flag is never a verdict. Report what is measured: the
    degraded streak when there is one, the engine's own analysis-timeout
    record when there is one, and otherwise the negative alone.
    """
    try:
        if analyzer is None or not hasattr(analyzer, "llm_health"):
            return ""
        h = analyzer.llm_health()
        streak = int(h.get("degraded_streak", 0) or 0)
        if streak > 0:
            return ("\n\n🚨 <b>Likely cause: LLM brain degraded</b> — every "
                    f"provider has failed {streak} analyses in a row, so each "
                    "symbol burns through the fallback chain. Check "
                    "<code>/llmstatus</code> and the configured model id.")

        # WHAT THE HEALTH SNAPSHOT ACTUALLY ENTITLES THIS LINE TO SAY.
        #
        # Every branch below opened "LLM brain is healthy" and then ruled the
        # fallback chain OUT — off `degraded_streak == 0`, which by its own
        # docstring means "healthy or LLM-by-design-off", and which is also 0
        # when nothing has been ATTEMPTED yet. On an untested brain that
        # sentence excludes the one subsystem nobody has checked, inside the
        # message an operator reads while diagnosing a timeout.
        #
        # This function's docstring already records what reading a green check
        # as a verdict cost: 37 tick timeouts blamed on the exchange. An UNRUN
        # check rendered as a green one is the same error, one step earlier.
        # /llmstatus was given the distinction and this surface was not.
        _icon, _label, _may_exclude = _BRAIN_TEXT[_brain_state(h)]
        _ruled = (
            "LLM brain is healthy, so the fallback chain is ruled out."
            if _may_exclude else
            f"{_icon} LLM brain is <b>{_label}</b> — no successful LLM call is "
            "on record, so it is NOT ruled out here. "
            # "confirms on the first scan" is true of the historical default
            # and false under LLM_BACKGROUND_SCANS=off, where the sweep
            # returns before any provider is attempted and the brain stays
            # untested for the life of the process. Ask what will actually
            # confirm it under the config in force.
            + _untested_confirm(h))

        # Measured, not inferred: the background loop caps each analysis, and
        # records the batch when any of them hit that cap. If it fired
        # recently, THAT is the slowness — same dependency, same symptom.
        at = _recent_analysis_timeout(engine)
        if at:
            # Name the symbols when the record carries them. "1 of 70 gave
            # up" without the WHICH sent the operator to the logs for the one
            # fact that makes the diagnosis instant. Absent on old-shape
            # records — omit, never guess.
            _syms = [html.escape(str(s)) for s in (at.get("symbols") or [])[:5]]
            _who = (" (" + ", ".join(f"<code>{s}</code>" for s in _syms) + ")"
                    if _syms else "")
            # One batch names WHAT hung. It cannot say whether the same
            # symbols hang every time -- that record is overwritten each
            # batch -- and the two call for opposite responses: a few bad
            # symbols get dropped, capacity pressure gets a smaller universe
            # or a longer budget. The running tally carries the distribution;
            # it describes the shape and stops short of naming the cause,
            # because a tally cannot see one.
            _shape = ""
            try:
                from bot.core import analysis_timeout_tally as _att
                _t = getattr(engine, "_analysis_timeout_tally", None)
                if _t:
                    _line = _att.render(_t)
                    if _line:
                        _shape = ("\n\n📊 <b>Across this run:</b> "
                                  + html.escape(_line))
            except Exception:
                _shape = ""
            return ("\n\n⚠️ <b>Analyses are hanging</b> — the last background "
                    f"sweep gave up on {at['skipped']} of {at['of']} symbols "
                    f"after {float(at['cap_s']):.0f}s each{_who}. {_ruled} "
                    "The stall is in a per-symbol dependency (exchange fetch "
                    "or a single provider call). See <code>/status</code>."
                    + _shape)
        # The interactive scan runs through the same batched analyzer, so the
        # engine's in-flight progress record covers the batch this deadline
        # just cancelled. Report what it MEASURED — "got through 12 of 40 in
        # 25s" and "finished none" call for different next moves — instead of
        # ending on "does not identify the cause" while the numbers that
        # narrow it sat on the engine. (Live: this branch fired twice on
        # 2026-07-30 with the progress record populated both times.)
        p = _inflight_analysis_progress(engine)
        if p and p["done"] > 0:
            return (f"\n\nℹ️ {_ruled} The analyze batch in "
                    f"flight had finished {p['done']} of {p['of']} signals "
                    f"in {p['elapsed_s']:.0f}s when the deadline hit — "
                    "measured progress, so this is slow, not hung. If it "
                    "repeats, the per-symbol dependency (exchange fetch or "
                    "a single provider call) is a candidate. See "
                    "<code>/status</code>.")
        if p:
            return (f"\n\nℹ️ {_ruled} The analyze batch in "
                    f"flight had finished 0 of {p['of']} signals after "
                    f"{p['elapsed_s']:.0f}s — nothing completed, which "
                    "points at a blocked dependency (exchange fetch or a "
                    "single provider call) rather than slowness. See "
                    "<code>/status</code>.")
        return (f"\n\nℹ️ {_ruled} That does not identify the cause. Check "
                "<code>/status</code> for a tick-phase timeout.")
    except Exception:
        return ""


def _inflight_analysis_progress(engine, *, max_age_s: float = 600.0):
    """Measured progress of the analyze batch in flight moments ago, or None.

    None when there is nothing honest to report: no record, a stale record
    (a batch from a prior epoch says nothing about the scan that just timed
    out), or a COMPLETED batch — done >= of means the batch finished, so it
    cannot be the thing that blew this deadline, and citing it would blame
    a bystander. Pure — no I/O.
    """
    try:
        rec = getattr(engine, "_analyze_progress", None)
        if not isinstance(rec, dict):
            return None
        of = int(rec.get("of") or 0)
        done = int(rec.get("done") or 0)
        started = float(rec.get("started") or 0.0)
        if of <= 0 or started <= 0:
            return None
        age = time.monotonic() - started
        if age < 0 or age > max_age_s:
            return None
        if done >= of:
            return None
        return {"done": done, "of": of, "elapsed_s": age}
    except Exception:
        return None


def _recent_analysis_timeout(engine, *, window_s: float = ANALYSIS_TIMEOUT_HINT_WINDOW_S):
    """The engine's analysis-timeout record if it is recent, else None.

    A record from hours ago says nothing about the scan that just timed out;
    citing it would be the same "conclude from stale evidence" mistake in the
    other direction. Pure — no I/O.
    """
    try:
        rec = getattr(engine, "_last_analysis_timeout", None)
        if not isinstance(rec, dict) or not rec.get("skipped"):
            return None
        at = float(rec.get("at") or 0.0)
        if at <= 0 or (time.monotonic() - at) > window_s:
            return None
        return rec
    except Exception:
        return None
