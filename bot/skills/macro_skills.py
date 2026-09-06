"""
RUNECLAW v2 macro-aware skills.

Integrates macro calendar, compliance, approval tokens, and kill-switch
into the Telegram skill system.
"""
from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING, List

# Lazy import — skill_registry lives in the same package tree.
try:
    from bot.skills.skill_registry import BaseSkill
except ImportError:
    # Fallback for standalone testing / linting.
    class BaseSkill:  # type: ignore[no-redef]
        name: str = ""
        description: str = ""
        command: str = ""
        async def execute(self, engine, **kwargs) -> str:
            raise NotImplementedError

if TYPE_CHECKING:
    pass  # forward refs only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _html_bold(text: str) -> str:
    return f"<b>{text}</b>"


def _safe_getattr(obj, attr, fallback=None):
    """getattr that also catches AttributeError inside properties."""
    try:
        return getattr(obj, attr, fallback)
    except Exception:
        return fallback


# A read that nobody could answer. NOT an empty result, and that distinction is
# the whole reason this sentinel exists rather than `None` or `[]`.
#
# Every skill in this module was written against an imagined "v2" API and probed
# attribute names that do not exist on the real objects — `upcoming_events` when
# the method is `get_upcoming_events`, `consent_ledger` when it is
# `get_consent_ledger`, `current_window` when the field lives on the context
# object. Seven probes, seven misses, and each miss rendered as a confident
# negative: "No upcoming events loaded" printed over a calendar holding 40
# events with Nonfarm Payrolls a week out, and "No consent ledger available"
# printed over a populated authorization ledger.
#
# On a fail-closed macro subsystem those sentences are worse than blank. "No
# upcoming events loaded" is the phrasing that means THE CALENDAR IS MISSING,
# which is the one condition an operator is told to treat as a reason to stop —
# so the card manufactured the alarm it exists to report, out of a typo.
#
# Nothing caught it because nothing could run it: when this was found, all five
# skills were registered and dispatched by no transport. Three are reachable
# now — /eventrisk, /compliance, and macro_brief as `/macro brief` and a chat
# tool — and tests/unreachable_skills_baseline.txt records the two that are
# not, and why.
_UNREADABLE = object()


def _read(obj, names, **kwargs):
    """First attribute among *names* that answers, or `_UNREADABLE`.

    Accepts a plain attribute or a method, and retries a method without
    keywords when it does not take them — the sources here are duck-typed
    (`MacroEventProvider.get_upcoming_events(hours=...)` vs
    `MacroCalendar.upcoming()`), which is exactly how the names drifted apart
    in the first place.

    A name that resolves to nothing keeps looking; only exhausting the list, or
    an accessor that raises, is `_UNREADABLE`. An accessor that answers `None`
    is also `_UNREADABLE` — a source that declines to say is not a source
    saying "none".
    """
    for name in names:
        fn = _safe_getattr(obj, name)
        if fn is None:
            continue
        if not callable(fn):
            return fn
        try:
            out = fn(**kwargs) if kwargs else fn()
        except TypeError:
            try:
                out = fn()
            except Exception:
                return _UNREADABLE
        except Exception:
            return _UNREADABLE
        return _UNREADABLE if out is None else out
    return _UNREADABLE


def _field(item, name, default=None):
    """Read *name* off a dict OR an object — the event sources return dicts."""
    if isinstance(item, dict):
        return item.get(name, default)
    return _safe_getattr(item, name, default)


def _event_line(ev) -> str:
    """One calendar event, rendered.

    `get_upcoming_events` yields DICTS. The old code read them with
    `getattr(ev, "label", str(ev))`, so had its accessor ever resolved it would
    have printed the raw dict repr into the card.

    `date_confidence` is carried through because the seed calendar marks some
    dates `estimated`, and an estimated NFP time shown in the same typeface as
    a confirmed one is a heuristic wearing a verdict's clothes.
    """
    # Two shapes reach here and they disagree on names: MacroEventProvider
    # yields dicts keyed `type`/`severity`, MacroCalendar yields MacroEvent
    # dataclasses with `event_type`/`impact`. Reading only the first pair
    # printed "[severity unknown]" beside a HIGH-impact FOMC decision.
    label = (_field(ev, "label") or _field(ev, "type")
             or _field(ev, "event_type") or "unnamed event")
    when = _field(ev, "scheduled_utc")
    sev = _field(ev, "severity") or _field(ev, "impact")
    conf = str(_field(ev, "date_confidence") or "").lower()

    out = str(label)
    out += f"  ({when})" if when else "  (time unknown)"
    out += f"  [{sev}]" if sev else "  [severity unknown]"
    if conf and conf != "confirmed":
        out += f"  ~{conf}"
    return out


def _present(obj, name) -> str:
    """Render one optional field with its three states kept apart.

    `unknown`  the attribute is not there — nobody answered.
    `none`     it is there and holds None — a real, measured absence.
    the value  otherwise.

    The distinction matters most on `size_multiplier`, where the old default
    of `1.0` meant an unreadable macro multiplier displayed as FULL SIZE.
    """
    if not hasattr(obj, name):
        return "unknown"
    value = _safe_getattr(obj, name)
    return "none" if value is None else str(value)


def _decision_line(entry) -> str:
    """One AuthorizationDecision, rendered — outcome first.

    The old code read `entry.action`, a field AuthorizationDecision does not
    have, and fell back to `str(entry)`: a raw dataclass repr per line. What it
    never printed, under any branch, was `granted` — whether the trade was
    ALLOWED OR DENIED, which is the only thing a consent ledger is for.

    `granted` is read with `is None`, not falsiness, because False is a real
    and highly consequential reading here and must not share a branch with
    "the field was not there".
    """
    granted = _field(entry, "granted")
    if granted is None:
        outcome = "UNKNOWN"
    else:
        outcome = "GRANTED" if granted else "DENIED"

    ts = _field(entry, "timestamp")
    out = f"[{ts if ts else 'time unknown'}] <b>{outcome}</b>"

    trade_id = _field(entry, "trade_id")
    if trade_id:
        out += f" trade <code>{trade_id}</code>"
    failed = _field(entry, "locks_failed") or []
    if failed:
        out += f" — failed: <code>{', '.join(str(f) for f in failed)}</code>"
    reasons = _field(entry, "reasons") or []
    if reasons:
        out += f" ({'; '.join(str(r) for r in reasons)})"
    return out


# ---------------------------------------------------------------------------
# 1. MacroBriefSkill
# ---------------------------------------------------------------------------

class MacroBriefSkill(BaseSkill):
    name = "macro_brief"
    description = "Current macro window status, next events, and risk state."
    # A sub-mode of the calendar's command, not a command of its own. This
    # advertised `/macro`, which the calendar skill already answers to, and
    # two commands under one name was the collision that kept the card dark.
    # `/macro brief` dispatches it; chat reaches it as a tool and a free-text
    # skill (permission `macro`, bot/skills/skill_permissions.py), which is
    # where "is macro cutting size right now" gets asked anyway.
    command = "/macro brief"

    async def execute(self, engine, **kwargs) -> str:
        provider = _safe_getattr(engine, "macro_provider")
        calendar = _safe_getattr(engine, "macro_calendar")

        if provider is None and calendar is None:
            return (
                f"{_html_bold('Macro Brief')}\n"
                "v2 macro provider not wired — no macro data available."
            )

        source = provider or calendar
        lines: list[str] = [_html_bold("Macro Brief")]
        ctx = None  # the context object, when one could be read at all

        # -- risk state (use get_context on v2 provider) --
        ctx_fn = _safe_getattr(source, "get_context")
        if ctx_fn and callable(ctx_fn):
            try:
                ctx = ctx_fn()
                risk_state = getattr(ctx, "risk_state", "UNKNOWN")
                severity = getattr(ctx, "severity", "")
                multiplier = getattr(ctx, "size_multiplier", 1.0)
                explanation = getattr(ctx, "explanation", "")
                is_stale = getattr(ctx, "is_stale", False)
                is_blind = getattr(ctx, "is_blind", False)
                lines.append(f"Risk state: <code>{risk_state}</code>")
                if severity:
                    lines.append(f"Severity: <code>{severity}</code>")
                lines.append(f"Size multiplier: <code>{multiplier}</code>")
                if is_stale:
                    lines.append("⚠️ Calendar data is <b>stale</b>")
                if is_blind:
                    lines.append("⚠️ Operating <b>blind</b> — no calendar loaded")
                if explanation:
                    lines.append(f"<i>{explanation}</i>")
            except Exception:
                ctx = None
                risk_state = _safe_getattr(source, "risk_state", "UNKNOWN")
                lines.append(f"Risk state: <code>{risk_state}</code>")
        else:
            risk_state = _safe_getattr(source, "risk_state", "UNKNOWN")
            lines.append(f"Risk state: <code>{risk_state}</code>")

        # -- current window --
        # The window lives on the CONTEXT (`MacroContext.window`); the source
        # has no `current_window` attribute and never had one, so the old probe
        # resolved to nothing on every call and "No active macro window" was
        # unfalsifiable — it printed identically whether a CPI blackout was
        # active or the provider had fallen over.
        if ctx is None:
            lines.append("Active window: <code>unknown</code> — "
                         "no readable macro context.")
        else:
            window = getattr(ctx, "window", None)
            lines.append(f"Active window: {window}" if window
                         else "No active macro window.")

        # -- next events --
        # `get_upcoming_events(hours)` on MacroEventProvider, `upcoming()` on
        # MacroCalendar. The old probe asked for `upcoming_events`, which is
        # neither, so this section could never render an event.
        # A week, not the 24h the accessor defaults to: this card exists to
        # warn about macro blackouts before they arrive, and the events that
        # matter (CPI, FOMC, NFP) are scheduled well over a day out.
        horizon_h = 24 * 7
        upcoming = _read(source, ("get_upcoming_events", "upcoming_events",
                                  "upcoming"), hours=horizon_h)
        # A BLIND provider answers `[]` to every event query — not because the
        # week is clear but because it holds no calendar to look in. Reporting
        # that as "no events scheduled" is this module's original defect
        # reappearing one level down, so blindness is checked before the empty
        # list is believed.
        blind = ctx is not None and getattr(ctx, "is_blind", False)
        lines.append("")
        if blind:
            lines.append("Upcoming events: <code>unknown</code> — no calendar "
                         "is loaded, so an empty week and an unread one are "
                         "indistinguishable.")
        elif upcoming is _UNREADABLE:
            # Distinct from "the calendar is empty", and deliberately so: an
            # operator acts on those two very differently.
            lines.append("Upcoming events: <code>unknown</code> — this macro "
                         "source exposes no readable event list.")
        elif upcoming:
            # The heading does NOT claim the horizon. `hours` is a hint, and
            # not every source honours it — MacroCalendar.upcoming() takes a
            # `limit` and returns the next N whatever their distance, so a
            # "next 7d" heading over its output was a false claim about timing
            # made by this card, not by the data. Each line carries its own
            # date, which is the fact either way.
            lines.append(_html_bold("Next events:"))
            for ev in list(upcoming)[:5]:
                lines.append(f"  - {_event_line(ev)}")
        else:
            # Nothing inside the horizon is not nothing at all. The provider
            # tracks the next event whatever its distance, so name it rather
            # than leaving the operator to read an empty week as an empty
            # calendar — the two look identical and mean opposite things.
            nxt = getattr(ctx, "next_event", None) if ctx is not None else None
            if nxt:
                lines.append(f"No macro events in the next {horizon_h // 24}d. "
                             f"After that: {_event_line(nxt)}")
            elif ctx is None:
                # The horizon is empty and there is no context to ask about
                # what lies past it. Saying "none scheduled beyond it" here
                # would be a claim made from a source that was never consulted.
                lines.append(f"No macro events in the next {horizon_h // 24}d; "
                             "beyond that, <code>unknown</code>.")
            else:
                lines.append(f"No macro events in the next {horizon_h // 24}d, "
                             "and none scheduled beyond it.")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. CheckEventRiskSkill
# ---------------------------------------------------------------------------

class CheckEventRiskSkill(BaseSkill):
    name = "check_event_risk"
    description = "Check macro-event risk for a given symbol."
    command = "/eventrisk"

    async def execute(self, engine, **kwargs) -> str:
        symbol = kwargs.get("symbol", "").upper()
        if not symbol:
            return "Usage: /eventrisk &lt;SYMBOL&gt;"

        provider = _safe_getattr(engine, "macro_provider")
        calendar = _safe_getattr(engine, "macro_calendar")

        if provider is None and calendar is None:
            return (
                f"{_html_bold('Event Risk')} — {symbol}\n"
                "v2 macro provider not wired — cannot evaluate event risk."
            )

        source = provider or calendar

        # Try v2 macro provider get_context() first, then check_risk() fallback
        ctx_fn = _safe_getattr(source, "get_context")
        check_fn = _safe_getattr(source, "check_risk")

        if ctx_fn and callable(ctx_fn):
            try:
                result = ctx_fn(symbol=symbol)
            except Exception as exc:
                return f"Error checking risk for {symbol}: {exc}"

            risk_state = getattr(result, "risk_state", "UNKNOWN")
            # `severity` and `window` are Optional on MacroContext and are None
            # whenever the state is CLEAR — a real reading, not a missing one.
            # They printed as the bare literal `None`, which reads as a failure
            # rather than as the all-clear it actually is. `unknown` stays
            # reserved for the attribute genuinely not being there.
            severity = _present(result, "severity")
            window = _present(result, "window")
            # No 1.0 default: an unreadable size multiplier is not "full size".
            multiplier = _present(result, "size_multiplier")
            explanation = getattr(result, "explanation", "")
            is_stale = getattr(result, "is_stale", False)
            is_blind = getattr(result, "is_blind", False)

            lines = [
                f"{_html_bold('Event Risk')} — {symbol}",
                f"Risk State:      <code>{risk_state}</code>",
                f"Severity:        <code>{severity}</code>",
                f"Window:          <code>{window}</code>",
                f"Size multiplier: <code>{multiplier}</code>",
            ]
            if is_stale:
                lines.append("⚠️ Data is <b>stale</b> — fail-closed active")
            if is_blind:
                lines.append("⚠️ Operating <b>blind</b> — no calendar loaded")
            if explanation:
                lines.append(f"\n<i>{explanation}</i>")
            return "\n".join(lines)

        elif check_fn and callable(check_fn):
            try:
                result = check_fn(symbol)
            except Exception as exc:
                return f"Error checking risk for {symbol}: {exc}"

            severity = getattr(result, "severity", _safe_getattr(result, "get", lambda k, d=None: d)("severity", "N/A"))
            window = getattr(result, "window", _safe_getattr(result, "get", lambda k, d=None: d)("window", "none"))
            multiplier = getattr(result, "size_multiplier", _safe_getattr(result, "get", lambda k, d=None: d)("size_multiplier", 1.0))
            explanation = getattr(result, "explanation", _safe_getattr(result, "get", lambda k, d=None: d)("explanation", ""))

            lines = [
                f"{_html_bold('Event Risk')} — {symbol}",
                f"Severity:        <code>{severity}</code>",
                f"Window:          <code>{window}</code>",
                f"Size multiplier: <code>{multiplier}</code>",
                f"Explanation:     {explanation}",
            ]
            return "\n".join(lines)

        # Fallback: no check_risk method.
        return (
            f"{_html_bold('Event Risk')} — {symbol}\n"
            "Macro source has no <code>check_risk()</code> method. "
            "Ensure the v2 macro provider is wired."
        )


# ---------------------------------------------------------------------------
# 3. ComplianceStatusSkill
# ---------------------------------------------------------------------------

class ComplianceStatusSkill(BaseSkill):
    name = "compliance_status"
    # Not "compliance profile": the engine holds none, and a description that
    # promises one is how the card came to report its absence as a fault.
    description = "Restricted jurisdictions and consent ledger summary."
    command = "/compliance"

    async def execute(self, engine, **kwargs) -> str:
        compliance = _safe_getattr(engine, "compliance")
        if compliance is None:
            return (
                f"{_html_bold('Compliance Status')}\n"
                "v2 compliance module not wired."
            )

        lines = [_html_bold("Compliance Status")]

        # -- what the engine actually holds --
        # It holds no profile. `ComplianceEngine.authorize()` takes a
        # SubjectProfile as an ARGUMENT on every call, so there is nothing to
        # load and nothing to fail to load. The old probe therefore printed
        # "No compliance profile loaded." unconditionally — a fault report on a
        # permissions panel, describing a design.
        #
        # The restricted-jurisdiction set IS held, and IS the standing policy
        # this card should be showing, so it is what gets shown.
        restricted = _read(compliance, ("restricted_jurisdictions",
                                        "_restricted"))
        lines.append("")
        if restricted is _UNREADABLE:
            lines.append("Restricted jurisdictions: <code>unknown</code>")
        else:
            # `_read` returns whatever the attribute holds, so the shape is not
            # guaranteed. A card that raises renders nothing at all, which is
            # the least honest outcome available.
            try:
                codes = sorted(str(j) for j in restricted)
            except TypeError:
                codes = None
            if codes is None:
                lines.append("Restricted jurisdictions: <code>unreadable</code>")
            else:
                lines.append("Restricted jurisdictions: <code>"
                             + (", ".join(codes) or "none") + "</code>")
        lines.append("<i>Per-trade permissions are evaluated at authorization "
                     "time from the caller's profile — none is held here.</i>")

        # -- consent ledger --
        # The accessor is `get_consent_ledger()`. The old probe asked for
        # `consent_ledger` — no such attribute — so this card announced "No
        # consent ledger available" over a ledger holding up to 5,000 real
        # authorization decisions. On the surface whose only job is to show
        # that record.
        ledger = _read(compliance, ("get_consent_ledger", "consent_ledger"))
        lines.append("")
        if ledger is _UNREADABLE:
            lines.append("Consent ledger: <code>could not read</code> — the "
                         "decisions may exist; this card cannot see them.")
        elif not ledger:
            lines.append("Consent ledger: no decisions recorded yet.")
        else:
            entries = list(ledger)
            # Tallied with `is None`, and unreadable outcomes counted as their
            # own class rather than folded into either side. A ledger summary
            # that silently sorts unscorable rows into "granted" or "denied" is
            # the `losses = len(all) - wins` shape from CLAUDE.md's table, on
            # the record of who was allowed to trade.
            granted = sum(1 for e in entries if _field(e, "granted") is True)
            denied = sum(1 for e in entries if _field(e, "granted") is False)
            unknown = len(entries) - granted - denied
            tally = f"{granted} granted, {denied} denied"
            if unknown:
                tally += f", {unknown} unreadable"
            lines.append(_html_bold(
                f"Consent ledger — {len(entries)} decision(s): {tally}"))
            lines.append("<i>Last 5:</i>")
            for entry in entries[-5:]:
                lines.append(f"  {_decision_line(entry)}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. RequestLiveApprovalSkill
# ---------------------------------------------------------------------------

class RequestLiveApprovalSkill(BaseSkill):
    name = "request_live_approval"
    description = "Issue a one-time approval token for a pending trade."
    command = "/approve"

    async def execute(self, engine, **kwargs) -> str:
        trade_id = kwargs.get("trade_id", "")
        if not trade_id:
            return "Usage: /approve &lt;trade_id&gt;"

        approvals = _safe_getattr(engine, "approval_manager")
        if approvals is None:
            return (
                f"{_html_bold('Live Approval')}\n"
                "v2 approval manager not wired."
            )

        issue_fn = _safe_getattr(approvals, "issue_token")
        if issue_fn is None or not callable(issue_fn):
            return (
                f"{_html_bold('Live Approval')}\n"
                "Approval manager has no <code>issue_token()</code> method."
            )

        try:
            token = issue_fn(trade_id=trade_id)
        except Exception as exc:
            return f"Failed to issue approval token: {exc}"

        # An unreadable token id used to be replaced by `str(uuid4())[:8]` —
        # a freshly INVENTED identifier, printed to the operator as the token
        # they must quote to authorize a live trade. It would never match
        # anything. If the id cannot be read the honest move is to say so and
        # let the operator re-issue, not to hand them a plausible-looking
        # string that silently authorizes nothing.
        token_id = _field(token, "token_id")
        expiry = _field(token, "expires_at", _field(token, "expires_utc"))
        # `one_time` defaulted to True: an unreadable single-use flag asserted
        # the safest-sounding answer on a security control. Absent is unknown.
        one_time = _field(token, "one_time")

        if not token_id:
            return (
                f"{_html_bold('Live Approval')}\n"
                f"The approval manager returned a token for trade "
                f"<code>{trade_id}</code> with no readable id. Nothing was "
                "printed because a made-up id authorizes nothing — re-issue, "
                "or check the approval manager."
            )

        lines = [
            f"{_html_bold('Approval Token Issued')}",
            f"Trade ID:  <code>{trade_id}</code>",
            f"Token:     <code>{token_id}</code>",
            f"Expires:   <code>{expiry if expiry else 'unknown'}</code>",
            "One-time:  <code>"
            + ("unknown" if one_time is None else ("yes" if one_time else "no"))
            + "</code>",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. KillSwitchSkill
# ---------------------------------------------------------------------------

class KillSwitchSkill(BaseSkill):
    name = "kill_switch"
    description = "Trip the circuit breaker immediately and seal to audit chain."
    command = "/kill"

    async def execute(self, engine, **kwargs) -> str:
        reason = kwargs.get("reason", "Manual kill via /kill command")
        seal_ts = _now_utc().isoformat()

        # `engine.circuit_breaker` does not exist and never did, so this
        # command — the KILL SWITCH — answered "v2 circuit breaker not wired"
        # every time it was called, while a complete, tested emergency halt sat
        # one attribute away.
        #
        # It DELEGATES to that halt rather than re-implementing it.
        # `engine.risk.emergency_halt()` alone is a strictly weaker stop than
        # `/halt`: it does not cancel pending ideas, does not halt the per-user
        # risk engines, and does not transition the engine to HALTED. Calling
        # just that and then printing "All positions frozen" — which is what
        # the old card said unconditionally — would be a false all-clear in the
        # opposite direction, claiming a stop stronger than the one performed.
        #
        # NOTE: this command remains dispatched by no transport (it is in
        # tests/unreachable_skills_baseline.txt). Arming a SECOND emergency
        # halt beside the working `/halt` is a product decision, not a bug fix,
        # so this change makes it correct-if-wired and nothing more.
        halted_via = None
        breaker = _safe_getattr(engine, "circuit_breaker")
        trip_fn = _safe_getattr(breaker, "trip") if breaker is not None else None
        try:
            if trip_fn is not None and callable(trip_fn):
                trip_fn(reason=reason)
                halted_via = "circuit breaker"
                body = [
                    f"{_html_bold('KILL SWITCH ACTIVATED')}",
                    f"Time:   <code>{seal_ts}</code>",
                    f"Reason: {reason}",
                    "Circuit breaker is OPEN.",
                ]
            elif _safe_getattr(engine, "risk") is not None:
                from bot.skills.skill_registry import HaltSkill
                halted_via = "emergency halt"
                body = [await HaltSkill().execute(engine, **kwargs)]
            else:
                return (
                    f"{_html_bold('KILL SWITCH')}\n"
                    "<b>NOTHING WAS STOPPED.</b> This engine exposes neither a "
                    "circuit breaker nor a risk engine, so there is no halt to "
                    "call. Stop the process directly."
                )
        except Exception as exc:
            # Never report a kill that did not happen.
            return (f"{_html_bold('KILL SWITCH FAILED')}\n"
                    f"<b>NOTHING WAS STOPPED.</b> {exc}")

        # Seal to the audit chain. The old probe looked for `seal()`, which
        # AuditChain does not have (`append`/`seal_decision` are the real
        # methods), so this was a permanent silent no-op under a card that
        # announced the kill as sealed. Whether it sealed is now REPORTED —
        # an unrecorded emergency stop is a fact the operator needs.
        audit = _safe_getattr(engine, "audit_chain")
        sealed = False
        if audit is not None:
            append_fn = _safe_getattr(audit, "append")
            if callable(append_fn):
                try:
                    append_fn("KILL_SWITCH", {"reason": reason,
                                              "timestamp": seal_ts,
                                              "via": halted_via})
                    sealed = True
                except Exception:
                    sealed = False
        body.append("")
        body.append("Audit chain: <code>sealed</code>" if sealed else
                    "Audit chain: <code>NOT sealed</code> — the halt happened; "
                    "the record of it did not.")
        return "\n".join(body)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_v2_skills() -> List[BaseSkill]:
    """Return all v2 macro-aware skill instances."""
    return [
        MacroBriefSkill(),
        CheckEventRiskSkill(),
        ComplianceStatusSkill(),
        RequestLiveApprovalSkill(),
        KillSwitchSkill(),
    ]
