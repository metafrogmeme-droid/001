"""What the POC-retest setup paid when it was replayed on frozen data.

`/pocretest` shows a confirmed setup with an entry, a stop, a target and a
verdict that the levels clear the R floor, and `/pocshadow` prints what the
setups the command recorded went on to pay. Neither said the one thing an
operator deciding whether to take the setup most needs: replayed over
seventeen months of frozen 1h snapshots, every closed bar, through the same
read and the same scorer, the setup averaged +0.03R a trade after fees, with
an interval straddling zero, and −0.39R on the window after the discovery
data. That measurement lived in `docs/FROZEN_BENCHMARK.md` and nowhere a card
could reach it.

A figure typed into a card is a claim; the parity card printed a benchmark
its own record had retracted twice because it did exactly that. So the replay
WRITES an artefact (`scripts/poc_retest_replay.py record`) and this module
reads it, the discipline `bot.backtest.benchmark_record` already sets:

  read          every window parses, is pinned to the snapshot it was read
                off, and was measured at the parameters and fees the live
                read uses now
  none          no artefact on record -- said, never read as "no edge"
  unreadable    a file that is there and cannot answer -- a parse failure, a
                missing field, a snapshot whose manifest no longer matches the
                hash the replay recorded, or a verdict word that disagrees
                with its own interval. Not "no history".
  other_params  a readable artefact measured at other parameters or other
                fees than the live read's. It is a true measurement of a
                DIFFERENT setup, so it is named and never printed as this
                one's, because a record at a 0.10 ATR buffer says nothing
                about a read at 0.25.

The verdict word is decided by `replay_verdict` -- one rule the writer and
this reader both ask -- over a (dataset, ISO week) cluster interval, because
setups on correlated symbols in one week are one market move, not several
samples. No dollar figure is carried: an outcome is denominated in its own
risk, which is what makes symbols comparable at all.
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, NamedTuple, Optional

from bot.backtest.benchmark_record import manifest_hash
from bot.core.poc_retest import PocRetestParams
from bot.core.poc_retest_record import MIN_SCORED_SETUPS, OUTCOMES
from bot.core.trade_costs import entry_rate_pct, exit_rate_pct

ROOT = Path(__file__).resolve().parents[2]
HISTORY_RESULT = ROOT / "benchmark" / "poc_retest" / "result.json"
KIND = "poc_retest_replay"
VERDICTS = ("survives", "does_not", "no_edge", "thin")


def replay_verdict(n_scored: int, interval: Optional[tuple[float, float]],
                   min_scored: int = MIN_SCORED_SETUPS) -> str:
    """The one rule for the replay's word: the whole interval clear of zero.

    ``thin`` is a floor unmet OR no interval at all (the cluster bootstrap
    answers none under five week clusters); ``no_edge`` is a floor met and an
    interval that reaches both sides. Those are different facts -- too few to
    say, and enough to say there is nothing measurable -- and the parity card
    draws the same line.
    """
    if n_scored < min_scored or interval is None:
        return "thin"
    lo, hi = interval
    if lo > 0:
        return "survives"
    if hi < 0:
        return "does_not"
    return "no_edge"


def live_settings() -> dict[str, Any]:
    """The parameters and fee rates the live read prices a setup with now.

    `observe_setup` reads with `PocRetestParams()` and `setup_verdict` prices
    the entry as a taker order, so these are what a replay has to have been
    measured at for its figure to describe the card's setup.
    """
    return {"params": asdict(PocRetestParams()),
            "fees": {"entry_pct": entry_rate_pct(None), "exit_pct": exit_rate_pct()}}


class HistoryWindow(NamedTuple):
    label: str
    since: Optional[str]
    recorded_at: Optional[str]
    code_sha: Optional[str]
    datasets: tuple[str, ...]
    first_retest: Optional[str]
    last_retest: Optional[str]
    armed: int
    scored: int
    n_target: int
    n_stop: int
    mean_r: Optional[float]
    interval: Optional[tuple[float, float]]
    clusters: Optional[int]
    verdict: str


class HistoryReading(NamedTuple):
    state: str                  # read | none | unreadable | other_params
    reason: str
    path: str
    windows: tuple[HistoryWindow, ...] = ()


def _count(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _real(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _text(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def _same(a: Any, b: Any) -> bool:
    """Parameter equality that a float's JSON round trip cannot defeat."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9)
    return bool(a == b)


def _settings_differ(recorded: dict, live: dict) -> Optional[str]:
    """Which recorded setting differs from the live one, or None."""
    for group in ("params", "fees"):
        rec, now = recorded.get(group), live[group]
        if not isinstance(rec, dict):
            return f"it carries no {group}"
        for key, want in now.items():
            if key not in rec:
                return f"it does not record {key}"
            if not _same(rec[key], want):
                return f"{key} {rec[key]!r} where the live read uses {want!r}"
    return None


def _window(w: Any, root: Path) -> tuple[Optional[HistoryWindow], str]:
    """One window, or the reason it cannot be read. Pins before anything
    else is trusted: a figure off data no longer on disk answers nothing."""
    if not isinstance(w, dict):
        return None, "a window is not an object"
    label = _text(w.get("label"))
    if label is None:
        return None, "a window carries no label"
    datasets = w.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        return None, f"window {label!r} names no snapshot"
    names = []
    for d in datasets:
        name = _text(d.get("dataset")) if isinstance(d, dict) else None
        recorded = _text(d.get("dataset_hash")) if isinstance(d, dict) else None
        if name is None or recorded is None:
            return None, f"window {label!r} names a snapshot without its hash"
        pinned = manifest_hash(root / name / "manifest.json")
        if pinned is None:
            return None, (f"the manifest of {name} could not be read, so window "
                          f"{label!r} cannot be pinned to it")
        if pinned != recorded:
            return None, (f"window {label!r} was read off {name} at {recorded[:12]}, and "
                          f"the manifest there now names {pinned[:12]}")
        names.append(name)
    outcomes = w.get("outcomes")
    if not isinstance(outcomes, dict):
        return None, f"window {label!r} carries no outcome counts"
    armed, scored = _count(w.get("armed")), _count(w.get("scored"))
    counts: dict[str, int] = {}
    for key in OUTCOMES:
        n = _count(outcomes.get(key))
        if n is None:
            armed = None
            break
        counts[key] = n
    if armed is None or scored is None:
        return None, f"window {label!r} carries a count that is not a count"
    if scored > armed or sum(counts.values()) != armed:
        return None, f"window {label!r}'s counts do not add up to its armed setups"
    mean_r = _real(w.get("mean_r"))
    if (mean_r is None) != (scored == 0):
        return None, f"window {label!r}'s mean does not match its scored count"
    raw = w.get("interval")
    interval: Optional[tuple[float, float]] = None
    if raw is not None:
        lo = _real(raw[0]) if isinstance(raw, list) and len(raw) == 2 else None
        hi = _real(raw[1]) if isinstance(raw, list) and len(raw) == 2 else None
        if lo is None or hi is None or lo > hi:
            return None, f"window {label!r}'s interval is not an interval"
        interval = (lo, hi)
    clusters = _count(w.get("clusters"))
    if (interval is None) != (clusters is None):
        # A cluster interval without the count of clusters it was drawn over
        # is half a statement, and a count with no interval is a sample size
        # of nothing: the card would print a verdict whose interval it cannot
        # show, or a count of clusters beside no interval.
        return None, f"window {label!r} carries an interval without its cluster count"
    verdict = w.get("verdict")
    if verdict not in VERDICTS:
        return None, f"window {label!r} carries a verdict word this build cannot place"
    if verdict != replay_verdict(scored, interval):
        return None, (f"window {label!r} says {verdict!r} where its own interval "
                      f"and count say {replay_verdict(scored, interval)!r}")
    return HistoryWindow(
        label=label, since=_text(w.get("since")), recorded_at=_text(w.get("recorded_at")),
        code_sha=_text(w.get("code_sha")), datasets=tuple(names),
        first_retest=_text(w.get("first_retest")), last_retest=_text(w.get("last_retest")),
        armed=armed, scored=scored, n_target=counts["target"],
        n_stop=counts["stop"], mean_r=mean_r, interval=interval,
        clusters=clusters, verdict=str(verdict)), ""


def history_on_record(path: Optional[Path] = None,
                      root: Optional[Path] = None) -> HistoryReading:
    """Read the artefact. Never raises: a raise here would take a card down
    over a line about the past."""
    p = Path(path) if path is not None else HISTORY_RESULT
    base = Path(root) if root is not None else ROOT
    try:
        shown = str(p.relative_to(base))
    except ValueError:
        shown = str(p)
    if not p.exists():
        return HistoryReading("none", "no replay artefact on record", shown)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return HistoryReading("unreadable", f"it could not be parsed ({type(exc).__name__})", shown)
    if not isinstance(d, dict) or d.get("kind") != KIND:
        return HistoryReading("unreadable", "it is not a POC-retest replay artefact", shown)
    raw_windows = d.get("windows")
    if not isinstance(raw_windows, list) or not raw_windows:
        return HistoryReading("unreadable", "it holds no replay window", shown)
    live = live_settings()
    windows = []
    for w in raw_windows:
        got, why = _window(w, base)
        if got is None:
            return HistoryReading("unreadable", why, shown)
        differs = _settings_differ(w, live)
        if differs is not None:
            return HistoryReading("other_params", f"window {got.label!r}: {differs}", shown)
        windows.append(got)
    return HistoryReading("read", "", shown, tuple(windows))


_VERDICT_WORDS = {
    "survives": "it paid after fees",
    "does_not": "it lost after fees",
    "no_edge": "no edge measurable either way",
    "thin": "too few setups to say",
}


def _span(w: HistoryWindow) -> str:
    first, last = (w.first_retest or "")[:10], (w.last_retest or "")[:10]
    return f"retests {first} to {last}" if first and last else "no retest in it"


def _window_line(w: HistoryWindow) -> str:
    head = f"• <b>{html.escape(w.label, quote=False)}</b> ({_span(w)}): "
    if w.mean_r is None:
        return head + f"{w.armed} armed, none scored, so no R is quoted"
    line = (f"<b>{w.mean_r:+.2f}R</b> per setup over {w.scored} scored "
            f"({w.n_target} target, {w.n_stop} stop)")
    if w.interval is not None:
        lo, hi = w.interval
        line += f", 95% {lo:+.2f} to {hi:+.2f} over {w.clusters} week clusters"
    return head + line + f" — {_VERDICT_WORDS[w.verdict]}"


def history_note(r: HistoryReading) -> str:
    """The lines a card appends, from the reading and nothing else."""
    if r.state == "none":
        return ("📚 No replayed history of this setup is on record, so nothing "
                "here says whether it has paid before.")
    if r.state == "unreadable":
        return (f"📚 The replayed history on record could not be read: "
                f"{html.escape(r.reason, quote=False)}. That is not \"no history\".")
    if r.state == "other_params":
        return (f"📚 The replayed history on record measured other settings "
                f"({html.escape(r.reason, quote=False)}), so it says nothing about the ones "
                f"this read uses.")
    lines = ["📚 <b>Replayed history</b>: this read and this scoring at every "
             "closed 1h bar of frozen snapshots, fees in"]
    lines += [_window_line(w) for w in r.windows]
    dates = sorted({(w.recorded_at or "")[:10] for w in r.windows} - {""})
    when = f"recorded {', '.join(dates)}" if dates else "recording date not kept"
    lines.append(f"<i>A replay of the past, not a forecast; slippage inside a bar is "
                 f"not modelled ({when}).</i>")
    return "\n".join(lines)
