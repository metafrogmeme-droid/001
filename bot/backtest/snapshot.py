"""Fixed cached benchmark dataset — fetch the A/B universe ONCE, then freeze it.

The honest benchmark (``runner --symbols ... --honest --walk-forward 6``) fetches
~6000 FRESH bars per symbol at run time, anchored to the exchange clock. Two runs
minutes apart therefore see DIFFERENT data windows, and ordinary run-to-run
variance (~0.5pp of return) swamps the small effects most signal/money A/Bs try to
measure. You cannot attribute a delta to a code change when the data underneath it
moved between the two runs.

This module freezes the universe into a versioned on-disk snapshot so every A/B
arm reads byte-identical candles:

  * ``snapshot_dataset(symbols, timeframe, limit, out_dir)`` fetches once and
    writes ``<out_dir>/<safe_symbol>.csv.gz`` plus a ``manifest.json`` recording,
    per symbol, the bar count / first+last timestamp / content sha256, and an
    overall ``dataset_hash`` over the whole universe.
  * ``load_symbol`` / ``load_dataset`` read them back; ``verify_dataset``
    recomputes every hash and flags any drift.
  * The runner's ``--dataset DIR`` flag routes every fetch through ``load_symbol``
    instead of the live exchange, and stamps the ``dataset_hash`` into the saved
    result so a run is self-describing about *which* frozen data it measured.

Committed under ``benchmark/``, the snapshot survives container rebuilds, so a
fresh cloud sandbox runs the IDENTICAL benchmark instead of silently re-fetching a
new window and quietly changing the answer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from bot.compat import UTC
from pathlib import Path

from bot.backtest.data_loader import DataLoader
from bot.backtest.models import BacktestBar

MANIFEST_NAME = "manifest.json"
SNAPSHOT_VERSION = 1

# The canonical honest-benchmark universe: the 10 liquid majors the A/B harness
# runs on, as Bitget USDT-M perps. Keeping this here means `python -m
# bot.backtest.snapshot` with no --symbols reproduces the exact benchmark set.
DEFAULT_BENCHMARK_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
    "DOGE/USDT:USDT", "ADA/USDT:USDT", "LINK/USDT:USDT", "AVAX/USDT:USDT",
    "LTC/USDT:USDT", "BNB/USDT:USDT",
]
# Where the frozen snapshots live, in preference order.
#
# `benchmark/` is the committed home. `data/benchmark/` was the original one,
# and the move is not cosmetic: on a deployed box `data/` is a SYMLINK into
# ~/runeclaw-persist/, and git will not traverse a symlink. So the negation in
# .gitignore — `!data/benchmark/`, written to let the snapshot be committed —
# could never fire there, and `git add -f` under it silently does nothing. The
# snapshot was not forgotten; it was unreachable, and every fresh clone
# (including CI) got a repository whose own benchmark data could not exist.
#
# The two requirements were contradictory in one directory. Everything else
# under data/ is runtime state that must NEVER be committed, which is exactly
# why the symlink is there; the frozen benchmark is a version-controlled build
# INPUT that must always be. Splitting them resolves it and leaves deploy.sh —
# and the 19 tests pinning its state-preservation layers — untouched.
#
# The old location stays as a fallback so a box that has not yet moved its
# copy keeps working, and so the move needs no flag-day ordering.
BENCHMARK_DIR_NAMES = ("benchmark", "data/benchmark")
DEFAULT_BENCHMARK_NAME = "majors_1h"


def benchmark_root() -> Path:
    """The directory holding the frozen snapshots, resolved at call time.

    Relative, matching every other path in this codebase (all of them are
    CWD-relative and correct only because the bot is launched from the repo
    root — which bot/utils/state_guard.py now checks rather than assumes).

    Returns the preferred name when neither exists, so an error message names
    where the data SHOULD be rather than where it used to be.
    """
    for name in BENCHMARK_DIR_NAMES:
        if Path(name).is_dir():
            return Path(name)
    return Path(BENCHMARK_DIR_NAMES[0])


def default_benchmark_dir() -> str:
    """The canonical majors snapshot path, resolved at call time."""
    return str(benchmark_root() / DEFAULT_BENCHMARK_NAME)


# Legacy constant, kept because it is part of this module's public surface.
# Prefer default_benchmark_dir(): this one cannot see which location exists.
DEFAULT_BENCHMARK_DIR = "benchmark/majors_1h"


def safe_symbol(symbol: str) -> str:
    """Filesystem-safe stem for a symbol: ``BTC/USDT:USDT`` -> ``BTC_USDT_USDT``."""
    return symbol.replace("/", "_").replace(":", "_")


def dataset_hash(per_symbol_hashes: dict[str, str]) -> str:
    """Deterministic hash over the whole universe: sha256 of the sorted
    ``symbol=content_hash`` lines. Independent of dict order and of file
    layout, so it changes iff some symbol's candles changed."""
    h = hashlib.sha256()
    for sym in sorted(per_symbol_hashes):
        h.update(f"{sym}={per_symbol_hashes[sym]}\n".encode())
    return h.hexdigest()


def _manifest_path(out_dir: str | Path) -> Path:
    return Path(out_dir) / MANIFEST_NAME


def missing_timestamps(bars: list[BacktestBar], timeframe: str) -> list[str]:
    """The open times of bars absent between a series' first bar and its last.

    A hole is read across by every backtest as if the hour never happened, so
    the manifest names it. Bitget's own outages leave real holes; the pager's
    dropped page boundary left six per symbol in every dataset frozen before
    it started each page at the last bar held.
    """
    from bot.backtest.data_loader import missing_bars
    from bot.utils.candles import timeframe_to_ms
    step = timeframe_to_ms(timeframe)
    if step <= 0:
        raise ValueError(f"unreadable timeframe {timeframe!r}")
    rows = [[int(b.timestamp.timestamp() * 1000)] for b in bars]
    return [datetime.fromtimestamp(ms / 1000, UTC).isoformat()
            for ms in missing_bars(rows, step)]


def build_manifest(
    out_dir: str | Path,
    timeframe: str,
    limit: int,
    per_symbol: dict[str, list[BacktestBar]],
    *,
    created_at: datetime | None = None,
) -> dict:
    """Assemble (but do not write) the manifest for a set of fetched bars."""
    entries: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    for sym, bars in per_symbol.items():
        chash = DataLoader.content_hash(bars)
        hashes[sym] = chash
        srt = sorted(bars, key=lambda b: b.timestamp)
        entries[sym] = {
            "file": f"{safe_symbol(sym)}.csv.gz",
            "bars": len(bars),
            "first": srt[0].timestamp.isoformat() if srt else None,
            "last": srt[-1].timestamp.isoformat() if srt else None,
            "sha256": chash,
            "missing": missing_timestamps(srt, timeframe),
        }
    return {
        "version": SNAPSHOT_VERSION,
        "timeframe": timeframe,
        "limit": limit,
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
        "dataset_hash": dataset_hash(hashes),
        "symbols": dict(sorted(entries.items())),
    }


async def snapshot_dataset(
    symbols: list[str],
    timeframe: str,
    limit: int,
    out_dir: str | Path,
    *,
    min_bars: int = 220,
    created_at: datetime | None = None,
) -> dict:
    """Fetch each symbol once from Bitget and freeze it under ``out_dir``.

    Writes one gzipped CSV per symbol plus ``manifest.json``. A symbol that
    returns fewer than ``min_bars`` is skipped with a warning rather than
    poisoning the dataset with a stub. Returns the written manifest.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    per_symbol: dict[str, list[BacktestBar]] = {}
    for sym in symbols:
        try:
            bars = await DataLoader.from_bitget(symbol=sym, timeframe=timeframe, limit=limit)
        except Exception as exc:  # noqa: BLE001 — network/venue errors are per-symbol
            print(f"  ✗ {sym}: fetch failed ({exc}) — skipped")
            continue
        if len(bars) < min_bars:
            print(f"  ✗ {sym}: only {len(bars)} bars (< {min_bars}) — skipped")
            continue
        path = out / f"{safe_symbol(sym)}.csv.gz"
        DataLoader.save_csv(bars, str(path))
        per_symbol[sym] = bars
        holes = missing_timestamps(sorted(bars, key=lambda b: b.timestamp), timeframe)
        note = f" ({len(holes)} missing, named in the manifest)" if holes else ""
        print(f"  ✓ {sym}: {len(bars)} bars -> {path.name}{note}")

    if not per_symbol:
        raise RuntimeError("no symbols fetched — refusing to write an empty snapshot")

    manifest = build_manifest(out, timeframe, limit, per_symbol, created_at=created_at)
    _manifest_path(out).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  manifest: {len(per_symbol)} symbols, dataset_hash={manifest['dataset_hash'][:12]}…")
    return manifest


#: How a repaired bar was read, recorded beside it in the manifest.
REPAIR_SOURCE = ("the venue's 1h candles for the missing hour and the hour either "
                 "side; filled only when both neighbours equal the stored bars")


async def repair_dataset(out_dir: str | Path, fetch, *, repaired_at: datetime | None = None) -> dict:
    """Fill the bars a frozen dataset's manifest names as missing, in its own window.

    A RE-FREEZE IS A DIFFERENT DATASET. The freezer reads up to now, so running
    it again replaces the window, and every card measured on it moves because
    the market moved, not because the holes were filled. The holes the old
    pager left (six a symbol, every dataset frozen before it started each page
    at the last bar held) are filled here instead, inside the window the
    dataset already has.

    ``fetch(symbol, since_ms, until_ms)`` returns ccxt rows. For each missing
    hour it is asked from two hours before it to two hours after. Bitget's
    ``until`` is exclusive on both its candle endpoints; its history candles
    include the bar at ``since`` and its recent candles (about the last two
    months) do not, so asking from one hour before lost the neighbour on every
    recent hole. Under either answer this window holds the hour before, the
    hour, and the hour after. The hour is filled
    only when both neighbours come back EQUAL to the stored bars, every field,
    volume included: that is the venue's history agreeing with what was frozen,
    and it is the only evidence that the missing bar belongs to the same
    series. A hole whose neighbours differ, or that the venue does not answer,
    stays named in ``missing``. Each filled hour is listed under ``repaired``,
    and ``created_at`` is kept: the window's freeze time has not changed.
    """
    from bot.utils.candles import timeframe_to_ms
    out = Path(out_dir)
    man = load_manifest(out)
    step = timeframe_to_ms(str(man.get("timeframe") or ""))
    if step <= 0:
        raise ValueError(f"unreadable timeframe in {out_dir}")
    hashes: dict[str, str] = {}
    filled_total = 0
    for sym, entry in man.get("symbols", {}).items():
        holes = list(entry.get("missing") or [])
        if not holes:
            hashes[sym] = entry["sha256"]
            continue
        path = out / entry["file"]
        bars = DataLoader.from_csv(str(path))
        by_ms = {int(b.timestamp.timestamp() * 1000): b for b in bars}
        filled: list[str] = []
        for ts in holes:
            at = int(datetime.fromisoformat(ts).timestamp() * 1000)
            rows = {int(r[0]): r for r in await fetch(sym, at - 2 * step, at + 2 * step)}

            def same(ms: int) -> bool:
                b, r = by_ms.get(ms), rows.get(ms)
                return (b is not None and r is not None
                        and (b.open, b.high, b.low, b.close, b.volume)
                        == tuple(float(x) for x in r[1:6]))

            if at in rows and same(at - step) and same(at + step):
                new = DataLoader.from_ohlcv_list([rows[at]], sym)[0]
                by_ms[at] = new
                filled.append(ts)
        if filled:
            series = [by_ms[k] for k in sorted(by_ms)]
            DataLoader.save_csv(series, str(path))
            entry["bars"] = len(series)
            entry["sha256"] = DataLoader.content_hash(series)
            entry["missing"] = missing_timestamps(series, str(man["timeframe"]))
            entry["repaired"] = sorted(set(entry.get("repaired") or []) | set(filled))
            filled_total += len(filled)
        hashes[sym] = entry["sha256"]
        print(f"  {'✓' if not entry['missing'] else '!'} {sym}: filled {len(filled)} of "
              f"{len(holes)}" + (f", {len(entry['missing'])} still missing" if entry["missing"] else ""))
    if filled_total:
        man["dataset_hash"] = dataset_hash(hashes)
        man.setdefault("repairs", []).append({
            "at": (repaired_at or datetime.now(UTC)).isoformat(),
            "filled": filled_total,
            "source": REPAIR_SOURCE,
        })
        _manifest_path(out).write_text(json.dumps(man, indent=2) + "\n")
    print(f"  {filled_total} bar(s) filled; dataset_hash={man['dataset_hash'][:12]}…")
    return man


def bitget_window_fetch(exchange, timeframe: str):
    """A `repair_dataset` fetch over a ccxt Bitget client: the bars from
    ``since`` up to ``until``, one call (a repair asks for four hours at most)."""
    async def fetch(symbol: str, since: int, until: int) -> list:
        return await exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=4,
                                          params={"until": until})
    return fetch


def load_manifest(out_dir: str | Path) -> dict:
    """Read the snapshot manifest. Raises FileNotFoundError if the dir is not
    a snapshot (so a mistyped --dataset path fails loudly, not silently)."""
    p = _manifest_path(out_dir)
    if not p.exists():
        raise FileNotFoundError(f"no {MANIFEST_NAME} in {out_dir} — not a benchmark snapshot")
    man = json.loads(p.read_text())
    if not isinstance(man, dict):
        raise ValueError(f"malformed manifest in {out_dir}: expected a JSON object")
    return man


def load_symbol(out_dir: str | Path, symbol: str, manifest: dict | None = None) -> list[BacktestBar]:
    """Load one symbol's frozen bars. Raises KeyError if the symbol is not in
    the snapshot — a --dataset run must never silently fall back to live data
    for a symbol it does not have."""
    man = manifest or load_manifest(out_dir)
    entry = man.get("symbols", {}).get(symbol)
    if entry is None:
        raise KeyError(
            f"symbol {symbol!r} not in snapshot {out_dir} "
            f"(have: {', '.join(sorted(man.get('symbols', {})))})"
        )
    bars = DataLoader.from_csv(str(Path(out_dir) / entry["file"]))
    for b in bars:
        b.symbol = symbol
    return bars


def load_dataset(out_dir: str | Path) -> dict[str, list[BacktestBar]]:
    """Load every symbol in the snapshot, keyed by symbol."""
    man = load_manifest(out_dir)
    return {sym: load_symbol(out_dir, sym, man) for sym in man.get("symbols", {})}


def split_dataset_arg(dataset: str) -> list[str]:
    """Split a `--dataset` CLI value into one or more snapshot directories.
    Comma-separated combines multiple frozen snapshots into one universe (e.g.
    `majors_1h,alts_1h`) for a combined-universe A/B — a single dir is just a
    one-element list, so every caller can use the multi-aware functions below
    unconditionally."""
    return [d.strip() for d in str(dataset).split(",") if d.strip()]


def load_manifest_multi(dataset: str) -> dict:
    """Load and merge manifests across one or more comma-separated snapshot
    dirs. A symbol present in more than one dir is ambiguous and raises —
    silently picking one would make the A/B depend on dict iteration order.
    Each merged symbol entry carries ``_dir`` so `load_symbol_multi` knows
    which snapshot to read it from. The merged `dataset_hash` is the same
    order-independent function as a single snapshot's, so it changes iff the
    underlying candles change (across any of the combined dirs)."""
    dirs = split_dataset_arg(dataset)
    merged: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    for d in dirs:
        man = load_manifest(d)
        for sym, entry in man.get("symbols", {}).items():
            if sym in merged:
                raise ValueError(
                    f"symbol {sym!r} present in multiple dataset dirs "
                    f"({merged[sym]['_dir']!r} and {d!r}) — ambiguous combined universe")
            e = dict(entry)
            e["_dir"] = d
            merged[sym] = e
            hashes[sym] = entry["sha256"]
    if not merged:
        raise FileNotFoundError(f"no symbols found across dataset dirs: {dirs}")
    return {
        "version": SNAPSHOT_VERSION,
        "dataset_hash": dataset_hash(hashes),
        "symbols": dict(sorted(merged.items())),
        "_dirs": dirs,
    }


def load_symbol_multi(dataset: str, symbol: str, manifest: dict | None = None) -> list[BacktestBar]:
    """Load one symbol from a (possibly multi-dir) `--dataset` value."""
    man = manifest or load_manifest_multi(dataset)
    entry = man.get("symbols", {}).get(symbol)
    if entry is None:
        raise KeyError(
            f"symbol {symbol!r} not in dataset {dataset!r} "
            f"(have: {', '.join(sorted(man.get('symbols', {})))})")
    return load_symbol(entry["_dir"], symbol)


def load_dataset_multi(dataset: str) -> dict[str, list[BacktestBar]]:
    """Load every symbol across a (possibly multi-dir) `--dataset` value."""
    man = load_manifest_multi(dataset)
    return {sym: load_symbol_multi(dataset, sym, man) for sym in man["symbols"]}


def verify_dataset(out_dir: str | Path) -> tuple[bool, list[str]]:
    """Recompute every content hash and the dataset hash from the files on disk
    and compare to the manifest. Returns ``(ok, problems)``. This is the
    integrity gate: it proves the committed candles are exactly what the
    manifest claims, so an A/B can trust the ``dataset_hash`` it stamps."""
    problems: list[str] = []
    try:
        man = load_manifest(out_dir)
    except FileNotFoundError as exc:
        return False, [str(exc)]

    recomputed: dict[str, str] = {}
    for sym, entry in man.get("symbols", {}).items():
        fpath = Path(out_dir) / entry["file"]
        if not fpath.exists():
            problems.append(f"{sym}: file {entry['file']} missing")
            continue
        bars = DataLoader.from_csv(str(fpath))
        chash = DataLoader.content_hash(bars)
        recomputed[sym] = chash
        if len(bars) != entry.get("bars"):
            problems.append(f"{sym}: bar count {len(bars)} != manifest {entry.get('bars')}")
        if chash != entry.get("sha256"):
            problems.append(f"{sym}: content hash drifted from manifest")
        if "missing" in entry:
            holes = missing_timestamps(sorted(bars, key=lambda b: b.timestamp),
                                       str(man.get("timeframe") or ""))
            if holes != entry["missing"]:
                problems.append(f"{sym}: missing bars differ from manifest")
        if entry.get("repaired"):
            held = {b.timestamp.isoformat() for b in bars}
            absent = [ts for ts in entry["repaired"] if ts not in held]
            if absent:
                problems.append(f"{sym}: {len(absent)} repaired bar(s) not in the file")

    if recomputed:
        recomputed_ds = dataset_hash(recomputed)
        if recomputed_ds != man.get("dataset_hash"):
            problems.append("dataset_hash drifted from manifest")
    return (not problems), problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze a benchmark OHLCV dataset for reproducible A/B testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Freeze the canonical honest-benchmark universe (10 majors, 6000x 1h bars):
  python -m bot.backtest.snapshot --limit 6000 --out benchmark/majors_1h

  # Verify a committed snapshot's integrity (no fetch):
  python -m bot.backtest.snapshot --verify --out benchmark/majors_1h

  # Then run any A/B against it — both arms read byte-identical data:
  python -m bot.backtest.runner --dataset benchmark/majors_1h \\
      --symbols BTC/USDT:USDT,ETH/USDT:USDT --honest --walk-forward 6
        """,
    )
    parser.add_argument("--symbols", type=str, default="",
                        help="Comma-separated symbols (default: the 10 benchmark majors)")
    parser.add_argument("--timeframe", type=str, default="1h", help="Candle timeframe (default: 1h)")
    parser.add_argument("--limit", type=int, default=6000, help="Bars per symbol (default: 6000)")
    _default_out = default_benchmark_dir()
    parser.add_argument("--out", type=str, default=_default_out,
                        help=f"Output directory (default: {_default_out})")
    parser.add_argument("--min-bars", type=int, default=220,
                        help="Skip a symbol returning fewer than this many bars (default: 220)")
    parser.add_argument("--verify", action="store_true",
                        help="Verify an existing snapshot's integrity instead of fetching")
    parser.add_argument("--repair", action="store_true",
                        help="Fill the bars the manifest names as missing, in the "
                             "snapshot's own window, instead of fetching a new one")
    return parser


async def _repair_from_bitget(out_dir: str) -> dict:
    import ccxt.async_support as ccxt
    # Public candles from the live venue, never the sandbox (see `from_bitget`).
    exchange = ccxt.bitget({"sandbox": False, "enableRateLimit": True,
                            "aiohttp_trust_env": True})
    try:
        tf = str(load_manifest(out_dir).get("timeframe") or "")
        return await repair_dataset(out_dir, bitget_window_fetch(exchange, tf))
    finally:
        await exchange.close()


def main() -> None:
    args = build_parser().parse_args()

    if args.verify:
        ok, problems = verify_dataset(args.out)
        if ok:
            man = load_manifest(args.out)
            print(f"  ✓ snapshot OK: {len(man.get('symbols', {}))} symbols, "
                  f"dataset_hash={man.get('dataset_hash', '')[:12]}…")
            sys.exit(0)
        print("  ✗ snapshot FAILED verification:")
        for p in problems:
            print(f"      - {p}")
        sys.exit(1)

    if args.repair:
        asyncio.run(_repair_from_bitget(args.out))
        ok, problems = verify_dataset(args.out)
        for p in problems:
            print(f"      - {p}")
        sys.exit(0 if ok else 1)

    symbols = ([s.strip() for s in args.symbols.split(",") if s.strip()]
               or list(DEFAULT_BENCHMARK_SYMBOLS))
    print(f"\n  Freezing {len(symbols)} symbols x {args.limit} {args.timeframe} "
          f"bars -> {args.out}")
    asyncio.run(snapshot_dataset(
        symbols, args.timeframe, args.limit, args.out, min_bars=args.min_bars))


if __name__ == "__main__":
    main()
