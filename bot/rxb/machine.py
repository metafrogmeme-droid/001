"""Watching, Alert, Fired, Recovered.

Watching becomes Alert when CBF > 5%, UF > 25% or DMS > 0.5.
Alert becomes Fired when CBF or LTS is above 10%, or UF is above 40%,
on two consecutive readable runs at least one hour apart.
Alert returns to Watching after 24 hours in which every run was readable
and under the alert lines. An unreadable run is not "below".
Fired becomes Recovered only after seven such clear days and a curator
re-raise through the timelock. The machine does not invent that re-raise.
"""

from __future__ import annotations

from fractions import Fraction

ALERT_CBF = Fraction(5, 100)
ALERT_UF = Fraction(25, 100)
ALERT_DMS = Fraction(1, 2)
FIRE_LINE = Fraction(10, 100)
FIRE_UF = Fraction(40, 100)
CONFIRM_S = 3600
ALERT_CLEAR_S = 24 * 3600
RECOVER_S = 7 * 86400


def _frac(text: object) -> Fraction | None:
    if not isinstance(text, str) or "/" not in text:
        return None
    num, den = text.split("/", 1)
    try:
        out = Fraction(int(num), int(den))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return out


def alert_hit(run: dict) -> bool | None:
    """True when a readable signal is over its alert line.

    False only when CBF, UF and DMS were all read and all sit on or under
    their lines. Anything else is unreadable, which is not an all-clear.
    """
    cbf, uf, dms = _frac(run.get("cbf")), _frac(run.get("uf")), _frac(run.get("dms"))
    if cbf is not None and cbf > ALERT_CBF:
        return True
    if uf is not None and uf > ALERT_UF:
        return True
    if dms is not None and dms > ALERT_DMS:
        return True
    if cbf is None or uf is None or dms is None:
        return None
    return False


def fire_hit(run: dict) -> bool | None:
    """True when CBF or LTS is over 10%, or UF is over 40%."""
    cbf, uf, lts = _frac(run.get("cbf")), _frac(run.get("uf")), _frac(run.get("lts"))
    if cbf is not None and cbf > FIRE_LINE:
        return True
    if lts is not None and lts > FIRE_LINE:
        return True
    if uf is not None and uf > FIRE_UF:
        return True
    if cbf is None and lts is None and uf is None:
        return None
    if (cbf is None or cbf <= FIRE_LINE) and (lts is None or lts <= FIRE_LINE) and (
        uf is None or uf <= FIRE_UF
    ):
        # A missing leg did not clear the line, and no readable leg did either.
        # That is not a fire. It is also not proof the missing leg is calm,
        # so the caller must not treat this as a clear day. Clear days use
        # alert_hit, which requires every alert input.
        return False
    return False


def fresh_state() -> dict:
    return {
        "state": "Watching",
        "below_since": None,
        "clear_since": None,
        "fire_seen_ts": None,
        "fired_on": None,
    }


def step(prev: dict, run: dict, *, caps_reraised: bool = False) -> dict:
    """One run. ``run['ts']`` is unix seconds. Caps re-raised is an event, not a guess."""
    state = dict(prev)
    ts = run.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, int):
        state["reason"] = "ts_unreadable"
        return state
    hit = alert_hit(run)
    fired = fire_hit(run)
    name = state["state"]

    if name == "Watching":
        state["fire_seen_ts"] = None
        state["clear_since"] = None
        if hit is True:
            state["state"] = "Alert"
            state["below_since"] = None
            if fired is True:
                state["fire_seen_ts"] = ts
        return state

    if name == "Alert":
        if hit is False:
            if state["below_since"] is None:
                state["below_since"] = ts
            elif ts - state["below_since"] >= ALERT_CLEAR_S:
                state["state"] = "Watching"
                state["below_since"] = None
                state["fire_seen_ts"] = None
        else:
            state["below_since"] = None
        if fired is True:
            seen = state.get("fire_seen_ts")
            if isinstance(seen, int) and ts - seen >= CONFIRM_S:
                state["state"] = "Fired"
                state["fired_on"] = ts
                state["fire_seen_ts"] = None
                state["below_since"] = None
                state["clear_since"] = None
            elif seen is None:
                state["fire_seen_ts"] = ts
        elif fired is False:
            state["fire_seen_ts"] = None
        return state

    if name == "Fired":
        if hit is False:
            if state["clear_since"] is None:
                state["clear_since"] = ts
            elif ts - state["clear_since"] >= RECOVER_S and caps_reraised is True:
                state["state"] = "Recovered"
        else:
            state["clear_since"] = None
        return state

    return state
