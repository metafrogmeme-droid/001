"""Trade QUALITY chooses a size and a leverage within the bounds -- one reading.

The bounds (`bot/core/size_bounds.py`) say how much an account may carry; the
notional cap, the governor, the throttle, the regime and the correlation
multipliers each tighten a trade for a reason of their own. None of them read
the one thing the analyzer measured about THIS trade: its confidence. It
reached sizing through Kelly's ``kelly_f * 0.5 * conf`` -- a ceiling that only
exists once twenty closes are on record -- and through `_high_conviction_margin`,
which is binary and flat (one floor, one dollar target, opt-in). So a 0.58
idea and a 0.92 idea were sized identically on every account that had not
yet accrued a Kelly estimate, and the leverage they were set at never saw
confidence at all.

THE LADDER IS A TABLE, AND EVERY RUNG TIGHTENS OR LEAVES ALONE
-------------------------------------------------------------
A rung is ``name:floor:size_mult:leverage_mult``. The top rung is the bounds
themselves (both multipliers 1.0); a lower rung shrinks the margin and caps
the leverage at a share of the standard one, REDUCE-ONLY, the rule every
multiplier in `_evaluate_locked` is written to. Growth is not a rung: a size
past the bounds is what `SIZE_BOUNDS_MAX_*` is for, typed deliberately, and a
confidence figure -- which the analyzer's own calibration can move by 0.05
per slice -- is not evidence that a larger position is safe. Every multiplier
in a table is refused above 1.0 rather than clamped, because a table whose
author wrote 1.3 wanted growth, and silently handing them 1.0 is a second
answer about what the table says.

A MANUAL TICKET'S CONFIDENCE IS A STAMP, NOT A MEASUREMENT
---------------------------------------------------------
`manual_trade.build_manual_idea` writes ``confidence=1.0`` on every hand-typed
ticket. Nothing measured that; it is the value that clears every floor. So
the reading here is three-valued: an analyzer idea is MEASURED at its
confidence, a manual ticket (``source == "manual"``) is UNMEASURED, and a
confidence the record cannot read (absent, non-numeric, NaN, outside [0, 1])
is UNMEASURED with its own reason. An unmeasured quality takes NO rung:
size multiplier 1.0, leverage untouched, and the sentence says why -- the
`user_sizing` ruling, because shrinking a position on the strength of a
stamp we chose not to trust would be a size nobody measured either.
`kelly_confidence_factor` is the same reading for Kelly, so a change to the
stamp cannot quietly start scaling manual tickets by a number that is still
not a measurement; and `_high_conviction_margin` asks it too, so a stamp
cannot clear the conviction floor.

Both halves are behind their own flag, default OFF, and SHADOW when off: the
engine audits the would-be size so the ladder's effect is measurable before
it moves money, the shape `USER_RISK_PREF_SIZING_ENABLED` already takes.

The default table is written TWICE on purpose -- here and as the config
field's own default -- because `bot/config.py` cannot import this module
(`bot.risk` reaches `bot.config` and the import would cycle). A guard pins
the two spellings equal; a second copy that a test compares is one answer.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple, Optional, Tuple

MANUAL_SOURCE = "manual"

#: ``name:floor:size_mult:leverage_mult``, top rung first. The last rung's
#: floor is 0.0 so every measured confidence lands on one; the CONFIDENCE
#: gate (``bot/risk/confidence_floor.py``) refuses below the strategy's
#: minimum before any rung is consulted, so rung C in practice covers
#: [min_confidence, 0.70).
DEFAULT_RUNGS_TEXT = "A:0.85:1.0:1.0,B:0.70:0.75:0.8,C:0.0:0.5:0.6"


class QualityReading(NamedTuple):
    """Whether this idea's confidence is a measurement, and of what."""
    measured: bool
    confidence: Optional[float]
    why: str


class Rung(NamedTuple):
    name: str
    floor: float
    size_mult: float
    leverage_mult: float


class LadderVerdict(NamedTuple):
    """What the ladder decided for one idea -- the reading, the rung, the two
    multipliers and the clause a check line or an audit record prints."""
    measured: bool
    confidence: Optional[float]
    rung: Optional[str]
    size_mult: float
    leverage_mult: float
    why: str
    table_note: str = ""   # non-empty when the configured table was refused


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def confidence_on_record(v: Any) -> Optional[float]:
    """The confidence the record holds, or ``None`` when it holds none.

    ``_num`` above already refuses what is not a number -- ``None``, a bool
    (``True`` is not full conviction, the argument ``pct_on_record`` makes one
    field over), a string that will not parse, NaN and the infinities. This
    adds the RANGE refusal, which is the half ``pct_on_record`` deliberately
    does NOT make: a percent has no declared bounds, so every real value is a
    reading, whereas a confidence has one and the product states it to the
    model at all three sites that ask for one --
    ``analyzer.py`` ("- confidence: float 0.0-1.0" and the json line) and
    ``token_optimizer.py``. A value outside that range is the model not
    answering the question it was asked, so it is refused rather than clamped
    INTO range: reading ``85`` as ``0.85`` would be a guess about what the
    model meant, which is the trap ``csf.market_is_perp`` records one module
    over.

    ``0.0`` and ``1.0`` are both KEPT. ``0.0`` is a real, measured
    no-conviction -- it is the confidence of the prompt's own no-trade
    contract, ``{"direction": null, "confidence": 0.0, ...}`` -- and
    ``confidence_floor.clears_confidence_floor`` says the same thing in its
    own docstring. Collapsing it with an absence is the defect this reading
    exists to remove, not a simplification of it.
    """
    if isinstance(v, str) and v.strip().endswith("%"):
        # A PERCENT SIGN IS A SPELLING, NOT AN INTERPRETATION, and that is the
        # same line the paragraph above draws: `"0.85"` reads because it is the
        # same number written differently, and a bare `85` does not because
        # choosing a denominator for it would be a guess. `72%` states its own
        # denominator, so reading it as 0.72 guesses nothing.
        #
        # This is not a corner. `ollama/Modelfile`'s SYSTEM prompt specifies
        # `Confidence: XX%` and the in-house model's ~50k-example corpus
        # carries 7,342 of them and ZERO instances of the JSON thesis key -- so
        # for `LLMProvider.RUNECLAW` this IS the format. The old clamp read
        # every one of the 47 distinct spellings in that corpus as the same
        # value, 1.0: a fine-tuned model's whole confidence vocabulary,
        # flattened to the maximum at the parser.
        pct = _num(v.strip()[:-1].strip())
        conf = None if pct is None else pct / 100.0
    else:
        conf = _num(v)
    if conf is None or conf < 0.0 or conf > 1.0:
        return None
    return conf


def quality_reading(idea: Any) -> QualityReading:
    """Three-valued: measured at the idea's confidence, or unmeasured with why.

    ``source == "manual"`` is unmeasured whatever the confidence field holds,
    because that field is a stamp on a manual ticket (see the module header).
    Never raises.

    IT DELIBERATELY DOES NOT ASK ``confidence_inherited_from``, and the reason
    is measured rather than preferred. A drift re-offer's confidence really is
    about another trade, so "unmeasured" reads like the honest answer -- and
    this reading's consumers take an unmeasured quality as *abstain*, not as
    *be careful*: `ladder_verdict` returns multipliers of 1.0 ("no rung, no
    reduction") and `kelly_confidence_factor` returns 1.0 ("half-Kelly
    unscaled"). Driven on a re-offer of a 0.30-confidence thesis, routing it
    through here moves size x0.50 -> x1.00, leverage 5x -> 5x (from 3x) and
    Kelly x0.30 -> x1.00: it DOUBLES the weakest re-offers and raises their
    leverage, in the flattering direction, which is the defect this module
    exists to remove. The auto-confirm door asks the extra question itself --
    see `auto_confirm_refusal` -- because refusing an execution and abstaining
    from a reduction are opposite consequences of one word.

    The verdict is ``confidence_on_record``'s -- one reading, so the parser
    that admits a confidence and the ladder that sizes off one cannot answer
    differently about what a readable confidence is. ``_num`` is asked again
    only for the REASON, which is three-valued where the verdict is two:
    "unread" and "outside [0, 1]" send an operator to different places.
    """
    if idea is None:
        return QualityReading(False, None, "no idea: confidence unread")
    source = str(getattr(idea, "source", "") or "").strip().lower()
    if source == MANUAL_SOURCE:
        return QualityReading(
            False, None,
            "manual ticket: confidence is a stamp, not a measurement")
    raw = getattr(idea, "confidence", None)
    conf = confidence_on_record(raw)
    if conf is None:
        n = _num(raw)
        why = ("confidence unread" if n is None
               else f"confidence {n:g} outside [0, 1]")
        return QualityReading(False, None, why)
    return QualityReading(True, conf, f"confidence {conf:.2f}")


def parse_rungs(text: Any) -> Tuple[Tuple[Rung, ...], str]:
    """``(rungs, note)``: the table the text describes, or the defaults with a
    note saying why the text was refused. A refused table is never silently
    "close enough" -- the note travels onto the check line and the audit
    record, because a ladder an operator typed that is not the one sizing
    their trades is the /vault hint shape pointed at an env var.
    """
    raw = str(text or "").strip()
    if not raw:
        return _DEFAULT_RUNGS, ""
    rungs: list[Rung] = []
    for chunk in raw.split(","):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) != 4 or not parts[0] or not parts[0].isalnum():
            return _DEFAULT_RUNGS, f"malformed rung {chunk.strip()!r}; defaults in use"
        floor, smult, lmult = (_num(parts[1]), _num(parts[2]), _num(parts[3]))
        if floor is None or smult is None or lmult is None:
            return _DEFAULT_RUNGS, f"unreadable number in rung {chunk.strip()!r}; defaults in use"
        if not (0.0 <= floor <= 1.0):
            return _DEFAULT_RUNGS, f"rung {parts[0]} floor {floor:g} outside [0, 1]; defaults in use"
        if not (0.0 < smult <= 1.0) or not (0.0 < lmult <= 1.0):
            # Refused, not clamped: see the module header.
            return _DEFAULT_RUNGS, (f"rung {parts[0]} multiplier outside (0, 1] -- "
                                    "the ladder is tighten-only; defaults in use")
        rungs.append(Rung(parts[0], floor, smult, lmult))
    if not rungs:
        return _DEFAULT_RUNGS, "empty table; defaults in use"
    floors = [r.floor for r in rungs]
    if any(a <= b for a, b in zip(floors, floors[1:])):
        return _DEFAULT_RUNGS, "rung floors are not strictly descending; defaults in use"
    if rungs[-1].floor != 0.0:
        return _DEFAULT_RUNGS, ("the last rung's floor must be 0.0 so every confidence "
                                "lands on a rung; defaults in use")
    if len({r.name for r in rungs}) != len(rungs):
        return _DEFAULT_RUNGS, "duplicate rung names; defaults in use"
    return tuple(rungs), ""


_DEFAULT_RUNGS: Tuple[Rung, ...] = (
    Rung("A", 0.85, 1.0, 1.0),
    Rung("B", 0.70, 0.75, 0.8),
    Rung("C", 0.0, 0.5, 0.6),
)
assert parse_rungs(DEFAULT_RUNGS_TEXT) == (_DEFAULT_RUNGS, ""), \
    "the default text must parse to the default table"
DEFAULT_RUNGS = _DEFAULT_RUNGS


def rungs_from_config(cfg: Any = None) -> Tuple[Tuple[Rung, ...], str]:
    """The configured table, read from ``CONFIG.risk.quality_ladder_rungs``
    (or the ``cfg`` handed in -- the risk engine passes its own module-level
    CONFIG so a test that plants one drives this too). Never raises."""
    try:
        if cfg is None:
            from bot.config import CONFIG
            cfg = CONFIG.risk
        text = getattr(cfg, "quality_ladder_rungs", DEFAULT_RUNGS_TEXT)
    except Exception:
        return _DEFAULT_RUNGS, "config unreadable; defaults in use"
    return parse_rungs(text)


def rung_for(reading: QualityReading,
             rungs: Tuple[Rung, ...] = DEFAULT_RUNGS) -> Optional[Rung]:
    """The first rung whose floor the measured confidence reaches (AT the floor
    counts: a rung is ``confidence >= floor``). None for an unmeasured reading."""
    if not reading.measured or reading.confidence is None:
        return None
    for r in rungs:
        if reading.confidence >= r.floor:
            return r
    return None


def ladder_verdict(idea: Any, rungs: Optional[Tuple[Rung, ...]] = None,
                   table_note: str = "") -> LadderVerdict:
    """The ladder's answer for one idea. Multipliers are 1.0 for an unmeasured
    quality -- no rung, no reduction -- and ``why`` is the clause every line
    that prints the verdict shares: ``rung B at confidence 0.72`` for a
    measured one, the reading's own reason plus ``no rung`` otherwise."""
    if rungs is None:
        rungs, table_note = rungs_from_config()
    reading = quality_reading(idea)
    if not reading.measured:
        return LadderVerdict(False, None, None, 1.0, 1.0,
                             f"{reading.why}; no rung", table_note)
    rung = rung_for(reading, rungs)
    if rung is None:   # unreachable while parse_rungs pins the last floor at 0.0
        return LadderVerdict(True, reading.confidence, None, 1.0, 1.0,
                             f"{reading.why} reaches no rung", table_note)
    return LadderVerdict(
        True, reading.confidence, rung.name, rung.size_mult, rung.leverage_mult,
        f"rung {rung.name} at {reading.why}", table_note)


def ladder_leverage(standard: int, verdict: LadderVerdict, floor: int = 1) -> int:
    """The leverage this rung allows, REDUCE-ONLY from ``standard`` and never
    under ``floor``. Identical to ``standard`` for an unmeasured quality or a
    rung whose leverage multiplier is 1.0."""
    try:
        base = max(1, int(standard))
    except (TypeError, ValueError):
        return 1
    lo = max(1, int(floor))
    if verdict.leverage_mult >= 1.0:
        return base
    reduced = int(base * verdict.leverage_mult)
    # Floor first, then never above the standard: the other order answered
    # 2 for a 1x standard under a 2x floor -- a reduce-only rule RAISING the
    # leverage, found by the fixture at exactly that boundary.
    return min(base, max(lo, reduced))


def kelly_confidence_factor(idea: Any) -> Tuple[float, str]:
    """The confidence Kelly's half-fraction is scaled by: the measured figure,
    or 1.0 (no claim) for an unmeasured one -- which is what a manual ticket
    has always received, now by reading rather than by stamp."""
    reading = quality_reading(idea)
    if reading.measured and reading.confidence is not None:
        return reading.confidence, reading.why
    return 1.0, f"{reading.why}: half-Kelly unscaled"


def auto_confirm_refusal(idea: Any) -> Optional[str]:
    """Why ``idea`` may not be auto-confirmed, or ``None`` when it may.

    Auto-confirm bypasses the human-decision gate, so what it has to ask of a
    confidence is not *what number is it* but *is it a MEASUREMENT I may act on
    with nobody looking*. `quality_reading` is that question and three
    money-facing consumers already take it -- the sizing ladder, Kelly's
    half-fraction, and `_high_conviction_margin`, whose own comment says a
    manual ticket's stamp "cleared every floor by construction". The EXECUTION
    gate never asked, so a hand-typed ticket's 1.0 cleared the bar the moment
    it was registered and the Confirm button under its card decided nothing.

    NO SOURCE LIST, IN EITHER DIRECTION, and that is measured rather than
    preferred. The autonomous path's own idea (`analyzer.py`'s ``TradeIdea(``)
    sets no ``source`` at all and carries the field's default ``"unknown"``, so
    an allowlist would have to admit the value a forgotten argument produces --
    the one value that must never be the value a mistake makes. A denylist
    naming ``"manual"`` is the `/setllm` ten-of-eleven shape, where the source
    added tomorrow is the one missing from it, and it would be a THIRD copy of
    a judgement production already writes twice.

    THE WRITER SET IS FOUR SOURCES, and it has been counted wrong twice. An
    earlier draft said EIGHT: it listed every ``source=`` literal in the tree,
    and ``getclaw``, ``swarm`` and ``mcp_shield`` build a `TradeIdea` that
    never reaches ``_pending_ideas`` at all (zero references in any of the
    three files) -- coverage claimed, not held. The correction said FIVE, which
    was right on the day and counted ``"scan_skill_retry"`` as its own source;
    that site now builds through `drift_offer.reanalyzed_idea` like its twin,
    so the string is retired and nothing in the tree writes or reads it. What
    writes there is the analyzer (``"unknown"``, via ``_analyze_signal`` --
    the tick, the force scan, and four `skill_registry` sites), `scan_skill`,
    the drift re-analysis (``"auto_reanalyze"``, from BOTH retry sites), and
    `manual`. This reading refuses the last; the door below also refuses a
    re-offer, by its provenance rather than by its source.

    AN UNREADABLE CONFIDENCE IS REFUSED TOO, and it is the same rule rather
    than a second one -- `auto_confirm_is_disabled`'s own docstring says a
    threshold nobody could read "is not a licence to place real-money orders
    without a human, and this is the one direction in which an unreadable value
    must not fail open". Stated honestly, it is a BACKSTOP and not a live path:
    ``TradeIdea.confidence`` is ``Field(ge=0.0, le=1.0)``, and driven, pydantic
    refuses NaN, an infinity, 2.5, -0.5 and None at construction (a bool
    coerces to 1.0). So no idea in ``_pending_ideas`` can carry one today, and
    what this half guards is the day that field is loosened or a caller hands
    the reading something that is not a `TradeIdea`.

    It does NOT refuse a manual ticket's PROPOSAL: `clears_confidence_floor`
    still admits one, the card is still built, and the Confirm button still
    executes it under the caller's own id. Proposing and executing are
    different acts and only the second one is being narrowed here.

    TWO QUESTIONS, NOT ONE, and the paragraph that used to sit here said the
    second could not be asked. It read: "no reading of a confidence can close
    it: the defect there is the GEOMETRY, not the number." Half right, and the
    wrong half decided the design. The geometry really is different -- driven,
    the drift re-offer's reward:risk is ``TARGET_PCT/STOP_PCT``, a CONSTANT, so
    an analyst thesis at 15:1 and one at 0.1:1 both come out ~2:1. But what
    closes it IS about the confidence: not its VALUE, its SUBJECT. A re-offer's
    number was measured, honestly, about a trade that is not this one, and
    `confidence_inherited_from` is the producer saying which -- so no reader
    has to infer it from a source string.

    SO THE QUESTION IS ASKED HERE AND NOT IN `quality_reading`, WHICH IS
    MEASURED RATHER THAN PREFERRED. That reading looks like the natural home
    and its consumers take "unmeasured" as *abstain*, not as *be careful*:
    `ladder_verdict` answers 1.0 ("no rung, no reduction") and
    `kelly_confidence_factor` answers 1.0 ("half-Kelly unscaled"). Driven on a
    re-offer of a 0.30-confidence thesis, routing it through there moves size
    x0.50 -> x1.00, leverage 3x -> 5x and Kelly x0.30 -> x1.00: it would
    DOUBLE the weakest re-offers and raise their leverage while closing this
    door. Refusing an execution and abstaining from a reduction are opposite
    consequences of one word, so the door asks its own question and the sizing
    path is left byte-identical -- driven, a re-offer still takes the rung its
    original's confidence buys.

    THAT IS STILL NOT A SOURCE LIST, and the distinction is drivable rather
    than asserted: an idea carrying ``source="auto_reanalyze"`` and nothing
    else PASSES, while `reanalyzed_idea`'s real product is refused. Same
    source, two verdicts, decided by the field the builder set.

    WHAT IT STILL CANNOT SEE, stated because a gate whose coverage is
    overstated is the failure this repo's gates exist to prevent: a producer
    that copies a confidence across a geometry change and does NOT set the
    field. There is one such producer today and it sets it. A second one
    written tomorrow would pass here, and nothing in this module can notice --
    which is why `tests/test_a_drift_re_offer_is_not_auto_confirmed.py` walks
    the tree for a `TradeIdea(` built from another object's ``.confidence`` and
    fails on one that neither declares its provenance nor carries a reason in
    `tests/confidence_provenance_baseline.txt`.
    """
    reading = quality_reading(idea)
    # `and reading.confidence is not None` was here and is DELETED: the one
    # return that answers measured=True is `QualityReading(True, conf, ...)`
    # below a guard that has already refused a None `conf`, so no input can
    # make that clause false -- and a line no input can reach is a claim that
    # there is a check. The property it was claiming is driven in the guard
    # instead, so the day that return type changes a test fails rather than
    # this reading quietly starting to admit an unmeasured one.
    if not reading.measured:
        return reading.why
    inherited = getattr(idea, "confidence_inherited_from", None)
    if inherited:
        # Measured, and not about this trade. See the docstring for why this
        # sits here rather than inside the reading above.
        return (f"confidence carried from {inherited}: measured about that "
                f"idea's levels, not these")
    return None


#: The word a learning record carries when its confidence was measured about
#: its own trade. Anything else in that field is the reason it was not.
MEASURED_BASIS = "measured"


def confidence_basis(idea: Any) -> str:
    """``"measured"``, or why this idea's confidence is not a measurement of
    this trade -- the words a decision row records for the learners.

    It is `auto_confirm_refusal`'s question and answers from it, because the
    calibrator's question is the same one: may this number be read as what the
    engine measured about THIS trade? A manual ticket's 1.0 is a stamp, and the
    calibrator joined it to the ticket's outcome as a measured 100%, in the top
    bin, the one the auto-confirm threshold is read against. A drift re-offer's
    confidence was measured about another trade's levels. A second copy of that
    judgement here would be a second answer, and the day the door learns a
    third kind of number that is not a measurement, the record learns it too.
    """
    why = auto_confirm_refusal(idea)
    return MEASURED_BASIS if why is None else why
