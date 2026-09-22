r"""A stamped confidence is not a licence to skip the human who was asked.

`RuneClawEngine._pending_ideas` is not only the autonomous book. Every
hand-typed ticket goes through it too -- `manual_trade.register_manual_idea`,
reached from `/trade` on Telegram and from the web's propose route -- and
`build_manual_idea` writes ``confidence=1.0`` on each one. Nothing measured
that; it is the value that clears every bar.

Two loops swept that dict and executed anything clearing the auto-confirm
threshold, with no filter on the idea at all:

    bot/core/engine.py  _tick               (the autonomous tick, every 60s)
    bot/core/engine.py  _force_scan_locked  (the /forcescan button)

Driven at the dataclass defaults (``AUTO_CONFIRM_THRESHOLD`` 0.85,
``AUTO_CONFIRM_LIVE_ENABLED`` True), the gate value of a manual ticket is
``1.0``, ``1.0 >= 0.85`` is True, and the live suppression below it does not
fire. So the card that had just been sent WITH A CONFIRM BUTTON AND A CO-PILOT
REVIEW -- the block that exists so a person decides -- was executed without
them.

NOT "on the next tick", and the difference decides what a fixture has to do.
`_tick` returns early while anything is pending (C2-26's `if
self._pending_ideas:` skip) and `_force_scan_locked` CLEARS the dict before it
scans, so a ticket merely SITTING there blocks the tick or is destroyed by the
button -- a guard that plants it before the cycle starts never reaches the
branch and measures nothing. What reaches it is a ticket registered DURING a
cycle's scan/analyze window: `register_manual_idea` takes no lock, and the
window is bounded by the scan sweep plus a 300s analyze cap, which is minutes
wide against a user action that takes seconds to type. The drive below
registers the ticket from inside that await, which is the race made
deterministic.

WHAT MAKES IT WORSE THAN A BYPASSED BUTTON, each read off the code:

  * it confirms as ``user_id="auto"``, so a regular user's ticket executes on
    the OPERATOR account, and `_confirm_trade_inner`'s per-user strategy gate
    (``if user_id and user_id != "auto":``) is skipped for it;
  * `is_manual` is TRUE on that ticket, so the price-drift and stale-R:R checks
    are skipped -- a concession made BECAUSE a human is looking at the card;
  * `/trade` and the web route both audit ``result="PENDING"``, a record that
    claims the ticket awaits a decision that a loop has already taken.

THREE WRITTEN CLAIMS WERE FALSE UNTIL THIS FIX, and the fix is what makes them
true rather than a wording change:

  * `.env.example`, beside the very knob: "a regular user's trade is NEVER
    auto-confirmed";
  * `bot/skills/chat_runtime.py`'s door rule -- the sentence given to the chat
    MODEL on the surface with the money -- "/trade renders a Confirm/Cancel
    card and `register_manual_idea` places nothing", explicitly "read off the
    code";
  * the RC-AUD-002 comment over the gate itself, which called it "disabled by
    default (threshold 1.0)" and said LIVE mode "refuses to place real-money
    orders unless AUTO_CONFIRM_LIVE_ENABLED is explicitly set". Both halves
    were false of the dataclass defaults, in the flattering direction.

WHY NO GUARD CAUGHT THE COMMENT, recorded rather than fixed.
`tests/default_comments.py` exists for exactly this class of error and the
tree is at zero with no baseline, so it is worth saying why it declines this
one -- and it is neither the trigger vocabulary nor the window. Two
independent declensions, each read off the rule:

  * ``_CONFIG_REF = re.compile(r"CONFIG\.(\w+)\.(\w+)")`` demands a SECTIONED
    two-dot reference, and `auto_confirm_live_enabled` is read FLAT
    (``CONFIG.auto_confirm_live_enabled``), so the resolver never sees it;
  * ``_DECL`` collects only ``: bool = _env_bool(...)``, and
    `auto_confirm_threshold` is a FLOAT whose "off" is the sentinel 1.0, so it
    is absent from `declared_defaults()` altogether and no resolver fix
    reaches it.

Widening the first is a slice of its own, not a line: it would put a body of
flat-attribute comments under the rule at once, each needing a reading, and
done alone it ACCUSES the correction above -- a retraction has to name the
sentence it corrects, so the false literal is still in the file. That is the
`@staticmethod` reachability gap's ruling one guard over: measured, named,
and left to the slice that can triage what it catches.

THE READING ALREADY EXISTED. `quality_ladder.quality_reading` answers
``measured=False`` for ``source == "manual"`` with "confidence is a stamp, not
a measurement", and three money-facing consumers take it -- the sizing ladder,
Kelly's half-fraction, and `_high_conviction_margin`, whose own comment says a
stamp "cleared every floor by construction". The EXECUTION gate did not ask.
That is the confidence-clamp slice's shape one gate over: the reading is
there, and the money-facing caller reads the field raw.

NO SOURCE LIST, and that is measured rather than preferred -- see
`test_the_refusal_is_derived_not_a_list` below.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core.adaptive_threshold import auto_confirm_is_disabled
from bot.core.engine import RuneClawEngine
from bot.risk.quality_ladder import auto_confirm_refusal, quality_reading
from bot.skills.manual_trade import build_manual_idea
from bot.utils.models import Direction, TradeIdea

ROOT = Path(__file__).resolve().parent.parent
ENGINE_SRC = (ROOT / "bot" / "core" / "engine.py").read_text()


def _idea(**kw):
    base = dict(asset="BTC/USDT:USDT", direction=Direction.LONG,
                entry_price=60000.0, stop_loss=59000.0, take_profit=63000.0,
                confidence=0.86, reasoning="x")
    base.update(kw)
    return TradeIdea(**base)


def _manual():
    return build_manual_idea("LONG", "BTC", 60000.0, 59000.0, 63000.0)


# ---------------------------------------------------------------------------
# The condition that makes this reachable at all.
# ---------------------------------------------------------------------------

def test_the_defect_is_reachable_at_the_dataclass_defaults():
    """Not an exotic configuration: it is what an absent `.env` key gives.

    `.env.example` ships the safe pair (1.0 / false) and
    `test_human_confirmation_claim_is_qualified.py` rests on that, correctly.
    But the dataclass defaults are what an operator gets for a key they did
    not write -- and they are also what this same file recommends for the
    admin auto-trade policy, "set 0.85 AND enable live auto-confirm".
    """
    assert CONFIG.auto_confirm_threshold == 0.85, (
        "AUTO_CONFIRM_THRESHOLD's default; if this moves, the comment over "
        "the gate in engine.py moves with it")
    assert auto_confirm_is_disabled(CONFIG.auto_confirm_threshold) is False, (
        "at the dataclass default the auto-confirm loop RUNS")
    assert CONFIG.auto_confirm_live_enabled is True, (
        "so the SUPPRESSED_LIVE branch does not fire either")


def test_a_manual_ticket_clears_the_threshold_which_is_why_a_reading_is_needed():
    """The stamp really does clear the bar -- refusing it is the whole slice.

    Without this the refusal below could pass against a ticket that would
    never have been picked up anyway, which measures nothing.
    """
    idea = _manual()
    assert idea.source == "manual" and idea.confidence == 1.0
    gate = RuneClawEngine._auto_confirm_gate_value(
        SimpleNamespace(analyzer=None), idea)
    assert gate == 1.0
    assert gate >= CONFIG.auto_confirm_threshold, (
        "a hand-typed ticket clears the auto-confirm bar by construction")


# ---------------------------------------------------------------------------
# The reading.
# ---------------------------------------------------------------------------

def test_a_manual_ticket_is_refused_by_name():
    why = auto_confirm_refusal(_manual())
    assert why is not None, "a stamp is not a measurement"
    assert "stamp" in why, f"and the reason says so, not just 'refused': {why!r}"


def test_the_refusal_is_derived_not_a_list():
    """Every writer into `_pending_ideas`, with the confidence it really sets.

    This is the argument for asking the READING rather than keeping a source
    list. An allowlist would have to admit ``"unknown"`` -- `analyzer.py`'s
    `TradeIdea(` sets no ``source`` at all, so the autonomous path carries the
    field's default, and admitting it admits every forgotten argument. A
    denylist naming "manual" is the shape where the source added tomorrow is
    the one missing from it. The derived reading needs neither: it refuses
    exactly one of the eight and passes the other seven.
    """
    # THE FIVE SOURCES THAT REALLY WRITE INTO `_pending_ideas`. An earlier
    # draft of this table listed eight -- every `source=` literal in the tree.
    # Driven, `getclaw`, `swarm` and `mcp_shield` build a TradeIdea that never
    # reaches that dict (zero `_pending_ideas` references in any of the three
    # files), so naming them claimed coverage this table does not have.
    cases = [
        ("analyzer (the autonomous tick)", _idea(confidence=0.86)),
        ("scan_skill", _idea(confidence=0.6, source="scan_skill")),
        ("scan_skill_retry", _idea(confidence=0.6, source="scan_skill_retry")),
        ("auto_reanalyze (the drift re-offer)",
         _idea(confidence=0.86, source="auto_reanalyze")),
    ]
    for name, idea in cases:
        assert auto_confirm_refusal(idea) is None, (
            f"{name} is an autonomous idea with a measured confidence and must "
            f"still auto-confirm; refusing it would stop the engine trading")
    assert auto_confirm_refusal(_manual()) is not None, (
        "and the manual ticket is the one that is refused")
    assert _idea().source == "unknown", (
        "the autonomous idea carries the field DEFAULT -- this is the fact "
        "that rules an allowlist out, so it is asserted rather than assumed")


@pytest.mark.parametrize("bad, label", [
    (None, "absent"),
    (float("nan"), "NaN"),
    (float("inf"), "infinite"),
    (2.5, "above 1"),
    (-0.5, "below 0"),
])
def test_an_unreadable_confidence_is_refused_too(bad, label):
    """The same rule, not a second one.

    `auto_confirm_is_disabled`'s own docstring says a threshold nobody could
    read "is not a licence to place real-money orders without a human, and
    this is the one direction in which an unreadable value must not fail
    open". The value side is no different.

    STATED AS A BACKSTOP, NOT A LIVE PATH, because that is what it is: these
    drive the READING through a stand-in, and no idea in `_pending_ideas` can
    carry one of these values -- see the pydantic test below. What this half
    guards is the day that field is loosened.
    """
    idea = SimpleNamespace(asset="BTC/USDT:USDT", source="scan_skill",
                           confidence=bad)
    assert auto_confirm_refusal(idea) is not None, (
        f"a {label} confidence is not a measurement to act on unattended")


def test_a_measured_zero_is_still_a_measurement():
    """`0.0` is a real reading of a worthless setup, and it is refused by the
    THRESHOLD, not by this reading. Collapsing the two would be the
    unreadable-is-zero shape in the gate written to remove it."""
    assert auto_confirm_refusal(_idea(confidence=0.0)) is None


def test_it_is_the_one_reading_not_a_second_copy(monkeypatch):
    """Planted, because a byte-identical copy agrees with every fixture.

    If `auto_confirm_refusal` restated the manual check instead of asking
    `quality_reading`, every assertion above would still pass and the two
    would diverge on the first edit to either.
    """
    import bot.risk.quality_ladder as ql
    sentinel = "PLANTED: the reading was asked"
    monkeypatch.setattr(
        ql, "quality_reading",
        lambda idea: ql.QualityReading(False, None, sentinel))
    assert auto_confirm_refusal(_idea(confidence=0.9)) == sentinel, (
        "the refusal must answer FROM quality_reading, not restate it")


# ---------------------------------------------------------------------------
# The helper both loops ask.
# ---------------------------------------------------------------------------

def _host():
    """A stand-in `self` carrying only what `_auto_confirm_suppressed` reaches."""
    host = SimpleNamespace()
    host._auto_confirm_suppressed = (
        RuneClawEngine._auto_confirm_suppressed.__get__(host))
    return host


def test_the_helper_suppresses_a_manual_ticket_and_says_why(monkeypatch):
    """The refusal is AUDITED, not silent.

    A ticket that quietly stops being auto-confirmed and one nobody proposed
    look identical from the trade log, and only one of them is a person
    waiting for a tap. `caplog` cannot see this: `audit` is the repo's own
    recorder, not a bare logger call, so it is planted the way every other
    audit assertion in this suite plants it.
    """
    import bot.core.engine as eng
    seen: list[dict] = []
    monkeypatch.setattr(eng, "audit",
                        lambda *a, **kw: seen.append({"msg": a[1] if len(a) > 1 else "",
                                                     **kw}))
    assert _host()._auto_confirm_suppressed("T-1", _manual()) is True
    assert seen, "the suppression is recorded"
    rec = seen[-1]
    assert rec.get("result") == "SUPPRESSED_UNMEASURED", (
        "under its own result word, so an operator can tell it from "
        "SUPPRESSED_LIVE beside it")
    assert rec.get("data", {}).get("trade_id") == "T-1"
    assert rec.get("data", {}).get("source") == "manual", (
        "the record names the source, which is what sends a reader to the "
        "right door")
    assert "stamp" in rec["msg"], (
        "and the sentence says WHY, not just that it was refused")


def test_the_helper_lets_a_measured_idea_through():
    """Without this arm the test above passes against a helper that suppresses
    everything -- which would stop the engine trading altogether."""
    assert _host()._auto_confirm_suppressed("T-2", _idea(confidence=0.9)) is False


# ---------------------------------------------------------------------------
# The wiring: both loops, driven where a drive is possible.
# ---------------------------------------------------------------------------

def _fn(name: str) -> ast.AST:
    """The ONE definition of `name` in engine.py, from the raw source.

    Asserting there is exactly one is the `command_gates.py` lesson: a second
    definition (a typing stub, an overload) makes a by-name lookup ambiguous,
    and a guard that silently resolves the wrong one measures nothing.
    """
    tree = ast.parse(ENGINE_SRC)
    found = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == name]
    assert len(found) == 1, f"{name} is defined {len(found)}x"
    return found[0]


@pytest.mark.parametrize("fn, asks", [
    ("_tick", "_auto_confirm_batch"),
    ("_force_scan_locked", "_auto_confirm_suppressed"),
])
def test_both_loops_ask_the_reading_before_they_confirm(fn, asks):
    """A SHAPE assertion, and stated as one.

    `_tick` is 434 lines behind a scanner, an analyzer and an exchange, so the
    honest instrument for it is the order of two calls rather than a fixture
    that stands all of that up. `_force_scan_locked` IS driven, below -- this
    pins the pair so the two gates cannot drift, which is what
    `SCAN_DISPATCH` records about two dispatchers with one meaning.

    The ORDER is the claim a name scan cannot make: a refusal that runs after
    the order has been placed is invisible from the source literal.
    """
    node = _fn(fn)
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]

    def _attr(c):
        return getattr(c.func, "attr", None)

    suppressed = [c.lineno for c in calls if _attr(c) == asks]
    confirmed = [c.lineno for c in calls if _attr(c) == "confirm_trade"]
    assert suppressed, f"{fn} must ask {asks}"
    assert confirmed, f"{fn} is expected to call confirm_trade at all"
    assert min(suppressed) < min(confirmed), (
        f"{fn} asks the reading BEFORE it places the order")


@pytest.mark.asyncio
async def test_a_ticket_registered_mid_scan_is_not_auto_confirmed_by_forcescan():
    """Driven end to end through the real `_force_scan_locked`.

    REACHABILITY, STATED RATHER THAN OVERCLAIMED. That method CLEARS
    `_pending_ideas` before it scans, so a ticket registered *before*
    `/forcescan` is destroyed rather than auto-confirmed (a separate defect --
    the Confirm button then answers "Trade not found or expired"). The window
    that reaches this loop is a ticket registered DURING the scan, and the two
    awaits it spans -- `scanner.scan()` and `_analyze_signals_batched` -- are
    the slowest things the engine does. The fixture registers the ticket from
    inside that second await, which is the race made deterministic.
    """
    confirmed: list[str] = []
    manual = _manual()
    engine = SimpleNamespace()

    async def _scan():
        return [SimpleNamespace(symbol="ETH/USDT:USDT")]

    async def _batched(signals, lightweight=False):
        # the race: a user types /trade while the scan is in flight
        engine._pending_ideas[manual.id] = manual
        return [_idea(asset="ETH/USDT:USDT", confidence=0.95)]

    async def _confirm(tid, user_id=""):
        confirmed.append(tid)
        return "ok"

    engine._pending_ideas = {}
    engine._pending_atr = {}
    engine._pending_timing = {}
    engine._pending_pyramid = {}
    engine._cooldown_until = 0.0
    engine._last_scan_signals = []
    engine.scanner = SimpleNamespace(scan=_scan)
    engine._transition = lambda *a, **k: None
    engine._analyze_signals_batched = _batched
    engine.confirm_trade = _confirm
    engine._auto_confirm_notify_callback = None
    engine.analyzer = None
    for name in ("_force_scan_locked", "_auto_confirm_gate_value",
                 "_auto_confirm_suppressed"):
        setattr(engine, name, getattr(RuneClawEngine, name).__get__(engine))

    summary = await engine._force_scan_locked()

    assert manual.id not in confirmed, (
        "the hand-typed ticket must NOT be auto-confirmed -- its Confirm "
        "button is the door, and `.env.example` promises a regular user's "
        "trade is never auto-confirmed")
    assert confirmed, (
        "and the engine's OWN idea still auto-confirms -- without this arm "
        "the assertion above passes against a loop that stopped working")
    assert summary["auto_confirmed"] == 1, (
        "the count reports what was confirmed, not what was swept")
    assert manual.id in engine._pending_ideas, (
        "the ticket is left PENDING for its human, not dropped")


# ---------------------------------------------------------------------------
# The written claims the fix makes true.
# ---------------------------------------------------------------------------

def test_the_env_example_promise_is_now_kept():
    """Two-way: the sentence is pinned AND the behaviour that makes it true.

    Asserting the sentence alone is the shape this repo keeps finding -- a
    guard that pins a claim nothing enforces.
    """
    raw = (ROOT / ".env.example").read_text()
    # The sentence WRAPS across two `# ` lines, so a literal search for it
    # finds nothing -- the trap `tests/default_comments.py` joins whole
    # comment blocks to avoid, met here from the author's side.
    joined = " ".join(
        line.lstrip().lstrip("#").strip() for line in raw.splitlines())
    joined = " ".join(joined.split())
    assert "a regular user's trade is NEVER auto-confirmed" in joined, (
        "if this sentence is reworded, re-point the behaviour assertion below")
    assert auto_confirm_refusal(_manual()) is not None, (
        "...and this is what makes it true")


def test_the_gate_comment_names_the_defaults_the_flags_have():
    """A PRESENCE assertion, deliberately.

    The correction has to name what it corrected, so the block quotes the
    false sentence it replaced -- and a bare scan for that sentence matches
    the fix and reports it as the defect. This repo has watched that misfire
    from the author's side three slices running. So the claim asserted is that
    the block states the REAL defaults beside the retraction.
    """
    node = _fn("_tick")
    seg = ast.get_source_segment(ENGINE_SRC, node) or ""
    block = seg[:seg.index("auto_threshold = RUNTIME.auto_confirm_threshold")]
    # NOT a bare "0.85": the block also quotes this file's admin policy
    # ("set 0.85 AND enable live auto-confirm"), so the digits alone are
    # satisfied by a sentence that says nothing about the DEFAULT -- which is
    # how the mutation reverting this comment survived the first round.
    assert "defaults to 0.85" in block, (
        "the comment names the threshold's real default")
    assert "defaults to True" in block, (
        "and that AUTO_CONFIRM_LIVE_ENABLED's default does not suppress")


def test_the_chat_door_rule_still_says_it_places_nothing():
    """The sentence given to the chat MODEL on the live surface.

    It was false; this slice is what makes it true, so it is pinned HERE
    rather than left to the file that states it -- a claim about one module
    checked by the module whose behaviour decides it.
    """
    # Read RAW: the rule is a `#:` doc comment over the constant it explains,
    # so `code_only` -- which blanks comments, and is right to -- deletes the
    # very sentence being pinned. "Strip comments first" is the advice for a
    # scan looking for CODE; this claim is prose by design.
    src = (ROOT / "bot" / "skills" / "chat_runtime.py").read_text()
    assert "register_manual_idea` places nothing" in src, (
        "if this rule is reworded, the behaviour pinned in this file is what "
        "it was asserting")


def test_a_measured_reading_always_carries_its_confidence():
    """The property `auto_confirm_refusal` relies on instead of re-checking.

    Its first draft asked `reading.measured and reading.confidence is not
    None`; the second clause can never be false, because the one return that
    answers measured=True sits under a guard that has already refused a None
    confidence. The clause is deleted and the property is driven here, so the
    day that changes a test fails rather than the refusal quietly admitting an
    unmeasured idea.
    """
    for idea in (_idea(confidence=0.0), _idea(confidence=1.0),
                 _idea(confidence=0.5, source="swarm")):
        r = quality_reading(idea)
        assert r.measured is True and r.confidence is not None, (
            "measured implies a confidence -- if this ever fails, put the "
            "second clause back in auto_confirm_refusal")


# ---------------------------------------------------------------------------
# The tick's selection, driven.
# ---------------------------------------------------------------------------

def _batcher(*ideas):
    """A stand-in `self` carrying only what `_auto_confirm_batch` reaches."""
    host = SimpleNamespace(analyzer=None)
    host._pending_ideas = {i.id: i for i in ideas}
    for name in ("_auto_confirm_batch", "_auto_confirm_gate_value",
                 "_auto_confirm_suppressed"):
        setattr(host, name, getattr(RuneClawEngine, name).__get__(host))
    return host


def test_the_tick_batch_drops_the_hand_typed_ticket_and_keeps_the_engines():
    """The assertion a source scan cannot make.

    `_tick` is 434 lines behind a scanner, an analyzer and an exchange, so the
    only instrument over this selection used to be an AST pin -- and the
    mutation that appended `or True` to the suppression condition kept the
    call, kept the ordering, and auto-executed every ticket again. This is the
    seam that closed it.
    """
    manual, engine_idea = _manual(), _idea(confidence=0.95)
    batch = _batcher(manual, engine_idea)._auto_confirm_batch(0.85)
    ids = [tid for tid, _ in batch]

    assert engine_idea.id in ids, (
        "the engine's own measured idea still auto-confirms -- without this "
        "arm the assertion below passes against a batch that is always empty")
    assert manual.id not in ids, (
        "and the hand-typed ticket does not, though its stamped 1.0 clears "
        "the bar")


def test_the_batch_is_empty_when_the_operator_switched_it_off():
    """The sentinel still decides first: `1.0` is OFF, not "needs a perfect
    score". Driven here so the new seam cannot quietly lose it."""
    assert _batcher(_idea(confidence=1.0))._auto_confirm_batch(1.0) == []


@pytest.mark.parametrize("bad, label", [
    (float("nan"), "NaN"), (float("inf"), "infinite"),
    (2.5, "above 1"), (-0.5, "below 0"), (None, "absent"),
])
def test_the_model_itself_refuses_an_unreadable_confidence(bad, label):
    """Why the unreadable half of the reading is a backstop and not a path.

    `TradeIdea.confidence` is `Field(ge=0.0, le=1.0)`, so pydantic refuses
    these at construction and nothing in `_pending_ideas` can carry one. Said
    here rather than left implied, because a guard whose coverage is
    overstated is the failure this repo's gates exist to prevent -- and the
    first draft of the docstring beside this claimed the opposite reason.
    """
    with pytest.raises(Exception):
        _idea(confidence=bad)


def test_a_bool_confidence_coerces_rather_than_refusing():
    """The one value that does NOT refuse, recorded because it is surprising.

    `confidence=True` becomes `1.0` -- the manual stamp, arrived at by
    coercion. It is refused here only when the SOURCE says manual, which is
    the reading doing its job for a reason unrelated to the bool.
    """
    assert _idea(confidence=True).confidence == 1.0
    assert auto_confirm_refusal(_idea(confidence=True)) is None
    assert auto_confirm_refusal(_idea(confidence=True, source="manual")) is not None
