"""Per-user risk sentry — proactive watch over a user's live posture.

Where the Authority Envelope AUTHORIZES a new order at confirm time, the sentry
WATCHES the standing book and warns when the current state drifts toward
trouble: a position that no longer fits the envelope, an oversized position,
concentration in one asset, stacked correlated risk, or a 24h spend nearing the
cap. It makes the envelope *proactive*, not just a gate.

Pure, deterministic, DETECTION-ONLY — it emits ranked alerts, never places,
closes, or resizes anything (that stays the user's confirm-gated action). No
network, no LLM: same posture → same alerts, and every number is verifiable.
"""

from __future__ import annotations

from typing import Any, Optional

from bot.guardian import book_read

# Alert severities, low→high, so the worst can be picked deterministically.
_ORDER = {"info": 0, "caution": 1, "warn": 2}

# Correlated majors — a stacked same-side book across these is one bet, not two.
_CORRELATED = frozenset({"BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "MATIC", "POL"})


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _base(sym: Any) -> str:
    return str(sym or "").split("/")[0].split(":")[0].upper().strip()


def _side(v: Any) -> str:
    s = str(v or "").lower()
    if s in ("long", "buy"):
        return "long"
    if s in ("short", "sell"):
        return "short"
    return ""


def _priced(p: Any) -> bool:
    """Can this row be assessed? A symbol to name it and a notional to size it.

    LOCAL, and deliberately: `book_read` owns the COUNT and its own docstring
    refuses to own what "priced" means, because each reader needs something
    different — the twin needs an entry and a quantity to shock, the escape
    plan a notional to rank, and this sentry a base symbol to group and a
    notional to weigh. Folding them would be a second answer about one word.
    """
    n = _f(p.get("notional_usd"))
    return bool(_base(p.get("symbol"))) and n is not None and n > 0


def _partial_sentence(cov: "book_read.BookCoverage") -> str:
    """What a user is told when part of their book could not be assessed.

    Two facts, two sentences. None of it readable means NOTHING below describes
    this book, so the checks that did run are named as not having run on it;
    part of it readable means the flags below are real but cover only that
    part, and the rows they missed are the ones that could have changed them.
    Written here rather than through `book_read.coverage_note`, because that
    template is phrased for a figure a card prints and this is a sentence in a
    list of alerts — the COUNT and the two-state split are what is shared.
    """
    names = ", ".join(cov.unpriced[:4])
    if len(cov.unpriced) > 4:
        names += f" and {len(cov.unpriced) - 4} more"
    if cov.nothing_read:
        return (f"None of your {cov.counted} open position(s) could be priced "
                f"({names}), so concentration, crowding and leverage were not "
                "assessed on your book. This is not an all-clear.")
    return (f"{cov.scored} of your {cov.counted} open position(s) could be "
            f"priced; {names} could not, so the flags here describe part of your "
            "book and the rows left out are ones that could change them.")


def assess(positions: Optional[list[dict]], *,
           envelope: Optional[dict] = None,
           equity_usd: Optional[float] = None,
           spent_today_usd: Optional[float] = 0.0,
           daily_cap: Optional[float] = None,
           concentration_pct: float = 40.0) -> dict:
    """Assess a user's standing posture. Returns
    ``{alerts:[{level,category,symbol?,msg}], count, worst_level, gross_usd,
    book_read}``.

    ``positions``: ``[{symbol, side, notional_usd}]`` (open positions), or
    **None when the book could not be read**. Those are different facts and
    they were the same value: the caller answered a failed portfolio read with
    ``[]``, nothing flagged on an empty list, and the endpoint returned
    "🟢 nothing flagged in your current posture" — an all-clear assembled from
    a crash, on a risk surface. Byte-for-byte the `escape_agent.plan()` defect
    CLAUDE.md records as fixed, and `integrity_veto.assess({})` answering
    ``clear`` over ``checked == 0``. This is the sibling that was missed.

    ``spent_today_usd`` is Optional for the same reason: the caller folded a
    failed ledger read to ``0.0``, which cannot trip ``spent >= 0.8 * cap``,
    so the daily-cap warning was structurally unreachable on any ledger fault.

    ``envelope``: the user's compiled Authority Envelope (or None).
    """
    if positions is None:
        # Not an empty book. NOTHING was assessed, so nothing may be reported
        # as clear — including the checks that do not need positions, because
        # a partial assessment printed as a whole one is the same lie.
        return {
            "alerts": [{"level": "unknown", "category": "book_unreadable",
                        "msg": "Your open positions could not be read, so "
                               "your posture was not assessed. This is a "
                               "failed read, not an empty book."}],
            "count": 0, "worst_level": "unknown", "gross_usd": None,
            "book_read": False, "book_coverage": None,
        }
    alerts: list[dict] = []
    norm: list[dict] = []
    for p in (positions or []):
        if not _priced(p):
            continue
        norm.append({"symbol": _base(p.get("symbol")), "side": _side(p.get("side")),
                     "notional_usd": _f(p.get("notional_usd"))})

    #: THE BRANCH ABOVE FIXED THE WHOLE-READ CASE AND THIS LOOP IS THE OTHER HALF.
    #: `positions is None` says the list never arrived; this walk used to drop
    #: every row it could not price with no trace at all, and then compute
    #: gross, concentration and book leverage over what was left. Driven, three
    #: open positions whose notional the book could not state returned
    #: `worst_level: "clear"`, `gross_usd: 0` and "nothing flagged in your
    #: current posture" — byte-identical to a FLAT book, while the same three
    #: rows readable raised a concentration warning. Today's one caller reads
    #: the paper book, where the reachable row is a dust position whose
    #: quantity rounds to 0.0; the rest is this function's contract for the
    #: day a live book (adopted positions and all) is wired to it.
    #:
    #: `book_read` stays True — the list WAS read, and the dashboard's own
    #: `/api/positions` reader keys a different payload's `book_read` on
    #: `!== true`, so repurposing the word would be a wire change for nobody's
    #: benefit. The coverage rides beside it, the shape `escape_agent` uses.
    cov = book_read.coverage_of(positions, _priced)
    if not cov.complete:
        alerts.append({"level": "unknown", "category": "book_partial",
                       "msg": _partial_sentence(cov)})

    gross = round(sum(p["notional_usd"] for p in norm), 2)

    # ── Envelope drift: does the STANDING book still fit the authority? ──
    if envelope:
        allow = set(envelope.get("symbol_allowlist") or [])
        block = set(envelope.get("symbol_blocklist") or [])
        per_trade = _f(envelope.get("max_notional_per_trade_usd"))
        for p in norm:
            if allow and p["symbol"] not in allow:
                alerts.append({"level": "warn", "category": "outside_authority",
                               "symbol": p["symbol"],
                               "msg": f"{p['symbol']} is held but no longer in your "
                                      "authorized symbol set — your envelope tightened "
                                      "under an open position."})
            if p["symbol"] in block:
                alerts.append({"level": "warn", "category": "blocklisted_held",
                               "symbol": p["symbol"],
                               "msg": f"{p['symbol']} is on your envelope's blocklist "
                                      "but is currently held."})
            if per_trade is not None and p["notional_usd"] > per_trade + 1e-9:
                alerts.append({"level": "warn", "category": "over_cap",
                               "symbol": p["symbol"],
                               "msg": f"{p['symbol']} position ${p['notional_usd']:,.0f} "
                                      f"exceeds your ${per_trade:,.0f} per-trade cap."})

    # ── 24h spend nearing the daily cap ──
    cap = _f(daily_cap) if daily_cap is not None else _f((envelope or {}).get("max_notional_daily_usd"))
    spent = _f(spent_today_usd)
    if cap is not None and cap > 0 and spent is None:
        # An unread ledger cannot clear a cap. Say so rather than pass.
        alerts.append({"level": "unknown", "category": "daily_spend",
                       "msg": f"Today's spend could not be read, so your "
                              f"${cap:,.0f} daily cap was not checked."})
    elif cap is not None and cap > 0 and spent is not None and spent >= 0.8 * cap:
        pct = spent / cap * 100
        lvl = "warn" if spent >= cap else "caution"
        alerts.append({"level": lvl, "category": "daily_spend",
                       "msg": f"You've used ${spent:,.0f} of your ${cap:,.0f} daily "
                              f"notional cap ({pct:.0f}%)."})

    # ── Concentration: one asset dominating gross exposure ──
    if gross > 0:
        by_sym: dict[str, float] = {}
        for p in norm:
            by_sym[p["symbol"]] = by_sym.get(p["symbol"], 0.0) + p["notional_usd"]
        top_sym, top_usd = max(by_sym.items(), key=lambda kv: kv[1])
        share = top_usd / gross * 100
        if share > concentration_pct and len(by_sym) > 1:
            alerts.append({"level": "caution", "category": "concentration",
                           "symbol": top_sym,
                           "msg": f"{top_sym} is {share:.0f}% of your gross exposure "
                                  f"(${top_usd:,.0f} of ${gross:,.0f}) — concentrated."})

    # ── Stacked correlated same-side risk ──
    for side in ("long", "short"):
        corr = sorted({p["symbol"] for p in norm
                       if p["side"] == side and p["symbol"] in _CORRELATED})
        if len(corr) >= 2:
            alerts.append({"level": "caution", "category": "stacked_correlated",
                           "msg": f"{len(corr)} correlated majors held {side} "
                                  f"({', '.join(corr)}) — this is closer to one bet than "
                                  "several; a market move hits them together."})

    # ── Size vs equity (whole-book leverage sanity) ──
    eq = _f(equity_usd)
    if eq and eq > 0 and gross > 3.0 * eq:
        alerts.append({"level": "warn", "category": "book_leverage",
                       "msg": f"Gross exposure ${gross:,.0f} is {gross/eq:.1f}× your "
                              f"${eq:,.0f} equity — a sharp move is amplified."})

    alerts.sort(key=lambda a: _ORDER.get(a["level"], 0), reverse=True)
    worst = alerts[0]["level"] if alerts else "clear"
    # A gross over a book where NOTHING could be priced is not a measured $0 —
    # it is no figure at all, and `0` on the wire is the shape this module's
    # own None branch exists to refuse. The arithmetic above still sees 0,
    # where both guards that read it (concentration, book leverage) skip; only
    # the published figure changes.
    return {"alerts": alerts, "count": len(alerts), "worst_level": worst,
            "gross_usd": None if cov.nothing_read else gross, "book_read": True,
            "book_coverage": cov.as_dict()}


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render (no markup)."""
    if not report:
        # A missing report is a failed assessment, not a clean posture.
        return ("⚪ Risk sentry: could not assess your posture. This is a "
                "failed read, not an all-clear.")
    if report.get("book_read") is False or not report.get("alerts"):
        if report.get("book_read") is False:
            return ("⚪ Risk sentry: " + report["alerts"][0]["msg"]
                    if report.get("alerts") else
                    "⚪ Risk sentry: your posture was not assessed.")
        return "🟢 Risk sentry: nothing flagged in your current posture."
    icon = {"warn": "🔴", "caution": "🟠", "info": "🔵", "unknown": "⚪"}
    lines = [f"Risk sentry — {report['count']} flag(s):"]
    for a in report["alerts"]:
        lines.append(f"{icon.get(a['level'], '•')} {a['msg']}")
    return "\n".join(lines)
