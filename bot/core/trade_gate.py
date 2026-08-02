"""One answer to "can this account open a trade right now", for every surface.

THE SAME BUG, THREE TIMES, BECAUSE EACH FIX WAS A SURFACE AND NOT A RULE

The pre-execute gate in ``engine.confirm_*`` refuses a new entry on FIVE
independent conditions. Every status surface reimplemented its own subset:

    engine._halted                              global kill switch
    engine.risk.circuit_breaker_active          shared breaker
    engine.risk_for(uid).circuit_breaker_active THAT USER's breaker
    risk.trading_blocked_by                     warning-rate / loss-streak
    engine.live_auth_healthy(uid)               venue auth halt (live only)

2026-07-29 — the warning-rate breaker rejected a live trade while /health
said ``circuit_breaker_active: false``. ``trading_blocked_by`` was added and
wired into _banner, /status, the /risk tile and the chat prompt.

2026-08-01 — a transport blip marked venue auth down. Entries were halted and
NOTHING said so; the operator's /start read "Active | LIVE". The auth gate was
then wired into the chat prompt.

Both times the widening reached the surfaces someone happened to think of.
/start — the most-used command, and the exact card in the screenshot of the
second incident — was never one of them, and to this day reads

    cb_active = self.engine.risk.circuit_breaker_active

which is ONE of the five, and not the one that fired either time. It also
reads the SHARED breaker, so a user whose own daily-loss breaker is open is
told they are Active.

    A GATE WITH FIVE CONDITIONS AND SIX RENDERINGS OF IT WILL DISAGREE
    WITH ITSELF. THE FIX FOR A CLAIM MADE IN SIX PLACES IS ONE PLACE.

WHY UNKNOWN IS A THIRD ANSWER AND NOT ROUNDED TO EITHER

Rounding an unreadable field to "clear" recreates the exact bug: a green
headline nobody can justify. Rounding it to "blocked" is the false alarm
#1029 was about — telling an operator their trading is down when it is not.
So an unreadable field says so, and the caller renders that instead of
picking a side. Omit, never invent.

This module DECIDES NOTHING. It reports what the gate would do. The gate
itself stays exactly where it is, on the money path, unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

__all__ = ["entry_gate", "gate_label", "gate_sentence", "UNREADABLE"]

#: Reason text when a field could not be read at all.
UNREADABLE = "status unreadable"

#: A bare URL can carry credentials in its query string once it is one token,
#: and no key=value pattern catches "?apiKey=...". Keep the host — "which
#: venue" is the diagnostic — and drop the rest. Same rule as _safe_exc_text.
_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]*")


def _safe_detail(raw: str, limit: int = 80) -> str:
    """The venue auth detail, safe to put on ANY surface.

    ``_live_auth_detail`` holds ``str(exc)`` from the credential preflight,
    stored unscrubbed. Until now its only reader was the chat system prompt.
    Routing it to /start, the /risk card and the WEBSITE's status chip widens
    where an unredacted ccxt exception can land, and F-15 is that no secret or
    internal config ever reaches user-facing text — a ccxt error carries the
    request URL, and an LLM provider error can echo an Authorization header.

    Angle brackets and ampersands are STRIPPED rather than escaped. Callers
    escape according to their own surface (/start uses html.escape); escaping
    here would double-escape there and still leave the web chip to fend for
    itself. Removing the characters is correct for every consumer at once, and
    a diagnostic string loses nothing by it.
    """
    try:
        msg = str(raw or "")
    except Exception:
        return ""
    if not msg:
        return ""
    try:
        from bot.utils.logger import _redact_string
        msg = _redact_string(msg)
    except Exception:
        # Never let a scrub failure decide to emit the UNSCRUBBED string.
        return ""
    msg = _URL_QUERY_RE.sub(r"\1?***", msg)
    msg = msg.replace("<", "").replace(">", "").replace("&", " and ")
    return " ".join(msg.split())[:limit]


def _read(obj: Any, name: str) -> Any:
    """Attribute value, or the ``_Unset`` marker when it cannot be read.

    ``trading_blocked_by`` is a property that computes a streak probe, so a
    read genuinely can raise. Distinguishing "raised" from "falsy" is the
    whole point — they are the unknown case and the clear case.
    """
    try:
        return getattr(obj, name)
    except Exception:
        return _Unset


class _Unset:
    """Sentinel: the field could not be read."""


def _risk_engines(engine: Any, user_id: str) -> Iterator[Any]:
    """The shared risk engine and the executing account's, deduped.

    The gate checks BOTH — a per-user breaker that trips does not open the
    shared one, and vice versa. With PER_USER_LIVE_ENABLED off, ``risk_for``
    returns the shared engine itself, so identity-dedup keeps a single
    blockage from being reported twice. Identity, not equality: two distinct
    engines in the same state are two real accounts.
    """
    seen: list[int] = []
    shared = _read(engine, "risk")
    if shared is not _Unset and shared is not None:
        seen.append(id(shared))
        yield shared
    try:
        own = engine.risk_for(str(user_id or ""))
    except Exception:
        return
    if own is not None and id(own) not in seen:
        yield own


def entry_gate(engine: Any, user_id: str = "", *,
               live: bool | None = None, include_detail: bool = True) -> dict:
    """What the pre-execute gate would do for ``user_id`` right now.

    Returns ``{"blocked": bool, "unknown": bool, "reasons": [str, ...]}``.

    ``blocked`` is True only on a POSITIVE reading of a blocking condition.
    ``unknown`` is True when some condition could not be read, and the two are
    independent: a confirmed breaker plus one unreadable field is still
    blocked, and the caller has no reason to hedge about it.

    ``include_detail=False`` drops the venue's own error text and keeps the
    CATEGORY. /health is unauthenticated: that trading is halted, and which
    class of gate did it, is operational status of the kind that endpoint
    already publishes, but the venue's raw response is not — it is the one
    part of a reason that is externally sourced and unbounded in content.
    Scrubbed is not the same as public.

    Never raises. A status helper that can take down /start is a worse defect
    than the one it fixes.
    """
    reasons: list[str] = []
    unknown = False

    if live is None:
        try:
            from bot.config import CONFIG
            live = bool(CONFIG.is_live())
        except Exception:
            # Cannot tell live from paper: the auth gate below only applies in
            # live, so skip it and say the picture is incomplete.
            live = False
            unknown = True

    halted = _read(engine, "_halted")
    if halted is _Unset:
        unknown = True
    elif halted:
        reasons.append("kill switch engaged")

    saw_a_risk_engine = False
    for risk in _risk_engines(engine, user_id):
        saw_a_risk_engine = True
        blocked_by = _read(risk, "trading_blocked_by")
        if blocked_by is _Unset:
            # Fall back to the narrow flag rather than giving up: it answers
            # less, but a confirmed open circuit is still worth reporting.
            narrow = _read(risk, "circuit_breaker_active")
            if narrow is _Unset:
                unknown = True
            elif narrow:
                reasons.append("circuit breaker open")
            else:
                unknown = True
            continue
        if blocked_by:
            # These are internal tokens today — `_circuit_trip_cause` is only
            # ever daily_loss / drawdown / streak / manual / state_unreadable,
            # plus `warning_rate:<key>` and `loss_streak:<n>`.
            #
            # An earlier version of this comment claimed a manual trip carried
            # the operator's `/halt <reason>` text. It does not:
            # `emergency_halt(reason)` passes `reason` to the AUDIT LOG and
            # lets `cause` default to "manual", so the free text never reaches
            # this field. The scrub stays — it is a no-op on a fixed token and
            # the cheapest possible insurance if a future caller starts
            # passing `cause` through — but it is defence in depth, not a
            # response to a live exposure, and saying otherwise would leave a
            # rationale in the tree that the code does not support.
            reasons.append(_safe_detail(blocked_by, limit=120) or "blocked")
    if not saw_a_risk_engine:
        unknown = True

    if live:
        try:
            healthy = engine.live_auth_healthy(str(user_id or ""))
        except Exception:
            healthy = _Unset
        if healthy is _Unset:
            unknown = True
        elif not healthy:
            detail = ""
            if include_detail:
                try:
                    detail = _safe_detail((engine._live_auth_detail or {}).get(
                        str(user_id or ""), ""))
                except Exception:
                    detail = ""
            # The recovery route travels WITH the reason. The flag is only
            # cleared by the preflight, so a surface that reports the halt
            # without saying that is a diagnosis with no next step — which is
            # what the operator hit on 2026-08-01.
            reasons.append("venue auth marked down" +
                           (f" ({detail})" if detail else "") +
                           ", a restart re-runs the check")

    # Dedup while preserving order: the shared and per-user engines can latch
    # on the same cause, and saying it twice reads like two problems.
    ordered: list[str] = []
    for r in reasons:
        if r not in ordered:
            ordered.append(r)
    return {"blocked": bool(ordered), "unknown": bool(unknown),
            "reasons": ordered}


def gate_label(gate: dict) -> str:
    """One word for a status card: ``Active`` / ``Paused`` / ``Unknown``.

    "Unknown" is deliberately not "Active". A card that cannot establish its
    own headline should say so — that is a smaller cost than a green light
    nobody checked.
    """
    if gate.get("blocked"):
        return "Paused"
    if gate.get("unknown"):
        return "Unknown"
    return "Active"


def gate_sentence(gate: dict) -> str:
    """Why entries are refused, or "" when they are not.

    Named causes, joined — never a count, and never a re-worded verdict. The
    reasons come straight from the fields the gate reads.
    """
    reasons = list(gate.get("reasons") or ())
    if reasons:
        return "New entries are refused: " + "; ".join(reasons) + "."
    if gate.get("unknown"):
        return ("Could not read the full trading-gate status — this card "
                "cannot confirm entries are open.")
    return ""
