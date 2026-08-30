"""What is actually enforcing right now — one answer, from the live config.

THE QUESTION NOBODY COULD ANSWER

`RiskLimits` carries 27 `*_enabled` flags and a mode. `/risk` shows the
drawdown backstop. `/guardian` shows the guardian suite. Neither shows the
set, and no surface anywhere answers the question an operator actually has
before risking money: **which of these would stop a bad trade right now?**

Counting flags does not answer it either, and that is the whole reason this
file is a curated table rather than a loop over `vars(CONFIG.risk)`. A flag
named `*_enabled` reading False does NOT mean a control is absent:

  var_covariance_enabled = False   the covariance VaR refinement is off; the
                                   per-trade VaR guard still runs. Its own
                                   comment: "it never silently downgrades the
                                   check to a skip."
  equity_curve_breaker_enabled     a real de-risk condition that genuinely
                                   does not exist while off — "its feeder was
                                   never called, leaving the breaker
                                   permanently inert."

Reporting those two the same way would be a false claim in one direction or
the other, on the screen whose entire job is to prevent false claims about
enforcement. So every flag is classified by WHAT IT DOES when off, and only
`refuse` gates are counted as "would stop a trade".

KINDS

  refuse   can reject a trade outright. Off means that refusal cannot happen.
  size     changes position size, never refuses. Off means bigger size, not
           an open door.
  tune     refines a check that runs either way. Off is a LOOSER or
           less-accurate version of a control that still exists — never an
           absent one.
  seal     records or attests an outcome. Never touches a trade; off costs
           evidence, not protection.
  behave   operational behaviour (a reset schedule). Not a control at all.

ABSENT IS NOT OFF

A flag missing from the config object reads `unknown`, never `off`. A
renderer that prints "off" for a value it could not read manufactures a
confident negative from a failed lookup — the defect this repo has a table
about. `unknown` is a third outcome everywhere below and is never counted as
armed OR as disarmed.
"""

from __future__ import annotations

from typing import Any, Optional

#: A control that can reject a trade.
KIND_REFUSE = "refuse"
#: Changes size only. Off is a bigger position, not a missing gate.
KIND_SIZE = "size"
#: Refines a check that runs regardless. Off is looser, never absent.
KIND_TUNE = "tune"
#: Records or attests. Off costs evidence, not protection.
KIND_SEAL = "seal"
#: Operational behaviour, not a control.
KIND_BEHAVE = "behave"

ON = "on"
OFF = "off"
SHADOW = "shadow"
ENFORCE = "enforce"
UNKNOWN = "unknown"

#: attr -> (label, kind, what OFF actually means)
#:
#: Every entry's third field is written from the flag's OWN comment in
#: bot/config.py, not from its name. `tests/test_gate_inventory.py` fails if
#: RiskLimits grows an `*_enabled` flag that is not classified here, so a new
#: control forces a decision instead of being silently left out of the answer.
GATES: dict[str, tuple[str, str, str]] = {
    # ── can refuse a trade ────────────────────────────────────────────────
    "fee_aware_entry_gate_enabled": (
        "Fee-aware entry", KIND_REFUSE,
        "entries whose reward barely clears round-trip cost are not rejected"),
    "reentry_cooldown_enabled": (
        "Re-entry cooldown", KIND_REFUSE,
        "rapid re-entry churn on the same symbol after a win/flat close is "
        "not blocked"),
    "mtf_alignment_gate_enabled": (
        "MTF alignment", KIND_REFUSE,
        "counter-trend entries against the higher-timeframe trend are allowed"),
    "funding_clock_gate_enabled": (
        "Funding clock", KIND_REFUSE,
        "entries on the paying side of extreme funding near settlement are "
        "allowed"),
    "symbol_loss_streak_enabled": (
        "Per-symbol loss streak", KIND_REFUSE,
        "a chronically losing symbol keeps being re-entered while other "
        "symbols win"),
    "equity_curve_breaker_enabled": (
        "Equity-curve breaker", KIND_REFUSE,
        "the de-risk/pause condition is inert — its feeder is never called"),
    "drawdown_recovery_enabled": (
        "Drawdown recovery", KIND_REFUSE,
        "recovery mode never activates; no higher-confidence or reduced-size "
        "restriction after a drawdown"),
    "intent_policy_enabled": (
        "Intent policy", KIND_REFUSE,
        "the compiled strategy-intent policy is not consulted at all"),
    "authority_envelope_enabled": (
        "Authority envelope", KIND_REFUSE,
        "the granted custody envelope (notional, symbol scope, expiry, "
        "revocation) is not enforced"),
    "validation_gate_enabled": (
        "Backtest validation", KIND_REFUSE,
        "a strategy that has never passed a backtest can trade unremarked"),
    "guardian_firewall_enabled": (
        "Prompt-injection firewall", KIND_REFUSE,
        "inbound chat text is not scanned for manipulation before it can "
        "steer an agent that acts"),

    # ── size only ─────────────────────────────────────────────────────────
    "vol_target_sizing_enabled": (
        "Volatility-target sizing", KIND_SIZE,
        "per-trade risk scales up with volatility instead of being capped"),
    "kelly_sizing_enabled": (
        "Kelly sizing", KIND_SIZE, "no half-Kelly tightening of size"),
    "correlation_sizing_enabled": (
        "Correlation sizing", KIND_SIZE,
        "correlated positions are not shrunk, only counted"),
    "regime_sizing_enabled": (
        "Regime sizing", KIND_SIZE, "size does not adapt to the regime"),
    "user_risk_pref_sizing_enabled": (
        "User risk preference", KIND_SIZE,
        "a user's self-declared risk appetite changes what the agent SAYS and "
        "not what it sizes — their positions are the same size as everybody "
        "else's"),
    "live_performance_governor_enabled": (
        "Live performance governor", KIND_SIZE,
        "no closed-loop de-risking when realized outcomes are losing"),
    "equity_throttle_enabled": (
        "Equity throttle", KIND_SIZE,
        "size does not scale down with a falling profit factor"),
    "live_risk_hardening_enabled": (
        "Live risk hardening", KIND_SIZE,
        "real money runs the same posture as paper"),

    # ── refines a check that runs either way ──────────────────────────────
    "var_covariance_enabled": (
        "Covariance VaR", KIND_TUNE,
        "portfolio VaR uses the per-trade-return proxy; the VaR guard itself "
        "still runs"),
    "per_strategy_notional_cap_enabled": (
        "Per-strategy notional cap", KIND_TUNE,
        "one global cap applies instead of a per-strategy one; the cap still "
        "binds"),
    "per_strategy_confidence_floor_enabled": (
        "Per-strategy confidence floor", KIND_TUNE,
        "one global confidence floor applies; the floor still binds"),
    "correlation_forward_intents_enabled": (
        "Forward-looking correlation", KIND_TUNE,
        "the correlation cap counts only OPEN positions, so a cluster "
        "signalling on the same bar can pass together"),
    "correlation_perp_group_mapping_enabled": (
        "Perp correlation mapping", KIND_TUNE,
        "perp symbols miss the spot-keyed map and pool into one bucket, so "
        "the cap measures the wrong thing"),

    # ── evidence, not protection ──────────────────────────────────────────
    "guardian_digital_twin_enabled": (
        "Digital twin sealing", KIND_SEAL,
        "stress-test verdicts are not sealed to the tamper-evident chain"),
    "guardian_risk_sentinel_enabled": (
        "Risk sentinel sealing", KIND_SEAL,
        "crowding/concentration verdicts are not sealed"),
    "guardian_escape_enabled": (
        "Escape plan sealing", KIND_SEAL,
        "emergency-exit plans are not sealed"),

    # ── not a control ─────────────────────────────────────────────────────
    "daily_loss_breaker_autoreset_enabled": (
        "Daily-loss breaker auto-reset", KIND_BEHAVE,
        "a tripped daily-loss breaker stays tripped past UTC rollover until "
        "an operator clears it"),
}

#: Flags whose mode lives in a second attribute. `off` on the master switch
#: wins: a mode of "enforce" on a disabled hook enforces nothing.
MODES: dict[str, str] = {
    "validation_gate_enabled": "validation_gate_mode",
}


def _status(risk: Any, attr: str) -> str:
    """`on`/`off`/`shadow`/`enforce`, or `unknown` when it cannot be read."""
    if risk is None or not hasattr(risk, attr):
        return UNKNOWN            # absent is not off
    enabled = getattr(risk, attr)
    if enabled is None:
        return UNKNOWN
    if not enabled:
        return OFF
    mode_attr = MODES.get(attr)
    if not mode_attr:
        return ON
    mode = getattr(risk, mode_attr, None)
    mode = str(mode or "").strip().lower()
    if mode in (SHADOW, ENFORCE):
        return mode
    if mode == OFF:
        return OFF                # mode:off disables an enabled hook
    return UNKNOWN                # enabled, but the mode is unreadable


def inventory(risk: Any) -> list[dict]:
    """Every classified control with its live status, sorted for reading.

    `refuse` first — that is the question being asked — then by label. Nothing
    is computed from the flag's NAME; the table is the authority.
    """
    order = {KIND_REFUSE: 0, KIND_SIZE: 1, KIND_TUNE: 2, KIND_SEAL: 3,
             KIND_BEHAVE: 4}
    # Annotated because `mode_attr` is Optional: without it the value type is
    # inferred as `str | None`, and the sort key below then fails arg-type.
    rows: list[dict[str, Any]] = []
    for attr, (label, kind, off_means) in GATES.items():
        rows.append({
            "attr": attr,
            "label": label,
            "kind": kind,
            "status": _status(risk, attr),
            "off_means": off_means,
            "mode_attr": MODES.get(attr),
        })
    rows.sort(key=lambda r: (order.get(r["kind"], 9), r["label"]))
    return rows


def refusal_summary(rows: Optional[list]) -> dict:
    """How many trade-refusing controls are armed, off, or unreadable.

    ONLY `refuse` rows are counted. A `size` or `tune` flag being off is not a
    missing gate, and folding them in would inflate the alarming number with
    controls that are still doing their job.

    `shadow` is NOT armed. A shadow gate records what it would have rejected
    and rejects nothing — counting it as protection is the exact substitution
    this module exists to prevent. It gets its own count so the distinction
    survives to the screen.
    """
    rows = rows or []
    refuse = [r for r in rows if r.get("kind") == KIND_REFUSE]
    armed = [r for r in refuse if r.get("status") in (ON, ENFORCE)]
    shadow = [r for r in refuse if r.get("status") == SHADOW]
    off = [r for r in refuse if r.get("status") == OFF]
    unknown = [r for r in refuse if r.get("status") == UNKNOWN]
    return {
        "total": len(refuse),
        "armed": len(armed),
        "shadow": len(shadow),
        "off": len(off),
        "unknown": len(unknown),
        "off_labels": [r["label"] for r in off],
        "shadow_labels": [r["label"] for r in shadow],
        "unknown_labels": [r["label"] for r in unknown],
        # True only when every refusal gate answered. With any unknown we do
        # not know the posture, and saying otherwise would be a reading
        # invented from a failed lookup.
        "complete": not unknown,
    }
