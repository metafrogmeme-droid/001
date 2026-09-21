"""The quality ladder's RECORD: what the rungs did, and would have done.

WHY A RECORD. `QUALITY_LADDER_SIZE_ENABLED` and `QUALITY_LADDER_LEVERAGE_ENABLED`
default OFF and "shadow when off": the risk gate audits the rung it would have
applied with ``result="SHADOW"``. Driven, that audit is a LOG LINE --
``audit(risk_log, ...)`` -- and nothing in the tree reads it back: no card, no
report, no evidence gatherer. #36 moved the sizing shadows from
``logger.debug`` (no handler) to the audit channel so they could be SEEN;
nothing made them READABLE, and the flag they exist to inform is flipped, if
at all, on a memory of grep. A shadow nobody can render is telemetry with a
good reputation -- the sentence this repo already uses of a detector whose
finding reaches no control.

WHAT IS RECORDED. One row per SIZED evaluation: an idea that reached the
gate's sizing. A refusal before it (a tripped breaker, a bad level, a halted
engine) takes no rung and leaves no row, and the card says "sized
evaluations" for that reason. EVERY sized row is kept -- measured or not,
applied or shadow -- because the denominators ("41 sized, 12 on rung B") are
as much the evidence as the cuts, and a record of the cuts alone is a partial
total printed as whole.

WHAT IS NOT CLAIMED. The would-be SIZE is the post-cap figure times the rung's
multiplier: the arithmetic the SHADOW audit has always used, exact for the
multiplicative steps and not for a ceiling that would have bound differently
at the smaller figure, so the card says "post-cap figure x rung multiplier".
The would-be LEVERAGE is exact (`ladder_leverage`). No recommendation is
derived: a count of would-have cuts is evidence about FREQUENCY and says
nothing about outcomes, and "the evidence supports enabling" off a count alone
is the self-audit's own recorded defect.

PERSISTENCE. ``data/ladder_ledger.json`` through `bot/utils/shadow_ledger`
(the class the bounds record shares): the newest ``MAX_ROWS`` kept, atomic
writes. A file that will not parse is NOT
overwritten -- `exchange_credentials._load`'s rule, because a record of
evidence destroyed by the reader that could not open it is the
`secrets_vault` defect one store over. Rows recorded after that live in
memory, the card says the file could not be read and how much was recorded
since, and the next boot reads the same file again.

WHOSE. The record spans one bot process, and each row names the ENGINE that
evaluated -- the reading `RiskEngine._person_user_id` already holds: "" on
the shared engine (the operator's, which `engine.risk_for` answers for EVERY
caller while PER_USER_LIVE_ENABLED is off, and for the operator, admins and
the auto path when it is on), and the user's own id on a per-user engine,
which `risk_for` tells whose it is through `set_person_identity` the moment
it builds one. The first draft of this module said a RiskEngine carries no
user id and that the record therefore could not name an account; driving
`risk_for` is what said otherwise. The card counts by engine, and a row an
older build wrote without the key is counted as "engine not recorded" --
never as the shared engine.
"""

from __future__ import annotations

import html
import os
import time
from collections import Counter
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from bot.risk.quality_ladder import (
    MANUAL_SOURCE,
    LadderVerdict,
    Rung,
    ladder_leverage,
    rungs_from_config,
)
from bot.utils.shadow_ledger import (  # noqa: F401  (re-exported: the ladder's readers spell them here)
    MAX_ROWS,
    STATE_FRESH,
    STATE_READ,
    STATE_UNREADABLE,
    ShadowLedger,
    stamp,
)

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
DEFAULT_STATE_FILE = os.path.join(_STATE_DIR, "ladder_ledger.json")

# The keys every row this build writes carries. A row missing one is a row
# some other build wrote (or a hand edit), and the summary COUNTS it as
# unreadable rather than folding it into a bucket it cannot place.
ROW_KEYS = ("ts", "symbol", "source", "measured", "confidence", "rung", "why",
            "size_mult", "leverage_mult", "size_enabled", "leverage_enabled",
            "size_usd", "size_would_usd", "leverage_std", "leverage_ladder",
            "table_note")

# The engine that evaluated is a key of its own and deliberately NOT in
# ROW_KEYS: a row a build wrote before the engine was recorded is readable in
# every other respect, and folding it into "unreadable" would drop it from
# the rung counts it can be placed in. Its absence is its own bucket on the
# card -- ABSENT IS NOT ZERO, PER BUCKET -- and never the shared engine,
# because "nobody wrote it down" and "the shared engine evaluated" are
# different facts that would otherwise share one count.
ENGINE_KEY = "engine"
# `RiskEngine._person_user_id` on the engine with no person identity: the
# shared operator engine. A per-user engine carries its user's id there.
SHARED_ENGINE = ""


def evaluation_row(*, idea: Any, verdict: LadderVerdict, size_usd: float,
                   standard_leverage: int, floor: int, size_enabled: bool,
                   leverage_enabled: bool, engine: str,
                   now: Optional[float] = None) -> Dict[str, Any]:
    """One sized evaluation, as the ledger records it. Pure.

    ``size_usd`` is the gate's post-cap figure AS SIZED -- with the size half
    ON and a rung that cuts, it is the cut figure; ``size_would_usd`` is set
    only when the half is OFF and the rung would have cut, and is that figure
    times the multiplier. ``leverage_ladder`` is the rung's leverage whenever
    the rung would cut it (exact, floor-aware), whether or not the leverage
    half applied it; ``leverage_std`` is the standard it would cut FROM.
    ``engine`` is the evaluating engine's own `_person_user_id` -- REQUIRED
    rather than defaulted, because a default of "" would file every caller
    that forgot it under the shared engine in silence.
    """
    size = float(size_usd)
    row: Dict[str, Any] = {
        "ts": float(time.time() if now is None else now),
        ENGINE_KEY: str(engine),
        "symbol": str(getattr(idea, "asset", "") or ""),
        "source": str(getattr(idea, "source", "") or ""),
        "measured": bool(verdict.measured),
        "confidence": (None if verdict.confidence is None else float(verdict.confidence)),
        "rung": verdict.rung,
        "why": str(verdict.why),
        "size_mult": float(verdict.size_mult),
        "leverage_mult": float(verdict.leverage_mult),
        "size_enabled": bool(size_enabled),
        "leverage_enabled": bool(leverage_enabled),
        "size_usd": round(size, 2),
        "size_would_usd": None,
        "leverage_std": max(1, int(standard_leverage)),
        "leverage_ladder": None,
        "table_note": str(verdict.table_note or ""),
    }
    if verdict.measured and verdict.size_mult < 1.0 and not size_enabled:
        row["size_would_usd"] = round(size * float(verdict.size_mult), 2)
    if verdict.measured and verdict.leverage_mult < 1.0:
        row["leverage_ladder"] = ladder_leverage(row["leverage_std"], verdict, floor=floor)
    return row


class LadderLedger(ShadowLedger):
    """The ladder's record: `bot/utils/shadow_ledger.ShadowLedger` on the
    ladder's own file. The three load states, the never-overwrite rule and
    the never-raise write are the shared class's; nothing here but the path."""

    def __init__(self, state_file: Optional[str] = None) -> None:
        super().__init__(state_file or DEFAULT_STATE_FILE)


class RungStat(NamedTuple):
    rung: str
    n: int
    size_would: int        # rows where the size half was OFF and the rung would have cut
    size_applied: int      # rows where the size half was ON and the rung cut
    lev_would: int         # rows where the leverage half was OFF and the rung would have cut
    lev_applied: int       # rows where the leverage half was ON and the rung cut
    size_sum: float        # sum of size_usd over the size_would rows
    size_would_sum: float  # sum of size_would_usd over the same rows
    lev_pairs: Tuple[Tuple[int, int, int], ...]  # (standard, ladder, count), most common first


class LadderSummary(NamedTuple):
    n: int                       # readable rows
    unreadable: int              # rows some other build wrote
    first_ts: Optional[float]
    last_ts: Optional[float]
    by_rung: Tuple[RungStat, ...]  # in order of first appearance
    unmeasured_manual: int
    unmeasured_other: int
    measured_no_rung: int        # measured, and the table reached no rung (unreachable while the last floor is 0)
    by_engine: Tuple[Tuple[str, int], ...]  # (engine id, readable rows), most rows first; "" is the shared engine
    engine_unrecorded: int       # readable rows a build wrote before the engine was recorded


def _readable(row: Dict[str, Any]) -> bool:
    return all(k in row for k in ROW_KEYS)


def _would_cut_lev(row: Dict[str, Any]) -> bool:
    lad = row.get("leverage_ladder")
    std = row.get("leverage_std")
    try:
        return lad is not None and std is not None and int(lad) < int(std)
    except (TypeError, ValueError):
        return False


def summarize(rows: Sequence[Dict[str, Any]]) -> LadderSummary:
    """Counts over the record. A row this build cannot read is COUNTED, never
    dropped: a summary over the rows it could place, printed as the whole, is
    the partial-total shape."""
    readable = [r for r in rows if _readable(r)]
    unreadable = len(rows) - len(readable)
    stats: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    man = oth = norung = 0
    tss: List[float] = []
    engines: Counter = Counter()
    engine_unrecorded = 0
    for r in readable:
        try:
            tss.append(float(r["ts"]))
        except (TypeError, ValueError):
            pass
        # Counted over EVERY readable row, measured or not: the question is
        # whose evaluations are on the record, and a manual ticket a user's
        # engine sized is one of that engine's.
        if ENGINE_KEY in r:
            engines[str(r[ENGINE_KEY])] += 1
        else:
            engine_unrecorded += 1
        if not r["measured"]:
            if r["source"] == MANUAL_SOURCE:
                man += 1
            else:
                oth += 1
            continue
        rung = r["rung"]
        if rung is None:
            norung += 1
            continue
        name = str(rung)
        if name not in stats:
            stats[name] = {"n": 0, "size_would": 0, "size_applied": 0, "lev_would": 0,
                           "lev_applied": 0, "size_sum": 0.0, "size_would_sum": 0.0,
                           "pairs": Counter()}
            order.append(name)
        s = stats[name]
        s["n"] += 1
        try:
            size_cuts = float(r["size_mult"]) < 1.0
        except (TypeError, ValueError):
            size_cuts = False
        if size_cuts:
            if r["size_enabled"]:
                s["size_applied"] += 1
            elif r["size_would_usd"] is not None:
                s["size_would"] += 1
                try:
                    s["size_sum"] += float(r["size_usd"])
                    s["size_would_sum"] += float(r["size_would_usd"])
                except (TypeError, ValueError):
                    pass
        if _would_cut_lev(r):
            if r["leverage_enabled"]:
                s["lev_applied"] += 1
            else:
                s["lev_would"] += 1
            s["pairs"][(int(r["leverage_std"]), int(r["leverage_ladder"]))] += 1
    by_rung = tuple(
        RungStat(name, st["n"], st["size_would"], st["size_applied"], st["lev_would"],
                 st["lev_applied"], st["size_sum"], st["size_would_sum"],
                 tuple((a, b, c) for (a, b), c in st["pairs"].most_common()))
        for name, st in ((n, stats[n]) for n in order))
    return LadderSummary(len(readable), unreadable,
                         min(tss) if tss else None, max(tss) if tss else None,
                         by_rung, man, oth, norung,
                         tuple(engines.most_common()), engine_unrecorded)


# ── the card ────────────────────────────────────────────────────────────────

def _flag_word(on: bool) -> str:
    return "✅ ON" if on else "⬜ OFF"


def _engine_word(engine: str) -> str:
    """The engine on the card: the shared one by name, a per-user one by its
    user. Escaped, because the id is whatever the user store handed the
    engine and this card is Telegram HTML."""
    if engine == SHARED_ENGINE:
        return "shared engine"
    return f"user {html.escape(engine)}"


def _record_line(s: LadderSummary, span: str) -> str:
    who = [f"{_engine_word(e)} {n}" for e, n in s.by_engine]
    if s.engine_unrecorded:
        who.append(f"engine not recorded on {s.engine_unrecorded}")
    return " · ".join([f"Record: {s.n} sized evaluation(s)", span] + who)


def _table_line(rungs: Sequence[Rung]) -> str:
    return " · ".join(f"{r.name} ≥{r.floor:.2f} → size x{r.size_mult:.2f} / "
                      f"leverage x{r.leverage_mult:.2f}" for r in rungs)


def _rung_line(r: Rung, st: Optional[RungStat]) -> str:
    head = f"  <b>{html.escape(r.name)}</b> ≥{r.floor:.2f}: "
    if st is None or st.n == 0:
        # A measured ZERO on a rung is a reading: nothing landed there.
        return head + "0"
    parts: List[str] = [f"{st.n}"]
    cuts_size = r.size_mult < 1.0
    cuts_lev = r.leverage_mult < 1.0
    if not cuts_size and not cuts_lev:
        parts.append("full size, standard leverage")
    if st.size_applied:
        parts.append(f"size cut x{r.size_mult:.2f} on {st.size_applied} (applied)")
    if st.size_would:
        avg_from = st.size_sum / st.size_would
        avg_to = st.size_would_sum / st.size_would
        parts.append(f"size would have been cut x{r.size_mult:.2f} on {st.size_would} "
                     f"(avg ${avg_from:,.2f} → ${avg_to:,.2f})")
    if st.lev_applied or st.lev_would:
        pairs = ", ".join(f"{a}x→{b}x" + (f" ({c})" if len(st.lev_pairs) > 1 else "")
                          for a, b, c in st.lev_pairs[:3])
        if st.lev_applied:
            parts.append(f"leverage cut {pairs} on {st.lev_applied} (applied)")
        if st.lev_would:
            parts.append(f"leverage would have been cut {pairs} on {st.lev_would}")
    elif cuts_lev and st.n:
        parts.append("leverage stays (the floor holds it)")
    return head + " — ".join([parts[0], " · ".join(parts[1:])]) if len(parts) > 1 else head + parts[0]


def render_ladder_report(ledger: LadderLedger, risk_cfg: Any,
                         now: Optional[float] = None) -> str:
    """Telegram-ready. Three states at the top -- the file could not be read,
    nothing sized on record, a record -- and every count with its span."""
    size_on = bool(getattr(risk_cfg, "quality_ladder_size_enabled", False))
    lev_on = bool(getattr(risk_cfg, "quality_ladder_leverage_enabled", False))
    rungs, note = rungs_from_config(risk_cfg)
    lines: List[str] = ["<b>Quality ladder — what the rungs would have done</b>",
                        "─" * 16,
                        f"Flags: size {_flag_word(size_on)} · leverage {_flag_word(lev_on)}",
                        f"Table: {_table_line(rungs)}"]
    if note:
        lines.append(f"⚠️ the configured table was refused ({html.escape(note)}) — "
                     f"the defaults above are in use")
    rows = ledger.rows()
    if ledger.load_state == STATE_UNREADABLE:
        lines.append(f"⚠️ The record on disk (<code>{html.escape(ledger.state_file)}</code>) "
                     f"could not be read ({html.escape(ledger.load_detail or 'unreadable')}) "
                     f"and is not being overwritten. {ledger.recorded_since_load} sized "
                     f"evaluation(s) recorded in memory since {stamp(ledger.loaded_at)}.")
        if not rows:
            return "\n".join(lines)
    elif not rows:
        lines.append("No sized evaluation on record yet — nothing has reached the "
                     "gate's sizing since the record began, so there is nothing to "
                     "read. This is not a reading of the ladder.")
        return "\n".join(lines)
    s = summarize(rows)
    span = f"{stamp(s.first_ts)} → {stamp(s.last_ts)}" if s.n else "no readable row"
    lines.append(_record_line(s, span))
    if s.unreadable:
        lines.append(f"  {s.unreadable} row(s) another build wrote could not be read and "
                     f"are counted here, not below")
    by_name = {st.rung: st for st in s.by_rung}
    for r in rungs:
        lines.append(_rung_line(r, by_name.get(r.name)))
    extra = [st for st in s.by_rung if st.rung not in {r.name for r in rungs}]
    if extra:
        lines.append("  rungs no longer in the table (the table changed): " +
                     ", ".join(f"{html.escape(st.rung)} {st.n}" for st in extra))
    if s.unmeasured_manual or s.unmeasured_other:
        parts = []
        if s.unmeasured_manual:
            parts.append(f"manual tickets {s.unmeasured_manual} — a stamp, not a measurement")
        if s.unmeasured_other:
            parts.append(f"confidence unreadable {s.unmeasured_other}")
        lines.append(f"  no rung: {s.unmeasured_manual + s.unmeasured_other} ({'; '.join(parts)})")
    if s.measured_no_rung:
        lines.append(f"  measured but reaching no rung: {s.measured_no_rung}")
    size_would = sum(st.size_would for st in s.by_rung)
    lev_would = sum(st.lev_would for st in s.by_rung)
    size_applied = sum(st.size_applied for st in s.by_rung)
    lev_applied = sum(st.lev_applied for st in s.by_rung)
    if size_would or lev_would:
        lines.append(f"With both halves on, the size would have been cut on {size_would} "
                     f"of {s.n} and the leverage on {lev_would} of {s.n}.")
    if size_applied or lev_applied:
        lines.append(f"A half that was on cut the size on {size_applied} of {s.n} and "
                     f"the leverage on {lev_applied} of {s.n}.")
    if not (size_would or lev_would or size_applied or lev_applied) and s.n:
        lines.append("No row would have been cut, and none was: every measured idea "
                     "landed on a rung that keeps full size and standard leverage.")
    foot = ["Would-be size = the post-cap figure × the rung multiplier. A refusal "
            "before sizing leaves no row."]
    if any(e == SHARED_ENGINE for e, _ in s.by_engine):
        # Said only when the word is on the card: a vocabulary note under a
        # record that names no shared engine is a caveat about nothing.
        foot.append("The shared engine is the operator's; it evaluates for every "
                    "caller while PER_USER_LIVE_ENABLED is off, and for the operator, "
                    "admins and the auto path when it is on.")
    if ledger.full:
        foot.append(f"The record keeps the last {MAX_ROWS} rows and is full, so older "
                    f"rows may have been dropped.")
    lines.append(f"<i>{' '.join(foot)}</i>")
    return "\n".join(lines)


# Shared singleton (the shadow book's pattern): the risk gate records into
# it, /shadow ladder reads it.
LADDER_LEDGER = LadderLedger()
