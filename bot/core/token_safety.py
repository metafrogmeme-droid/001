"""Token Safety Scanner — detection-only rug/honeypot shape detection.

    Detects rug/honeypot/manipulation shapes so the agent can stand down.
    It never proposes a buy, and it never treats "no data" as "safe".

Pure, deterministic scorer: a token's on-chain + market safety features → a verdict
(``safe`` / ``caution`` / ``danger``) + a per-check report. Two roles:

* user-facing (research dossiers / meme radar): shows WHY a token is dangerous;
* feeder: ``to_veto_features`` maps readings onto the Guardian Integrity Veto's
  keys, so this scanner is what unblocks the veto's engine wiring.

Discipline (matches veto-only + honest-UNVERIFIED):
* Detection, never generation — outputs are only stand-down signals; there is no
  buy / positive / up-vote output.
* No data ≠ safe — a missing input is ``unknown``, never a pass. ``safe`` requires
  positive evidence; a mostly-unknown token is at best ``caution``.
* A single disqualifying reading (hard flag) forces ``danger``.

No engine/network/clock import — the caller fetches the features (from
``bot.core.onchain`` etc.) and passes them in.
"""

from __future__ import annotations

from typing import Any, Optional

SAFE = "safe"
CAUTION = "caution"
DANGER = "danger"

OK = "ok"
FLAG = "flag"
HARD = "hard"
UNKNOWN = "unknown"


def _num(x: Any) -> Optional[float]:
    # bools are handled by the boolean checks, never the numeric ones
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _b(x: Any) -> Optional[bool]:
    return x if isinstance(x, bool) else None


# Weighted soft-flag score → verdict (a hard flag overrides to DANGER outright).
_CAUTION_SCORE = 1.5
_DANGER_SCORE = 3.0
# If most checks are unknown we cannot certify safety → at best caution.
_MIN_EVIDENCE_FRAC = 0.5


def assess_token(features: Optional[dict]) -> dict:
    """Score a token's safety features. Returns::

        {verdict, score, checks:[{name,status,detail}], flags:[str],
         evidence, unknowns, veto_features}

    ``verdict`` ∈ {safe, caution, danger} — never a positive/buy signal.
    """
    f = features or {}
    checks: list[dict] = []
    score = 0.0
    hard = False

    def boolean(name: str, danger_when: bool, weight: float, danger_hard: bool,
                on_bad: str) -> None:
        nonlocal score, hard
        v = _b(f.get(name))
        if v is None:
            checks.append({"name": name, "status": UNKNOWN, "detail": "not provided"})
            return
        bad = (v is danger_when)
        if not bad:
            checks.append({"name": name, "status": OK, "detail": "ok"})
            return
        if danger_hard:
            hard = True
            checks.append({"name": name, "status": HARD, "detail": on_bad})
        else:
            score += weight
            checks.append({"name": name, "status": FLAG, "detail": on_bad})

    def numeric(name: str, direction: str, soft: float, hard_th: Optional[float],
                weight: float, on_soft: str, on_hard: str) -> None:
        nonlocal score, hard
        v = _num(f.get(name))
        if v is None:
            checks.append({"name": name, "status": UNKNOWN, "detail": "not provided"})
            return
        high = direction == "high"
        hard_hit = (hard_th is not None) and ((v >= hard_th) if high else (v <= hard_th))
        soft_hit = (v >= soft) if high else (v <= soft)
        if hard_hit:
            hard = True
            checks.append({"name": name, "status": HARD, "detail": on_hard})
        elif soft_hit:
            score += weight
            checks.append({"name": name, "status": FLAG, "detail": on_soft})
        else:
            checks.append({"name": name, "status": OK, "detail": "ok"})

    # -- hard-capable checks --
    boolean("honeypot_cannot_sell", True, 0, True, "HONEYPOT: token cannot be sold")
    boolean("mint_authority_active", True, 0, True, "live mint authority — supply can be inflated")
    boolean("freeze_authority_active", True, 1.0, True, "live freeze authority — balances can be frozen")
    numeric("sell_tax_pct", "high", 10.0, 30.0, 1.0,
            "elevated sell tax", "sell tax ≥30% — exit trap")
    numeric("top_holder_pct", "high", 0.3, 0.5, 2.0,
            "concentrated top holder", "one wallet holds ≥50% — can dump everything")

    # -- soft-only checks --
    boolean("ownership_renounced", False, 1.0, False, "ownership NOT renounced")
    boolean("lp_locked", False, 1.5, False, "liquidity NOT locked")
    numeric("buy_tax_pct", "high", 10.0, None, 0.5, "elevated buy tax", "")
    numeric("liquidity_usd", "low", 10_000.0, None, 1.0, "thin liquidity (<$10k)", "")
    numeric("holder_count", "low", 50.0, None, 1.0, "few holders (<50)", "")
    numeric("listing_age_hours", "low", 24.0, None, 0.5, "brand-new listing (<24h)", "")

    evidence = sum(1 for c in checks if c["status"] in (OK, FLAG, HARD))
    unknowns = sum(1 for c in checks if c["status"] == UNKNOWN)
    total = len(checks)

    # Verdict.
    if hard:
        verdict = DANGER
    elif score >= _DANGER_SCORE:
        verdict = DANGER
    elif score >= _CAUTION_SCORE:
        verdict = CAUTION
    elif total and (evidence / total) < _MIN_EVIDENCE_FRAC:
        # Not enough positive evidence to certify safety → cannot say safe.
        verdict = CAUTION
    else:
        verdict = SAFE

    flags = [c["detail"] for c in checks if c["status"] in (FLAG, HARD)]
    return {
        "verdict": verdict,
        "score": round(score, 3),
        "checks": checks,
        "flags": flags,
        "evidence": evidence,
        "unknowns": unknowns,
        # COVERAGE — how much of the token this verdict is actually based on.
        #
        # The verdict alone was already honest: 0-of-11 readable returns
        # `caution`, never `safe`, and _MIN_EVIDENCE_FRAC is what enforces it.
        # But `caution` is then the SAME WORD for "we read ten checks and one
        # flagged" and "we could not read anything at all", which are opposite
        # decisions for whoever is holding the wallet. human_readable() prints
        # the unknown count; every programmatic consumer — dossiers, the MCP
        # tools, the veto — reads `verdict` and loses it.
        #
        # That matters most for exactly the tokens this scanner is aimed at. A
        # two-hour-old contract has no holder history, no listing age and often
        # no liquidity reading, so `unknown` is its NORMAL state rather than a
        # failure — and a bare "caution" invites a reader to hear "we checked,
        # it's borderline". Shipping the basis alongside the verdict is the
        # difference between a measurement and an impression.
        "coverage": coverage(checks),
        "veto_features": to_veto_features(f),
    }


#: Coverage bands. Names, not bare ratios, because a caller printing "0.27"
#: has to invent a word for it and each caller will invent a different one.
#:
#: "none" is NOT a band — it is `readable == 0` exactly, handled before these.
#: The first draft made it the 0.0 edge of the ladder, so 1-of-11 readable
#: printed "none basis": one reading rendered as no readings, which is the
#: precise conflation this whole field exists to prevent, in the code adding it.
_BASIS_BANDS = ((0.0, "thin"), (0.25, "thin"), (0.5, "partial"), (0.75, "broad"))


def coverage(checks: Optional[list]) -> dict:
    """How much of the token a report is based on::

        {readable, total, ratio, basis}

    ``basis`` ∈ {none, thin, partial, broad, full}. ``ratio`` is None when there
    are no checks at all — a zero would read as "nothing was readable" when the
    truth is that nothing was asked, and those are different failures.
    """
    ck = checks or []
    total = len(ck)
    readable = sum(1 for c in ck
                   if isinstance(c, dict) and c.get("status") != UNKNOWN)
    if not total:
        return {"readable": 0, "total": 0, "ratio": None, "basis": "none"}
    if not readable:
        return {"readable": 0, "total": total, "ratio": 0.0, "basis": "none"}
    ratio = readable / total
    basis = "full" if readable == total else next(
        name for edge, name in reversed(_BASIS_BANDS) if ratio >= edge)
    return {"readable": readable, "total": total,
            "ratio": round(ratio, 3), "basis": basis}


def to_veto_features(features: Optional[dict]) -> dict:
    """Map token features onto the Guardian Integrity Veto's feature keys, so the
    scanner can feed the veto. Only maps keys that are present (missing → skipped,
    so the veto's own fail-open-per-feature rule applies)."""
    f = features or {}
    out: dict[str, Any] = {}
    if f.get("top_holder_pct") is not None:
        out["holder_concentration"] = f["top_holder_pct"]
    if f.get("listing_age_hours") is not None:
        out["listing_age_hours"] = f["listing_age_hours"]
    if f.get("wash_volume_ratio") is not None:
        out["wash_volume_ratio"] = f["wash_volume_ratio"]
    if f.get("price_liquidity_divergence") is not None:
        out["price_liquidity_divergence"] = f["price_liquidity_divergence"]
    return out


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render of a safety report (no markup)."""
    if not report or not isinstance(report, dict):
        return "No token-safety report."
    v = report.get("verdict", CAUTION)
    icon = {SAFE: "✓", CAUTION: "⚠", DANGER: "⛔"}.get(v, "·")
    # The basis travels WITH the verdict word, on the same line, rather than as
    # a footnote below the flags. A reader who stops at the headline — which is
    # most readers — must not be able to take away "caution" without also
    # taking away how much was actually read.
    cov = report.get("coverage") or coverage(report.get("checks"))
    lines = [f"{icon} TOKEN SAFETY: {v.upper()} "
             f"[{cov.get('basis')} basis — {cov.get('readable')}/{cov.get('total')} "
             f"checks readable] (score {report.get('score')})"]
    for fl in report.get("flags", []):
        lines.append(f"   – {fl}")
    if v == SAFE and not report.get("flags"):
        lines.append("   renounced, LP-locked, distributed, low tax, deep liquidity")
    if report.get("unknowns", 0) and v != DANGER:
        lines.append(f"   note: {report['unknowns']} check(s) had no data — safety not fully verified")
    return "\n".join(lines)
