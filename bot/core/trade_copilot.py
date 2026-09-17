"""Trade co-pilot — a deterministic second opinion on a proposed order.

Before a user confirms a manual trade, the co-pilot reviews it against the
objective facts that matter — reward:risk, stop distance, geometry, size vs
equity, and the engine's current bias and the user's existing exposure for that
symbol. It returns a structured verdict + flags the UI shows right in the
ticket.

Deliberately DETERMINISTIC and pure: no LLM dependency, no network, same input →
same review every time. It ADVISES — it never blocks or places anything (the
risk gate and, for live, the Authority Envelope are the authorities). An LLM
one-liner can be layered on top by the caller, but the substance here is real
arithmetic the user can verify.

**IT SAYS WHAT IT CHECKED.** Driven with a clean long — R:R 3, stop 2% — and
nothing else readable, this function used to answer ``verdict "clear"``,
``score 100`` and *"✅ Looks disciplined (score 100/100)"* with THREE of its five
subjects silently skipped, on the door that opens a real position. That is
``integrity_veto.assess({})`` verbatim: "clear is what a reader takes as a clean
bill of health, and printing it over ``checked == 0`` is a confident all-clear
manufactured from no data". The score made it worse rather than better —
it starts at 100 and only ever subtracts, so 100/100 over two checks and
100/100 over four were one number.

So every review carries ``checks`` (per subject: ``ok`` / ``flag`` /
``unchecked``), ``unchecked`` (each with the REASON, so "no margin on this
ticket" is not confused with "your live balance could not be read"), and
``score_basis``, because a figure without its span is the ``summary.scored``
defect one card over. ``verdict`` has a fourth word, ``partial``: nothing
flagged, and not everything could be looked at.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# Thresholds (percentage points / ratios). Tuned to flag, not to nag.
_MIN_RR = 1.5
_STOP_TIGHT_PCT = 0.3
_STOP_WIDE_PCT = 15.0
_SIZE_HEAVY_PCT = 20.0        # margin > 20% of equity → concentration flag

#: Nothing flagged and every subject was looked at.
VERDICT_CLEAR = "clear"
#: Nothing flagged, but at least one subject could not be looked at. A CAUTION
#: outranks it: a flag is the louder fact, and the unchecked list prints either
#: way, so folding them would lose the flag rather than the gap.
VERDICT_PARTIAL = "partial"
VERDICT_CAUTION = "caution"
VERDICT_INVALID = "invalid"

#: The four subjects that move the score, in the order the card prints them.
SCORED_CHECKS = ("reward_risk", "stop_distance", "size_vs_equity", "engine_bias")
#: ``existing_exposure`` is covered but NOT scored — it only ever adds a note,
#: and counting a subject that cannot subtract would make the basis a count of
#: something other than what the score is over.
ALL_CHECKS = SCORED_CHECKS + ("existing_exposure",)

#: How each subject is NAMED to a person. One table, printed by the text
#: renderer here and by the dashboard's block, because `size_vs_equity` is an
#: internal identifier and a renderer that prettifies it on its own is the
#: `Pro_scan scan` shape — an identifier, tidied up, in a sentence shown to a
#: user, decided in two places.
CHECK_LABELS = {
    "reward_risk": "reward:risk",
    "stop_distance": "stop distance",
    "size_vs_equity": "size vs equity",
    "engine_bias": "the engine's bias",
    "existing_exposure": "your existing exposure",
}

#: What the caller is saying about a subject it could not read. A reason is
#: only ever the caller's or this module's own; nothing is invented for a
#: subject whose absence nobody explained.
_NOT_SUPPLIED = {
    "size_vs_equity": "the equity for this account was not supplied",
    "engine_bias": "the engine's current lean was not supplied",
    "existing_exposure": "your existing position on this symbol was not supplied",
}


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def review(trade: dict, *, equity_usd: Optional[float] = None,
           engine_bias: Optional[str] = None,
           existing_exposure: Optional[str] = None,
           unread: Optional[Mapping[str, str]] = None) -> dict:
    """Review a proposed trade. Returns
    ``{verdict, score, score_basis, rr, stop_pct, target_pct, checks,
    unchecked:[{name,reason}], flags:[{level,msg}], notes:[...]}``.

    ``trade`` = ``{direction, symbol, entry, sl, tp, margin?}``.
    ``engine_bias`` ∈ {"long","short",None}: the engine's current lean on the
    symbol. ``existing_exposure`` ∈ {"long","short","flat",None}: the user's
    current net side on the symbol — ``"flat"`` is a READ empty book and is a
    measurement, where ``None`` is a book nobody could read, and the two get
    different sentences.

    ``unread`` maps a check name to the CALLER's own sentence for why its input
    is absent. It is a second parameter rather than a widening of the three
    above because only the caller knows the difference between "your live
    balance could not be read just now" (look at the venue) and "no live
    account is linked to you" (link one) — this module would have to invent it,
    which is the thing it exists to stop. Where the caller says nothing, the
    module's own neutral sentence is used and nothing is guessed.
    """
    direction = str(trade.get("direction") or "").upper().strip()
    is_long = direction in ("LONG", "BUY")
    e, sl, tp = _f(trade.get("entry")), _f(trade.get("sl")), _f(trade.get("tp"))
    flags: list[dict] = []
    notes: list[str] = []
    checks: dict[str, str] = {}
    unchecked: list[dict] = []
    why = dict(unread or {})

    def _uncheck(name: str, reason: str) -> None:
        checks[name] = "unchecked"
        unchecked.append({"name": name, "label": CHECK_LABELS[name],
                          "reason": reason})

    def _said(name: str) -> str:
        return str(why.get(name) or _NOT_SUPPLIED[name])

    # Geometry must be valid before anything else means anything.
    geom_ok = (e is not None and sl is not None and tp is not None and e > 0
               and ((is_long and sl < e < tp) or (not is_long and tp < e < sl)))
    if not geom_ok:
        # Nothing downstream ran, and the review says so rather than reporting
        # four silent skips: a verdict of `invalid` is about the TICKET, and a
        # reader still needs to know that no other subject was reached.
        reason = ("the ticket's geometry is not valid, so nothing else could "
                  "be reviewed")
        for name in ALL_CHECKS:
            checks[name] = "unchecked"
        return {"verdict": VERDICT_INVALID, "score": None,
                "score_basis": {"applied": 0, "total": len(SCORED_CHECKS)},
                "rr": None, "stop_pct": None, "target_pct": None,
                "checks": checks,
                "unchecked": [{"name": n, "label": CHECK_LABELS[n],
                                "reason": reason} for n in ALL_CHECKS],
                "flags": [{"level": "block", "msg":
                           "Stop/target are on the wrong side of entry for a "
                           f"{'long' if is_long else 'short'}."}],
                "notes": []}

    assert e is not None and sl is not None and tp is not None  # narrowed by geom_ok
    # `geom_ok` demands sl < e < tp (or tp < e < sl), both STRICT, so `risk` is
    # positive here by construction and the `if risk > 0 else None` this line
    # used to carry could not fire. A line no input can reach is not a check,
    # it is a claim that there is one — and it was a claim the two renderers
    # disagreed about (`human_readable` formats `rr` with `:g`, which raises on
    # None; the dashboard prints `d.rr ?? '—'`). The arithmetic is driven in
    # the suite instead, so the day the geometry gate loosens, that fails
    # rather than this quietly dividing by zero.
    risk = abs(e - sl)
    reward = abs(tp - e)
    rr = round(reward / risk, 2)
    stop_pct = round(risk / e * 100, 2)
    target_pct = round(reward / e * 100, 2)
    score = 100

    # Reward:risk.
    if rr < _MIN_RR:
        flags.append({"level": "warn",
                      "msg": f"Reward:risk is {rr:g} — below {_MIN_RR:g}. The target "
                             "doesn't pay enough for the risk."})
        score -= 25
        checks["reward_risk"] = "flag"
    else:
        if rr >= 2.5:
            notes.append(f"Strong reward:risk ({rr:g}).")
        checks["reward_risk"] = "ok"

    # Stop distance.
    if stop_pct < _STOP_TIGHT_PCT:
        flags.append({"level": "warn",
                      "msg": f"Stop is only {stop_pct:g}% away — likely to be wicked "
                             "out by noise."})
        score -= 20
        checks["stop_distance"] = "flag"
    elif stop_pct > _STOP_WIDE_PCT:
        flags.append({"level": "warn",
                      "msg": f"Stop is {stop_pct:g}% away — a wide stop means a large "
                             "loss if hit; size accordingly."})
        score -= 15
        checks["stop_distance"] = "flag"
    else:
        checks["stop_distance"] = "ok"

    # Size vs equity.
    margin = _f(trade.get("margin"))
    eq = _f(equity_usd)
    if margin is None:
        _uncheck("size_vs_equity", "no margin on this ticket")
    elif eq is None:
        _uncheck("size_vs_equity", _said("size_vs_equity"))
    elif eq > 0:
        share = margin / eq * 100
        if share > _SIZE_HEAVY_PCT:
            flags.append({"level": "warn",
                          "msg": f"Margin is {share:.0f}% of your equity — heavy "
                                 "concentration on one trade."})
            score -= 20
            checks["size_vs_equity"] = "flag"
        else:
            # `{:.0f}` printed "Margin is 0% of equity" for a real 0.5% stake:
            # a rounding that reads as no margin at all, on the line whose job
            # is to say how much of the account is at risk. A share that is
            # small is said to be small.
            # The test is what the FORMAT prints, not a threshold guessed
            # beside it: `f"{0.5:.0f}"` is "0" (banker's rounding), so a rule
            # written as `share >= 0.5` still publishes "0%" on its own edge.
            rounded = f"{share:.0f}"
            shown = "<1%" if rounded == "0" and share > 0 else f"{rounded}%"
            notes.append(f"Margin is {shown} of equity.")
            checks["size_vs_equity"] = "ok"
    else:
        # A READ zero (or negative) equity is a MEASUREMENT, not a gap, and it
        # is the loudest size finding there is: there is nothing for the margin
        # to be a share of. Filing it as "unchecked" would hide a real state
        # behind the word for a failed read.
        flags.append({"level": "warn",
                      "msg": f"Your equity reads ${eq:,.2f} — there is nothing for "
                             "this margin to be a share of."})
        score -= 20
        checks["size_vs_equity"] = "flag"

    # Alignment with the engine's current bias.
    side = "long" if is_long else "short"
    if engine_bias in ("long", "short"):
        if engine_bias != side:
            flags.append({"level": "caution",
                          "msg": f"This {side} runs counter to the engine's current "
                                 f"{engine_bias} bias on {trade.get('symbol', 'the symbol')}."})
            score -= 15
            checks["engine_bias"] = "flag"
        else:
            notes.append(f"Aligned with the engine's {engine_bias} bias.")
            checks["engine_bias"] = "ok"
    else:
        _uncheck("engine_bias", _said("engine_bias"))

    # Existing exposure (stacking / hedging). Never scored — a note is not a
    # verdict — but it IS covered, because the header promises it.
    if existing_exposure in ("long", "short"):
        if existing_exposure == side:
            notes.append(f"You're already {existing_exposure} this symbol — this "
                         "stacks the position (correlated risk).")
        else:
            notes.append(f"You're currently {existing_exposure} this symbol — this "
                         "would hedge/reduce it.")
        checks["existing_exposure"] = "ok"
    elif existing_exposure == "flat":
        notes.append(f"No open position on {trade.get('symbol', 'this symbol')} — "
                     "this would open a new one.")
        checks["existing_exposure"] = "ok"
    else:
        _uncheck("existing_exposure", _said("existing_exposure"))

    score = max(0, min(100, score))
    applied = sum(1 for n in SCORED_CHECKS if checks.get(n) in ("ok", "flag"))
    has_flag = bool(flags)
    if has_flag:
        verdict = VERDICT_CAUTION
    elif unchecked:
        verdict = VERDICT_PARTIAL
    else:
        verdict = VERDICT_CLEAR
    return {"verdict": verdict, "score": score,
            "score_basis": {"applied": applied, "total": len(SCORED_CHECKS)},
            "rr": rr, "stop_pct": stop_pct, "target_pct": target_pct,
            "checks": checks, "unchecked": unchecked,
            "flags": flags, "notes": notes}


#: What the head of the card says. `partial` is deliberately NOT a reassuring
#: sentence: the one thing that must not happen is a reader taking "nothing
#: flagged" for "everything looked at".
_HEADS = {
    VERDICT_CLEAR: "✅ Looks disciplined",
    VERDICT_CAUTION: "⚠️ Worth a second look",
    VERDICT_PARTIAL: "🟡 Only partly reviewed — nothing flagged in what was checked",
}


def score_line(rev: dict) -> str:
    """``score 80/100 over 3 of the 4 checks`` — the figure with its own span.

    The span is on the figure rather than in a footnote because the whole
    defect was a reader taking 100/100 as a verdict about the trade when it was
    a verdict about however many checks happened to run.
    """
    basis = rev.get("score_basis") or {}
    applied = basis.get("applied")
    total = basis.get("total")
    head = f"score {rev.get('score')}/100"
    if not isinstance(applied, int) or not isinstance(total, int) or total <= 0:
        return head
    if applied >= total:
        return f"{head} over all {total} checks"
    return f"{head} over {applied} of the {total} checks"


def human_readable(rev: dict) -> str:
    """Plain-text render of a review (no markup)."""
    if not rev or rev.get("verdict") == VERDICT_INVALID:
        msg = (rev.get("flags") or [{}])[0].get("msg", "Invalid trade geometry.")
        return f"⛔ {msg}"
    head = _HEADS.get(rev["verdict"], rev["verdict"])
    bits = [f"{head} ({score_line(rev)})",
            f"R:R {rev['rr']:g} · stop {rev['stop_pct']:g}% · target {rev['target_pct']:g}%"]
    for f in rev.get("flags", []):
        bits.append(f"• {f['msg']}")
    for n in rev.get("notes", []):
        bits.append(f"· {n}")
    for u in rev.get("unchecked", []):
        label = u.get("label") or CHECK_LABELS.get(u.get("name"), u.get("name"))
        bits.append(f"◦ Not checked — {label}: {u['reason']}")
    return "\n".join(bits)
