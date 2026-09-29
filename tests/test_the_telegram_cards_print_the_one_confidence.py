"""The Telegram cards of a signal print the one confidence its other cards do.

`displayed_confidence` is the one reading (#105), and #153 widened the rule
that holds every surface to it. That rule walked for the ATTRIBUTE
`x.confidence` inside a percent format or a comparison, and four surfaces read
the field under two other spellings it could not see:

* the chart subtitle baked into the signal PNG, in both of its builders, read
  `conf = getattr(idea, "confidence", None)` and formatted `conf` later;
* the `/analyze` card bound `conf = idea.confidence` and drew its bar, ring
  and pill from the local;
* the model's PENDING TRADE IDEAS row bound `_conf = getattr(idea, ...)`;
* the web chat's "Trade this" hint handed `getattr(idea, "confidence")` to the
  browser under the analysis card that printed the blend.

Each printed the figure the calibration curve and the setup-expectancy nudge
left on the field, beside a signal row and an alert caption printing the
blend, and each printed a hand-typed ticket's STAMP (`build_manual_idea` writes
1.0) as a measured 100%. They are driven here; the rule that now sees a local
and a `getattr` is `tests/test_every_confidence_reader_asks_the_one_reading.py`.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from bot.skills import skill_registry as sr
from bot.utils.models import Direction, TradeIdea


def _idea(*, confidence=0.31, blend=0.70, source="unknown", entry=100.0):
    return TradeIdea(
        asset="LINK/USDT", direction=Direction.LONG, entry_price=entry,
        stop_loss=entry * 0.97, take_profit=entry * 1.06, confidence=confidence,
        blended_confidence_raw=blend, source=source,
        reasoning="[rule|TREND_UP|x|swing|C=0.66] test", order_type="market")


# ── the chart subtitle ──────────────────────────────────────────────────────

class TestTheChartSubtitle:

    def test_it_prints_the_blend_and_not_the_calibrated_field(self):
        from bot.skills.chart_renderer import _idea_meta
        _, _, subtitle, _ = _idea_meta(_idea())
        assert "conf 70%" in subtitle and "31%" not in subtitle

    def test_a_stamp_is_not_a_measured_hundred_percent(self):
        from bot.skills.chart_renderer import _idea_meta
        _, _, subtitle, _ = _idea_meta(_idea(confidence=1.0, blend=None, source="manual"))
        assert "conf not measured" in subtitle and "100%" not in subtitle

    def test_the_single_chart_uses_the_same_builder(self, monkeypatch):
        """`send_idea_chart` kept its own copy of the subtitle lines. It asks
        `_idea_meta` now, proved by planting it: a byte-identical second copy
        agrees with every honest fixture."""
        from bot.skills import chart_renderer as cr
        seen = {}

        async def _send_chart(bot, chat_id, candles, **kw):
            seen.update(kw)
            return True

        monkeypatch.setattr(cr, "send_chart", _send_chart)
        monkeypatch.setattr(cr, "_idea_meta",
                            lambda idea: ("PAIR", "LONG", "PLANTED SUBTITLE", []))
        assert asyncio.run(cr.send_idea_chart(None, 1, [], _idea())) is True
        assert seen["subtitle"] == "PLANTED SUBTITLE"
        assert seen["levels"] == []


# ── the /analyze card ───────────────────────────────────────────────────────

def _analyze_card(idea):
    class _Ex:
        async def fetch_ticker(self, sym):
            return {"last": idea.entry_price, "percentage": 1.2, "quoteVolume": 5e6}

    class _Scanner:
        async def _get_exchange(self):
            return _Ex()

        async def _get_futures_exchange(self):
            return _Ex()

    async def _an(sig, **kw):
        return idea

    eng = NS(scanner=_Scanner(), _analyze_signal=_an, _pending_ideas={},
             _pending_atr={}, analyzer=NS(_last_rejection_diag=None),
             journal=None, _engine_pending_ids=lambda: set())
    return asyncio.run(sr.AnalyzeAssetSkill().execute(eng, symbol="LINK/USDT"))


def _confidence_line(card):
    return next(ln for ln in card.splitlines() if "Confidence" in ln)


class TestTheAnalyzeCard:

    def test_it_prints_the_blend(self):
        line = _confidence_line(_analyze_card(_idea()))
        assert "70%" in line and "31%" not in line

    def test_its_bar_is_drawn_from_the_blend(self):
        """The bar and the pill are one reading: 70% of twelve cells is eight
        filled, and 31% would be three."""
        line = _confidence_line(_analyze_card(_idea()))
        bar = line.split("│")[1]
        assert bar.count(sr._BLOCKS[7]) == 8, bar

    def test_a_stamp_draws_no_bar_and_prints_no_figure(self):
        """A figure nobody measured draws no fill: an empty bar of blocks
        would be a reading of zero, and a full one a reading of certainty."""
        line = _confidence_line(_analyze_card(
            _idea(confidence=1.0, blend=None, source="manual")))
        assert "not measured" in line and "100%" not in line
        bar = line.split("│")[1]
        assert sr._BLOCKS[7] not in bar and sr._BLOCKS[0] not in bar, bar


# ── the model's pending-ideas row ───────────────────────────────────────────

def _pending_block(ideas):
    from bot.skills.telegram_handler import TelegramHandler
    return TelegramHandler._pending_ideas_block(NS(engine=NS(pending_ideas=ideas)))


class TestThePendingIdeasRow:

    def test_the_model_is_told_the_blend(self):
        out = _pending_block([_idea()])
        assert "confidence 70%" in out and "31%" not in out

    def test_an_unreadable_confidence_is_said_not_dropped(self):
        """The row used to drop the clause when the field was not a number,
        so an idea with no readable confidence read as one that had simply
        not been scored."""
        idea = NS(asset="SOL/USDT", direction=NS(value="LONG"),
                  confidence="junk", entry_price=180.0, source="unknown")
        assert "confidence —" in _pending_block([idea])


# ── the web chat's "Trade this" hint ────────────────────────────────────────

class TestTheTradeThisHint:

    def _hint(self, idea):
        from bot.web.user_gateway import _setup_from_new_idea
        return _setup_from_new_idea(NS(_pending_ideas={idea.id: idea}), set())

    def test_it_carries_the_blend(self):
        assert self._hint(_idea())["confidence"] == 0.7

    @pytest.mark.parametrize("confidence,blend,source", [
        (1.0, None, "manual"),     # a hand-typed ticket's stamp
    ])
    def test_a_figure_nobody_measured_is_none_not_a_number(self, confidence,
                                                           blend, source):
        hint = self._hint(_idea(confidence=confidence, blend=blend, source=source))
        assert hint["confidence"] is None
