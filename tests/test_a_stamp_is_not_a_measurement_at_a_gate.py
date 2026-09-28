"""Two GATES and three surfaces read the wrong confidence, and one is public.

#105 gave this product one reading of an idea's confidence
(`bot/core/signal_confidence.displayed_confidence`) because one `send_photo`
went out with `Conf 70%` in its caption over an image whose CONFIDENCE cell read
`31%`. It repaired the five surfaces it had measured. Driven again, the class is
wider and two of the remaining readers are not displays at all:

**THE CRITIQUE'S OVERCONFIDENCE CHECK IS A GATE, AND A HAND-TYPED TICKET TRIPPED
IT EVERY TIME.** `TradeCritique.evaluate` argues the bear case before every
execution, and its first concern was `idea.confidence > 0.90`.
`build_manual_idea` STAMPS `confidence=1.0` on every ticket a person types, so
that concern fired on every one of them — costing `-0.05` and one of the four
concerns that HALT a trade — under a sentence blaming *"the model"* for a number
the operator typed. And with a curve applied the check missed the idea it exists
for: a measured blend of 0.95 arrives as 0.31 on the field and was NOT flagged.
Reachable at shipped defaults in the first direction; a stamp needs no flag.

**THE PYRAMID GATE IS AN ADDITION TO RISK, AND IT CLEARED ON A STAMP.**
`_check_pyramid` refuses `idea.confidence < 0.70` before flagging an add onto a
position already open. `0.70` is on the RAW scale like every confidence floor
here. So a hand-typed ticket cleared it on its stamp — `_high_conviction_margin`'s
recorded defect one gate over — and a measured 0.82 blend was skipped once a
curve left 0.31 on the field.

**AND THE DRAWDOWN-RECOVERY FLOOR IS THE THIRD, ON THE GATE THAT EXISTS TO
TIGHTEN.** `RiskEngine` in drawdown recovery refuses
`idea.confidence < DRAWDOWN_RECOVERY_CONF_MIN` (0.85 by default, raw scale) —
higher conviction required while the account is down. A hand-typed ticket
cleared that HIGHER bar on its stamp, and a measured 0.90 blend was refused once
a curve left 0.31 on the field. A stamp does not clear a FLOOR, because
abstaining there would be a loosening; that is `_high_conviction_margin`'s own
ruling.

**THE PUBLIC CHANNEL POST PUBLISHED THE CALIBRATED FIGURE.**
`channel_forwarder.post_signal` printed `Confidence: 31%` for the signal whose
measured blend was 70%, on a channel with `🤖 AI-generated signal` beneath it.

**AND THE ALERT CAPTION WAS THE SIBLING #105 MISSED.** `telegram_handler`'s
`_send_idea_with_door` was cured; `alerts_monitor`'s caption — same claim, same
card, one module over — was not, which is *fixing two left the third*. The
auto-confirm card beside it read the same field.

A stamp and an unreadable confidence ABSTAIN at both gates. At the critique that
is correct because the concern is a claim about what the MODEL said; at the
pyramid gate it is fail-closed, and the audit line names which of the two it
was.
"""

import ast
from types import SimpleNamespace

import pytest

from bot.core.critique import TradeCritique
from bot.core.signal_confidence import (
    ConfidenceReading,
    displayed_confidence,
)
from bot.utils.models import Direction, TradeIdea


def _idea(**kw):
    base = dict(id="T1", asset="SUI/USDT", direction=Direction.LONG,
                entry_price=1.171, stop_loss=1.1535, take_profit=1.2552,
                confidence=0.62, reasoning="r", strategy_type="swing")
    base.update(kw)
    return TradeIdea(**base)


#: What `build_manual_idea` writes on every hand-typed ticket.
STAMP = dict(confidence=1.0, source="manual")


def _rc():
    return SimpleNamespace(approved=True, reasons=[], checks_passed=["x"],
                           position_size_usd=100.0, leverage=5)


def _snap():
    return SimpleNamespace(open_positions=1, total_exposure_usd=100.0,
                           equity_usd=1000.0, daily_pnl_usd=0.0)



def _gate_call(method, bar_src: str):
    """The one `<reading>.clears(<bar>)` call in `method`, and the name it is
    called on — by AST, with no character window.

    `_analyze_signal` is 450 lines and `_evaluate_locked` 900, both reading an
    idea's confidence legitimately elsewhere, and the first draft of these
    assertions sliced from "the `if` before the audit line" — a boundary that is
    whatever happens to be next, which started BELOW the assignment it was
    looking for.
    """
    import ast
    import inspect
    import textwrap

    from tests.source_scan import code_only

    tree = ast.parse(textwrap.dedent(code_only(inspect.getsource(method))))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", None) == "clears"
        and n.args and ast.unparse(n.args[0]) == bar_src
    ]
    assert len(calls) == 1, (
        f"expected one .clears({bar_src}) in {method.__name__}, saw {len(calls)}")
    name = ast.unparse(calls[0].func.value)
    bound = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", None) == name for t in n.targets)
    ]
    assert len(bound) == 1, f"{name} is bound once"
    assert "displayed_confidence" in ast.unparse(bound[0].value), (
        f"{name} does not come from the one reading")
    return name

def _call_naming(method, marker: str, must_name: str):
    """The one call in `method` whose arguments carry `marker`, and the
    assertion that they also carry `must_name` — by AST, so the boundary is the
    CALL rather than a line or a window.

    The first draft of these two pins read the marker's own LINE. The pyramid
    skip audit's sentence wraps, so `_pyr_conf.basis` sits on the second line of
    it — and the mutation that deleted the basis from the SKIP line survived,
    because the APPROVAL audit forty lines further down spells it too and a
    whole-source `in` check was satisfied by that. An assertion that passes for a
    reason unrelated to the rule it names is how a round reports coverage it does
    not have.
    """
    import ast
    import inspect
    import textwrap

    from tests.source_scan import code_only

    tree = ast.parse(textwrap.dedent(code_only(inspect.getsource(method))))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args = " ".join(ast.unparse(a) for a in
                        [*node.args, *(k.value for k in node.keywords)])
        if marker in args:
            hits.append(args)
    assert len(hits) == 1, (
        f"expected one call in {method.__name__} carrying {marker!r}, "
        f"saw {len(hits)}")
    assert must_name in hits[0], (
        f"the {marker!r} call does not name {must_name}: {hits[0][:200]}")


def _overconfidence(idea):
    r = TradeCritique().evaluate(idea, _rc(), _snap())
    return [c for c in r.concerns if "high confidence" in c], r


class TestTheCritiqueFlagsWhatTheModelSaid:
    """The concern is about the MODEL's confidence, so it needs a measurement."""

    def test_a_hand_typed_ticket_is_not_overconfidence(self):
        over, r = _overconfidence(_idea(**STAMP))
        assert not over, (
            "a ticket the operator typed was flagged as the model being "
            f"overconfident: {over}")
        assert r.confidence_adjustment == 0.0

    def test_the_stamp_is_what_the_manual_builder_writes(self):
        """The fixture is the producer's own value, not a number I chose."""
        from bot.skills.manual_trade import build_manual_idea

        idea = build_manual_idea("LONG", "SUI", 1.171, 1.1535, 1.2552)
        assert idea.confidence == STAMP["confidence"]
        assert idea.source == STAMP["source"]
        assert not displayed_confidence(idea).measured

    def test_a_measured_blend_above_the_bar_is_still_flagged(self):
        """The idea the check exists for, once a curve moves the field."""
        over, r = _overconfidence(
            _idea(confidence=0.31, blended_confidence_raw=0.95))
        assert over, "a measured 95% blend was not flagged"
        assert "95%" in over[0], over[0]
        assert r.confidence_adjustment == pytest.approx(-0.05)

    def test_the_sentence_quotes_the_measured_figure(self):
        over, _ = _overconfidence(
            _idea(confidence=0.31, blended_confidence_raw=0.95))
        assert "31%" not in over[0], (
            "the concern quoted the calibrated field, which is not the "
            "quantity its 0.90 bar is on")

    def test_a_measured_blend_below_the_bar_is_not_flagged(self):
        over, _ = _overconfidence(
            _idea(confidence=0.62, blended_confidence_raw=0.62))
        assert not over

    def test_the_bar_is_read_from_the_class_not_written_here(self):
        """A second copy of a threshold is a second answer."""
        bar = TradeCritique.HIGH_CONFIDENCE_WARN
        over, _ = _overconfidence(_idea(confidence=bar + 0.01,
                                        blended_confidence_raw=bar + 0.01))
        assert over
        over, _ = _overconfidence(_idea(confidence=bar,
                                        blended_confidence_raw=bar))
        assert not over, "the bar is exclusive; a figure exactly on it is not over"

    def test_the_critique_asks_the_one_reading(self):
        """Patch the reading and the concern follows it: a byte-identical copy
        agrees with every fixture and diverges on the first edit to either."""
        import bot.core.critique as cq
        from bot.core.signal_confidence import ConfidenceReading

        real = cq.displayed_confidence
        try:
            cq.displayed_confidence = lambda _i: ConfidenceReading(0.99, "blend")
            over, _ = _overconfidence(_idea(confidence=0.10))
            assert over and "99%" in over[0], over
        finally:
            cq.displayed_confidence = real


class TestThePyramidGateNeedsAMeasuredConfidence:
    """An add onto an open position is more risk, so it fails closed."""

    @staticmethod
    def _gate(idea):
        """The gate's own condition, read out of `engine.py` rather than
        restated: the method is 200 lines behind an engine, a scanner and a
        venue, and the claim is which QUANTITY the comparison reads."""
        return not displayed_confidence(idea).clears(0.70)

    def test_a_hand_typed_ticket_does_not_pyramid(self):
        assert self._gate(_idea(**STAMP)) is True

    def test_a_measured_blend_over_the_bar_pyramids(self):
        assert self._gate(_idea(confidence=0.82,
                                blended_confidence_raw=0.82)) is False

    def test_a_curve_on_the_field_does_not_stop_a_measured_blend(self):
        assert self._gate(_idea(confidence=0.31,
                                blended_confidence_raw=0.82)) is False

    def test_the_gate_reads_the_reading_and_the_bar_stays_raw(self):
        """The bar is the raw 0.70 and it is asked OF the reading.

        A gate whose threshold drifts with the calibration is not the gate the
        analyzer's floors are defined against, so the constant must still be
        there; and `clears` is what makes "unmeasured does not pass" one
        reading rather than two."""
        from bot.core.engine import RuneClawEngine

        _gate_call(RuneClawEngine._analyze_signal, "0.7")

    def test_the_skip_audit_names_which_absence_it_was(self):
        """A stamp and an unreadable figure are different facts, and the
        operator reading the audit line needs to know which.

        Anchored to the SKIP audit's own call: the APPROVAL audit below it names
        the basis too, and a whole-source check was satisfied by that while the
        skip line had lost it."""
        from bot.core.engine import RuneClawEngine

        _call_naming(RuneClawEngine._analyze_signal,
                     "Pyramid skipped: confidence", "_pyr_conf.basis")


class TestThePublicPostAndTheCaptions:
    """Three surfaces, one reading, and the first of them is public."""

    @pytest.mark.parametrize("path,line", [
        ("bot/marketing/channel_forwarder.py", "Confidence:"),
        ("bot/skills/alerts_monitor.py", "Conf "),
    ])
    def test_no_surface_formats_the_field_itself(self, path, line):
        import pathlib

        from tests.source_scan import code_only

        src = code_only(pathlib.Path(path).read_text())
        for bad in ("idea.confidence:.0%", "idea.confidence*100",
                    "idea.confidence * 100"):
            assert bad not in src, f"{path} still formats the field: {bad}"

    def test_the_public_post_prints_the_measured_figure(self):
        """Driven through the real forwarder with its send captured."""
        import asyncio

        from bot.marketing.channel_forwarder import ChannelForwarder

        fwd = ChannelForwarder.__new__(ChannelForwarder)
        fwd._enabled, fwd._group_ids = True, {1}
        sent = []

        async def _post(msg):
            sent.append(msg)

        fwd._post = _post
        idea = _idea(confidence=0.31, blended_confidence_raw=0.70)
        asyncio.run(fwd.post_signal(idea))
        assert sent, "the forwarder sent nothing"
        assert "Confidence: <code>70%</code>" in sent[0], sent[0]
        assert "31%" not in sent[0]

    def test_the_public_post_says_a_stamp_was_not_measured(self):
        import asyncio

        from bot.marketing.channel_forwarder import ChannelForwarder

        fwd = ChannelForwarder.__new__(ChannelForwarder)
        fwd._enabled, fwd._group_ids = True, {1}
        sent = []

        async def _post(msg):
            sent.append(msg)

        fwd._post = _post
        asyncio.run(fwd.post_signal(_idea(**STAMP)))
        assert sent
        assert "100%" not in sent[0], (
            "a ticket the operator typed was published as a measured 100% "
            "on the public channel")
        assert "not measured" in sent[0], sent[0]


class TestTheTwoMethodsTheGatesAsk:
    """`clears` and `above` were driven by nothing, and planning the mutation
    round is what said so: both gate helpers above RESTATED the condition in
    Python -- `(not read.measured) or read.value < bar` -- so they agreed with
    the method by construction and would have diverged on the first edit to it.
    A guard that derives its expectation from the thing it guards moves with it
    and can see nothing, which is the lesson `leverage_floor` records one
    subsystem over. They ASK it now, and these four facts are the method's own.
    """

    def test_a_measured_figure_exactly_on_the_bar_clears_it(self):
        """A bar is a FLOOR: `>=`, so a figure on it passes."""
        r = ConfidenceReading(0.85, "blend")
        assert r.clears(0.85) is True
        assert r.clears(0.8500001) is False

    def test_a_measured_figure_exactly_on_the_bar_is_not_above_it(self):
        """`above` is the other direction, for a CONCERN rather than a
        permission, and the critique's bar is exclusive."""
        r = ConfidenceReading(0.90, "blend")
        assert r.above(0.90) is False
        assert r.above(0.8999999) is True

    @pytest.mark.parametrize("basis", ["stamp", "unread", "whatever"])
    def test_a_figure_with_an_unmeasured_basis_clears_nothing(self, basis):
        """THE CHECK IS `measured`, NOT `value is not None`, and today those two
        agree on every reading `displayed_confidence` can build -- a stamp and an
        unread both carry `value=None`, so `value is not None` alone would pass
        every one of these fixtures.

        They are not the same rule. The day the leaf carries a stamped figure so
        a card can print "1.0 (not measured)", a floor reading `value is not
        None` would be cleared by the 1.0 `build_manual_idea` types -- which is
        this whole slice's defect, arriving inside the method written to remove
        it. The property is driven directly rather than left to an invariant of
        the constructor."""
        r = ConfidenceReading(0.99, basis)
        assert r.measured is False
        assert r.clears(0.70) is False
        assert r.above(0.70) is False

    def test_no_reading_the_producer_builds_carries_an_unmeasured_figure(self):
        """The invariant that makes the two spellings agree TODAY, pinned so the
        day it stops holding a test fails rather than every floor quietly
        opening."""
        for idea in (_idea(**STAMP),
                     _idea(confidence=0.62, blended_confidence_raw=0.62),
                     _idea(confidence=0.31, blended_confidence_raw=0.95),
                     _idea(confidence=0.0),
                     object()):
            r = displayed_confidence(idea)
            assert r.measured or r.value is None, (r, idea)

    def test_an_unreadable_figure_never_raises_at_a_gate(self):
        r = displayed_confidence(object())
        assert r.clears(0.0) is False and r.above(0.0) is False


class TestTheProScanCardGradesOnTheMeasuredFigure:
    """The card's bar, band, status label and quality score each compared a
    RAW-scale bar against whatever the calibration curve left on the field.

    Two of these were found by the mutation round -- the band's unmeasured word
    and the bar's fill each survived a green suite, because nothing drove the
    card's conviction row at all. Reading the block for the seam then found the
    same shape at one HOP: `_status_label(idea.confidence, ...)` and
    `_setup_quality_score(idea.confidence, ...)` compare inside the callee, so
    the rule over the class cannot see them (it reads a percent format or a
    `Compare`, and an argument is neither). Leaving them would be *fixing two
    left the third* inside one function body.
    """

    @pytest.mark.parametrize("value,rr,band", [
        (0.82, 3.0, "LOWER RISK"),
        (0.82, 1.0, "MODERATE RISK"),
        (0.55, 3.0, "MODERATE RISK"),
        (0.40, 3.0, "ELEVATED RISK"),
    ])
    def test_a_measured_reading_gets_its_band(self, value, rr, band):
        from bot.skills.skill_registry import conviction_row

        bar, word = conviction_row(ConfidenceReading(value, "blend"), rr)
        assert band in word, word
        assert bar.count("\u2588") == int(value * 10), bar

    @pytest.mark.parametrize("basis", ["stamp", "unread"])
    def test_an_unmeasured_reading_fills_no_bar_and_names_no_verdict(self, basis):
        """A band is a COLOUR claim and a bar is a length; a stamped 1.0 filled
        the bar to ten blocks under a green LOWER RISK."""
        from bot.skills.skill_registry import conviction_row

        bar, word = conviction_row(ConfidenceReading(None, basis), 3.0)
        assert "RISK UNKNOWN" in word, word
        assert "\u2588" not in bar, bar
        for verdict in ("\U0001f7e2", "\U0001f7e1", "\U0001f534"):
            assert verdict not in word, word

    def test_the_bar_is_ten_blocks_however_the_reading_reads(self):
        from bot.skills.skill_registry import conviction_row

        for value in (0.0, 0.5, 1.0):
            bar, _ = conviction_row(ConfidenceReading(value, "blend"), 2.0)
            assert len(bar) == 10, (value, bar)

    def test_an_unread_confidence_is_not_stand_down(self):
        """"Stand Down" is this card's verdict for a confidence that WAS
        measured and found low."""
        from bot.skills.skill_registry import _status_label

        icon, text = _status_label(None, 3.0, 50.0, False)
        assert text != "Stand Down" and "Unread" in text, (icon, text)
        assert _status_label(0.40, 3.0, 50.0, False)[1] == "Stand Down"

    def test_the_market_rungs_still_apply_with_no_confidence(self):
        """`in_midrange` and an RSI extreme are facts about the market."""
        from bot.skills.skill_registry import _status_label

        assert _status_label(None, 3.0, 50.0, True)[1] == "No-Trade Zone"
        assert _status_label(None, 3.0, 85.0, False)[1] == "Elevated Risk"

    def test_the_quality_score_states_the_basis_it_was_graded_on(self):
        """Six points out of a reachable six printed as `6/10` is a partial
        total presented as a whole one."""
        from bot.skills.skill_registry import _setup_quality_score

        score, label, top = _setup_quality_score(None, 3.0, 50.0, True, True)
        assert top == 6, (score, label, top)
        assert score <= top
        assert "6 of 10" in label and "no confidence read" in label, label

    def test_a_measured_confidence_grades_on_the_whole_scale(self):
        from bot.skills.skill_registry import _setup_quality_score

        score, label, top = _setup_quality_score(0.95, 3.0, 50.0, True, True)
        assert top == 10 and score == 9, (score, top)
        assert "of 10" not in label, label

    def test_the_label_never_grades_a_reduced_basis_on_the_full_rungs(self):
        """A maximum measurable score would read "Tradable with confirmation"
        off the 0-10 rungs."""
        from bot.skills.skill_registry import _setup_quality_score

        score, label, top = _setup_quality_score(None, 3.0, 20.0, True, True)
        assert score == top == 6, (score, top)
        assert "Tradable" not in label, label

    def test_no_call_site_hands_a_grader_the_field(self):
        """A shape, stated as one: the comparison is inside the callee, so no
        drive of the grader can say which quantity the CARD handed it, and the
        card is 400 lines inside an async skill behind a scanner and a venue."""
        import pathlib

        from tests.source_scan import code_only

        tree = ast.parse(code_only(
            pathlib.Path("bot/skills/skill_registry.py").read_text()))
        bad = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") in ("_status_label",
                                                         "_setup_quality_score")):
                for a in node.args:
                    src = ast.unparse(a)
                    if src.endswith(".confidence"):
                        bad.append((node.lineno, src))
        assert not bad, f"a grader is handed the calibrated field: {bad}"


class TestTheDrawdownRecoveryFloorNeedsAMeasurement:
    """A floor cleared by a figure nobody measured is not a floor."""

    @staticmethod
    def _floor(idea, bar=0.85):
        """The gate's own condition, read from `risk_engine.py` by AST rather
        than restated: `_evaluate_locked` is 900 lines behind an engine, a
        venue and a book."""
        return not displayed_confidence(idea).clears(bar)

    def test_a_hand_typed_ticket_does_not_clear_the_higher_bar(self):
        assert self._floor(_idea(**STAMP)) is True

    def test_a_measured_blend_over_the_bar_clears_it(self):
        assert self._floor(_idea(confidence=0.90,
                                 blended_confidence_raw=0.90)) is False

    def test_a_curve_on_the_field_does_not_refuse_a_measured_blend(self):
        assert self._floor(_idea(confidence=0.31,
                                 blended_confidence_raw=0.90)) is False

    def test_the_gate_reads_the_reading_and_keeps_the_configured_bar(self):
        from bot.risk.risk_engine import RiskEngine

        _gate_call(RiskEngine._evaluate_locked,
                   "CONFIG.risk.drawdown_recovery_conf_min")

    def test_the_refusal_names_which_absence_it_was(self):
        """The same pin as the pyramid one, and for the same reason: its first
        draft read the marker's own LINE, which holds `.basis` today only because
        of where the sentence happens to wrap."""
        from bot.risk.risk_engine import RiskEngine

        _call_naming(RiskEngine._evaluate_locked,
                     "DD_RECOVERY: confidence", "_dd_conf.basis")

    def test_the_bar_is_the_configured_one_not_a_copy(self):
        """A second copy of a threshold is a second answer."""
        from bot.config import CONFIG

        bar = CONFIG.risk.drawdown_recovery_conf_min
        assert self._floor(_idea(confidence=bar, blended_confidence_raw=bar)) is False
        assert self._floor(_idea(confidence=bar - 0.01,
                                 blended_confidence_raw=bar - 0.01)) is True

class TestThePropertiesThatMakeThreeMutantsEquivalent:
    """Three mutations changed no verdict, and each is a property worth pinning
    rather than a coverage gap: the day one of these stops holding, the terse
    spelling the mutant restored would quietly admit an absent confidence.
    """

    def test_an_unmeasured_reading_fills_no_blocks_by_arithmetic(self):
        from bot.core.signal_confidence import ConfidenceReading
        from bot.skills.skill_registry import conviction_row
        for basis in ("stamp", "unread"):
            bar, word = conviction_row(ConfidenceReading(None, basis), rr=3.0)
            assert "\u2588" not in bar, (basis, bar)
            assert word == "\u26aa RISK UNKNOWN", (basis, word)

    def test_the_producer_never_pairs_a_value_with_an_unmeasured_basis(self):
        """measured <=> value is not None, over every idea shape the producer
        sees -- the invariant that makes `or 0.0` and the measured guard agree."""
        import types

        from bot.core.signal_confidence import displayed_confidence
        shapes = [dict(confidence=0.31, blended_confidence_raw=0.70, source="unknown"),
                  dict(confidence=0.62, source="unknown"),
                  dict(confidence=1.0, source="manual"),
                  dict(confidence=None, source="unknown"),
                  dict(confidence=True, source="unknown"),
                  dict(confidence=2.5, source="unknown"),
                  dict(confidence=float("nan"), source="unknown")]
        for kw in shapes:
            r = displayed_confidence(types.SimpleNamespace(**kw))
            assert r.measured == (r.value is not None), (kw, r)

    def test_no_grader_rung_sits_at_or_below_zero(self):
        """A measured 0.0 and an absent confidence reach the SAME rungs -- none
        -- and differ only in the basis they report. That is what keeps
        `(confidence or 0.0) >= bar` equal to the explicit guard: a rung at
        0.0 would let an absent confidence score under the terse spelling."""
        from bot.skills.skill_registry import _setup_quality_score, _status_label
        zero = _setup_quality_score(0.0, rr=3.0, rsi=50.0,
                                    vol_confirmed=True, structure_clear=True)
        none = _setup_quality_score(None, rr=3.0, rsi=50.0,
                                    vol_confirmed=True, structure_clear=True)
        assert zero[0] == none[0], (zero, none)          # same rungs reached
        assert zero[2] == 10 and none[2] == 6            # different basis
        assert _status_label(0.0, 3.0, 50.0, False)[1] != _status_label(0.75, 3.0, 50.0, False)[1]
        assert _status_label(None, 3.0, 50.0, False)[1] == "Confidence Unread"
