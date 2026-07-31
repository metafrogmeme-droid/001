"""Is it three bad symbols, or is it load? A single-shot record cannot say.

`_last_analysis_timeout` holds ONE batch and is overwritten by the next, so
the operator surface answers "what timed out most recently" and the question
actually being asked is "what keeps timing out". On 2026-07-30 the only way
to tell those apart was to read /status by hand across several ticks and
notice that FET/RENDER/LINK had become TRUMP/ENA/RENDER — a rotation, which
means capacity, not three bad symbols. That is a real finding reached by an
unreliable method, and if the operator had happened to look twice at the
same tick they would have concluded the opposite.

This module keeps the distribution instead of the last sample, and it stops
one step short of the conclusion.

  A TALLY DESCRIBES A SHAPE. IT DOES NOT NAME A CAUSE.

Concentration and spread are the two shapes worth telling apart, and the
counts show which one is present. What they do NOT show is why — a symbol
can dominate the tally because its venue feed is slow, because it is always
scheduled last, or because it happens to be alphabetically unlucky in a
bounded gather. §4: heuristic flags are never verdicts. So the summary
reports the numbers and names the shape; it never says "the exchange is
throttling" or "drop these symbols".

And a shape is not claimable from two events. Below `MIN_SAMPLE` the summary
says so rather than calling a rotation from a coin flip — the same refusal
the coverage note, the position mark, and the edge metrics now make.
"""
from __future__ import annotations

#: Below this many recorded timeouts, no shape is claimed. Three events can
#: look concentrated or spread purely by chance, and an operator who acts on
#: that reads a coin flip as a diagnosis.
MIN_SAMPLE = 5

#: A symbol holding at least this share of all timeouts is "concentrated".
#: Deliberately high: the interesting claim is "this one symbol dominates",
#: and a low bar would call every mild imbalance a culprit.
CONCENTRATION_SHARE = 0.5


def new_tally() -> dict:
    """A fresh accumulator. Plain dict so it survives being logged as JSON."""
    return {"total": 0, "batches": 0, "by_symbol": {}, "by_stage": {}}


def record(tally: dict, events: list) -> None:
    """Fold one batch's timeouts in. Never raises — this is instrumentation.

    ``events`` is a list of (symbol, stage) pairs; stage may be "" when the
    symbol was never marked, which is recorded as unknown rather than
    guessed at.
    """
    try:
        if not isinstance(tally, dict) or not events:
            return
        tally["batches"] = int(tally.get("batches", 0)) + 1
        by_sym = tally.setdefault("by_symbol", {})
        by_stg = tally.setdefault("by_stage", {})
        for ev in events:
            try:
                sym, stage = ev
            except (TypeError, ValueError):
                sym, stage = ev, ""
            sym = str(sym or "?")
            stage = str(stage or "") or "unmarked"
            tally["total"] = int(tally.get("total", 0)) + 1
            by_sym[sym] = by_sym.get(sym, 0) + 1
            by_stg[stage] = by_stg.get(stage, 0) + 1
    except Exception:
        pass


def summarize(tally: dict, *, min_sample: int = MIN_SAMPLE) -> dict:
    """Describe the shape of the tally. Returns {} when there is nothing.

    ``shape`` is one of:

      "insufficient" — fewer than ``min_sample`` events; no claim is made
      "concentrated" — one symbol holds >= CONCENTRATION_SHARE of them
      "spread"       — no symbol dominates

    ``shape`` is a description of the counts, not a diagnosis of the cause.
    Callers must render it that way.
    """
    try:
        total = int((tally or {}).get("total", 0))
    except Exception:
        return {}
    if total <= 0:
        return {}
    by_sym = dict((tally or {}).get("by_symbol", {}) or {})
    by_stg = dict((tally or {}).get("by_stage", {}) or {})
    top = sorted(by_sym.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    top_stage = sorted(by_stg.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    out = {
        "total": total,
        "batches": int((tally or {}).get("batches", 0)),
        "distinct_symbols": len(by_sym),
        "top_symbols": top,
        "top_stages": top_stage,
    }
    if total < max(1, int(min_sample)):
        out["shape"] = "insufficient"
        return out
    lead = top[0][1] if top else 0
    out["shape"] = ("concentrated" if lead >= total * CONCENTRATION_SHARE
                    else "spread")
    return out


def render(tally: dict, *, min_sample: int = MIN_SAMPLE) -> str:
    """One operator-facing line, or "" when there is nothing to report.

    Reports counts and names the shape. Never names a cause — a tally cannot
    see one, and a sentence that supplies one anyway is the verdict-from-a-
    heuristic this codebase keeps deleting.
    """
    s = summarize(tally, min_sample=min_sample)
    if not s:
        return ""
    names = ", ".join(f"{sym} ×{n}" for sym, n in s["top_symbols"][:3])
    stages = ", ".join(f"{st} ×{n}" for st, n in s["top_stages"][:2])
    head = (f"{s['total']} analysis timeout(s) across {s['distinct_symbols']} "
            f"symbol(s) in {s['batches']} batch(es)")
    if s["shape"] == "insufficient":
        return (f"{head}. Too few to characterise — no pattern is claimed "
                f"from this many.\nMost seen: {names}"
                + (f"\nStages: {stages}" if stages else ""))
    if s["shape"] == "concentrated":
        tail = ("One symbol holds most of them, which is what a per-symbol "
                "problem looks like — though a tally cannot tell that apart "
                "from scheduling on its own.")
    else:
        tail = ("No symbol dominates, which is what capacity pressure looks "
                "like rather than a few bad symbols — though a tally cannot "
                "tell that apart from a rotating feed on its own.")
    return f"{head}. {tail}\nMost seen: {names}" + (
        f"\nStages: {stages}" if stages else "")
