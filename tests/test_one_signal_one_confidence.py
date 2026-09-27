"""One signal, one confidence — on the card, its caption, and the check line.

An idea carries two confidences once calibration is on: ``blended_confidence_raw``
(the analyzer's blend, the scale every floor here is defined on) and
``confidence`` (what the curve left on the field). Five surfaces printed them
under one word with five hand-written fallbacks, and the sharpest instance is
ONE MESSAGE: `_send_idea_with_door` builds the PNG and its own caption in the
same function, so a SUI signal went out as a caption reading ``Conf 70%`` over
an image whose CONFIDENCE cell read ``31%``.

Driven, not scanned: the card is rendered and its text read back with
`tests/png_text.capture_text`, and the door is driven with a stand-in self so
the caption is the one the handler really composes.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re
import types

import pytest

from bot.core.signal_confidence import (
    STAMP_TEXT,
    UNREAD_DASH,
    ConfidenceReading,
    displayed_confidence,
)
from bot.utils.models import Direction, TradeIdea
from tests.png_text import capture_text
from tests.source_scan import code_only

# The live figures: SUI at $1.171, blend 0.70, the curve's output 0.31.
BLEND = 0.70
CALIBRATED = 0.31


def _idea(**kw) -> TradeIdea:
    d = dict(
        id="T-1",
        asset="SUI/USDT",
        direction=Direction.LONG,
        entry_price=1.171,
        stop_loss=1.1535,
        take_profit=1.2552,
        confidence=CALIBRATED,
        reasoning="drive",
    )
    d.update(kw)
    return TradeIdea(**d)


def _analyzer_idea(**kw) -> TradeIdea:
    idea = _idea(**kw)
    idea.blended_confidence_raw = BLEND
    return idea


def _drawn(idea) -> list[str]:
    from bot.formatters.signal_card import signal_card_from_idea

    with capture_text() as drawn:
        signal_card_from_idea(idea, rank=1)
    return [text for text, _fill in drawn]


class TestTheReading:
    def test_the_blend_wins_because_it_is_the_scale_the_gates_use(self):
        r = displayed_confidence(_analyzer_idea())
        assert r == ConfidenceReading(BLEND, "blend")
        assert r.measured and r.pct() == "70%"

    def test_a_bool_blend_is_a_flag_not_a_confidence(self):
        # `float(True)` is 1.0 — the value that clears every floor and prints
        # as 100%. Two of the five fallbacks did exactly that.
        idea = _idea()
        idea.blended_confidence_raw = True
        assert displayed_confidence(idea).value != 1.0

    def test_the_unset_blend_is_absent_not_a_measured_zero(self):
        idea = _idea()
        idea.blended_confidence_raw = 0.0
        r = displayed_confidence(idea)
        assert r.basis != "blend"

    def test_a_manual_stamp_is_not_a_measurement(self):
        r = displayed_confidence(_idea(confidence=1.0, source="manual"))
        assert r == ConfidenceReading(None, "stamp")
        assert not r.measured
        assert r.pct() == STAMP_TEXT

    def test_a_producer_that_measured_its_own_score_is_read(self):
        # A scan row / drift re-offer sets no blend; its score is its own.
        r = displayed_confidence(_idea(confidence=0.55))
        assert r == ConfidenceReading(0.55, "own")

    def test_nothing_readable_is_unread_and_never_raises(self):
        r = displayed_confidence(object())
        assert r == ConfidenceReading(None, "unread")
        assert r.pct() == UNREAD_DASH

    def test_a_stamp_and_an_unread_field_get_different_words(self):
        # Different facts: a figure nobody measured, and a field nobody read.
        assert STAMP_TEXT != UNREAD_DASH


class TestOneMessage:
    """The caption and the picture it labels cannot disagree."""

    def _door(self, idea):
        """Drive the real `_send_idea_with_door` with a stand-in self."""
        from bot.skills.telegram_handler import TelegramHandler

        sent: dict = {}

        async def _send_photo(update, png, cap, reply_markup=None):
            sent["caption"] = cap
            return True

        async def _send(update, text, **kw):
            sent["text"] = text

        host = types.SimpleNamespace(
            _lang=lambda u: "en",
            _send_photo=_send_photo,
            _send=_send,
        )
        update = types.SimpleNamespace(
            effective_user=types.SimpleNamespace(id=4242)
        )
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            TelegramHandler._send_idea_with_door(host, update, "ok", idea)
        )
        return sent

    def test_the_caption_and_the_card_show_the_same_figure(self):
        idea = _analyzer_idea()
        caption = self._door(idea)["caption"]
        drawn = _drawn(idea)

        assert "Conf 70%" in caption, caption
        # The cell the image draws under CONFIDENCE, and the summary's Score.
        assert "70%" in drawn
        # The defect: the calibrated figure on one of the two surfaces.
        assert "31%" not in caption
        assert "31%" not in drawn

    def test_a_hand_typed_ticket_shows_no_fabricated_certainty(self):
        idea = _idea(confidence=1.0, source="manual")
        caption = self._door(idea)["caption"]
        drawn = _drawn(idea)

        assert STAMP_TEXT in caption, caption
        assert "100%" not in caption
        assert "100%" not in drawn
        # The cell abstains rather than printing the stamp.
        assert UNREAD_DASH in drawn
        # And the summary makes no Score claim at all.
        assert not [t for t in drawn if "Score" in t]

    def test_the_proactive_caption_matches_the_card(self):
        from bot.skills.alerts_monitor import signal_card_caption

        idea = _analyzer_idea()
        caption = signal_card_caption(idea)
        drawn = _drawn(idea)

        assert "Conf 70%" in caption
        assert "70%" in drawn
        assert "31%" not in caption


class TestTheCheckLine:
    """The line quotes the figure the comparison used."""

    def test_with_calibration_on_the_gate_reads_the_blend(self):
        from unittest.mock import patch

        from bot.risk.confidence_floor import (
            clears_confidence_floor,
            confidence_for_floor,
        )

        idea = _analyzer_idea()
        with patch("bot.risk.confidence_floor.CONFIG") as m:
            m.analyzer.confidence_calibration_enabled = True
            m.risk.min_confidence = 0.60
            m.risk.per_strategy_confidence_floor_enabled = False
            # The figure the comparison uses is the blend, so the line that
            # quotes it cannot read "0.31 OK" against a 0.60 minimum.
            assert confidence_for_floor(idea) == pytest.approx(BLEND)
            assert clears_confidence_floor(idea)

    def test_with_calibration_off_the_field_is_already_the_raw_figure(self):
        # The shipped default: nothing moved `confidence`, so the gate's
        # reading is that field and the line quotes it.
        from bot.risk.confidence_floor import confidence_for_floor

        assert confidence_for_floor(_idea(confidence=0.64)) == pytest.approx(0.64)

    def test_the_engine_prints_the_figure_it_compared(self):
        import inspect

        from bot.risk.risk_engine import RiskEngine

        src = code_only(inspect.getsource(RiskEngine))
        tree = ast.parse(src)
        # Every f-string that opens "CONFIDENCE: " must interpolate the
        # gate's own reading, not `idea.confidence`.
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            head = node.values[0] if node.values else None
            if not (isinstance(head, ast.Constant)
                    and isinstance(head.value, str)
                    and head.value.startswith("CONFIDENCE: ")):
                continue
            names = {ast.unparse(v.value) for v in node.values
                     if isinstance(v, ast.FormattedValue)}
            if any(n.endswith(".confidence") for n in names):
                bad.append(ast.unparse(node))
        assert not bad, f"check line quotes a figure it did not compare: {bad}"


# ---------------------------------------------------------------------------
# The rule: one reading, every surface.
#
# The first version of this walked `handler_sources()` -- the 16 Telegram
# handler files -- and looked for `getattr(x, "blended_confidence_raw")`. That
# was two things short of the claim read off it, and both are shapes this repo
# already records.
#
# SCOPE. Three of the five surfaces the slice repaired are not handler files:
# `bot/core/proactive_monitor.py`, `bot/formatters/signal_card.py` and
# `bot/risk/risk_engine.py`. The rule reported clean because of where it
# looked, which is the `command_gates.py` lesson (COVERAGE OF A SPELLING IS NOT
# COVERAGE OF THE GUARD) with the scope as the spelling.
#
# SPELLING. `getattr` is one of three ways to ask. A plain attribute read
# (`idea.blended_confidence_raw`) is invisible to it, and so is a project-local
# accessor -- which is the honesty gate's own recorded defect, where `_attr`
# made "the single most expensive instance in the tree invisible to the gate
# written to find it". So a READ is an attribute load of that name, or ANY call
# carrying it as a constant argument.
#
# A RECORDER IS NOT A READER, and that distinction is what lets the rule cover
# the whole tree with ONE exclusion and no baseline. `engine.py` writes the field into a
# decision row three times and `flight_recorder.py` seals it once; each is the
# value bound to its OWN name, and writing a field verbatim is the opposite of
# re-deriving what it means. So an expression inside a keyword argument or a
# dict entry of the same name is excused, at any depth, because
# `_round(_get(idea, "blended_confidence_raw"), 4)` wraps the read two calls
# deep.
# ---------------------------------------------------------------------------

FIELD = "blended_confidence_raw"

#: ONE module in the tree reads the raw field, and it is the one that DEFINES
#: what the field means: `pre_calibration_confidence` is where "the question is
#: whether the figure is PRESENT, not what number stands in for it" is decided.
#:
#: The display leaf needs no entry at all, which is worth stating because the
#: first draft excused it: `signal_confidence.py` asks that function rather than
#: the field, so it passes the rule on its own merits -- and an exemption for a
#: file that does not need one is the next reader's false acquittal. The entry
#: below must still hold a read, or it is stale (the `known_failures.txt` rule).
FIELD_DEFINITION = ("bot/learning/confidence_calibration.py",)


def _surfaces():
    """Every surface the rule walks. ONE definition, because the scope test
    below asserts what this returns: its first draft built a `rglob` of its own
    and so passed against a rule narrowed straight back to `handler_sources()`
    -- a guard deriving its expectation from anything but the thing it guards
    moves with it and can see nothing."""
    return sorted(pathlib.Path("bot").rglob("*.py"))


def _recorded_values(tree):
    """Every node whose subtree is a value being WRITTEN to this same name."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == FIELD:
            out.append(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == FIELD:
                    out.append(value)
    return out


def _blend_reads(src: str):
    """Lines that ASK an object for the raw blend, in any of the three
    spellings, excluding the ones that hand it straight back to its own name.

    Parsed RAW, and that is deliberate rather than an omission of the repo's
    "strip comments first" rule. An AST walk cannot see a comment at all, and a
    docstring is a bare string constant carrying no Attribute or Call node, so
    `code_only` buys this rule nothing -- and it COSTS: it blanks docstrings,
    so a class whose body is only one no longer parses. Driven over `bot/`,
    11 files are unparseable after `code_only` -- `bot/core/live_executor.py`
    and `bot/utils/audit_chain.py` among them -- so a whole-tree rule that
    stripped first would have to swallow a SyntaxError for each: 11 files
    silently unchecked, which is the blind spot this rule was widened to
    remove. The count is pinned against a live measurement below, because a
    number in prose is the part that rots first."""
    tree = ast.parse(src)
    excused = {id(d) for v in _recorded_values(tree) for d in ast.walk(v)}
    out = []
    for node in ast.walk(tree):
        is_read = (
            isinstance(node, ast.Attribute)
            and node.attr == FIELD
            and isinstance(node.ctx, ast.Load)
        ) or (
            isinstance(node, ast.Call)
            and any(isinstance(a, ast.Constant) and a.value == FIELD
                    for a in node.args)
        )
        if is_read and id(node) not in excused:
            out.append(node.lineno)
    return out


class TestEveryReaderAsksIt:
    """No sixth hand-written copy of the question, on any surface."""

    def test_no_surface_reads_the_blend_itself(self):
        offenders = {}
        for path in _surfaces():
            if str(path) in FIELD_DEFINITION:
                continue
            lines = _blend_reads(path.read_text())
            if lines:
                offenders[str(path)] = lines
        assert not offenders, (
            "a surface reads the raw blend itself instead of asking "
            f"displayed_confidence(): {offenders}"
        )

    def test_the_scope_is_every_surface_not_the_handler_files(self):
        """The assertion that would have failed before this was widened.

        `handler_sources()` is 16 files under `bot/skills/`, and three of the
        five surfaces this slice repaired are not among them. Pinning the
        membership rather than the count, because a count can be right while
        the set is wrong -- and reading the rule's OWN walk, so narrowing it
        fails here."""
        from tests.source_scan import handler_sources

        walked = {str(p) for p in _surfaces()}
        handlers = {str(p) for p in handler_sources()}
        for missed in ("bot/core/proactive_monitor.py",
                       "bot/formatters/signal_card.py",
                       "bot/risk/risk_engine.py"):
            assert missed in walked, f"the rule no longer walks {missed}"
            assert not any(h.endswith(missed) for h in handlers), (
                f"{missed} is a handler file now, so this test's premise has "
                "changed -- re-read the scope argument above"
            )

    def test_the_one_excused_module_still_reads_it(self):
        """A stale exemption is the thing that hides the next copy."""
        for path in FIELD_DEFINITION:
            assert _blend_reads(pathlib.Path(path).read_text()), (
                f"{path} is excused from the rule and no longer reads the "
                "field: delete the entry in the same commit"
            )

    @pytest.mark.parametrize(
        "spelling,src",
        [
            ("getattr, the monitor's own float(... or 0.0)",
             'def f(idea):\n'
             '    raw = getattr(idea, "blended_confidence_raw", None)\n'
             '    return float((raw if raw is not None else idea.confidence) or 0.0)\n'),
            ("a plain attribute read",
             'def f(idea):\n'
             '    return idea.blended_confidence_raw or idea.confidence\n'),
            ("a project-local accessor",
             'def f(idea):\n'
             '    return _attr(idea, "blended_confidence_raw", 0.0)\n'),
            # The input that separates "excused because it is written BACK to
            # this field" from "excused because it sits in some keyword": the
            # mutation round added it, and it is the only plant that kills an
            # excusal which ignores the NAME.
            ("the blend handed to a DIFFERENT field",
             'def f(idea):\n'
             '    return Row(confidence=getattr(\n'
             '        idea, "blended_confidence_raw", None))\n'),
        ],
    )
    def test_the_rule_flags_each_spelling_the_slice_removed(self, spelling, src):
        assert _blend_reads(src), spelling

    @pytest.mark.parametrize(
        "shape,src",
        [
            ("a keyword argument of the same name",
             'def f(idea):\n'
             '    return Row(blended_confidence_raw=getattr(\n'
             '        idea, "blended_confidence_raw", None) or 0.0)\n'),
            ("a dict entry of the same name, two calls deep",
             'def f(idea):\n'
             '    return {"blended_confidence_raw":\n'
             '            _round(_get(idea, "blended_confidence_raw"), 4)}\n'),
            ("a field declaration",
             'class R:\n    blended_confidence_raw: float = 0.0\n'),
            ("the honest reader",
             'def f(idea):\n    return displayed_confidence(idea).pct()\n'),
            ("a producer ASSIGNING the field",
             'def f(idea, blend):\n    idea.blended_confidence_raw = blend\n'),
        ],
    )
    def test_the_rule_leaves_a_recorder_alone(self, shape, src):
        assert not _blend_reads(src), shape

    def test_the_rule_reads_code_and_not_prose(self):
        """A comment and a docstring both name the field; neither is a read.

        The AST is why, and it is a stronger guarantee than stripping: a
        comment is not in the tree, and a docstring is a string constant with
        no Attribute or Call inside it. This chapter's own prose names the
        field a dozen times."""
        assert not _blend_reads(
            "def f(idea):\n"
            '    """And idea.blended_confidence_raw in a docstring."""\n'
            "    # blended_confidence_raw is the blend the curve is fitted on\n"
            "    return displayed_confidence(idea).value\n"
        )

    def test_the_reason_for_parsing_raw_is_the_one_measured(self):
        """`_blend_reads`' docstring counts the files `code_only` breaks, and a
        number in prose is the part that rots first -- so it is read back out
        and compared to a live measurement rather than trusted."""
        unparseable = set()
        for path in sorted(pathlib.Path("bot").rglob("*.py")):
            try:
                ast.parse(code_only(path.read_text()))
            except SyntaxError:
                unparseable.add(path.stem)
        assert unparseable, (
            "code_only() now parses every bot/ file: the raw parse above is "
            "still correct, but this reason for it has gone -- re-read it"
        )
        claimed = {int(n) for n in re.findall(
            r"(\d+) files are unparseable", _blend_reads.__doc__)}
        assert claimed == {len(unparseable)}, (
            "the docstring's count of files code_only() breaks is stale: "
            f"claims {claimed}, measured {len(unparseable)} "
            f"({sorted(unparseable)})"
        )
        for example in ("live_executor", "audit_chain"):
            assert example in unparseable, (
                f"the docstring names {example} as an example and it parses"
            )

def _signal_engine(idea):
    """The engine's pending book with the ownership reading the real engine
    keeps, bound off the class (the harness `test_the_scheduled_posts...`
    uses, replicated rather than imported so this suite stands alone)."""
    from bot.core.engine import RuneClawEngine

    eng = types.SimpleNamespace(
        _pending_ideas={}, _engine_idea_ids=set(),
        live_executor=types.SimpleNamespace(_last_close_data=None, _positions={}),
    )
    for name in ("_engine_pending_ids", "_register_engine_idea"):
        setattr(eng, name, types.MethodType(getattr(RuneClawEngine, name), eng))
    eng._register_engine_idea(idea)
    return eng


class TestTheMonitorCard:
    """The NEW SIGNAL card is the third surface, and it gated on one figure
    while printing another."""

    def _alerts(self, idea):
        from bot.core.proactive_monitor import ProactiveMonitor

        return ProactiveMonitor(_signal_engine(idea))._check_trade_signals()

    def test_the_card_prints_the_figure_the_gate_admitted_it_on(self):
        alerts = self._alerts(_analyzer_idea())
        assert alerts, "the drive's own fixture produced no signal"
        body = alerts[0].body
        assert "70%" in body, body
        assert "31%" not in body

    def test_a_flag_never_clears_the_alert_bar(self):
        """`float(x or 0.0)` read a blend of `True` as 1.0.

        The first draft of this test planted `confidence=0.9` beside the
        bool, which `displayed_confidence` reads perfectly well as the idea's
        own figure -- so the mutant and the fix both alerted, and the test
        could not tell them apart. The idea's own confidence has to sit
        BELOW the bar for the bool to be the only thing that could clear it.
        """
        idea = _idea(confidence=0.20)
        idea.blended_confidence_raw = True
        assert not self._alerts(idea), (
            "a flag read as 1.0 cleared the alert bar for a 20% idea"
        )
