"""The agent feed's thesis event and the signal row state one confidence.

Every fresh engine idea goes to two public surfaces: the signal-stream row on
`GET /api/signals` (`website_sync.build_signal_payload`, which asks
`displayed_confidence`) and the agent feed's "thesis" event. The event read
`idea.confidence`, which is not that reading: the setup-expectancy nudge (on
by default) moves it after the analyzer snapshots its blend, and so does the
calibration curve when it is enabled. One idea, two published confidences, on
two public surfaces -- the defect `test_one_signal_one_confidence.py` records
for the Telegram card, one surface over.

The guard over the class could not see it: it judged a receiver by a
hand-written list of names, and the engine's loop variable was `_fi`. That rule
covers every receiver now (`test_every_confidence_reader_asks_the_one_reading`).
"""

import ast
import pathlib
from types import SimpleNamespace

from bot.core.agent_feed import thesis_event
from bot.core.signal_confidence import STAMP_TEXT
from bot.utils.models import Direction, TradeIdea
from bot.utils.website_sync import build_signal_payload

BLEND = 0.62     # what the analyzer measured, and what the signal row publishes
NUDGED = 0.67    # what the setup-expectancy nudge left on the field


def _engine_idea(**kw) -> TradeIdea:
    d = dict(id="T-7", asset="PENDLE/USDT", direction=Direction.LONG,
             entry_price=2.367, stop_loss=2.30, take_profit=2.52,
             confidence=NUDGED, reasoning="Against: thin book.")
    d.update(kw)
    idea = TradeIdea(**d)
    idea.blended_confidence_raw = BLEND
    return idea


class TestOneIdeaOneFigure:

    def test_the_event_publishes_the_signal_rows_confidence(self):
        idea = _engine_idea()
        ev = thesis_event(idea)
        row = build_signal_payload(idea.id, idea, score=idea.confidence)
        assert ev["data"]["confidence"] == round(row["confidence"], 3) == BLEND
        assert ev["title"] == "LONG PENDLE/USDT — confidence 62%"

    def test_the_nudged_field_is_not_what_it_publishes(self):
        """The asymmetric fixture: with the blend and the field equal, reading
        either answers the same and the test could not tell them apart."""
        ev = thesis_event(_engine_idea())
        assert "67%" not in ev["title"]
        assert ev["data"]["confidence"] != NUDGED

    def test_a_stamped_ticket_prints_the_stamp_not_a_certainty(self):
        """The engine hands the feed its own ideas only, and a hand-typed
        ticket's 1.0 is a stamp; the builder must not print it as 100% for
        the day a person's idea reaches it."""
        from bot.skills.manual_trade import build_manual_idea

        ticket = build_manual_idea("LONG", "ETH", 3000, 2950, 3100)
        ev = thesis_event(ticket)
        assert "100%" not in ev["title"]
        assert STAMP_TEXT in ev["title"]
        assert ev["data"]["confidence"] is None


class TestTheRestOfTheEvent:

    def test_direction_symbol_body_and_levels(self):
        ev = thesis_event(_engine_idea())
        assert ev["symbol"] == "PENDLE/USDT"
        assert ev["body"] == "Against: thin book."
        assert ev["data"]["direction"] == "LONG"
        assert (ev["data"]["entry"], ev["data"]["sl"], ev["data"]["tp"]) == (2.367, 2.30, 2.52)

    def test_a_level_the_idea_does_not_state_is_none_not_zero(self):
        """A zero stop on a public payload reads as a stop AT zero."""
        bare = SimpleNamespace(direction="SHORT", asset="ARB/USDT", reasoning="",
                               entry_price=1.0, stop_loss=None, take_profit=0.0,
                               confidence=0.5, blended_confidence_raw=0.5)
        ev = thesis_event(bare)
        assert ev["data"]["sl"] is None
        assert ev["data"]["tp"] is None
        assert ev["data"]["entry"] == 1.0

    def test_the_body_is_bounded(self):
        ev = thesis_event(_engine_idea(reasoning="x" * 900))
        assert len(ev["body"]) == 300


class TestTheEngineAsksTheBuilder:
    """A scan, stated as one: the emit sits inside `_tick`, 434 lines behind a
    scanner, an analyzer and an exchange. What it pins is that no `"thesis"`
    event anywhere in `bot/` is built by hand again."""

    def _thesis_emits(self):
        out = []
        for p in sorted(pathlib.Path("bot").rglob("*.py")):
            for node in ast.walk(ast.parse(p.read_text())):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "emit" and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "thesis"):
                    out.append((str(p), node))
        return out

    def test_every_thesis_emit_is_the_builder(self):
        emits = self._thesis_emits()
        assert emits, "no thesis event is emitted anywhere; the scan found nothing"
        for path, call in emits:
            assert len(call.args) == 1, (path, ast.unparse(call))
            spread = [k for k in call.keywords if k.arg is None]
            assert len(spread) == 1 and len(call.keywords) == 1, (path, ast.unparse(call))
            inner = spread[0].value
            assert (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "thesis_event"), (path, ast.unparse(call))
