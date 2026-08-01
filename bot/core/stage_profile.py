"""1086s of fetch across 70 symbols is the same number either way.

From the operator's 2026-08-01 work report: a tick stall, fetch stage
dominating, ~1086s cumulative and 192s wall-clock. The line they were reading
is engine._stage_report, and it stops exactly one step short of the answer:

    fetch 1086s (62%) — 1740s of work across 192s wall-clock at 12 concurrent

A percentage says which stage owns the tick. It cannot say whether that is

    all 85 symbols at ~15s each            -> systemic
    three symbols at 300s and 82 at 2s     -> a few bad symbols

and those call for opposite fixes. The analysis-timeout tally cannot see the
case at all when the analyses COMPLETE — it only fires on timeouts.

WHY THIS IS PER-STAGE AND NOT PER-FETCH

The first version of this module profiled `fetch` only, because that was the
stage in the report. Production data from later the same day, at a widened
85-symbol universe:

    fetch ~36-41%,  mtf ~59-64%

THE BOTTLENECK MOVED. A profiler bolted to whichever stage was slow when it
was written would have been aimed at the wrong one within a day, so it keys
by stage and follows the dominant one.

WHY THE MEAN IS THE WRONG STATISTIC

`fetch` and `mtf` are both exchange I/O — mtf is several extra timeframes of
`_cached_ohlcv` — issued against ONE cached ccxt instance with
enableRateLimit=True. Calls therefore queue through a single token bucket,
and two very different situations produce a similar mean:

    calls hitting the per-call ceiling  ->  durations PILE UP at the ceiling
    rate-limit queueing                 ->  durations SPREAD smoothly

Only the SHAPE separates them, which is why this records per-symbol durations
and reports clustering rather than an average.

    A PROFILE DESCRIBES A SHAPE. IT DOES NOT NAME A CAUSE.

Same doctrine as bot/core/analysis_timeout_tally.py, and the same refusals:
no shape below MIN_SAMPLE, and never a sentence like "the venue is
throttling" or "drop these symbols". §4 — a heuristic flag is not a verdict.
The near-ceiling count in particular is a MEASUREMENT ("n of m finished at or
above X s"), deliberately not the conclusion it invites, because queueing
behind a full bucket can also park a symbol near the ceiling.
"""
from __future__ import annotations

__all__ = ["new_profile", "record", "summarize", "render",
           "MIN_SAMPLE", "CEILING_TOLERANCE", "CEILING_SHARE",
           "CONCENTRATION_SHARE"]

#: Below this many recorded fetches, no shape is claimed. Same refusal as the
#: timeout tally: a handful of samples can look clustered by chance.
MIN_SAMPLE = 8

#: A fetch within this fraction of the per-call ceiling counts as "near it".
#: 0.9 rather than 1.0 because a call that times out at 15.000s is measured a
#: little under or over depending on where the clock is read.
CEILING_TOLERANCE = 0.9

#: This share of samples near the ceiling is what "piling up" looks like.
CEILING_SHARE = 0.5

#: A near-ceiling group at least this large is a SLOW SUBSET worth naming
#: even when it is nowhere near a majority.
#:
#: Added because the first version got the operator's real case wrong. Their
#: 2026-08-01 data was ~85 symbols with the stall "almost all R* stocks" —
#: about 25 tokenized equities pinned near the per-call limit while ~60 other
#: symbols ran in a couple of seconds. That is 29% near the ceiling: under
#: CEILING_SHARE, and far too many symbols for the top-few concentration
#: test. Both checks missed, and the module reported "spread — broad load",
#: which is the reading that argues for MORE CONCURRENCY. On a stage that is
#: exchange I/O behind one rate-limited ccxt instance, more concurrency adds
#: no throughput at all, so the tool would have pointed at the one fix that
#: cannot work.
#:
#: A bimodal distribution is not a shapeless one, and it is the most
#: actionable shape there is: the slow group can simply be named.
SUBSET_MIN_SHARE = 0.1

#: The TOP FEW symbols holding this share of total time is "concentrated".
#:
#: Deliberately not a top-1 test. The timeout tally counts events, where one
#: symbol really can dominate; durations do not behave that way. The operator's
#: own 2026-08-01 data is the counter-example — the stall was "almost all R*
#: stocks (RMSFT, RNVDA, RTSLA, RAAPL)", a HANDFUL of symbols. Split three
#: ways, no single symbol reaches half, so a top-1 rule reports "spread" for
#: the textbook few-bad-symbols case and points at capacity instead.
CONCENTRATION_SHARE = 0.5

#: How many symbols count as "the top few": 5% of those seen, at least 1 and
#: at most 5. Proportional so a wide universe is not called concentrated
#: because five of eighty-five symbols are slow, and a narrow one is not
#: forced to have a single culprit.
CONCENTRATION_HEAD_FRACTION = 0.05
CONCENTRATION_HEAD_MAX = 5


def new_profile() -> dict:
    """A fresh accumulator. Plain dict so it survives being logged as JSON."""
    return {"count": 0, "total_s": 0.0, "max_s": 0.0, "by_symbol": {}}


def record(profile: dict, symbol: str, seconds: float) -> None:
    """Fold one symbol's fetch duration in. Never raises — instrumentation.

    A negative or non-finite duration is dropped rather than accumulated: a
    clock that went backwards would otherwise drag the mean below every
    sample and make the profile disagree with its own maximum.
    """
    try:
        if not isinstance(profile, dict):
            return
        s = float(seconds)
        if s != s or s in (float("inf"), float("-inf")) or s < 0:
            return
        sym = str(symbol or "?")
        profile["count"] = int(profile.get("count", 0)) + 1
        profile["total_s"] = float(profile.get("total_s", 0.0)) + s
        if s > float(profile.get("max_s", 0.0)):
            profile["max_s"] = s
        by_sym = profile.setdefault("by_symbol", {})
        cur = by_sym.get(sym) or {"n": 0, "total_s": 0.0, "max_s": 0.0}
        cur["n"] = int(cur.get("n", 0)) + 1
        cur["total_s"] = float(cur.get("total_s", 0.0)) + s
        if s > float(cur.get("max_s", 0.0)):
            cur["max_s"] = s
        by_sym[sym] = cur
        # Kept for the clustering test. Bounded: a long-running engine must
        # not accumulate an unbounded list, and the shape is established long
        # before the cap.
        samples = profile.setdefault("samples", [])
        if len(samples) < 2000:
            samples.append(s)
    except Exception:
        pass


def summarize(profile: dict, *, ceiling_s: float = 0.0,
              min_sample: int = MIN_SAMPLE) -> dict:
    """Describe the shape of the durations. Returns {} when there is nothing.

    ``shape`` is one of:

      "insufficient" — fewer than ``min_sample`` samples; no claim is made
      "at_ceiling"   — most samples sit at/above CEILING_TOLERANCE × ceiling
      "concentrated" — one symbol holds most of the TOTAL time
      "spread"       — neither; time is distributed across symbols

    Checked in that order, most specific first: a run that is both clustered
    at the ceiling and dominated by one symbol is more usefully described as
    the former, because that is the reading that identifies WHICH measurement
    to look at next.

    ``ceiling_s`` <= 0 disables the ceiling test — with no known per-call
    limit there is nothing to be near, and inventing one would manufacture
    the very finding this is supposed to measure.
    """
    try:
        count = int((profile or {}).get("count", 0))
    except Exception:
        return {}
    if count <= 0:
        return {}
    total = float((profile or {}).get("total_s", 0.0))
    by_sym = dict((profile or {}).get("by_symbol", {}) or {})
    samples = list((profile or {}).get("samples", []) or [])

    ranked = sorted(by_sym.items(),
                    key=lambda kv: (-float(kv[1].get("total_s", 0.0)), kv[0]))
    out = {
        "count": count,
        "total_s": round(total, 1),
        "mean_s": round(total / count, 2) if count else 0.0,
        "max_s": round(float((profile or {}).get("max_s", 0.0)), 2),
        "distinct_symbols": len(by_sym),
        "top_symbols": [(s, round(float(v.get("total_s", 0.0)), 1),
                         int(v.get("n", 0))) for s, v in ranked[:5]],
    }

    # Coerce before comparing: a caller passing "15" (or None) must get the
    # ceiling test SKIPPED, not a TypeError out of a status helper.
    try:
        _ceil = float(ceiling_s)
    except (TypeError, ValueError):
        _ceil = 0.0
    if _ceil != _ceil or _ceil in (float("inf"), float("-inf")):
        _ceil = 0.0

    near = 0
    if _ceil > 0 and samples:
        bar = _ceil * CEILING_TOLERANCE
        near = sum(1 for s in samples if s >= bar)
        out["near_ceiling"] = near
        out["near_ceiling_of"] = len(samples)
        out["ceiling_s"] = round(_ceil, 1)

    if count < max(1, int(min_sample)):
        out["shape"] = "insufficient"
        return out

    if samples and out.get("near_ceiling_of"):
        if near >= len(samples) * CEILING_SHARE:
            out["shape"] = "at_ceiling"
            return out
        if near >= len(samples) * SUBSET_MIN_SHARE:
            # A distinct slow group. Name it: which symbols is the whole
            # answer, and it is cheap to supply.
            bar = _ceil * CEILING_TOLERANCE
            out["subset"] = [sym for sym, v in ranked
                             if float(v.get("max_s", 0.0)) >= bar][:12]
            out["shape"] = "slow_subset"
            return out

    head_n = max(1, min(CONCENTRATION_HEAD_MAX,
                        int(len(ranked) * CONCENTRATION_HEAD_FRACTION)))
    head_total = sum(float(v.get("total_s", 0.0)) for _, v in ranked[:head_n])
    out["head_n"] = head_n
    out["shape"] = ("concentrated"
                    if total > 0 and head_total >= total * CONCENTRATION_SHARE
                    else "spread")
    return out


def render(profile: dict, *, ceiling_s: float = 0.0,
           min_sample: int = MIN_SAMPLE) -> str:
    """Operator-facing lines, or "" when there is nothing to report.

    Reports the numbers and names the shape. Never names a cause: this cannot
    see one, and the near-ceiling count is exactly the kind of number that
    reads as a diagnosis if it is not explicitly framed as a measurement.
    """
    s = summarize(profile, ceiling_s=ceiling_s, min_sample=min_sample)
    if not s:
        return ""
    names = ", ".join(f"{sym} {tot}s/{n}" for sym, tot, n in s["top_symbols"][:3])
    head = (f"fetch: {s['count']} sample(s) across {s['distinct_symbols']} "
            f"symbol(s), {s['total_s']}s total, mean {s['mean_s']}s, "
            f"max {s['max_s']}s")
    if s["shape"] == "insufficient":
        return (f"{head}.\nToo few to characterise — no pattern is claimed "
                f"from this many.\nSlowest: {names}")
    if s["shape"] == "at_ceiling":
        tail = (f"{s['near_ceiling']}/{s['near_ceiling_of']} finished at or "
                f"above {round(s['ceiling_s'] * CEILING_TOLERANCE, 1)}s, "
                f"against a {s['ceiling_s']}s per-call limit. Durations "
                f"piling up just under a limit is what calls reaching that "
                f"limit look like — though queueing behind a full rate-limit "
                f"bucket can park a fetch there too, so this narrows where to "
                f"measure next rather than settling it.")
    elif s["shape"] == "slow_subset":
        _named = ", ".join(s.get("subset", [])[:8])
        tail = (f"{s['near_ceiling']} of {s['near_ceiling_of']} finished at "
                f"or above {round(s['ceiling_s'] * CEILING_TOLERANCE, 1)}s "
                f"against a {s['ceiling_s']}s per-call limit while the rest "
                f"did not — a distinct slow GROUP rather than broad load, "
                f"which is the shape that can be acted on by name: {_named}. "
                f"What the group has in common is the question a profile "
                f"cannot answer for you.")
    elif s["shape"] == "concentrated":
        tail = (f"The slowest {s['head_n']} of {s['distinct_symbols']} "
                f"symbol(s) hold most of the total, which is what a "
                f"per-symbol problem looks like — though a profile cannot "
                f"tell that apart from scheduling on its own.")
    else:
        tail = ("No small group of symbols dominates and durations are not "
                "piled at the "
                "limit, which is what broad load looks like rather than a few "
                "bad symbols — though a profile cannot tell that apart from a "
                "uniformly slow feed on its own.")
    return f"{head}.\n{tail}\nSlowest: {names}"
