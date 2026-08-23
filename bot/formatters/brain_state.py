"""Is the LLM brain answering? `degraded_streak == 0` cannot tell you.

`analyzer.llm_health()` reports a streak of consecutive all-provider failures.
Zero is the value for three different situations, and its own docstring says
so: "0 = healthy or LLM-by-design-off". The third is worse — nothing has been
ATTEMPTED yet, so the streak is zero because no call has run.

`/llmstatus` already separates them, and its comment records why:

    streak==0 but no success recorded either: nothing has been attempted
    since restart. Don't claim "answering" — the live incident showed
    "healthy" at 18:07 then 18 failures at 18:08 because the first status
    simply pre-dated any LLM call.

`_scan_timeout_hint` did not, and it is the surface where the cost is highest:
it is read while an operator is diagnosing a scan timeout, and it says "LLM
brain is healthy" and then points at "a per-symbol dependency (exchange fetch
…), not the fallback chain". On an untested brain that sentence rules out the
subsystem nobody has checked, using a negative nobody measured.

That function's own docstring already names the failure — "a green LLM health
check rules ONE cause out; it does not name the cause", written after 37
consecutive tick timeouts were blamed on the exchange — so this is that lesson
one level in: not a green check misread as a cause, but an UNRUN check
rendered as a green one.

THE SAME SPLIT AS `integrity_veto.is_reading()`. `degraded_streak` is the
scoring value and it is correct for scoring: the monitor should page on a real
streak and not on silence. `brain_state()` is the display answer, and the two
had to stop being the same expression the moment a surface used it to rule
something out.
"""

from __future__ import annotations

from typing import Optional

#: Never inferred. Each is a state the health snapshot can actually support.
DEGRADED = "degraded"     # a real streak: every provider failed, N times running
UNTESTED = "untested"     # streak 0 because nothing has been attempted yet
HEALTHY = "healthy"       # streak 0 AND a success on record
UNKNOWN = "unknown"       # no analyzer, or no health snapshot to read

#: (icon, short label, whether the LLM may be ruled OUT as a cause).
#: The third field is the one that matters: only `healthy` earns it.
BRAIN_TEXT: dict[str, tuple[str, str, bool]] = {
    DEGRADED: ("🚨", "degraded", False),
    UNTESTED: ("⚪", "untested", False),
    HEALTHY: ("✅", "healthy", True),
    UNKNOWN: ("❔", "unknown", False),
}


def brain_state(health: Optional[dict]) -> str:
    """One of DEGRADED / UNTESTED / HEALTHY / UNKNOWN from a health snapshot.

    An unreadable or absent snapshot is UNKNOWN, never HEALTHY — that is the
    whole point. A missing measurement is not a passing one.
    """
    if not isinstance(health, dict):
        return UNKNOWN
    try:
        streak = int(health.get("degraded_streak", 0) or 0)
    except (TypeError, ValueError):
        return UNKNOWN
    if streak > 0:
        return DEGRADED
    # `is None`, not falsiness. `last_ok_seconds_ago` is a duration, and 0.0 —
    # a success THIS INSTANT — is falsy and is the healthiest reading there is.
    if "last_ok_seconds_ago" not in health:
        return UNKNOWN
    if health.get("last_ok_seconds_ago") is None:
        return UNTESTED
    return HEALTHY


def may_rule_out_llm(health: Optional[dict]) -> bool:
    """Whether a diagnostic line is entitled to say "not the AI".

    The predicate exists so that entitlement is asked for explicitly, at every
    surface that wants to exclude the LLM, instead of being assumed from a
    zero somebody read off a dict.
    """
    return BRAIN_TEXT[brain_state(health)][2]


def brain_state_of(analyzer) -> str:
    """`brain_state` for an analyzer that may be absent or may not carry the
    method at all. Never raises; absent reads as UNKNOWN."""
    if analyzer is None or not hasattr(analyzer, "llm_health"):
        return UNKNOWN
    try:
        return brain_state(analyzer.llm_health())
    except Exception:
        return UNKNOWN


# ── Is the SWEEP asking? A different question from "is the brain answering". ──
#
# LLM_BACKGROUND_SCANS=off sends the autonomous sweep to the rule engine. That
# is a deliberate, correct state — and it is invisible to everything above,
# because the four states are about the BRAIN and this is about the CALLER.
#
# The gap is not hypothetical. proactive_monitor._check_llm_degraded exists to
# fire "🚨 LLM BRAIN OFFLINE — RUNNING ON RULES … still scanning and trading,
# but on the rule engine only — no AI thesis, weaker signals" at CRITICAL
# severity. The valve makes that exact condition permanent and unalertable: it
# returns before any provider is attempted, so it never touches the streak, and
# the alert whose whole subject is this state can never fire for it.
#
# Worse, the two claims can disagree in the direction that misleads. One user
# /analyze keeps the streak at 0 and last_ok fresh, so brain_state() reads
# HEALTHY — truthfully — while every background signal that tick came from the
# rule engine. An operator reading "✅ healthy" would take the sweep's signals
# for AI theses. So this is reported as its own sentence, never merged into the
# brain's.

SWEEP_LLM = "llm"          # background sweeps ask the LLM (historical default)
SWEEP_RULES = "rules"      # LLM_BACKGROUND_SCANS=off — rule engine, by design
SWEEP_UNKNOWN = "unknown"  # no snapshot, or a snapshot too old to carry the field

SWEEP_TEXT: dict[str, tuple[str, str]] = {
    SWEEP_LLM: ("🧠", "background scans use the AI thesis"),
    SWEEP_RULES: ("🔇", "background scans are rule-engine only (by configuration)"),
    SWEEP_UNKNOWN: ("❔", "background scan mode unknown"),
}


def sweep_mode(health: Optional[dict]) -> str:
    """Does the autonomous sweep ask the LLM?

    UNKNOWN when the snapshot cannot say — including a snapshot that predates
    the field. Absent is not "yes": reading a missing field as the historical
    default would print a confident 🧠 for a deployment whose sweep is silent.
    """
    if not isinstance(health, dict) or "background_scans_llm" not in health:
        return SWEEP_UNKNOWN
    value = health.get("background_scans_llm")
    if value is None:
        return SWEEP_UNKNOWN
    return SWEEP_LLM if bool(value) else SWEEP_RULES


def sweep_note(health: Optional[dict]) -> str:
    """One line an operator can read, or '' when there is nothing to add.

    Empty for the historical default so the common case stays quiet — a note
    on every status line is a note nobody reads.
    """
    mode = sweep_mode(health)
    if mode == SWEEP_LLM:
        return ""
    icon, text = SWEEP_TEXT[mode]
    return f"{icon} {text}"
