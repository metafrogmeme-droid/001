"""Does the POC-retest setup pay after fees? Replayed on a frozen 1h snapshot.

`/pocretest` records a confirmed setup into a shadow book only when somebody
asks for one, so its verdict arrives at the pace of the asking. This replays
the SAME read over a frozen snapshot, one closed 1h bar at a time, and asks
what the record would say if the observer had been reading every hour:

  * every closed 1h bar t is read exactly as `observe_setup` reads it -- the
    last `ENTRY_BARS` closed 1h bars and the last `STRUCTURE_BARS` closed 4h
    bars (resampled from the 1h ones, closed groups only), through the live
    `retest_state` and `setup_verdict`;
  * a setup is ARMED only when the read is confirmed, the verdict is ok, and
    the retest candle is bar t itself -- known at t's close, never later;
  * it is scored by the live `score_setup` over the bars the observer could
    score it on (t+1 .. t+ENTRY_BARS-1), and the verdict is the live
    `shadow_verdict`.

Nothing here restates the strategy: the read, the verdict, the scoring and the
shadow verdict are the functions the command calls. The one number this adds
is a (dataset, ISO week) cluster bootstrap on the mean R beside the verdict's
own interval, because setups on correlated symbols in one week are one market
move rather than independent samples. It is `signal_edge`'s bootstrap, loaded
from beside this file rather than copied.

Two things it cannot see, stated rather than hidden. The fill is at the entry
price, as the shadow book assumes; `report` prints how many triggers GAPPED
through the entry and what that costs in R, as a sensitivity rather than a
correction. And a bar that reaches the entry and the stop is a stop-out,
because OHLC cannot say the entry came first -- the conservative reading the
scorer already makes.

`observer` emulates the command as it is used: `observe_setup` queried every
N hours, deduplicated by (side, retest candle), within the window it fetched.
By default it applies the arming rule as first shipped -- ANY confirmed read,
scored from its retest candle -- which is how the record was found to read
"survives" on hindsight; `--forward` applies the rule the observer uses now.

`record` writes one window of `benchmark/poc_retest/result.json`, the file
`/pocretest` and `/pocshadow` read (`bot/core/poc_retest_history.py`). Each
window is pinned to the snapshots its reads were collected off, and it records
the parameters and fee rates it was measured at, so the cards can refuse a
window that no longer describes the live read.

    RUNECLAW_STATE_DIR=$(mktemp -d) python scripts/poc_retest_replay.py collect \
        --dataset benchmark/majors_1h_v2 --out poc_majors_v2.json
    python scripts/poc_retest_replay.py report poc_majors_v2.json poc_alts_v2.json
    python scripts/poc_retest_replay.py grid poc_majors_v2.json poc_alts_v2.json
    python scripts/poc_retest_replay.py observer --every 24 poc_majors_v2.json
    python scripts/poc_retest_replay.py record --label "10 majors + 8 alts" \
        poc_majors_v2.json poc_alts_v2.json
"""
from __future__ import annotations

import argparse
import bisect
import collections
import importlib.util
import itertools
import json
import multiprocessing
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bot.backtest.benchmark_record import code_sha, manifest_hash  # noqa: E402
from bot.core.poc_retest import PocRetestParams, RetestRead, retest_state, setup_verdict  # noqa: E402
from bot.core.poc_retest_history import HISTORY_RESULT, KIND, history_on_record, replay_verdict  # noqa: E402
from bot.core.poc_retest_record import OUTCOMES, entry_traded, score_setup, shadow_verdict  # noqa: E402
from bot.core.poc_retest_scan import ENTRY_BARS, STRUCTURE_BARS  # noqa: E402
from bot.core.trade_costs import entry_rate_pct, exit_rate_pct, fee_usd  # noqa: E402
from bot.utils.candles import resample_ohlcv  # noqa: E402

_se_spec = importlib.util.spec_from_file_location(
    "signal_edge", Path(__file__).with_name("signal_edge.py"))
assert _se_spec is not None and _se_spec.loader is not None
_se = importlib.util.module_from_spec(_se_spec)
_se_spec.loader.exec_module(_se)
cluster_ci_sums = _se.cluster_ci_sums

H1_MS = 3_600_000
H4_MS = 4 * H1_MS

#: The two parameters that change the READ; the other two only change the
#: verdict over it, so they are applied at report time and cost nothing.
ATR_BUFFERS: tuple[float, ...] = (0.10, 0.25, 0.50)
RETEST_WINDOWS: tuple[int, ...] = (3, 5, 8)
MIN_NET_RS: tuple[float, ...] = (1.5, 2.0, 3.0)
MAX_STOP_ATRS: tuple[float, ...] = (1.5, 2.0, 3.0)

#: A candidate needs this many scored setups before its interval is read: the
#: grid has 81 cells and a thin one clearing zero is the likeliest false lead.
GRID_MIN_SCORED = 30


def _read_key(atr_buffer: float, retest_window: int) -> str:
    return f"{atr_buffer:g}/{retest_window}"


# ── collect: every hour's read, per symbol and read-combo ──────────────────


def replay_reads(rows: list[list[float]], params: PocRetestParams) -> tuple[dict, list[dict]]:
    """Read one symbol at every closed 1h bar, as the observer would.

    `rows` are ccxt-shaped [ts_ms, o, h, l, c, v] closed 1h bars. Returns the
    count of every state read and one record per hour whose read was
    ``confirmed`` -- at ANY retest index, because the observer arms stale
    confirmed reads too; `fresh` is the subset whose retest candle is the bar
    being read.
    """
    h4 = resample_ohlcv(rows, "1h", "4h")
    h4_end = [r[0] + H4_MS for r in h4]
    states: collections.Counter = collections.Counter()
    confirmed: list[dict] = []
    # `read_setup` refuses a read with fewer than 2 x swing_order + 2 closed
    # 4h candles. It is not restated here because it cannot bite: the first
    # read already holds ENTRY_BARS closed 1h bars, which is ENTRY_BARS / 4
    # closed 4h candles, and a test drives that rather than a branch no input
    # reaches.
    for t in range(ENTRY_BARS - 1, len(rows)):
        close_ms = rows[t][0] + H1_MS
        k = bisect.bisect_right(h4_end, close_ms)      # 4h bars closed by then
        w4 = h4[max(0, k - STRUCTURE_BARS):k]
        w1 = rows[t - ENTRY_BARS + 1:t + 1]
        read = retest_state([r[2] for r in w4], [r[3] for r in w4], [r[4] for r in w4],
                            [r[5] for r in w4], [r[2] for r in w1], [r[3] for r in w1],
                            [r[4] for r in w1], params=params)
        states[read.state] += 1
        if read.state != "confirmed" or read.retest_index is None:
            continue
        confirmed.append({"t": t, "retest_t": t - (len(w1) - 1 - read.retest_index),
                          "side": read.side, "entry": read.entry, "stop": read.stop,
                          "target": read.target, "atr": read.atr})
    return dict(states), confirmed


_JOB_ROWS: dict[str, list[list[float]]] = {}


def _job(args: tuple[str, float, int]) -> tuple[str, str, dict, list[dict]]:
    sym, buf, win = args
    p = replace(PocRetestParams(), atr_buffer=buf, retest_window=win)
    states, confirmed = replay_reads(_JOB_ROWS[sym], p)
    return sym, _read_key(buf, win), states, confirmed


def _rows_of(bars: Sequence[Any]) -> list[list[float]]:
    return [[int(b.timestamp.timestamp() * 1000), float(b.open), float(b.high),
             float(b.low), float(b.close), float(b.volume)] for b in bars]


def collect(dataset: str, jobs: int = 1) -> dict:
    """Every hour's read for every read-combo, plus the bars to score them on."""
    from bot.backtest.snapshot import load_dataset
    bars = load_dataset(dataset)
    _JOB_ROWS.clear()
    _JOB_ROWS.update({sym: _rows_of(bs) for sym, bs in bars.items()})
    work = [(sym, b, w) for sym in sorted(_JOB_ROWS)
            for b, w in itertools.product(ATR_BUFFERS, RETEST_WINDOWS)]
    if jobs > 1:
        with multiprocessing.get_context("fork").Pool(jobs) as pool:
            done = pool.map(_job, work)
    else:
        done = [_job(w) for w in work]
    reads: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for sym, key, states, confirmed in done:
        reads[key][sym] = {"states": states, "confirmed": confirmed}
    # The snapshot the reads were computed off, pinned by its own manifest
    # hash, so a result recorded from them can be checked against the data on
    # disk later. None when the manifest cannot be read, which `record` refuses.
    return {"dataset": Path(dataset).name, "dataset_path": str(dataset),
            "dataset_hash": manifest_hash(Path(dataset) / "manifest.json"),
            "code_sha": code_sha(),
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entry_bars": ENTRY_BARS,
            "structure_bars": STRUCTURE_BARS,
            "bars": {sym: [[r[0], r[1], r[2], r[3]] for r in rows]
                     for sym, rows in _JOB_ROWS.items()},
            "reads": reads}


# ── scoring: the live functions over the stored reads ─────────────────────


def _verdict(c: dict, p: PocRetestParams) -> Any:
    read = RetestRead("confirmed", "", side=c["side"], entry=c["entry"], stop=c["stop"],
                      target=c["target"], atr=c["atr"], params=p)
    return setup_verdict(read, params=p)


def _risk_unit(entry: float, stop: float) -> float:
    """The R unit `net_reward_risk` divides by: the stopped-out loss with both
    fee legs in it. Used only to size a gapped fill in R."""
    return abs(entry - stop) + fee_usd(entry, entry_rate_pct(None)) + fee_usd(stop, exit_rate_pct())


def _gap_r(side: str, entry: float, stop: float, trigger_open: float) -> float:
    """How far past the entry a trigger bar OPENED, in R. Zero when it opened
    on the entry's side, where a resting stop order fills at the entry."""
    gap = (trigger_open - entry) if side == "long" else (entry - trigger_open)
    return max(0.0, gap) / _risk_unit(entry, stop)


def _iso_week(ms: int) -> tuple[int, int]:
    wk = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isocalendar()
    return wk[0], wk[1]


def _fresh_reads(data: dict, p: PocRetestParams, since_ms: Optional[int]):
    """Every confirmed read whose retest candle is the bar being read -- the
    only reads a bar-by-bar observer could act on at that bar's close."""
    key = _read_key(p.atr_buffer, p.retest_window)
    if key not in data["reads"]:
        raise SystemExit(f"{data['dataset']}: no reads for atr_buffer {p.atr_buffer:g}, "
                         f"retest_window {p.retest_window}; collect covers "
                         f"{sorted(data['reads'])}")
    for sym, per in sorted(data["reads"][key].items()):
        bars = data["bars"][sym]
        for c in per["confirmed"]:
            if c["retest_t"] != c["t"]:
                continue
            if since_ms is not None and bars[c["t"]][0] <= since_ms:
                continue
            yield sym, bars, c


def fresh_setups(data: dict, p: PocRetestParams, since_ms: Optional[int] = None) -> list[dict]:
    """The setups a bar-by-bar observer arms, each scored where it could be:
    a fresh confirmed read whose verdict is ok."""
    win = int(data["entry_bars"])
    out: list[dict] = []
    for sym, bars, c in _fresh_reads(data, p, since_ms):
        v = _verdict(c, p)
        if v.verdict != "ok":
            continue
        t = c["t"]
        after = bars[t + 1:t + win]
        o = score_setup(c["side"], c["entry"], c["stop"], c["target"], v.net_r,
                        [b[2] for b in after], [b[3] for b in after])
        gap = (_gap_r(c["side"], c["entry"], c["stop"], after[o.trigger_index][1])
               if o.trigger_index is not None else None)
        out.append({"dataset": data["dataset"], "symbol": sym, "t": t,
                    "retest_ms": bars[t][0], "side": c["side"],
                    "net_r": v.net_r, "outcome": o, "gap_r": gap,
                    "week": [data["dataset"], *_iso_week(bars[t][0])]})
    return out


def verdicts_seen(data: dict, p: PocRetestParams, since_ms: Optional[int] = None) -> collections.Counter:
    """What `setup_verdict` answered for every fresh confirmed read."""
    return collections.Counter(_verdict(c, p).verdict
                               for _s, _b, c in _fresh_reads(data, p, since_ms))


def week_interval(setups: Sequence[dict], level: float = 0.95) -> Optional[tuple]:
    """(n, clusters, mean, lo, hi) of the scored R, by (dataset, week)."""
    cl: dict[tuple, list[float]] = collections.defaultdict(lambda: [0.0, 0])
    for s in setups:
        o = s["outcome"]
        if o.scored:
            acc = cl[tuple(s["week"])]
            acc[0] += o.r
            acc[1] += 1
    ci: Optional[tuple] = cluster_ci_sums(cl, level=level)
    return ci


# ── the observer as it is used: queried every N hours ─────────────────────


def observer_setups(data: dict, p: PocRetestParams, every: int, phase: int = 0,
                    forward: bool = False) -> list[dict]:
    """What `observe_setup` would have recorded if run every `every` hours.

    At each query bar q (q % every == phase) it arms the read at q if that
    read is confirmed and ok, keyed by (side, retest candle) as `setup_key`
    keys it; then it re-scores every armed setup that is not terminal while
    the bar it is scored from is still inside the fetched window. The last
    score stands.

    `forward=False` is the rule as it shipped: any confirmed read is armed,
    whatever the retest candle's age, and scored from the bar after the
    retest candle -- over bars that had closed before anybody asked.
    `forward=True` is the rule `observe_setup` applies now: a read whose
    entry has already traded since its retest candle is not armed (it is no
    longer takeable at its levels; `entry_traded` is the one reading), and an
    armed setup is scored from the bar after the read that armed it. A fresh
    read is armed and scored the same either way.
    """
    key = _read_key(p.atr_buffer, p.retest_window)
    win = int(data["entry_bars"])
    out: list[dict] = []
    for sym, per in sorted(data["reads"].get(key, {}).items()):
        bars = data["bars"][sym]
        at_t = {c["t"]: c for c in per["confirmed"]}
        armed: dict[tuple, dict] = {}
        for q in range(len(bars)):
            if q % every != phase:
                continue
            c = at_t.get(q)
            if c is not None:
                v = _verdict(c, p)
                k = (c["side"], c["retest_t"])
                if v.verdict == "ok" and k not in armed:
                    i = c["retest_t"]
                    since = bars[i + 1:q + 1]
                    if forward and entry_traded(c["side"], c["entry"], [b[2] for b in since],
                                                [b[3] for b in since]) is not False:
                        armed[k] = {"missed": True}
                    else:
                        armed[k] = {"dataset": data["dataset"], "symbol": sym,
                                    "t": i, "armed_t": q,
                                    "from_t": q if forward else i,
                                    "retest_ms": bars[i][0], "side": c["side"],
                                    "levels": (c["entry"], c["stop"], c["target"], v.net_r),
                                    "outcome": None,
                                    "week": [data["dataset"], *_iso_week(bars[i][0])]}
            for row in armed.values():
                if row.get("missed"):
                    continue
                # The live observer skips a terminal row. A walk's first
                # resolution never changes as its window grows, so the skip
                # saves work and changes no answer.
                prev = row["outcome"]
                if prev is not None and prev.outcome in ("target", "stop", "ambiguous"):
                    continue
                start = row["from_t"]
                if start < q - (win - 1):
                    continue                       # slid out of the fetched window
                e, s, tg, r = row["levels"]
                after = bars[start + 1:q + 1]
                row["outcome"] = score_setup(row["side"], e, s, tg, r,
                                             [b[2] for b in after], [b[3] for b in after])
        out.extend(armed.values())
    return out


# ── reports ────────────────────────────────────────────────────────────────


def _load(path: str) -> dict:
    data: dict = json.loads(Path(path).read_text())
    return data


def _params(args: argparse.Namespace) -> PocRetestParams:
    return replace(PocRetestParams(), atr_buffer=args.atr_buffer,
                   retest_window=args.retest_window, min_net_r=args.min_net_r,
                   max_stop_atr=args.max_stop_atr)


def _since_ms(since: Optional[str]) -> Optional[int]:
    if not since:
        return None
    ts = datetime.fromisoformat(since)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp() * 1000)


def _wi(ci: Optional[tuple]) -> str:
    if ci is None:
        return "thin (under five week clusters)"
    return f"{ci[2]:+.2f}R [{ci[3]:+.2f}, {ci[4]:+.2f}] over {ci[1]} weeks"


def _outcome_line(setups: Sequence[dict]) -> str:
    n = collections.Counter(s["outcome"].outcome for s in setups)
    return " · ".join(f"{k} {n[k]}" for k in ("target", "stop", "ambiguous", "open",
                                                "not_triggered", "unscored") if n[k])


def report(paths: Sequence[str], p: PocRetestParams, since: Optional[str] = None) -> str:
    since_ms = _since_ms(since)
    files = [_load(x) for x in paths]
    out = [f"params: atr_buffer {p.atr_buffer:g} · retest_window {p.retest_window} · "
           f"min_net_r {p.min_net_r:g} · max_stop_atr {p.max_stop_atr:g} · entry taker "
           f"{entry_rate_pct(None):g}% · exit {exit_rate_pct():g}%"
           + (f" · retest candles after {since}" if since else "")]
    pooled: list[dict] = []
    for d in files:
        key = _read_key(p.atr_buffer, p.retest_window)
        states: collections.Counter = collections.Counter()
        for per in d["reads"].get(key, {}).values():
            states.update(per["states"])
        setups = fresh_setups(d, p, since_ms)
        pooled += setups
        seen = verdicts_seen(d, p, since_ms)
        out.append("")
        out.append(f"{d['dataset']}: {sum(states.values())} hourly reads · "
                   + " · ".join(f"{k} {v}" for k, v in states.most_common()))
        out.append("  fresh confirmed reads by verdict: "
                   + " · ".join(f"{k} {v}" for k, v in seen.most_common()))
        out.append(f"  armed {len(setups)}: {_outcome_line(setups)}")
        out.append(f"  {shadow_verdict([s['outcome'] for s in setups]).why}")
        out.append(f"  week clusters: {_wi(week_interval(setups))}")
    if len(files) > 1:
        v = shadow_verdict([s["outcome"] for s in pooled])
        out.append("")
        out.append(f"POOLED: armed {len(pooled)}: {_outcome_line(pooled)}")
        out.append(f"  verdict {v.verdict}: {v.why}")
        out.append(f"  week clusters: {_wi(week_interval(pooled))}")
    trig = [s for s in pooled if s["gap_r"] is not None and s["outcome"].scored]
    gapped = [s for s in trig if s["gap_r"] > 0]
    if trig:
        adj = [s["outcome"].r - s["gap_r"] for s in trig]
        out.append(f"  fill sensitivity: {len(gapped)} of {len(trig)} scored triggers opened "
                   f"past the entry; filled at that open instead, the mean is "
                   f"{sum(adj) / len(adj):+.2f}R (at the entry: "
                   f"{sum(s['outcome'].r for s in trig) / len(trig):+.2f}R)")
    return "\n".join(out)


def grid(paths: Sequence[str], since: Optional[str] = None, level: float = 0.95,
         cells: Optional[Sequence[str]] = None) -> str:
    """Every parameter cell, pooled over the files given, by the lower bound."""
    since_ms = _since_ms(since)
    files = [_load(x) for x in paths]
    rows = []
    combos = itertools.product(ATR_BUFFERS, RETEST_WINDOWS, MIN_NET_RS, MAX_STOP_ATRS)
    for b, w, r, s in combos:
        name = f"{b:g}/{w}/{r:g}/{s:g}"
        if cells is not None and name not in cells:
            continue
        p = replace(PocRetestParams(), atr_buffer=b, retest_window=w,
                    min_net_r=r, max_stop_atr=s)
        setups = [x for d in files for x in fresh_setups(d, p, since_ms)]
        v = shadow_verdict([x["outcome"] for x in setups])
        ci = week_interval(setups, level)
        rows.append((name, len(setups), v, ci))
    rows.sort(key=lambda x: -(x[3][3] if x[3] is not None else float("-inf")))
    pct = f"{level * 100:g}%"
    head = (f"{'buffer/window/min_r/max_stop':<30}{'armed':>6}{'scored':>7}  "
            f"{'verdict':<10}  mean R [{pct} week clusters]")
    out = [head, "-" * len(head)]
    for name, n, v, ci in rows:
        flag = ("  <- candidate" if ci is not None and v.n_scored >= GRID_MIN_SCORED
                and ci[3] > 0 else "")
        out.append(f"{name:<30}{n:>6}{v.n_scored:>7}  {v.verdict:<10}  {_wi(ci)}{flag}")
    out.append("")
    out.append(f"candidate: {GRID_MIN_SCORED}+ scored setups and the whole week-cluster "
               f"interval above zero. {len(rows)} cells read.")
    return "\n".join(out)


def observer_report(paths: Sequence[str], p: PocRetestParams, every: int,
                    forward: bool = False) -> str:
    """The observer queried every `every` hours, beside the bar-by-bar record."""
    files = [_load(x) for x in paths]
    fresh = [x for d in files for x in fresh_setups(d, p)]
    rows = [x for d in files for x in observer_setups(d, p, every, forward=forward)]
    missed = [x for x in rows if x.get("missed")]
    seen = [x for x in rows if not x.get("missed")]
    fresh_keys = {(x["dataset"], x["symbol"], x["side"], x["t"]) for x in fresh}
    stale = [x for x in seen if x["armed_t"] > x["t"]]
    hindsight = [x for x in seen if (x["dataset"], x["symbol"], x["side"], x["t"]) not in fresh_keys]
    known = [x for x in seen if x["outcome"].resolve_index is not None
             and x["from_t"] + 1 + x["outcome"].resolve_index <= x["armed_t"]]
    fv = shadow_verdict([x["outcome"] for x in fresh])
    ov = shadow_verdict([x["outcome"] for x in seen])
    rule = "armed forward only" if forward else "armed as shipped"
    lines = [
        f"bar-by-bar: armed {len(fresh)} · {_outcome_line(fresh)}",
        f"  verdict {fv.verdict}: {fv.why}",
        f"queried every {every}h, {rule}: armed {len(seen)} · {_outcome_line(seen)}",
        f"  verdict {ov.verdict}: {ov.why}",
        f"  {len(stale)} armed after their retest candle had closed; {len(hindsight)} are "
        f"setups the bar-by-bar reader never armed (the read that confirmed them was "
        f"not confirmed when the retest candle closed)",
        f"  {len(known)} had already resolved before the read that armed them",
    ]
    if forward:
        lines.append(f"  {len(missed)} confirmed reads not armed: their entry had "
                     f"already traded since the retest candle")
    return "\n".join(lines)


# ── record: the artefact the cards read ────────────────────────────────


def _snapshot_name(path: str) -> str:
    """A collected file's snapshot, as the repository-relative path the card's
    reader pins against. A snapshot outside the repository cannot be pinned by
    anybody reading the artefact, so it is refused rather than recorded."""
    p = Path(path).resolve()
    try:
        return str(p.relative_to(_ROOT))
    except ValueError:
        raise SystemExit(f"{path}: not a snapshot under the repository, so no reader "
                         f"of the artefact could pin it") from None


def record_window(paths: Sequence[str], p: PocRetestParams, label: str,
                  since: Optional[str] = None) -> dict:
    """One replay window as the artefact carries it: the fresh-arm record at
    one parameter set over the files given, pinned to their snapshots, with
    the verdict the card's reader will re-derive and refuse if it disagrees."""
    since_ms = _since_ms(since)
    files = [_load(x) for x in paths]
    datasets = []
    for d in files:
        if not d.get("dataset_path") or not d.get("dataset_hash"):
            raise SystemExit(f"{d.get('dataset')}: collected without the snapshot's hash "
                             f"(an older collect, or a manifest that could not be read); "
                             f"re-run collect")
        datasets.append({"dataset": _snapshot_name(d["dataset_path"]),
                         "dataset_hash": d["dataset_hash"]})
    shas = {d.get("code_sha") for d in files}
    if len(shas) != 1:
        raise SystemExit("the files were collected at different commits; collect them "
                         "at one, because the read they record is the code's")
    setups = [x for d in files for x in fresh_setups(d, p, since_ms)]
    outcomes = collections.Counter(s["outcome"].outcome for s in setups)
    unknown = set(outcomes) - set(OUTCOMES)
    if unknown:
        raise SystemExit(f"outcome word(s) the artefact cannot carry: {sorted(unknown)}")
    rs = [s["outcome"].r for s in setups if s["outcome"].scored]
    ci = week_interval(setups)
    interval = (round(ci[3], 6), round(ci[4], 6)) if ci is not None else None
    retests = sorted(s["retest_ms"] for s in setups)

    def iso(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

    return {
        "label": label,
        "since": iso(since_ms) if since_ms is not None else None,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_sha": shas.pop(),
        "params": asdict(p),
        "fees": {"entry_pct": entry_rate_pct(None), "exit_pct": exit_rate_pct()},
        "datasets": datasets,
        "first_retest": iso(retests[0]) if retests else None,
        "last_retest": iso(retests[-1]) if retests else None,
        "armed": len(setups),
        "scored": len(rs),
        "outcomes": {k: outcomes[k] for k in OUTCOMES},
        "mean_r": round(sum(rs) / len(rs), 6) if rs else None,
        "interval": list(interval) if interval is not None else None,
        "clusters": ci[1] if ci is not None else None,
        "level": 0.95,
        "verdict": replay_verdict(len(rs), interval),
    }


def write_record(out: Path, window: dict) -> None:
    """Put a window into the artefact, replacing one of the same label in
    place. An artefact that is there and will not read is NEVER overwritten:
    it may be the only copy of a result, and a writer that cannot open a file
    has no business deciding it holds nothing."""
    doc: dict = {"kind": KIND, "windows": []}
    if out.exists():
        try:
            doc = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SystemExit(f"{out}: an artefact is there and will not parse; move it "
                             f"aside rather than have it overwritten") from None
        if not isinstance(doc, dict) or doc.get("kind") != KIND \
                or not isinstance(doc.get("windows"), list):
            raise SystemExit(f"{out}: not a POC-retest replay artefact; refusing to "
                             f"overwrite it")
    windows = doc["windows"]
    for i, w in enumerate(windows):
        if isinstance(w, dict) and w.get("label") == window["label"]:
            windows[i] = window
            break
    else:
        windows.append(window)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="every hour's read over a snapshot")
    c.add_argument("--dataset", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--jobs", type=int, default=1)
    for name, helptext in (("report", "the fresh-arm record at one parameter set"),
                           ("grid", "every parameter cell, pooled"),
                           ("observer", "the command as it is used, every N hours"),
                           ("record", "write one window of the artefact the cards read")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("files", nargs="+")
        if name == "record":
            s.add_argument("--label", required=True)
            s.add_argument("--out", default=str(HISTORY_RESULT))
        if name != "observer":
            s.add_argument("--since", default=None,
                           help="arm only retest candles after this ISO time (UTC if no zone)")
        if name != "grid":
            d = PocRetestParams()
            s.add_argument("--atr-buffer", type=float, default=d.atr_buffer)
            s.add_argument("--retest-window", type=int, default=d.retest_window)
            s.add_argument("--min-net-r", type=float, default=d.min_net_r)
            s.add_argument("--max-stop-atr", type=float, default=d.max_stop_atr)
        if name == "grid":
            s.add_argument("--level", type=float, default=0.95)
            s.add_argument("--cells", default=None,
                           help="comma-list of buffer/window/min_r/max_stop cells")
        if name == "observer":
            s.add_argument("--every", type=int, default=24)
            s.add_argument("--forward", action="store_true",
                           help="the rule observe_setup applies now: arm only a "
                                "setup still takeable, scored from the read that "
                                "armed it (default: the rule as first shipped)")
    args = ap.parse_args(argv)
    if args.cmd == "collect":
        data = collect(args.dataset, args.jobs)
        Path(args.out).write_text(json.dumps(data))
        n = sum(len(per["confirmed"]) for by in data["reads"].values() for per in by.values())
        print(f"{data['dataset']}: {len(data['bars'])} symbols, {len(data['reads'])} read "
              f"combos, {n} confirmed reads -> {args.out}")
        return 0
    if args.cmd == "grid":
        if not 0.5 < args.level < 1.0:
            ap.error("--level is an interval coverage between 0.5 and 1")
        cells = [x.strip() for x in args.cells.split(",")] if args.cells else None
        print(grid(args.files, args.since, args.level, cells))
        return 0
    p = _params(args)
    if args.cmd == "observer":
        if args.every < 1:
            ap.error("--every is a number of hours, at least 1")
        print(observer_report(args.files, p, args.every, args.forward))
        return 0
    if args.cmd == "record":
        if not args.label.strip():
            ap.error("--label names the window, and a blank one names nothing")
        out = Path(args.out)
        write_record(out, record_window(args.files, p, args.label.strip(), args.since))
        back = history_on_record(out, root=_ROOT)
        print(f"{out}: {back.state}" + (f" ({back.reason})" if back.reason else ""))
        for w in back.windows:
            print(f"  {w.label}: armed {w.armed}, scored {w.scored}, mean "
                  f"{'n/a' if w.mean_r is None else f'{w.mean_r:+.2f}R'}, {w.verdict}")
        return 0 if back.state == "read" else 1
    print(report(args.files, p, args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
