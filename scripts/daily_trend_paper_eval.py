#!/usr/bin/env python3
"""Status and the one evaluation of the sealed daily trend paper forward test.

Design and rule: docs/research/prereg/daily_trend_paper_forward_2026-11/
(PREREGISTRATION.md, config.json, ADDENDUM_1.md).

    python3 scripts/daily_trend_paper_eval.py status              # counts only, any time
    python3 scripts/daily_trend_paper_eval.py evaluate --fetch    # once, after the close

``status`` reads the writer's chain and prints counts (days recorded, days
missed, episodes closed, weeks elapsed, whether the chain verifies, whether the
drawdown limit was breached, whether the window has closed). It never prints a
return, an equity or a drawdown figure.

``evaluate`` refuses while a result exists and before the window has closed.
With ``--fetch`` it fetches Bitget's public candles and funding once into the
study's ``inputs/`` and writes their sha256 to ``inputs/manifest.json`` before
any figure is computed; without it, it reads inputs already there and refuses
any whose bytes are not the manifest's. It replays the whole book from those
inputs with the rule at the pinned commit (the replay decides every figure),
checks the writer's chain against the replay, and writes ``result.json`` and
its sha256 once.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_trend_paper_book as bk  # noqa: E402

UTC = timezone.utc
RESULT = bk.STUDY / "result.json"
INPUTS = bk.STUDY / "inputs"
HEADS = bk.STUDY / "heads"
BOOT_DRAWS = 10_000
BOOT_SEED = 7
LOWER_PCT = 0.05
MATCH_TOL = 1e-6
COVERAGE_MIN = 0.95
MATCH_MIN = 0.99
ANCHOR_MIN = 0.95


# ── the close rule (shared by status and the replay) ───────────────────────

def close_day(study: bk.Study, closes_before: Callable[[int], int],
              breach_day: Optional[int], now_ms: int) -> Optional[tuple[int, str]]:
    """(close_ms, why) or None when the window is still open.

    The first daily mark whose drawdown exceeds the limit closes it as an
    early stop. Otherwise the first Monday 00:00 UTC at or after 26 weeks on
    which at least 150 episodes have closed, read at or before ``now``, or the
    52-week hard close."""
    scheduled: Optional[tuple[int, str]] = None
    monday = study.earliest_close
    while monday < study.hard_close and monday <= now_ms:
        if closes_before(monday) >= bk.MIN_CLOSED_EPISODES:
            scheduled = (monday, "minimum count reached")
            break
        monday += bk.WEEK_MS
    if scheduled is None and now_ms >= study.hard_close:
        scheduled = (study.hard_close, "hard close")
    if breach_day is not None and breach_day <= now_ms and (
            scheduled is None or breach_day < scheduled[0]):
        return breach_day, "drawdown limit"
    return scheduled


def first_breach(equities: list[tuple[int, float]]) -> Optional[int]:
    peak: Optional[float] = None
    for day, eq in equities:
        peak = eq if peak is None else max(peak, eq)
        if peak > 0 and (peak - eq) / peak > bk.MAX_DRAWDOWN:
            return day
    return None


def max_drawdown(equities: list[float]) -> float:
    peak = equities[0]
    worst = 0.0
    for eq in equities:
        peak = max(peak, eq)
        worst = max(worst, (peak - eq) / peak)
    return worst


# ── status: counts from the chain only ─────────────────────────────────────

def _first_by_day(entries: list[dict], event: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in entries:
        if e.get("event_type") != event:
            continue
        day = (e.get("payload") or {}).get("day")
        if isinstance(day, str) and day not in out:
            out[day] = e
    return out


def _day_ms(iso: str) -> int:
    return bk.parse_utc_ms(iso + "T00:00:00Z")


def covered(entry: Optional[dict], day_ms: int) -> bool:
    """Read on its own day, at or before the fill time: both the writer's
    ``read_at`` and the chain's own timestamp on the entry."""
    if entry is None:
        return False
    try:
        read = bk.parse_utc_ms(str((entry.get("payload") or {}).get("read_at")))
        stamped = bk.parse_utc_ms(str(entry.get("timestamp")))
    except (TypeError, ValueError):
        return False
    return all(day_ms <= t <= day_ms + bk.COVER_BY_MS for t in (read, stamped))


def chain_status(study: bk.Study, chain_file: Path, now_ms: int) -> dict:
    """Counts the chain supports, and nothing a return could be read from."""
    from bot.utils.audit_chain import AuditChain
    ok, problems = AuditChain.verify(str(chain_file)) if chain_file.is_file() else (False, ["no chain"])
    entries = bk.read_chain(chain_file)
    decisions = _first_by_day(entries, bk.DECISION)
    fills = _first_by_day(entries, bk.FILLS)
    end = min(now_ms, study.hard_close)
    # A day counts once its cover time has passed: at 00:20 today's decision
    # is not due yet, and is not "missed".
    window = [d for d in bk.days_between(study.window_open, end + 1)
              if study.window_open <= d < study.hard_close and d + bk.COVER_BY_MS <= now_ms]
    recorded = [d for d in window if bk.iso_day(d) in decisions]
    closes_by_day: list[int] = []
    equities: list[tuple[int, float]] = []
    for d in window:
        e = decisions.get(bk.iso_day(d))
        if e is None:
            continue
        p = e.get("payload") or {}
        eq = p.get("mark_equity")
        if isinstance(eq, (int, float)):
            equities.append((d, float(eq)))
        f = fills.get(bk.iso_day(d))
        if f is None:
            continue
        unfilled = set((f.get("payload") or {}).get("unfilled") or [])
        held, after = p.get("held") or {}, p.get("after") or {}
        for b, a in after.items():
            if a == 0.0 and held.get(b) not in (0.0, None) and b not in unfilled:
                closes_by_day.append(d)
    breach = first_breach(equities)

    def closes_before(ms: int) -> int:
        return sum(1 for d in closes_by_day if d < ms)

    closed = close_day(study, closes_before, breach, now_ms)
    published = {(e.get("payload") or {}).get("day") for e in entries
                 if e.get("event_type") == bk.HEAD_PUBLISHED}
    return {
        "window_opens": bk.iso_day(study.window_open),
        "weeks_elapsed": max(0, (end - study.window_open) // bk.WEEK_MS),
        "days_in_window_so_far": len(window),
        "days_recorded": len(recorded),
        "days_missed": len(window) - len(recorded),
        "days_covered_by_01_00": sum(1 for d in window if covered(decisions.get(bk.iso_day(d)), d)),
        "days_head_published": sum(1 for d in window if bk.iso_day(d) in published),
        "episodes_closed": closes_before(closed[0]) if closed else len(closes_by_day),
        "chain_verifies": bool(ok),
        "chain_problems": len(problems),
        "drawdown_limit_breached": breach is not None,
        "window_closed": closed is not None,
        "closes_on": bk.iso_day(closed[0]) if closed else None,
        "closed_because": closed[1] if closed else None,
    }


# ── inputs: fetched once, hashed before use ────────────────────────────────

def fetch_inputs(study: bk.Study, inputs: Path, now_ms: int,
                 get: Callable[[str], dict] = bk._get) -> dict:
    if (inputs / "manifest.json").exists():
        raise bk.Refused("inputs were already fetched; they are fetched once")
    start = study.window_open - bk.WARMUP_DAYS * bk.DAY_MS
    end = min(bk.day_floor(now_ms), study.hard_close) + bk.HOUR_MS
    files: dict[str, str] = {}
    for sym in study.symbols:
        rows = bk.clean_rows(bk.fetch_candles(sym, start, end, get), now_ms)
        funding = bk.fetch_funding(sym, study.window_open, get)
        p = bk.write_market_file(inputs, sym, rows, [f for f in funding if f[0] <= end])
        files[p.name] = bk.sha256_bytes(p.read_bytes())
    manifest = {"fetched_at": datetime.fromtimestamp(now_ms / 1000, tz=UTC).isoformat(),
                "source": "Bitget public v3 history-candles (1H) and v2 history-fund-rate",
                "files": files}
    (inputs / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def load_inputs(study: bk.Study, inputs: Path) -> tuple[bk.Data, dict]:
    mf = inputs / "manifest.json"
    if not mf.is_file():
        raise bk.Refused("no inputs/manifest.json: fetch the inputs first (--fetch)")
    manifest = json.loads(mf.read_text())
    for name, want in (manifest.get("files") or {}).items():
        got = bk.sha256_bytes((inputs / name).read_bytes())
        if got != want:
            raise bk.Refused(f"inputs/{name} is {got[:12]}, the manifest says {want[:12]}")
    return bk.load_market_files(inputs, study), manifest


# ── the replay ─────────────────────────────────────────────────────────────

@dataclass
class Replay:
    close_ms: int
    why: str
    book: bk.Book
    stress: bk.Book


def replay(study: bk.Study, data: bk.Data, rule_dir: Path, now_ms: int) -> Replay:
    data_end = max((int(m.rows[-1][0]) for m in data.values() if m.rows), default=0)
    last_day = min(bk.day_floor(data_end + bk.HOUR_MS), study.hard_close, bk.day_floor(now_ms))
    days = bk.days_between(study.window_open, last_day + bk.DAY_MS)
    targets = bk.rule_targets(data, study, days, rule_dir)
    full = bk.simulate(data, study, targets, days, study.costs, fill_last=False)
    breach = first_breach([(d.day, d.equity) for d in full.days])
    closes = [e.close_day for e in full.closed if e.close_day is not None]
    found = close_day(study, lambda ms: sum(1 for c in closes if c < ms), breach, last_day)
    if found is None:
        raise bk.Refused("the window has not closed on the replayed data")
    close_ms, why = found
    span = bk.days_between(study.window_open, close_ms + bk.DAY_MS)
    book = bk.simulate(data, study, targets, span, study.costs, fill_last=False)
    stress = bk.simulate(data, study, targets, span, study.costs.scaled(study.stress), fill_last=False)
    return Replay(close_ms, why, book, stress)


def weekly_lower_bound(episodes: list[bk.Episode], draws: int = BOOT_DRAWS,
                       seed: int = BOOT_SEED) -> Optional[float]:
    """The 5th percentile of the mean episode return over a bootstrap that
    resamples ISO weeks of episode close (config.json ``expectancy``)."""
    weeks: dict[tuple[int, int], list[float]] = {}
    for e in episodes:
        if e.close_day is None:
            continue
        iso = datetime.fromtimestamp(e.close_day / 1000, tz=UTC).isocalendar()
        weeks.setdefault((iso[0], iso[1]), []).append(e.ret)
    if not weeks:
        return None
    blocks = [weeks[k] for k in sorted(weeks)]
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        pick = [x for _ in blocks for x in rng.choice(blocks)]
        means.append(sum(pick) / len(pick))
    means.sort()
    return means[int(LOWER_PCT * draws)]


def _same(a: object, b: object) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(float(a) - float(b)) <= MATCH_TOL


def day_matches(entry: dict, day: bk.Day, bases: tuple[str, ...]) -> bool:
    p = entry.get("payload") or {}
    for key, ours in (("targets", day.targets), ("held", day.held), ("after", day.after)):
        theirs = p.get(key) or {}
        if any(not _same(theirs.get(b), ours.get(b)) for b in bases):
            return False
    return True


def heads_file_problems(heads_dir: Path, entries: list[dict]) -> list[str]:
    """Every committed weekly head (``<sequence> <sha256> ...``) must be the
    chain's entry at that sequence."""
    by_seq = {e.get("sequence"): e.get("entry_hash") for e in entries}
    problems = []
    for f in sorted(heads_dir.glob("*.txt")) if heads_dir.is_dir() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            if by_seq.get(int(parts[0])) != parts[1]:
                problems.append(f"{f.name}: #{parts[0]} is not in the chain as published")
    return problems


def _share(part: float, gross: float) -> Optional[float]:
    return part / gross if gross > 0 else None


def evaluate(study: bk.Study, chain_file: Path, data: bk.Data, manifest: dict, rule_dir: Path,
             now_ms: int, heads_dir: Path = HEADS) -> dict:
    from bot.utils.audit_chain import AuditChain
    rp = replay(study, data, rule_dir, now_ms)
    book, stress = rp.book, rp.stress
    window = [d for d in book.days if d.day < rp.close_ms]
    equities = [d.equity for d in book.days]
    equity_close = book.days[-1].equity
    rets = [e.ret for e in book.closed]
    n = len(rets)
    mean = statistics.fmean(rets) if rets else None
    lower = weekly_lower_bound(book.closed)
    stress_rets = [e.ret for e in stress.closed]
    stress_mean = statistics.fmean(stress_rets) if stress_rets else None
    mdd = max_drawdown(equities)

    # the record
    chain_ok, chain_problems = (AuditChain.verify(str(chain_file)) if chain_file.is_file()
                                else (False, ["no chain"]))
    entries = bk.read_chain(chain_file)
    decisions = _first_by_day(entries, bk.DECISION)
    published: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("event_type") == bk.HEAD_PUBLISHED:
            p = e.get("payload") or {}
            published.setdefault(str(p.get("day")), []).append(e)
    covered_days, matched_days, anchored_days = [], [], []
    mismatched: list[str] = []
    for d in window:
        iso = bk.iso_day(d.day)
        entry = decisions.get(iso)
        if covered(entry, d.day):
            covered_days.append(iso)
            if entry is not None and day_matches(entry, d, study.bases):
                matched_days.append(iso)
            else:
                mismatched.append(iso)
        for h in published.get(iso, []):
            stamp = str(h.get("timestamp") or "")
            seq = (h.get("payload") or {}).get("head_sequence")
            dec_seq = entry.get("sequence") if entry else None
            if (stamp[:10] == iso and isinstance(seq, int) and isinstance(dec_seq, int)
                    and seq >= dec_seq):
                anchored_days.append(iso)
                break
    head_problems = heads_file_problems(heads_dir, entries)
    nwin = len(window)
    coverage = len(covered_days) / nwin if nwin else 0.0
    match = len(matched_days) / len(covered_days) if covered_days else 0.0
    anchors = len(anchored_days) / nwin if nwin else 0.0
    record = {
        "chain_verifies": bool(chain_ok),
        "chain_problems": chain_problems[:20],
        "published_heads_in_chain": not head_problems,
        "published_head_problems": head_problems[:20],
        "live_coverage": coverage, "live_coverage_ok": coverage >= COVERAGE_MIN,
        "replay_match": match, "replay_match_ok": match >= MATCH_MIN,
        "anchor_coverage": anchors, "anchor_coverage_ok": anchors >= ANCHOR_MIN,
        "mismatched_days": mismatched,
    }
    record_ok = (record["chain_verifies"] and record["published_heads_in_chain"]
                 and record["live_coverage_ok"] and record["replay_match_ok"]
                 and record["anchor_coverage_ok"])

    criteria = {
        "expectancy_mean": mean,
        "expectancy_lower_5pct": lower,
        "expectancy_ok": mean is not None and lower is not None and mean > 0 and lower > 0,
        "stress_mean": stress_mean,
        "stress_ok": stress_mean is not None and stress_mean > 0,
        "max_drawdown": mdd,
        "max_drawdown_ok": mdd <= bk.MAX_DRAWDOWN,
        "book_equity_at_close": equity_close,
        "book_net_ok": equity_close > 1.0,
    }
    early = rp.why == "drawdown limit"
    if not record_ok:
        verdict, because = "NO VERDICT", "the record does not stand"
    elif early:
        verdict, because = "FAIL", "drawdown limit breached (early stop)"
    elif n < bk.MIN_CLOSED_EPISODES:
        verdict, because = "NO VERDICT", f"{n} closed episodes, {bk.MIN_CLOSED_EPISODES} needed"
    elif all(criteria[k] for k in ("expectancy_ok", "stress_ok", "max_drawdown_ok", "book_net_ok")):
        verdict, because = "PASS", "every criterion holds"
    else:
        misses = [k for k in ("expectancy_ok", "stress_ok", "max_drawdown_ok", "book_net_ok")
                  if not criteria[k]]
        verdict, because = "FAIL", "missed: " + ", ".join(misses)

    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    fees = sum(e.fees for e in book.closed)
    slip = sum(e.slippage for e in book.closed)
    fund = sum(e.funding for e in book.closed)
    net = sum(e.flows for e in book.closed)
    gross = net + fees + slip + fund
    by_market: dict[str, float] = {}
    for ep in book.closed:
        by_market[ep.base] = by_market.get(ep.base, 0.0) + ep.ret
    btc = data.get("BTC")
    btc_line: dict[str, Optional[float]] = {"return": None, "max_drawdown": None}
    if btc is not None:
        marks = [btc.last_close_before(d.day, d.day - bk.DAY_MS) for d in book.days]
        if all(m is not None for m in marks) and marks:
            vals = [float(m) for m in marks if m is not None]
            btc_line = {"return": vals[-1] / vals[0] - 1.0, "max_drawdown": max_drawdown(vals)}
    reported = {
        "closed_episodes": n,
        "open_episodes_at_close": len(book.open),
        "win_rate": len(wins) / n if n else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
        "median_holding_days": (statistics.median(
            (e.close_day - e.open_day) / bk.DAY_MS for e in book.closed if e.close_day is not None)
            if n else None),
        "fees_share_of_gross": _share(fees, gross),
        "slippage_share_of_gross": _share(slip, gross),
        "funding_share_of_gross": _share(fund, gross),
        "contribution_by_market": dict(sorted(by_market.items())),
        "btc_buy_and_hold": btc_line,
        "counted_out": {
            "days_missed": nwin - len([d for d in window if bk.iso_day(d.day) in decisions]),
            "days_not_covered": nwin - len(covered_days),
            "unread_market_days": sum(1 for d in window for t in d.targets.values() if t is None),
            "unfilled_trades": sum(len(d.unfilled) for d in window),
            "unpriced_targets": sum(len(d.unpriced) for d in window),
            "funding_priced_from_an_earlier_close": book.funding_carried,
            "marks_carried": book.marks_missing,
        },
    }
    return {
        "study": "daily_trend_paper_forward_2026-11",
        "config_sha256": bk.CONFIG_SHA256,
        "rule_commit": study.commit,
        "window": {"opened": bk.iso_day(study.window_open), "closed": bk.iso_day(rp.close_ms),
                   "closed_because": rp.why, "decision_days": nwin},
        "inputs_manifest": manifest,
        "verdict": verdict,
        "because": because,
        "criteria": criteria,
        "record": record,
        "reported_deciding_nothing": reported,
    }


def write_result(result: dict, path: Path = RESULT) -> str:
    if path.exists():
        raise bk.Refused(f"{path.name} exists; the evaluation runs once")
    body = (json.dumps(result, indent=1, sort_keys=True) + "\n").encode()
    path.write_bytes(body)
    digest = bk.sha256_bytes(body)
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")
    return digest


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status")
    st.add_argument("--chain", type=Path, default=None)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--chain", type=Path, default=None)
    ev.add_argument("--fetch", action="store_true")
    args = ap.parse_args(argv)
    chain_file = args.chain or bk.chain_path()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    try:
        study = bk.study_from(bk.load_config())
        status = chain_status(study, chain_file, now_ms)
        if args.cmd == "status":
            for k, v in status.items():
                print(f"{k}: {v}")
            return 0
        if RESULT.exists():
            raise bk.Refused("result.json exists; the evaluation runs once")
        if not status["window_closed"] and now_ms < study.hard_close:
            raise bk.Refused("the window has not closed (status)")
        if args.fetch:
            fetch_inputs(study, INPUTS, now_ms)
        data, manifest = load_inputs(study, INPUTS)
        with bk.pinned_rule(study) as rule_dir:
            result = evaluate(study, chain_file, data, manifest, rule_dir, now_ms)
        digest = write_result(result)
        print(f"{result['verdict']}: {result['because']} (result.json sha256 {digest})")
        return 0
    except bk.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
