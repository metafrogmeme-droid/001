"""Universal Escape Agent — the recovery layer of Guardian.

    The AI proposes. Deterministic controls authorize. The escape agent recovers.

When something goes wrong — a crash, a compromised key, an operator who just wants
*out* — the question is not only "close everything" but **in what order**, so the
unwind itself doesn't make things worse. The Escape Agent produces a **safe,
ordered emergency-exit plan**: which position to close first, why, and how much
margin each close frees for the positions still open.

``plan(positions)`` is a **pure, deterministic** planner (no engine, no exchange,
no clock, no network). It ranks the book by *escape urgency* — a risk-weighted
blend of how close each position sits to its own liquidation (fragility) and how
large it is (exposure) — so the most dangerous positions are unwound first, and
each close frees isolated margin that widens the liquidation buffer on everything
still open.

Scope + safety stance (this is the plan-only module):

* **Plan, don't pull the trigger.** This module *describes* the exit; it never
  closes anything. Execution stays with the existing, battle-tested primitives
  (``engine.flatten_all_positions`` / ``executor.close_all_positions`` /
  ``close_position`` / reduce-only ``_partial_close``). The plan names the
  recommended path; a human (or a later, explicitly-gated executor) acts on it.
* **Pure + deterministic + fail-open.** Ranking is a pure function of the book
  snapshot, so it is trivially testable and can never touch the trade path; a bad
  position is skipped, a fault degrades to an empty plan.
* **Ordered for safety, with the reason attached.** Every step carries *why* it
  is where it is (fragility, exposure, margin freed), so the plan is auditable,
  not a black box.

Reuses the Digital Twin's canonical ``liquidation_move_frac`` (one liquidation
formula across Guardian, no drift).
"""

from __future__ import annotations

from typing import Any, Optional

from bot.guardian import book_read
from bot.guardian.digital_twin import liquidation_move_frac

ESCAPE_VERSION = 1

RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

# Escape-urgency (book danger) thresholds, keyed on the MOST fragile position's
# adverse-move-to-liquidation %. Closer to liquidation → more urgent to unwind.
_URGENT_HIGH = 8.0      # a position within an 8% adverse move of liquidation
_URGENT_MEDIUM = 15.0
_URGENT_LOW = 30.0

# When leverage is unknown we can't estimate fragility; treat as a wide, low-
# urgency move so unknown-leverage positions sort last, never first.
_UNKNOWN_MOVE = 1.0


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _notional(pos: dict) -> float:
    entry, qty = _num(pos.get("entry")), _num(pos.get("qty"))
    if entry is not None and qty is not None:
        return abs(entry * qty)
    cost, lev = _num(pos.get("cost_usd")), _num(pos.get("leverage"))
    if cost is not None:
        return abs(cost * (lev or 1.0))
    return 0.0


def _margin(pos: dict) -> float:
    cost = _num(pos.get("cost_usd"))
    if cost is not None:
        return abs(cost)
    # Fall back to notional / leverage.
    lev = _num(pos.get("leverage")) or 1.0
    return _notional(pos) / lev if lev > 0 else 0.0


def _direction(pos: dict) -> str:
    return "SHORT" if str(pos.get("direction", "LONG")).upper().startswith("S") else "LONG"


def _urgency(notional: float, liq_move_frac: Optional[float]) -> float:
    """Risk-weighted escape priority: exposure amplified by fragility. A big,
    near-liquidation position outranks a small, well-collateralised one. Scaled
    so a 10× position (≈0.10 move) weights roughly at its full notional."""
    move = liq_move_frac if (liq_move_frac is not None and liq_move_frac > 0) else _UNKNOWN_MOVE
    return notional * (0.10 / max(move, 0.01))


def _book_risk(min_move_pct: Optional[float]) -> Optional[str]:
    """Unwind urgency, or None when fragility could not be measured at all.

    `min_move_pct` is None only when NO position had a readable leverage, so
    nothing here knows how close anything sits to liquidation. Returning
    "none" for that said the book was as calm as a book can be, on the exact
    evidence that it could not be assessed.

    Note the contrast with `_urgency` above, which deliberately treats unknown
    leverage as a wide, low-urgency move. That is an ORDERING choice — where
    to sort a position we cannot rank — and it is right. This is a VERDICT
    about the whole book, and the same substitution is wrong here. Fail-open
    per item, fail-loud on the summary.
    """
    if min_move_pct is None:
        return None
    if min_move_pct < _URGENT_HIGH:
        return "high"
    if min_move_pct < _URGENT_MEDIUM:
        return "medium"
    if min_move_pct < _URGENT_LOW:
        return "low"
    return "none"


def _reason(rank: int, group: str, liq_move_pct: Optional[float], share_pct: float) -> str:
    bits = []
    if liq_move_pct is not None and liq_move_pct < _URGENT_MEDIUM:
        bits.append(f"~{liq_move_pct}% from liquidation")
    if share_pct >= 25.0:
        bits.append(f"{share_pct}% of the book")
    if group not in ("", "*"):
        bits.append(f"{group} exposure")
    if not bits:
        return "reduce remaining exposure"
    if rank == 1:
        return "close first — " + ", ".join(bits)
    return ", ".join(bits)


def _unpriceable(cov: book_read.BookCoverage) -> dict:
    """The document for a book that WAS read and none of whose rows could be
    priced. Three facts, three documents:

    * `base`  — the book is flat. Nothing to unwind, and that is a reading.
    * `failed` — the planner raised. Nothing is known, including the count.
    * this   — N positions are open and not one of them could be priced. The
      count is a MEASUREMENT and the symbols are named, which is strictly
      more than `failed` can say and the opposite of what `base` says.

    `risk` is None rather than `book_read.verdict_over`'s "unknown" on purpose:
    this module already spells unknown urgency as None (`_book_risk`'s own
    docstring, and `escape_card.risk_icon`'s dedicated `risk is None` arm), and
    two spellings of one word in one document is the second-copy shape. The two
    agree — `_book_risk(None)` is None for exactly the book this branch is for
    — and `TestTheTwoUnknownsAgree` drives that rather than assuming it.

    `ok` stays True because the planner RAN. What it could not do is priced in
    `book_coverage`, which the card branches on and the sealed payload carries.
    """
    return {
        "version": ESCAPE_VERSION, "ok": True,
        "position_count": 0,          # priced rows: a counted zero, not an absence
        "gross_notional_usd": None, "total_margin_usd": None,
        "risk": None, "steps": [],
        "book_coverage": cov.as_dict(),
        "coverage_note": book_read.coverage_note(cov, figure="the plan figures"),
        "recommended": ("no position could be priced, so no exit order could be "
                        "worked out — this is not a flat book. Close manually "
                        "with /closeall, or /emergency_stop to halt and flatten"),
    }


def plan(positions: list[dict]) -> dict:
    """Build a safe, ordered emergency-exit plan. Pure; never raises.

    Returns::

        {
          "version": int,
          "position_count": int,
          "gross_notional_usd": float,
          "total_margin_usd": float,
          "risk": "none"|"low"|"medium"|"high",   # how urgent unwinding is
          "steps": [ {"order","symbol","direction","group","notional_usd",
                      "leverage","liq_move_pct","urgency","margin_freed_cum_usd",
                      "reason"}, ... ],            # most-dangerous first
          "recommended": str,                      # the execution primitive to use
        }
    """
    def base(cov: book_read.BookCoverage) -> dict:
        return {"version": ESCAPE_VERSION, "ok": True, "position_count": 0,
                "gross_notional_usd": 0.0, "total_margin_usd": 0.0,
                "risk": "none", "steps": [],
                "book_coverage": cov.as_dict(), "coverage_note": "",
                "recommended": "no open positions — nothing to unwind"}
    #: THE SAME DOCUMENT MEANT TWO OPPOSITE THINGS. `base` was returned both
    #: for a genuinely flat book and from the `except` arm below — so a planner
    #: that crashed with positions open answered with a document that asserts
    #: `position_count: 0`, `risk: "none"` and "nothing to unwind", and
    #: `/escape` rendered it as "🪂 no open positions to unwind".
    #:
    #: That is this repository's central rule broken on the emergency-exit
    #: surface: a failed read rendered as a confident empty result, to an
    #: operator who is reading it precisely because something is wrong.
    #: `ok` is what lets a caller tell the two apart, and `risk: None` means
    #: unknown rather than "none" — the word for the calmest reading there is.
    failed = {"version": ESCAPE_VERSION, "ok": False, "position_count": None,
              "gross_notional_usd": None, "total_margin_usd": None,
              "risk": None, "steps": [],
              "book_coverage": None, "coverage_note": "",
              "recommended": "the escape plan could not be built — this says "
                             "nothing about whether the book is flat"}
    try:
        #: A ROW THIS PLANNER CANNOT PRICE IS NOT A ROW THAT IS NOT THERE.
        #: The filter below has always dropped them, and then `if not rows`
        #: returned the FLAT document — so a book of adopted positions the
        #: venue priced neither way (`entry_price=0.0` and `cost_usd=0.0`,
        #: which `live_executor` writes and the restore path reads back)
        #: rendered byte-for-byte as "🪂 no open positions to unwind", with
        #: `ok: True`. That is the defect the comment above `base` describes
        #: as fixed: it was fixed for the `except` arm and left standing one
        #: arm over, in the same function.
        #:
        #: `book_read` is the shared count, and the PREDICATE stays local —
        #: that module's own docstring refuses to own it, because what
        #: "priced" means differs per reader and this one needs a notional.
        cov = book_read.coverage_of(positions, lambda p: _notional(p) > 0)
        rows = [p for p in (positions or []) if _notional(p) > 0]
        if not rows:
            if cov.nothing_read:
                return _unpriceable(cov)
            return base(cov)
        #: `if gross <= 0: return base` used to sit here. `_notional` returns
        #: `abs()` on both arms and every row is already filtered `> 0`, so no
        #: input can make the sum non-positive — a line that cannot fire is a
        #: claim that there is a check. The property is driven in the suite
        #: instead, so the day either half changes a test fails rather than an
        #: unreachable branch quietly becoming reachable.
        gross = sum(_notional(p) for p in rows)

        ranked: list[dict[str, Any]] = []
        min_move_pct: Optional[float] = None
        for p in rows:
            notional = _notional(p)
            frac = liquidation_move_frac(p.get("leverage"))
            liq_move_pct = round(frac * 100, 2) if frac is not None else None
            if liq_move_pct is not None:
                min_move_pct = liq_move_pct if min_move_pct is None else min(min_move_pct, liq_move_pct)
            ranked.append({
                "symbol": str(p.get("symbol", "?")),
                "direction": _direction(p),
                "group": str(p.get("group") or "*"),
                "notional_usd": round(notional, 2),
                "leverage": _num(p.get("leverage")) or 1.0,
                "liq_move_pct": liq_move_pct,
                "margin": _margin(p),
                "_urgency": _urgency(notional, frac),
                "_share": notional / gross * 100,
            })
        # Most dangerous first; stable tie-break by notional then symbol.
        ranked.sort(key=lambda r: (-r["_urgency"], -r["notional_usd"], r["symbol"]))

        steps = []
        margin_cum = 0.0
        for i, r in enumerate(ranked, start=1):
            margin_cum += r["margin"]
            steps.append({
                "order": i,
                "symbol": r["symbol"],
                "direction": r["direction"],
                "group": r["group"],
                "notional_usd": r["notional_usd"],
                "leverage": r["leverage"],
                "liq_move_pct": r["liq_move_pct"],
                "urgency": round(r["_urgency"], 2),
                "margin_freed_cum_usd": round(margin_cum, 2),
                "reason": _reason(i, r["group"], r["liq_move_pct"], round(r["_share"], 1)),
            })

        return {
            "version": ESCAPE_VERSION,
            "ok": True,
            "position_count": len(rows),
            "gross_notional_usd": round(gross, 2),
            "total_margin_usd": round(sum(r["margin"] for r in ranked), 2),
            "risk": _book_risk(min_move_pct),
            "steps": steps,
            "book_coverage": cov.as_dict(),
            "coverage_note": book_read.coverage_note(cov, figure="the plan figures"),
            "recommended": ("flatten via reduce-only market closes, most-fragile "
                            "first (engine.flatten_all_positions / "
                            "executor.close_all_positions)"),
        }
    except Exception:
        return failed


def escape_payload(positions: list[dict]) -> dict:
    """A compact, JSON-serialisable record for the Flight Recorder / telemetry:
    the escape urgency, the book totals, and the ordered exit symbols with their
    reasons — enough to prove what the plan was, without the full per-step detail."""
    p = plan(positions)
    return {
        "version": ESCAPE_VERSION,
        "ok": p["ok"],
        "risk": p["risk"],
        "position_count": p["position_count"],
        "gross_notional_usd": p["gross_notional_usd"],
        "total_margin_usd": p["total_margin_usd"],
        # THE SEALED RECORD MUST NOT LOOK COMPLETE WHEN IT IS NOT. This goes on
        # the tamper-evident chain as proof of "what the plan was", and `[:12]`
        # dropped steps 13+ without a trace — a permanent record of a partial
        # plan, indistinguishable from a record of a whole one.
        "order_total": len(p["steps"]),
        "order_truncated": max(0, len(p["steps"]) - 12),
        # AND THE SAME ARGUMENT ONE ROW OVER. `order_truncated` exists because
        # a sealed record of a partial plan must not read as a record of a
        # whole one — and the rows this planner could not price were dropped
        # with no trace at all, which is the same omission upstream of the
        # step list rather than inside it.
        "book_coverage": p.get("book_coverage"),
        "order": [
            {"order": s["order"], "symbol": s["symbol"], "direction": s["direction"],
             "liq_move_pct": s["liq_move_pct"], "reason": s["reason"]}
            for s in p["steps"][:12]
        ],
        "recommended": p["recommended"],
    }
