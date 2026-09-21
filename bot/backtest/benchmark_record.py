"""The benchmark on record — a WRITTEN artefact the parity card reads.

``docs/FROZEN_BENCHMARK.md`` moved its headline twice (+0.31% / PF 1.14, then
+0.49% / PF 1.24) and then recorded, on 2026-09-11, that the same command on
the same frozen data reproduces −0.38% / PF 0.63 — and said in as many words:
"Re-baseline from a written file, not from a number typed into prose." The
parity card went on printing the FIRST of those three numbers as a string
literal for the whole of that history, under a sentence telling the reader
that a live PF under it means execution, not the strategy, is the leak. A
benchmark typed into a card is a claim; this module reads the file the
benchmark command writes.

``benchmark/majors_1h/result.json`` is produced by

    python -m bot.backtest.runner --dataset benchmark/majors_1h \\
        --honest --walk-forward 6 -o benchmark/majors_1h/result.json

and COMMITTED, because ``app/`` and ``bot/`` are different deploy targets and
a card that had to run a seventy-second backtest to learn the benchmark would
print nothing on the box that cannot run one. The reading is three-valued:

  read        the artefact parsed and carries every field the card needs
  none        no artefact on record — the sentence names the command that
              writes one, never a number
  unreadable  a file that is there and will not parse, or lacks a field —
              which is not "no benchmark", and is said as its own thing

A stale artefact is stated rather than hidden: the card prints the commit
and the date it was recorded at, and a guard pins the artefact's dataset
hash to the manifest's, so a benchmark measured on other data cannot be read
as this one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple, Optional

ROOT = Path(__file__).resolve().parents[2]
DATASET = "benchmark/majors_1h"
BENCHMARK_RESULT = ROOT / DATASET / "result.json"
MANIFEST = ROOT / DATASET / "manifest.json"

#: The one command that writes the artefact. The "none on record" sentence
#: quotes it, and docs/FROZEN_BENCHMARK.md names the same one — a test pins
#: the two spellings equal, because a sentence telling an operator what to
#: run is a claim about another surface.
WRITE_COMMAND = (f"python -m bot.backtest.runner --dataset {DATASET} "
                 f"--honest --walk-forward 6 -o {DATASET}/result.json")


class BenchmarkReading(NamedTuple):
    state: str                       # read | none | unreadable
    reason: str
    path: str
    dataset: Optional[str] = None
    dataset_hash: Optional[str] = None
    recorded_at: Optional[str] = None
    code_sha: Optional[str] = None
    folds_run: Optional[int] = None
    profitable_folds: Optional[int] = None
    mean_oos_return_pct: Optional[float] = None
    pooled_trades: Optional[int] = None
    pooled_wins: Optional[int] = None
    pooled_losses: Optional[int] = None
    pooled_net_usd: Optional[float] = None
    pooled_win_rate: Optional[float] = None
    pooled_pf: Optional[float] = None       # None with no losing trade (a ratio over nothing)
    pooled_mean_net_usd: Optional[float] = None
    universe: tuple = ()                    # symbol BASES the benchmark measured


_POOLED_FIELDS = ("trades", "wins", "losses", "net_usd", "win_rate", "mean_net_usd")


def profit_factor(nets: list[float]) -> Optional[float]:
    """Gross win over gross loss, or None when there is no losing trade: over
    nothing it is not a ratio. The console used to print ``inf`` there, which
    reads as a measurement of infinite edge, and the parity card printed the
    same ``inf`` one module over -- two copies of one arithmetic, so it lives
    here, the leaf the runner's pooled block and the card both import."""
    gross_win = sum(x for x in nets if x > 0)
    gross_loss = sum(-x for x in nets if x < 0)
    return (gross_win / gross_loss) if gross_loss > 0 else None


def symbol_base(symbol: str) -> str:
    """``"BTC/USDT:USDT"`` → ``"BTC"``; ``"BTCUSDT"`` → ``"BTC"``; ``"ETH/USD"``
    → ``"ETH"``. Live closed records and the benchmark universe spell the same
    market two ways, and the in-universe test has to read both."""
    s = str(symbol or "").strip().upper().split(":")[0]
    if "/" in s:
        return s.split("/")[0]
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v) -> Optional[int]:
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


def benchmark_on_record(path: Optional[Path] = None) -> BenchmarkReading:
    """Read the artefact. Never raises: the three states above are the answer,
    and a raise here would take the whole parity card down over its
    benchmark line."""
    p = Path(path) if path is not None else BENCHMARK_RESULT
    shown = str(p) if path is not None else str(p.relative_to(ROOT))
    if not p.exists():
        return BenchmarkReading("none", "no benchmark artefact on record", shown)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return BenchmarkReading("unreadable", f"could not parse ({type(exc).__name__})", shown)
    if not isinstance(d, dict):
        return BenchmarkReading("unreadable", "artefact is not an object", shown)
    if d.get("mode") != "portfolio_walk_forward":
        return BenchmarkReading("unreadable",
                                f"artefact mode is {d.get('mode')!r}, not a walk-forward", shown)
    pooled = d.get("pooled")
    if not isinstance(pooled, dict):
        return BenchmarkReading("unreadable",
                                "artefact carries no pooled block — regenerate it", shown)
    missing = [k for k in _POOLED_FIELDS if k not in pooled]
    if missing:
        # An artefact from a runner that predates part of the pooled block is
        # a file that parses and cannot answer the card's question -- and the
        # sentence names which part, because "no pooled block" over a block
        # that is there sends a reader to look for the wrong thing.
        return BenchmarkReading("unreadable",
                                f"artefact's pooled block lacks {', '.join(missing)} — "
                                "regenerate it", shown)
    source = str(d.get("data_source") or "")
    if not source.startswith("frozen_snapshot:"):
        return BenchmarkReading("unreadable",
                                f"artefact data_source is {source!r}, not a frozen snapshot",
                                shown)
    folds_run = _int(d.get("folds_run"))
    if folds_run is None:
        return BenchmarkReading("unreadable", "artefact carries no folds_run", shown)
    # The artefact is pinned to the snapshot BESIDE it. A result recorded off
    # a dataset that has since been re-frozen answers a question about data
    # no longer on disk, and a card comparing live against it would name a
    # benchmark that is not there -- the /vault hint shape pointed at a
    # dataset. A manifest nobody can read cannot pin anything, and that is
    # said rather than read as "pinned".
    recorded_hash = source.split(":", 1)[1]
    manifest_path = p.parent / "manifest.json"
    pinned_hash = manifest_hash(manifest_path)
    if pinned_hash is None:
        return BenchmarkReading(
            "unreadable",
            f"the snapshot manifest beside the artefact could not be read "
            f"({manifest_path.name}), so the artefact cannot be pinned to the dataset",
            shown)
    if pinned_hash != recorded_hash:
        return BenchmarkReading(
            "unreadable",
            f"artefact was recorded off snapshot {recorded_hash[:12]}, the manifest beside "
            f"it names {pinned_hash[:12]} — regenerate it",
            shown)
    universe_raw = d.get("universe")
    universe = universe_raw if isinstance(universe_raw, dict) else {}
    measured_raw = universe.get("measured")
    measured = measured_raw if isinstance(measured_raw, list) else []
    return BenchmarkReading(
        "read", "", shown,
        dataset=str(d.get("dataset") or DATASET),
        dataset_hash=recorded_hash,
        recorded_at=str(d.get("recorded_at")) if d.get("recorded_at") else None,
        code_sha=str(d.get("code_sha")) if d.get("code_sha") else None,
        folds_run=folds_run,
        profitable_folds=_int(d.get("profitable_folds")),
        mean_oos_return_pct=_num(d.get("mean_oos_return_pct")),
        pooled_trades=_int(pooled.get("trades")),
        pooled_wins=_int(pooled.get("wins")),
        pooled_losses=_int(pooled.get("losses")),
        pooled_net_usd=_num(pooled.get("net_usd")),
        pooled_win_rate=_num(pooled.get("win_rate")),
        pooled_pf=_num(pooled.get("pf")),
        pooled_mean_net_usd=_num(pooled.get("mean_net_usd")),
        universe=tuple(symbol_base(s) for s in measured),
    )


def manifest_hash(path: Optional[Path] = None) -> Optional[str]:
    """The frozen dataset's own hash — what the artefact beside it is pinned to,
    by ``benchmark_on_record`` and by the guard over the committed pair. None
    when the manifest is missing or will not parse: a pin nobody can read."""
    p = Path(path) if path is not None else MANIFEST
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("dataset_hash") or "") or None
    except (OSError, ValueError):
        return None


def card_line(r: BenchmarkReading) -> str:
    """The parity card's benchmark line, from the reading and nothing else."""
    if r.state == "none":
        return (f"  Benchmark: none on record ({r.path}) — write one with:\n"
                f"    {WRITE_COMMAND}")
    if r.state == "unreadable":
        return (f"  Benchmark: artefact on record could not be read — {r.reason} "
                f"({r.path}). That is not \"no benchmark\".")
    when = (r.recorded_at or "date not recorded")[:10]
    at = f"at {r.code_sha[:7]}" if r.code_sha else "commit not recorded"
    if not r.folds_run:
        return (f"  Benchmark on record ({r.dataset}, --honest, recorded {when} {at}): "
                f"NO FOLD RAN — nothing was measured, so no return is quoted.")
    pf = "— (no losing trade)" if r.pooled_pf is None else f"{r.pooled_pf:.2f}"
    mean = ("n/a" if r.mean_oos_return_pct is None
            else f"{r.mean_oos_return_pct:+.2f}%")
    win = "n/a" if r.pooled_win_rate is None else f"{r.pooled_win_rate:.0%}"
    net = "n/a" if r.pooled_net_usd is None else f"${r.pooled_net_usd:+,.2f}"
    return (f"  Benchmark on record ({r.dataset}, --honest, {r.folds_run} folds, recorded "
            f"{when} {at}): mean OOS {mean} · {r.profitable_folds}/{r.folds_run} folds "
            f"profitable · pooled {r.pooled_trades} tr  net {net}  win {win}  PF {pf}")
