"""The shadow record for the POC-retest setup: what a confirmed sequence PAID.

`poc_retest` answers whether a sequence completed and whether its geometry
clears the spec's two rejections. Neither is a claim that the setup WORKS, and
until this module there was nothing that could become one: the detector had a
single reader, the `/pocretest` card, and nothing scored what it found.
A detector whose finding nothing scores is telemetry with a good reputation.

THE UNIT IS ALREADY FEE-AWARE, AND THAT DECIDES THE LOSS.
`net_reward_risk` builds its denominator as ``risk_px + entry_fee + stop_fee``
-- the whole cost of being stopped out, fees inside it -- so a stop-out is
**exactly -1.0R by construction** and subtracting fees from it again would
charge them twice. The target pays the arm-time ``net_r`` and nothing is
recomputed at scoring time: re-running the fee model later would answer a
different question (today's rates against yesterday's ticket), which is the
second-copy shape this repo keeps finding in maps, gates and thresholds.

WHAT THIS DOES NOT MODEL, STATED RATHER THAN GUESSED AT. A fill is modelled AT
the entry price. A bar that opens beyond the entry fills worse than that, so
the recorded R is the OPTIMISTIC bound -- the same disclosure
`bot/core/funding_arb` makes about slippage, and for the same reason: folding a
modelled cost into a measured one publishes an estimate with the authority of a
charge. Pricing the gap needs the bar's OPEN and a re-run of the fee model per
row, which is a different quantity and its own slice.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from bot.core.shadow_book import MIN_GATE_TRADES, mean_r_interval

#: Every way a recorded setup can end. The OUTCOMES close -- they are mutually
#: exclusive and exhaustive, so ``total`` is their sum and no reader has to
#: complete the taxonomy by subtracting, which is the `win_stats` rule (both
#: available subtractions are wrong in a different direction).
OUTCOMES = ("target", "stop", "ambiguous", "open", "not_triggered", "unscored")

#: Outcomes that are KNOWN to have put the setup in the market. ``unscored`` is
#: deliberately not among them and is not its complement either: a read that
#: failed at bar 7 may have triggered at bar 3, so "did this trigger" is
#: three-valued and `SetupOutcome.triggered` answers ``None`` where it cannot
#: be said. The first draft of this module made ``triggered`` a bare ``in``
#: test, which answers False for exactly that row -- a confident negative about
#: a read that never finished, inside the module written to keep them apart.
#: Driving the six outcomes is what said so; reading the walk did not.
TRIGGERED = ("target", "stop", "ambiguous", "open")

#: Filing ``not_triggered`` as a loss is the shapes table's
#: ``losses = len(all) - wins``: entry is a BREAK of the retest candle's
#: extreme, and price that never broke it never asked the trade to be taken.
_NOT_A_LOSS = "not_triggered"

#: Outcomes carrying a realized R. ``ambiguous`` deliberately does NOT: a bar
#: that spans both levels cannot say which came first, so it has no R and must
#: not be assigned one in either direction.
SCORED = ("target", "stop")

#: The sample floor, READ from `shadow_book` rather than restated, because it
#: is the same argument about the same instrument: a degenerate sample has a
#: sample sd of zero and therefore a lower bound at its own mean, which reads
#: as certainty and is only tininess. A second copy of a threshold is a second
#: answer, which is the rule `winrate-bar.js` states about MIN_RATED.
MIN_SCORED_SETUPS = MIN_GATE_TRADES

_RECORD_FILE = (Path(__file__).resolve().parent.parent.parent
                / "data" / "learning" / "poc_retest.jsonl")


@dataclass(frozen=True)
class SetupOutcome:
    """What a recorded setup did, and what it paid if that can be said."""

    outcome: str
    why: str
    r: Optional[float] = None
    trigger_index: Optional[int] = None
    resolve_index: Optional[int] = None

    @property
    def triggered(self) -> Optional[bool]:
        """Did this setup reach the market? ``None`` when it cannot be said.

        An ``unscored`` row that already carries a ``trigger_index`` DID
        trigger -- the walk saw it before the read failed -- and one that does
        not may have triggered on the very bar that could not be read. Those
        are different facts and a bare membership test answers False for both.
        """
        if self.outcome in TRIGGERED:
            return True
        if self.outcome == _NOT_A_LOSS:
            return False
        return True if self.trigger_index is not None else None

    @property
    def scored(self) -> bool:
        return self.outcome in SCORED and self.r is not None


def _f(value: object) -> Optional[float]:
    """A finite float, or ``None``. A nan is not a price and not a zero."""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def score_setup(side: object, entry: object, stop: object, target: object,
                target_r: object,
                highs: Sequence[Any], lows: Sequence[Any]) -> SetupOutcome:
    """Walk the bars AFTER the retest candle and say what the setup did.

    ``highs``/``lows`` are the bars that came after the retest -- the caller
    slices, because the record stores the retest's own index and this function
    has no opinion about where a series starts.

    THE ORDER OF TWO TOUCHES INSIDE ONE BAR IS NOT KNOWABLE FROM OHLC, and
    that is the whole reason ``ambiguous`` exists. A bar whose range spans the
    stop AND the target says both were reached and says nothing about which
    came first. Assuming stop-first is conservative and false; assuming
    target-first is flattering and false; folding either into a win or a loss
    puts a number nobody measured into the mean the verdict is read off. It is
    kept apart for the reason the scan partial keeps "not reached" apart from
    "errors": the two have different causes and folding them reports one as
    the other.

    The stop and the target are only consulted from the TRIGGER bar onward. A
    bar that reaches the stop while the entry is still untouched is not a loss
    -- nothing was in the market to lose.
    """
    e, s, t = _f(entry), _f(stop), _f(target)
    r_target = _f(target_r)
    if side not in ("long", "short") or e is None or s is None or t is None:
        return SetupOutcome("unscored",
                            "the recorded levels could not be read")
    if r_target is None:
        return SetupOutcome("unscored",
                            "the setup carries no arm-time R to pay")
    if len(highs) != len(lows):
        return SetupOutcome("unscored",
                            "the scoring bars are not a series")

    long = side == "long"
    trigger: Optional[int] = None

    for i in range(len(highs)):
        hi, lo = _f(highs[i]), _f(lows[i])
        if hi is None or lo is None:
            # One unreadable bar is not a resolution and not a break in the
            # walk: the level it would have touched is unknown, so the honest
            # answer is to stop rather than to skip it and claim the bars
            # after it resolved something this one may already have.
            return SetupOutcome("unscored",
                                f"bar {i} of the scoring window could not be "
                                f"read", trigger_index=trigger)

        if trigger is None:
            took = (hi >= e) if long else (lo <= e)
            if not took:
                continue
            trigger = i

        hit_stop = (lo <= s) if long else (hi >= s)
        hit_target = (hi >= t) if long else (lo <= t)

        if hit_stop and hit_target:
            return SetupOutcome(
                "ambiguous",
                f"bar {i} spans the stop and the target — OHLC cannot say "
                f"which was reached first",
                trigger_index=trigger, resolve_index=i)
        if hit_target:
            return SetupOutcome("target", "the target was reached",
                                r=r_target,
                                trigger_index=trigger, resolve_index=i)
        if hit_stop:
            # -1R exactly: the R unit's own denominator IS the stopped-out
            # loss with both fee legs in it, so charging fees again here
            # would charge them twice.
            return SetupOutcome("stop", "the stop was reached", r=-1.0,
                                trigger_index=trigger, resolve_index=i)

    if trigger is None:
        return SetupOutcome(
            "not_triggered",
            f"price never broke the entry in {len(highs)} bar(s) — the "
            f"setup was never taken")
    return SetupOutcome(
        "open",
        f"still open after {len(highs) - trigger} bar(s) in the market",
        trigger_index=trigger)


# ---------------------------------------------------------------- the record

@dataclass(frozen=True)
class RecordedSetup:
    """One confirmed setup, as it was armed. Levels, not opinions."""

    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    target_r: float
    retest_ms: int
    entry_tf: str
    recorded_at: str


def _record_path(path: Optional[Path] = None) -> Path:
    return Path(path) if path else _RECORD_FILE


def setup_key(symbol: object, side: object, retest_ms: object) -> str:
    """The identity of a setup, so re-reading a symbol cannot record it twice.

    A sequence is identified by the candle it retested on, not by when anybody
    happened to look at it: `/pocretest SOL` run three times in one hour is one
    setup seen three times, and recording it three times would put the same
    outcome into the sample three times and tighten an interval on no new
    evidence.
    """
    return f"{symbol}|{side}|{retest_ms}"


def load_rows(path: Optional[Path] = None) -> list[dict]:
    """Every recorded row, or ``[]`` when nothing has been recorded yet.

    An absent file is "no history"; a file that will not open RAISES, because
    those are different facts and `arb_tracker.load_snapshots` draws the same
    line for the same reason -- a reader that folds them answers "nothing
    recorded" about a record it could not read.
    """
    p = _record_path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # One malformed line is not an unreadable FILE. It is skipped
                # and counted by the reading, never silently dropped -- the
                # `secrets_vault` lesson: what cannot be read is kept apart,
                # not destroyed and not reported as absent.
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def record_confirmed(setup: RecordedSetup,
                     path: Optional[Path] = None) -> bool:
    """Append a confirmed setup. ``False`` when this one is already on record.

    Returns whether a row was written, so a caller can say "already recorded"
    rather than claiming an arming it did not make.
    """
    p = _record_path(path)
    key = setup_key(setup.symbol, setup.side, setup.retest_ms)
    for row in load_setups(p):
        if setup_key(row.get("symbol"), row.get("side"),
                     row.get("retest_ms")) == key:
            return False
    p.parent.mkdir(parents=True, exist_ok=True)
    # The discriminator is stamped HERE and the reading filters on it. The
    # first draft wrote a bare `asdict(setup)`: every setup landed on disk and
    # `load_setups` filtered all of them out, so the record accepted writes
    # and reported an empty history forever. Both halves read correct alone --
    # only the round trip says so, which is why it is driven.
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": KIND_SETUP, **asdict(setup)},
                            sort_keys=True) + "\n")
    return True


# --------------------------------------------------------------- the verdict

VERDICTS = ("survives", "does_not", "too_thin", "unread")


@dataclass(frozen=True)
class ShadowVerdict:
    """What the record says, and on how much of it."""

    verdict: str
    why: str
    n_total: int = 0
    n_scored: int = 0
    n_target: int = 0
    n_stop: int = 0
    n_ambiguous: int = 0
    n_open: int = 0
    n_not_triggered: int = 0
    n_unscored: int = 0
    mean_r: Optional[float] = None
    interval: Optional[tuple] = None


def _unscored_clause(n_ambiguous: int, n_unscored: int) -> str:
    """What the mean was NOT taken over, named, and only when there is some.

    Printed only for a non-zero count, because a permanent "0 ambiguous" on
    every healthy record is the row that trains a reader to stop reading the
    line -- the rule the analyze-budget note states about its own buckets.
    """
    parts = []
    if n_ambiguous:
        parts.append(f"{n_ambiguous} could not be ordered from OHLC")
    if n_unscored:
        parts.append(f"{n_unscored} could not be read")
    if not parts:
        return ""
    return (" — " + " and ".join(parts)
            + ", so they carry no R and are not in that mean")


def shadow_verdict(outcomes: Sequence[SetupOutcome], *,
                   min_scored: int = MIN_SCORED_SETUPS) -> ShadowVerdict:
    """Does this setup pay, on the evidence recorded so far?

    The instrument is `mean_r_interval`, READ from `shadow_book` rather than
    restated: an outcome is a signed R magnitude, not a proportion, so Wilson
    would answer a question nobody asked. What is shared is the DISCIPLINE --
    the whole interval clear of the null, never its near end -- and the floor,
    which exists because a degenerate sample has a sample sd of zero and
    therefore a lower bound at its own mean.

    ``ambiguous`` rows are counted and are NOT in the mean, and the sentence
    says so. They are not a neutral subset: a bar wide enough to span both
    levels is a VOLATILE bar, so dropping them silently would report a mean
    over the calm half of the record as the mean over all of it -- a partial
    total printed as whole. No threshold is invented for "too many of them",
    because a bar-count share that changes a verdict would be a number nobody
    measured; the count is printed and the reader can see it.
    """
    n_total = len(outcomes)
    counts = {name: 0 for name in OUTCOMES}
    for o in outcomes:
        if o.outcome in counts:
            counts[o.outcome] += 1

    rs = [o.r for o in outcomes if o.scored and o.r is not None]
    n = len(rs)
    def made(kind: str, why: str, *, mean_r: Optional[float] = None,
             interval: Optional[tuple] = None) -> ShadowVerdict:
        """Every verdict carries the same counts, written once.

        The counts are spelled here rather than splatted from a dict: a
        `**dict[str, int]` into a dataclass that also holds an interval tells
        the type checker nothing about which field each key lands on, and a
        count arriving in `interval` is exactly the kind of mistake the
        checker exists to refuse.
        """
        return ShadowVerdict(
            kind, why, n_total=n_total, n_scored=n,
            n_target=counts["target"], n_stop=counts["stop"],
            n_ambiguous=counts["ambiguous"], n_open=counts["open"],
            n_not_triggered=counts["not_triggered"],
            n_unscored=counts["unscored"],
            mean_r=mean_r, interval=interval)

    if n_total == 0:
        # An EMPTY record is a fact about the record; a THIN one is a fact
        # about the sample, and "0 of 10" reads as the second. `arb_reading`
        # draws the same line one tracker over.
        return made("too_thin", "no setups have been recorded yet")
    if n < min_scored:
        return made("too_thin",
                    f"{n} scored setup(s) of {n_total} recorded, under the "
                    f"{min_scored} needed before an interval means anything")

    mean = sum(rs) / n
    interval = mean_r_interval(n, sum(rs), sum(r * r for r in rs))
    if interval is None:
        return made("too_thin",
                    f"{n} scored setup(s) but the interval could not be "
                    f"computed", mean_r=round(mean, 4))

    lo, hi = interval
    if lo > 0:
        return made(
            "survives",
            f"mean {mean:+.2f}R over {n} scored setup(s); the whole 95% "
            f"interval ({lo:+.2f}R to {hi:+.2f}R) is above zero"
            + _unscored_clause(counts["ambiguous"], counts["unscored"]),
            mean_r=round(mean, 4), interval=interval)
    if hi < 0:
        return made(
            "does_not",
            f"mean {mean:+.2f}R over {n} scored setup(s); the whole 95% "
            f"interval ({lo:+.2f}R to {hi:+.2f}R) is below zero"
            + _unscored_clause(counts["ambiguous"], counts["unscored"]),
            mean_r=round(mean, 4), interval=interval)
    return made(
        "too_thin",
        f"mean {mean:+.2f}R over {n} scored setup(s), but the 95% interval "
        f"({lo:+.2f}R to {hi:+.2f}R) straddles zero"
        + _unscored_clause(counts["ambiguous"], counts["unscored"]),
        mean_r=round(mean, 4), interval=interval)


#: The record is APPEND-ONLY and rows are discriminated by ``kind``. An
#: outcome does not rewrite the setup it scores: a setup read again with more
#: bars behind it can move from ``open`` to ``target``, so the later row wins
#: and the earlier one stays on disk. Rewriting the file in place is how
#: `secrets_vault._load_vault` destroyed the entries it could not read, and an
#: append-only log with a last-wins join cannot lose a row it did not
#: understand.
KIND_SETUP = "setup"
KIND_OUTCOME = "outcome"


def load_setups(path: Optional[Path] = None) -> list[dict]:
    """The armed setups on record, newest last."""
    return [r for r in load_rows(path) if r.get("kind") == KIND_SETUP]


def load_outcomes(path: Optional[Path] = None) -> dict[str, dict]:
    """The LAST outcome recorded for each setup key.

    Last wins because a re-score is a better reading of the same setup, not a
    second setup: an ``open`` row scored again a day later with the bars that
    resolved it should become that resolution, and keeping the first would
    pin every setup at whatever it looked like the first time anybody asked.
    """
    out: dict[str, dict] = {}
    for row in load_rows(path):
        if row.get("kind") != KIND_OUTCOME:
            continue
        key = row.get("key")
        if isinstance(key, str):
            out[key] = row
    return out


def record_setup_outcome(key: str, outcome: SetupOutcome,
                   path: Optional[Path] = None) -> None:
    """Append what a setup did. Never rewrites the setup row it scores."""
    p = _record_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"kind": KIND_OUTCOME, "key": key, "outcome": outcome.outcome,
           "why": outcome.why, "r": outcome.r,
           "trigger_index": outcome.trigger_index,
           "resolve_index": outcome.resolve_index}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def shadow_reading(path: Optional[Path] = None
                   ) -> tuple[list[dict], ShadowVerdict]:
    """``(setups, verdict)`` for the whole record.

    NO HISTORY YET AND COULD NOT BE READ ARE DIFFERENT FACTS, which is the
    distinction `arb_reading` draws one tracker over: `load_rows` answers
    ``[]`` for a file that is not there and RAISES for one that will not open,
    and folding the second into the first would report "nothing recorded"
    about a record nobody could read.

    A setup with no outcome row yet is ``unscored`` and says so -- it is not
    dropped, because a denominator that quietly excludes the rows nobody has
    got to is a partial total printed as whole.
    """
    try:
        setups = load_setups(path)
        scored = load_outcomes(path)
    except OSError as exc:
        return [], ShadowVerdict(
            "unread", f"the shadow record could not be read ({type(exc).__name__})")

    outcomes: list[SetupOutcome] = []
    for row in setups:
        key = setup_key(row.get("symbol"), row.get("side"),
                        row.get("retest_ms"))
        got = scored.get(key)
        if got is None:
            outcomes.append(SetupOutcome(
                "unscored", "no outcome has been recorded for this setup yet"))
            continue
        name = got.get("outcome")
        outcomes.append(SetupOutcome(
            name if name in OUTCOMES else "unscored",
            str(got.get("why") or ""),
            r=_f(got.get("r")),
            trigger_index=got.get("trigger_index"),
            resolve_index=got.get("resolve_index")))
    return setups, shadow_verdict(outcomes)


# ------------------------------------------------------------------ the card

#: The headline per verdict. `too_thin` and `unread` are BOTH muted and they
#: are different sentences: colour is a claim, and a green tick over a record
#: nobody could read would be the loudest thing on the card.
_HEAD = {
    "survives": "✅ The recorded setups pay, after fees",
    "does_not": "❌ The recorded setups do not pay",
    "too_thin": "⏳ Not enough scored setups to say",
    "unread": "⚪ The shadow record could not be read",
}


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.0f}%" if whole else "—"


def shadow_card(verdict: ShadowVerdict) -> str:
    """The record, what it says, and what it is NOT saying.

    Every figure on it is a count or an R multiple. No dollar amount appears
    and none can: a setup's outcome is denominated in its own risk, which is
    what makes the record comparable across symbols in the first place.
    """
    head = _HEAD.get(verdict.verdict)
    if head is None:
        # A verdict word this build cannot place is not a market that did
        # nothing. Same refusal `setup_card` makes one module over.
        head = "⚪ This build cannot place that verdict"
    out = ["📓 <b>POC-RETEST SHADOW RECORD</b>", "", head, f"<i>{verdict.why}</i>"]

    if verdict.verdict != "unread" and verdict.n_total:
        out.append("")
        out.append(f"<b>{verdict.n_total}</b> setup(s) recorded:")
        rows = [
            ("reached target", verdict.n_target),
            ("stopped out", verdict.n_stop),
            ("spanned both in one bar", verdict.n_ambiguous),
            ("still open", verdict.n_open),
            ("never triggered", verdict.n_not_triggered),
            ("not scored yet", verdict.n_unscored),
        ]
        for label, n in rows:
            # A permanent zero row is the row that trains a reader to stop
            # reading the list, so only what happened is printed.
            if n:
                out.append(f"  · {label}: <b>{n}</b> ({_pct(n, verdict.n_total)})")

    if verdict.mean_r is not None:
        out.append("")
        out.append(f"Mean: <b>{verdict.mean_r:+.2f}R</b> over "
                   f"{verdict.n_scored} scored")
        if verdict.interval:
            lo, hi = verdict.interval
            out.append(f"95% interval: {lo:+.2f}R to {hi:+.2f}R")

    out.append("")
    out.append("<i>A shadow record only. Nothing here was placed and nothing "
               "is armed. R is net of one round trip of fees, so a stop-out "
               "is exactly -1.00R.</i>")
    return "\n".join(out)
