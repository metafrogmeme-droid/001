"""Does the analyzer's DIRECTION predict price? Measured on a frozen snapshot.

The benchmark answers "did the whole system make money", which folds together
the signal, the entry model, the exits, the sizing and the portfolio caps. It
cannot say which of those is where the edge is, or is not. This asks the first
question alone, before any entry or exit exists:

  for every idea the analyzer emits during the canonical honest walk-forward,
  how far did price move in the idea's direction over the next h bars, in units
  of ATR(14) at the signal bar -- and how does that compare with what the same
  direction earned UNCONDITIONALLY over the same window?

That difference ("excess") is the signal's directional edge. A market that
fell all year pays every SHORT; subtracting the direction's unconditional
drift is what keeps that from reading as skill.

HONEST INTERVALS, because the naive one is too narrow. Consecutive ideas on
one symbol share their forward windows, so they are not independent samples:
  * de-overlapped -- per (dataset, symbol), an idea is kept only if it is at
    least h bars after the last kept one;
  * cluster bootstrap -- (dataset, symbol, ISO week) clusters resampled with
    replacement, fixed seed, percentile 95% interval.

Snapshots overlap (the v1 majors/alts are time subsets of the v2 ones, and the
corr-dense universe mixes both), so POOLING files that share symbols and
months double-counts. `report` pools whatever it is given; choose disjoint
files for a pooled row.

Usage:
    python scripts/signal_edge.py collect --dataset benchmark/majors_1h --out rec.json
    python scripts/signal_edge.py report rec_majors_v2.json rec_alts_v2.json
    python scripts/signal_edge.py continuation --from 12 --to 48 \
        --signal momentum_confluence rec_majors_v2.json rec_alts_v2.json

`continuation` asks what a hold limit costs: from bar `--from` to bar `--to`,
how far did price keep moving the idea's way, net of the direction's drift over
that same segment -- over every idea, and over the ideas still in the idea's
favour at `--from`, which are the ones a trade would still be carrying.

`voters` takes the direction apart. The backtest's direction is the sign of the
weighted confluence vote, so a direction with no edge is either voters with no
edge or voters whose edges cancel. At EVERY analyzer call -- not only the ones
that became ideas -- `collect` records each voter's vote, and `voters` measures
each voter as if it were the whole signal: the excess move in the direction it
voted. A voter that abstained, or carried no weight, cast no vote.

    python scripts/signal_edge.py voters rec_majors_v2.json rec_alts_v2.json
    python scripts/signal_edge.py collect --dataset benchmark/majors_1h_v3 \
        --since 2026-07-06T09:00:00+00:00 --out fresh_majors.json

`collect` runs the same in-process walk-forward as
`python -m bot.backtest.runner --dataset <d> --honest --walk-forward 6`, with
two read-only taps (the analyzer's `analyze` and the risk engine's
`evaluate`). Set RUNECLAW_STATE_DIR to a scratch directory, as for any
benchmark run.
"""
from __future__ import annotations

import argparse
import collections
import contextvars
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HORIZONS = (1, 4, 12, 24, 48)
ATR_N = 14
BOOT = 2000
SEED = 7


# ── pure pieces (tests/test_signal_edge_script.py drives these) ──────────


def atr_at(bars: Sequence[Any], i: int, n: int = ATR_N) -> Optional[float]:
    """Mean true range over the n bars ending at i; None without the history
    or over a range that never moved (no unit to measure a move in)."""
    if i < n:
        return None
    trs = [max(bars[j].high - bars[j].low,
               abs(bars[j].high - bars[j - 1].close),
               abs(bars[j].low - bars[j - 1].close))
           for j in range(i - n + 1, i + 1)]
    a = sum(trs) / n
    return a if a > 0 else None


def signed_moves(bars: Sequence[Any], i: int, direction: str,
                 horizons: Sequence[int] = HORIZONS) -> dict[int, float]:
    """Close-to-close move from bar i in the idea's direction, in ATR units.
    A horizon past the end of the data is absent, never zero."""
    a = atr_at(bars, i)
    if a is None:
        return {}
    s = 1.0 if direction == "LONG" else -1.0
    return {h: s * (bars[i + h].close - bars[i].close) / a
            for h in horizons if i + h < len(bars)}


def unconditional(bars_by_sym: dict[str, Sequence[Any]], since: Optional[datetime],
                  horizons: Sequence[int] = HORIZONS) -> dict[str, dict[int, float]]:
    """Mean ATR-scaled move at every bar from `since` on, per direction."""
    acc: dict[str, dict[int, list[float]]] = {
        "LONG": {h: [] for h in horizons}, "SHORT": {h: [] for h in horizons}}
    for bars in bars_by_sym.values():
        for i in range(len(bars)):
            if since is not None and bars[i].timestamp < since:
                continue
            for h, m in signed_moves(bars, i, "LONG", horizons).items():
                acc["LONG"][h].append(m)
                acc["SHORT"][h].append(-m)
    return {d: {h: (sum(v) / len(v)) for h, v in by_h.items() if v}
            for d, by_h in acc.items()}


def deoverlap(rows: list[dict], h: int) -> list[dict]:
    """Per (dataset, symbol), keep an idea only if it is >= h bars after the
    last kept one, so no two kept forward windows overlap."""
    out: list[dict] = []
    last: dict[tuple, int] = {}
    for r in sorted(rows, key=lambda r: (r["dataset"], r["symbol"], r["i"])):
        k = (r["dataset"], r["symbol"])
        if k not in last or r["i"] >= last[k] + h:
            out.append(r)
            last[k] = r["i"]
    return out


def normal_ci(xs: Sequence[float]) -> Optional[tuple[int, float, float, float]]:
    """(n, mean, lo95, hi95); None under three samples."""
    n = len(xs)
    if n < 3:
        return None
    mu = sum(xs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    se = sd / math.sqrt(n)
    return n, mu, mu - 1.96 * se, mu + 1.96 * se


def cluster_ci(rows: list[dict], h: int, boot: int = BOOT, seed: int = SEED,
               level: float = 0.95) -> Optional[tuple[int, int, float, float, float]]:
    """(n, clusters, mean, lo, hi) by cluster bootstrap; None under five
    clusters, where a percentile interval is not a reading."""
    cl: dict[tuple, list[float]] = collections.defaultdict(lambda: [0.0, 0])
    for r in rows:
        if h in r["excess"]:
            acc = cl[tuple(r["cluster"])]
            acc[0] += r["excess"][h]
            acc[1] += 1
    return cluster_ci_sums(cl, boot, seed, level)


def cluster_ci_sums(cl: dict, boot: int = BOOT, seed: int = SEED, level: float = 0.95
                    ) -> Optional[tuple[int, int, float, float, float]]:
    """The bootstrap itself, over per-cluster [sum, count]: every interval in
    this script is this one function, whatever the rows were."""
    keys = [k for k in cl if cl[k][1] > 0]
    if len(keys) < 5:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(boot):
        s = c = 0.0
        for _k in range(len(keys)):
            v = cl[keys[rng.randrange(len(keys))]]
            s += v[0]
            c += v[1]
        means.append(s / c)
    means.sort()
    total = sum(cl[k][0] for k in keys)
    count = sum(cl[k][1] for k in keys)
    tail = (1.0 - level) / 2.0
    lo_i = int(round(tail * boot, 9))
    hi_i = min(boot - 1, int(round((1.0 - tail) * boot, 9)))
    return int(count), len(keys), total / count, means[lo_i], means[hi_i]


def with_excess(rows: list[dict], unc: dict[str, dict[int, float]]) -> list[dict]:
    """Each row's move minus its direction's unconditional mean, per horizon.
    A horizon the unconditional side has no mean for is absent, never zero."""
    out = []
    for r in rows:
        base = unc.get(r["direction"], {})
        ex = {h: m - base[h] for h, m in r["moves"].items() if h in base}
        out.append({**r, "excess": ex})
    return out


def continuation(rows: list[dict], a: int, b: int,
                 min_move: Optional[float] = None) -> list[dict]:
    """Each idea's excess from bar `a` to bar `b`, keyed under `b`.

    That is its excess at `b` minus its excess at `a`: the move over the
    segment, net of the direction's drift over the same segment. With
    `min_move`, only ideas whose RAW move at `a` was above it -- a trade's
    profit is the raw move, so that is the filter for "still in profit at
    `a`", and the drift is subtracted only from what is measured after it.
    A row missing either horizon is dropped, never read as a zero.
    """
    out = []
    for r in rows:
        ex = r["excess"]
        if a not in ex or b not in ex:
            continue
        if min_move is not None and not r["moves"][a] > min_move:
            continue
        out.append({**r, "excess": {b: ex[b] - ex[a]}})
    return out


# ── per-voter: which parts of the electorate carry the direction ─────────


def voter_sums(calls: list[dict], unc: dict[str, dict[int, float]],
               horizons: Sequence[int] = HORIZONS) -> dict[str, dict[int, dict]]:
    """Per voter, per horizon, per WEEK cluster: [sum, count] of the excess
    move in the direction that voter voted.

    A vote above zero is a LONG and below zero a SHORT; its move is the call's
    LONG move signed that way, minus that direction's unconditional drift.
    The cluster is the (dataset, ISO week), not the (symbol, week) the idea
    table uses: one voter votes on every symbol in the same bar, and those
    symbols move together, so a symbol-week cluster would count one market
    move as ten independent samples.
    """
    out: dict[str, dict[int, dict]] = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0])))
    for c in calls:
        key = tuple(c["week"])
        for name, vote in c["votes"].items():
            d = "LONG" if vote > 0 else "SHORT"
            sign = 1.0 if vote > 0 else -1.0
            base = unc.get(d, {})
            for h in horizons:
                if h in c["moves"] and h in base:
                    acc = out[name][h][key]
                    acc[0] += sign * c["moves"][h] - base[h]
                    acc[1] += 1
    return out


def _load_calls(path: str) -> tuple[list[dict], dict[str, dict[int, float]]]:
    d = json.loads(Path(path).read_text())
    if "calls" not in d:
        raise SystemExit(f"{path}: collected before voters were recorded; collect it again")
    unc = {dr: {int(h): v for h, v in by_h.items()}
           for dr, by_h in d["calls_unconditional"].items()}
    calls = [{**c, "moves": {int(h): v for h, v in c["moves"].items()}} for c in d["calls"]]
    return calls, unc


def _merge(into: dict, add: dict) -> None:
    for name, by_h in add.items():
        for h, by_cl in by_h.items():
            for k, (sm, n) in by_cl.items():
                acc = into[name][h][k]
                acc[0] += sm
                acc[1] += n


def voters_report(paths: Sequence[str], level: float = 0.95,
                  only: Optional[Sequence[str]] = None) -> str:
    """One row per voter, pooled over the files given, sorted by the h24 mean.

    `level` widens every interval (0.99, or 1 - 0.05/k for k pre-registered
    cells). `per file h24` is each file's own point estimate, so a pooled cell
    carried by one universe reads as one."""
    pooled: dict = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0])))
    per_file: list[tuple[str, dict]] = []
    n_calls = 0
    for p in paths:
        calls, unc = _load_calls(p)
        n_calls += len(calls)
        sums = voter_sums(calls, unc)
        per_file.append((Path(p).stem, sums))
        _merge(pooled, sums)
    names = sorted(pooled) if only is None else [n for n in only]

    def _mean(by_cl: dict) -> Optional[float]:
        n = sum(v[1] for v in by_cl.values())
        return sum(v[0] for v in by_cl.values()) / n if n else None

    def _key(name: str) -> float:
        m = _mean(pooled[name][24]) if name in pooled else None
        return -(m if m is not None else float("-inf"))

    pct = f"{level * 100:g}%"
    head = (f"{'voter':<24}{'votes':>7}  "
            + "  ".join(f"{'h' + str(h):^18}" for h in HORIZONS)
            + "  per file h24")
    out = [f"{n_calls} analyzer calls over {len(paths)} file(s); cells: mean excess ATR "
           f"in the vote's direction [{pct} week-cluster bootstrap]", head, "-" * len(head)]
    for name in sorted(names, key=_key):
        by_h = pooled.get(name)
        if by_h is None:
            out.append(f"{name:<24}{'none':>7}  never voted in these files")
            continue
        votes = sum(v[1] for v in by_h.get(24, {}).values())
        cells = "  ".join(f"{_cell(cluster_ci_sums(by_h[h], level=level) if h in by_h else None):^18}"
                          for h in HORIZONS)
        file_means = [_mean(fs[name][24]) if name in fs and 24 in fs[name] else None
                      for _f, fs in per_file]
        files = " ".join(f"{m:+.2f}" if m is not None else "  -  " for m in file_means)
        out.append(f"{name[:24]:<24}{votes:>7}  {cells}  {files}")
    out.append("")
    out.append("votes: calls where the voter voted and the 24-bar move exists; a voter that "
               "abstained or carried no weight cast no vote. 'thin' = under five week clusters.")
    return "\n".join(out)


# ── collect: one snapshot, in process ─────────────────────────────────────


def collect(dataset: str, since: Optional[datetime] = None) -> dict:
    """The canonical walk-forward, tapped. With `since`, only ideas and calls
    after it are kept, and the unconditional drift is measured from it too."""
    from bot.backtest import runner
    from bot.backtest.snapshot import load_dataset
    from bot.core.analyzer import Analyzer
    from bot.risk import risk_engine as rk
    from bot.utils.models import RiskVerdict

    ideas: dict[str, dict] = {}
    approved: set[str] = set()
    # Every analyzer call's named electorate, keyed (symbol, bar): the vote each
    # voter cast whether or not the call became an idea. A bar analysed twice
    # keeps its first reading and is counted, never silently merged.
    calls: dict[tuple, dict[str, float]] = {}
    repeats = 0
    current: contextvars.ContextVar = contextvars.ContextVar("signal_edge_call", default=None)
    real_analyze, real_eval = Analyzer.analyze, rk.RiskEngine.evaluate
    real_score = Analyzer._score_confluence

    async def _tap_analyze(self, signal, candles, *a, **k):
        token = current.set((signal.symbol, k.get("as_of")))
        try:
            idea = await real_analyze(self, signal, candles, *a, **k)
        finally:
            current.reset(token)
        as_of = k.get("as_of")
        if idea is not None and as_of is not None:
            ideas[idea.id] = {"symbol": signal.symbol, "ts": as_of,
                              "direction": idea.direction.value,
                              "signal_type": str(idea.signal_type),
                              "confidence": float(idea.confidence)}
        return idea

    def _tap_score(*a, **k):
        nonlocal repeats
        own = k.get("breakdown")
        if own is None:
            own = k["breakdown"] = []
        start = len(own)
        value = real_score(*a, **k)
        where = current.get()
        if where is not None and where[1] is not None:
            if where in calls:
                repeats += 1
            else:
                # A voter that abstained (0) or was muted to no weight cast no
                # vote; both are left out rather than read as a vote of zero.
                calls[where] = {str(n): float(v) for n, v, w in own[start:]
                                if abs(float(v)) > 1e-9 and float(w) > 0}
        return value

    def _tap_eval(self, idea, *a, **k):
        res = real_eval(self, idea, *a, **k)
        if res.verdict == RiskVerdict.APPROVED:
            approved.add(idea.id)
        return res

    saved_argv = sys.argv
    Analyzer.analyze = _tap_analyze            # type: ignore[method-assign]
    Analyzer._score_confluence = staticmethod(_tap_score)  # type: ignore[method-assign,assignment]
    rk.RiskEngine.evaluate = _tap_eval         # type: ignore[method-assign]
    try:
        sys.argv = ["runner", "--dataset", dataset, "--honest", "--walk-forward", "6"]
        runner.main()
    finally:
        Analyzer.analyze = real_analyze        # type: ignore[method-assign]
        Analyzer._score_confluence = staticmethod(real_score)  # type: ignore[method-assign,assignment]
        rk.RiskEngine.evaluate = real_eval     # type: ignore[method-assign]
        sys.argv = saved_argv

    bars = load_dataset(dataset)
    index = {sym: {b.timestamp: i for i, b in enumerate(bs)} for sym, bs in bars.items()}
    name = Path(dataset).name
    rows: list[dict] = []
    unplaced = 0
    for iid, r in ideas.items():
        if since is not None and r["ts"] <= since:
            continue
        i = index.get(r["symbol"], {}).get(r["ts"])
        if i is None:
            unplaced += 1
            continue
        moves = signed_moves(bars[r["symbol"]], i, r["direction"])
        if not moves:
            unplaced += 1
            continue
        wk = r["ts"].isocalendar()
        rows.append({"dataset": name, "symbol": r["symbol"], "i": i,
                     "ts": r["ts"].isoformat(), "direction": r["direction"],
                     "signal_type": r["signal_type"], "confidence": r["confidence"],
                     "approved": iid in approved, "moves": moves,
                     "cluster": [name, r["symbol"], wk[0], wk[1]]})
    first = since or min((r["ts"] for r in ideas.values()), default=None)
    call_rows: list[dict] = []
    calls_unplaced = 0
    for (sym, ts), votes in calls.items():
        if since is not None and ts <= since:
            continue
        i = index.get(sym, {}).get(ts)
        moves = signed_moves(bars[sym], i, "LONG") if i is not None else {}
        if not moves:
            calls_unplaced += 1
            continue
        wk = ts.isocalendar()
        call_rows.append({"dataset": name, "symbol": sym, "i": i, "ts": ts.isoformat(),
                          "votes": votes, "moves": moves, "week": [name, wk[0], wk[1]]})
    call_first = since or min((ts for _s, ts in calls), default=None)
    return {"dataset": name, "ideas": len(ideas), "unplaced": unplaced,
            "rows": rows, "unconditional": unconditional(bars, first),
            "since": since.isoformat() if since is not None else None,
            "calls": call_rows, "calls_unplaced": calls_unplaced,
            "calls_repeated": repeats,
            "calls_unconditional": unconditional(bars, call_first)}


# ── report ────────────────────────────────────────────────────────────────


def _load(path: str) -> list[dict]:
    d = json.loads(Path(path).read_text())
    unc = {dr: {int(h): v for h, v in by_h.items()} for dr, by_h in d["unconditional"].items()}
    rows = [{**r, "moves": {int(h): v for h, v in r["moves"].items()}} for r in d["rows"]]
    return with_excess(rows, unc)


def _cell(ci: Optional[tuple]) -> str:
    if ci is None:
        return "      thin      "
    return f"{ci[-3]:+.2f} [{ci[-2]:+.2f},{ci[-1]:+.2f}]"


def report(paths: Sequence[str]) -> str:
    rows = [r for p in paths for r in _load(p)]
    groups: list[tuple[str, list[dict]]] = []
    for ds in sorted({r["dataset"] for r in rows}):
        g = [r for r in rows if r["dataset"] == ds]
        groups.append((ds, g))
        for sig in sorted({r["signal_type"] for r in g}):
            groups.append((f"  {sig}", [r for r in g if r["signal_type"] == sig]))
    if len(paths) > 1:
        groups.append(("POOLED (all files given)", rows))
        for sig in sorted({r["signal_type"] for r in rows}):
            groups.append((f"  {sig}", [r for r in rows if r["signal_type"] == sig]))
        for d in ("LONG", "SHORT"):
            groups.append((f"  direction {d}", [r for r in rows if r["direction"] == d]))
        groups.append(("  approved by the risk gate", [r for r in rows if r["approved"]]))
    head = f"{'group':<34}{'n':>6}  " + "  ".join(f"{'h' + str(h) + ' excess ATR':^18}" for h in HORIZONS)
    out = [head, "-" * len(head)]
    for label, g in groups:
        cells = "  ".join(f"{_cell(cluster_ci(g, h)):^18}" for h in HORIZONS)
        out.append(f"{label[:34]:<34}{len(g):>6}  {cells}")
    out.append("")
    out.append("cells: mean excess [95% cluster-bootstrap interval]; 'thin' = under five clusters.")
    out.append("de-overlapped n at h24: " + ", ".join(
        f"{label.strip()} {sum(24 in r['excess'] for r in deoverlap(g, 24))}"
        for label, g in groups if not label.startswith("  ")))
    return "\n".join(out)


def continuation_report(paths: Sequence[str], a: int, b: int,
                        signal: Optional[str] = None) -> str:
    """Continuation from bar `a` to bar `b`, per file and pooled.

    Each group is de-overlapped at `b` bars first, so no two kept ideas on
    one symbol share any part of their forward window, and its interval is
    the cluster bootstrap every other figure here uses.
    """
    rows = [r for p in paths for r in _load(p)]
    if signal is not None:
        rows = [r for r in rows if r["signal_type"] == signal]
    groups: list[tuple[str, list[dict]]] = [
        (ds, [r for r in rows if r["dataset"] == ds])
        for ds in sorted({r["dataset"] for r in rows})]
    if len(paths) > 1:
        groups.append(("POOLED (all files given)", rows))
    subsets = (("every idea", None), (f"in favour at bar {a}", 0.0),
               (f"1+ ATR in favour at bar {a}", 1.0))
    head = f"{'group':<40}{'n':>6}  {f'excess ATR, bar {a} -> {b}':^24}"
    out = [f"signal: {signal or 'every signal type'}", head, "-" * len(head)]
    for label, g in groups:
        kept = deoverlap(g, b)
        out.append(label)
        for sub, floor in subsets:
            seg = continuation(kept, a, b, floor)
            out.append(f"  {sub:<38}{len(seg):>6}  {_cell(cluster_ci(seg, b)):^24}")
    out.append("")
    out.append(f"cells: mean excess [95% cluster-bootstrap interval]; each group "
               f"de-overlapped at {b} bars; 'thin' = under five clusters.")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="run the honest walk-forward with taps")
    c.add_argument("--dataset", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--since", default=None,
                   help="keep only ideas and calls after this ISO time (UTC if no zone); "
                        "the drift is measured from it too")
    r = sub.add_parser("report", help="honest intervals over collected files")
    r.add_argument("files", nargs="+")
    k = sub.add_parser("continuation", help="what a hold limit between two horizons forgoes")
    k.add_argument("files", nargs="+")
    k.add_argument("--from", dest="a", type=int, default=12)
    k.add_argument("--to", dest="b", type=int, default=48)
    k.add_argument("--signal", default=None)
    v = sub.add_parser("voters", help="each voter's directional edge, pooled over files")
    v.add_argument("files", nargs="+")
    v.add_argument("--level", type=float, default=0.95)
    v.add_argument("--voters", default=None, help="comma-list; default every voter on file")
    args = ap.parse_args(argv)
    if args.cmd == "voters":
        if not 0.5 < args.level < 1.0:
            ap.error("--level is an interval coverage between 0.5 and 1")
        only = [x.strip() for x in args.voters.split(",") if x.strip()] if args.voters else None
        print(voters_report(args.files, args.level, only))
        return 0
    if args.cmd == "continuation":
        if args.a not in HORIZONS or args.b not in HORIZONS or not args.a < args.b:
            ap.error(f"--from and --to must be recorded horizons {HORIZONS}, from before to")
        print(continuation_report(args.files, args.a, args.b, args.signal))
        return 0
    if args.cmd == "collect":
        since = None
        if args.since:
            since = datetime.fromisoformat(args.since)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        data = collect(args.dataset, since)
        Path(args.out).write_text(json.dumps(data))
        print(f"{data['dataset']}: {data['ideas']} ideas, {len(data['rows'])} measured, "
              f"{data['unplaced']} unplaced; {len(data['calls'])} analyzer calls measured, "
              f"{data['calls_unplaced']} unplaced, {data['calls_repeated']} repeated "
              f"-> {args.out}")
        return 0
    print(report(args.files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
