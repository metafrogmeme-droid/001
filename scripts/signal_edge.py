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

`collect` runs the same in-process walk-forward as
`python -m bot.backtest.runner --dataset <d> --honest --walk-forward 6`, with
two read-only taps (the analyzer's `analyze` and the risk engine's
`evaluate`). Set RUNECLAW_STATE_DIR to a scratch directory, as for any
benchmark run.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
import sys
from datetime import datetime
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


def cluster_ci(rows: list[dict], h: int, boot: int = BOOT, seed: int = SEED
               ) -> Optional[tuple[int, int, float, float, float]]:
    """(n, clusters, mean, lo95, hi95) by cluster bootstrap; None under five
    clusters, where a percentile interval is not a reading."""
    cl: dict[tuple, list[float]] = collections.defaultdict(list)
    for r in rows:
        if h in r["excess"]:
            cl[tuple(r["cluster"])].append(r["excess"][h])
    keys = list(cl)
    if len(keys) < 5:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(boot):
        s = c = 0.0
        for _k in range(len(keys)):
            v = cl[keys[rng.randrange(len(keys))]]
            s += sum(v)
            c += len(v)
        means.append(s / c)
    means.sort()
    total = sum(sum(v) for v in cl.values())
    count = sum(len(v) for v in cl.values())
    return (count, len(keys), total / count,
            means[int(0.025 * boot)], means[int(0.975 * boot)])


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


# ── collect: one snapshot, in process ─────────────────────────────────────


def collect(dataset: str) -> dict:
    from bot.backtest import runner
    from bot.backtest.snapshot import load_dataset
    from bot.core.analyzer import Analyzer
    from bot.risk import risk_engine as rk
    from bot.utils.models import RiskVerdict

    ideas: dict[str, dict] = {}
    approved: set[str] = set()
    real_analyze, real_eval = Analyzer.analyze, rk.RiskEngine.evaluate

    async def _tap_analyze(self, signal, candles, *a, **k):
        idea = await real_analyze(self, signal, candles, *a, **k)
        as_of = k.get("as_of")
        if idea is not None and as_of is not None:
            ideas[idea.id] = {"symbol": signal.symbol, "ts": as_of,
                              "direction": idea.direction.value,
                              "signal_type": str(idea.signal_type),
                              "confidence": float(idea.confidence)}
        return idea

    def _tap_eval(self, idea, *a, **k):
        res = real_eval(self, idea, *a, **k)
        if res.verdict == RiskVerdict.APPROVED:
            approved.add(idea.id)
        return res

    saved_argv = sys.argv
    Analyzer.analyze = _tap_analyze            # type: ignore[method-assign]
    rk.RiskEngine.evaluate = _tap_eval         # type: ignore[method-assign]
    try:
        sys.argv = ["runner", "--dataset", dataset, "--honest", "--walk-forward", "6"]
        runner.main()
    finally:
        Analyzer.analyze = real_analyze        # type: ignore[method-assign]
        rk.RiskEngine.evaluate = real_eval     # type: ignore[method-assign]
        sys.argv = saved_argv

    bars = load_dataset(dataset)
    index = {sym: {b.timestamp: i for i, b in enumerate(bs)} for sym, bs in bars.items()}
    name = Path(dataset).name
    rows: list[dict] = []
    unplaced = 0
    for iid, r in ideas.items():
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
    first = min((r["ts"] for r in ideas.values()), default=None)
    return {"dataset": name, "ideas": len(ideas), "unplaced": unplaced,
            "rows": rows, "unconditional": unconditional(bars, first)}


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
    r = sub.add_parser("report", help="honest intervals over collected files")
    r.add_argument("files", nargs="+")
    k = sub.add_parser("continuation", help="what a hold limit between two horizons forgoes")
    k.add_argument("files", nargs="+")
    k.add_argument("--from", dest="a", type=int, default=12)
    k.add_argument("--to", dest="b", type=int, default=48)
    k.add_argument("--signal", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "continuation":
        if args.a not in HORIZONS or args.b not in HORIZONS or not args.a < args.b:
            ap.error(f"--from and --to must be recorded horizons {HORIZONS}, from before to")
        print(continuation_report(args.files, args.a, args.b, args.signal))
        return 0
    if args.cmd == "collect":
        data = collect(args.dataset)
        Path(args.out).write_text(json.dumps(data))
        print(f"{data['dataset']}: {data['ideas']} ideas, {len(data['rows'])} measured, "
              f"{data['unplaced']} unplaced -> {args.out}")
        return 0
    print(report(args.files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
