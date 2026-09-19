"""
CROSS-2 — guided cross-chain yield execution (operator-signed plans).

The middle rung between CROSS-1 (read-only scan, ``app/lib/cross_yield.js``
``planMoves``) and CROSS-3 (the autonomous loop, DESIGN-ONLY in
``docs/CROSS_CHAIN_REBALANCE_DESIGN.md``). Here a HUMAN stays in the per-move
loop: this module compiles a single scanned move into an execution PLAN and runs
the same triple-gate the autonomous design specifies — but it only *decides* and
*previews*. The operator signs the first leg through the existing testnet signer;
nothing here signs, broadcasts, or moves a coin.

The triple-gate (§3 of the design doc), each evaluated independently, any
failure → skip, fail-closed:
  1. SCANNER — the move is ``worth: 'yes'`` with a positive net-of-cost return
     over the horizon (``net_horizon_usd > 0``).
  2. POLICY — the deterministic yield policy passes (the rule catalogue below).
  3. AUTHORITY — the Authority Envelope authorizes the first leg as a ``transfer``
     to an allowlisted destination (``authority.authorize``).

Locked v1 scope: stables-only, non-custodial + recallable REQUIRED, a 30-day
default horizon, and FIRST-LEG-TRANSFER ONLY — the plan's executable step is a
single transfer of stables on the source chain to an operator-controlled
(allowlisted, recallable) destination. Bridge legs + destination deposits are a
later per-protocol slice. Pure + deterministic + never raises.
"""

from __future__ import annotations

from typing import Any, Optional

# Stablecoins CROSS-2 will move. Anything else is refused (stables-only, v1).
STABLES: frozenset[str] = frozenset({
    "USDC", "USDT", "DAI", "TUSD", "USDE", "PYUSD", "FRAX", "USDP", "GUSD", "LUSD",
})

DEFAULT_HORIZON_DAYS = 30

# The default yield policy — the doc's §5 catalogue, conservative v1 values. An
# operator's hand-authored envelope/policy overrides these; every rule only ever
# TIGHTENS (a stricter operator value wins). Mirrors the NL:
#   "move stables only when it clears costs within 30 days, ≥1% better APY,
#    ≤$50 a move / ≤$150 a day, non-custodial + recallable, testnet only."
DEFAULT_YIELD_POLICY: tuple[dict[str, Any], ...] = (
    {"type": "min_delta_apy_pct", "value": 1.0},
    {"type": "max_breakeven_days", "value": 30},
    {"type": "min_net_horizon_usd", "value": 0.01},
    {"type": "max_move_notional_usd", "value": 50.0},
    {"type": "max_daily_move_usd", "value": 150.0},
    {"type": "allowed_assets", "value": ["USDC"]},
    {"type": "require_noncustodial", "value": True},
    {"type": "require_recallable", "value": True},
)


def _num(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _flag(v: Any) -> Optional[bool]:
    """A boolean the move REPORTED, or None for one it did not.

    `bool(v)` is the wrong reader for a safety flag twice over: `bool(None)`
    is False, so an unreported field reads as the SAFE answer, and
    `bool("false")` is True, so the string spelling reads as the unsafe one.
    Only a real boolean (or the 0/1 JSON sometimes carries) is a reading."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    return None


def evaluate_yield_policy(rules: Any, move: dict, *,
                          spent_today_usd: float = 0.0) -> dict:
    """Pure, per-rule fail-open evaluation of a yield policy against one move.

    A rule that cannot be evaluated (unknown type, non-numeric input) SKIPS —
    it neither passes nor blocks — leaving the envelope's own gate as the floor.
    Returns ``{verdict: 'pass'|'fail', reasons: [...], checked: int}``. Never
    raises. ``verdict`` is 'pass' only when NO rule failed."""
    reasons: list[str] = []
    checked = 0
    move = move or {}
    asset = str(move.get("asset") or "").upper()
    # NOT `or 0.0`. A size nobody reported cannot be SHOWN to be under a cap,
    # and $0 passes every cap there is — so the coercion turned an unread
    # notional into a move that clears the per-move and per-day limits.
    amount = _num(move.get("amount_usd"))
    for rule in (rules or []):
        if not isinstance(rule, dict):
            continue
        rtype = str(rule.get("type") or "").strip()
        val = rule.get("value")
        if rtype == "min_delta_apy_pct":
            v, d = _num(val), _num(move.get("delta_apy"))
            if v is not None and d is not None:
                checked += 1
                if d < v:
                    reasons.append(f"APY gain {d:.2f}% is below the {v:.2f}% minimum")
        elif rtype == "max_breakeven_days":
            v, d = _num(val), _num(move.get("breakeven_days"))
            if v is not None:
                checked += 1
                # This is the ONE rule that read None correctly from the start —
                # it just said it badly, interpolating the absence as a figure
                # ("breakeven None days exceeds…"). Same refusal, named.
                if d is None:
                    reasons.append(
                        f"the move's breakeven was not reported, so the "
                        f"{int(v)}-day horizon could not be checked")
                elif d > v:
                    reasons.append(f"breakeven {d} days exceeds the {int(v)}-day horizon")
        elif rtype == "min_net_horizon_usd":
            v, d = _num(val), _num(move.get("net_horizon_usd"))
            if v is not None and d is not None:
                checked += 1
                if d < v:
                    reasons.append(f"net-of-cost ${d:.2f} is below the ${v:.2f} minimum")
        elif rtype == "max_move_notional_usd":
            v = _num(val)
            if v is not None:
                checked += 1
                if amount is None:
                    reasons.append(
                        f"the move's size was not reported, so the ${v:.2f} "
                        f"per-move cap could not be checked")
                elif amount > v + 1e-9:
                    reasons.append(f"move ${amount:.2f} exceeds the ${v:.2f} per-move cap")
        elif rtype == "max_daily_move_usd":
            v = _num(val)
            if v is not None:
                checked += 1
                spent = _num(spent_today_usd) or 0.0
                if amount is None:
                    reasons.append(
                        f"the move's size was not reported, so the ${v:.2f} "
                        f"daily cap could not be checked")
                elif spent + amount > v + 1e-9:
                    reasons.append(
                        f"daily total ${spent + amount:.2f} would exceed the ${v:.2f} cap")
        elif rtype == "allowed_assets":
            allow = [str(a).upper() for a in (val or [])]
            if allow:
                checked += 1
                if asset not in allow:
                    reasons.append(f"{asset or '?'} is not in the allowed assets ({', '.join(allow)})")
        elif rtype == "allowed_chains":
            allow = [str(c).lower() for c in (val or [])]
            if allow:
                checked += 1
                frm = str(move.get("from_chain") or "").lower()
                if frm and frm not in allow:
                    reasons.append(f"chain '{frm}' is not allowed ({', '.join(allow)})")
        elif rtype == "require_noncustodial":
            # A REQUIREMENT, not a bound: the module's locked v1 scope calls
            # non-custodial REQUIRED, so a route nobody described is refused
            # rather than assumed safe. `bool(move.get("custodial"))` read an
            # unreported field as False — the reassuring answer, from no data.
            if bool(val):
                checked += 1
                cust = _flag(move.get("custodial"))
                if cust is None:
                    reasons.append(
                        "the move does not say whether the route is custodial — "
                        "a non-custodial route has to be shown, not assumed")
                elif cust:
                    reasons.append("move is custodial — a non-custodial route is required")
        elif rtype == "require_recallable":
            # Same reading, same reason. `(… or 0.0) > 0` made an unreported
            # lockup indistinguishable from a measured "withdraw anytime".
            if bool(val):
                checked += 1
                lockup = _num(move.get("lockup_days"))
                if lockup is None:
                    reasons.append(
                        "the move does not say whether the destination locks funds up — "
                        "a recallable route has to be shown, not assumed")
                elif lockup > 0:
                    reasons.append("destination has a lockup — a recallable route is required")
        # unknown rule type → skip (fail-open), never block.
    return {"verdict": "pass" if not reasons else "fail",
            "reasons": reasons, "checked": checked}


def evaluate_yield_move(*, move: dict, to_chain: str, dest: str,
                        policy_rules: Any = None, envelope: Optional[dict] = None,
                        now_ts: float, spent_today_usd: float = 0.0) -> dict:
    """Compile + triple-gate a single yield move. Returns a decision:

      {verdict: 'execute'|'skip',
       reasons: [...],                       # every failing gate's reason
       gates: {scanner: bool, policy: bool, authority: bool},
       first_leg: {kind, asset, dest, network, notional_usd} | None,
       stables_only_ok: bool}

    'execute' means ALL THREE gates pass AND the locked hard-gates hold
    (stables-only, non-custodial, recallable). Even then, this only proposes —
    the operator still signs the first leg through the gated testnet signer.
    Pure + fail-closed: any missing/malformed input → skip with a reason, never
    an exception, never a guess."""
    move = move or {}
    reasons: list[str] = []
    asset = str(move.get("asset") or "").upper()
    amount = _num(move.get("amount_usd"))          # None = the size was not reported
    dest = str(dest or "").strip()

    # Locked hard-gate: stables only (v1 moves nothing else).
    stables_ok = asset in STABLES
    if not stables_ok:
        reasons.append(f"{asset or 'asset'} is not a supported stablecoin (stables-only v1)")

    # Gate 1 — scanner worth.
    worth = str(move.get("worth") or "").lower()
    net_h = _num(move.get("net_horizon_usd"))
    scanner_ok = worth == "yes" and net_h is not None and net_h > 0
    if not scanner_ok:
        reasons.append(f"scanner does not rate this worth moving (worth={worth or '?'}, "
                       f"net-of-cost ${net_h if net_h is not None else '?'})")

    # Gate 2 — deterministic yield policy.
    rules = policy_rules if policy_rules is not None else DEFAULT_YIELD_POLICY
    pol = evaluate_yield_policy(rules, move, spent_today_usd=spent_today_usd)
    policy_ok = pol["verdict"] == "pass"
    if not policy_ok:
        reasons.extend(pol["reasons"])

    # Gate 3 — the Authority Envelope authorizes the first-leg transfer.
    authority_ok = False
    if not dest:
        reasons.append("no destination address for the first-leg transfer")
    elif amount is None:
        # The envelope's own limits are NOTIONAL limits. Passing 0.0 here asked
        # it to authorise a $0 transfer and took the allow as authority for a
        # move of unknown size — so the gate is refused with its own reason
        # rather than answered from a number nobody reported.
        reasons.append(
            "the move's size was not reported, so the authority envelope's "
            "notional limits could not be checked")
    else:
        try:
            from bot.guardian.authority import authorize
            result = authorize(envelope,
                               {"kind": "transfer", "asset": asset,
                                "notional_usd": amount, "dest": dest},
                               now_ts=now_ts, spent_today_usd=spent_today_usd)
            authority_ok = result.get("decision") == "allow"
            if not authority_ok:
                reasons.extend(list(result.get("reasons") or ["authority denied the transfer"]))
        except Exception:
            reasons.append("authority check failed")

    execute = bool(stables_ok and scanner_ok and policy_ok and authority_ok)
    first_leg = None
    if stables_ok and dest:
        # The executable step: transfer stables on the SOURCE chain toward the
        # operator-controlled (allowlisted, recallable) destination. Bridge +
        # deposit legs are a later slice — this is first-leg-transfer scope only.
        first_leg = {
            "kind": "transfer",
            "asset": asset,
            "dest": dest,
            "network": str(move.get("from_chain") or "").lower() or None,
            "to_chain": str(to_chain or "").lower() or None,
            # None, never 0.00: this is the figure on the preview an operator
            # signs from, and $0.00 reads as a measured size rather than as one
            # the move never stated.
            "notional_usd": None if amount is None else round(amount, 2),
        }
    return {
        "verdict": "execute" if execute else "skip",
        "reasons": reasons,
        "gates": {"scanner": scanner_ok, "policy": policy_ok, "authority": authority_ok},
        "stables_only_ok": stables_ok,
        "first_leg": first_leg,
        "horizon_days": DEFAULT_HORIZON_DAYS,
    }
