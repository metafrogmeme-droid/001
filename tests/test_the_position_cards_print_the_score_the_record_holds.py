"""The position and close cards print the confidence the signal was opened on.

The signal card has printed the one reading (`displayed_confidence`) under
SCORE (UNCALIBRATED) since #105; the position card and the close card printed
no score at all, because neither book recorded one: a position knew its
entry, stop and target, and not what the engine had scored the idea it came
from. Asked for on 2026-10-19 ("the confidence score on the cards").

So the reading is RECORDED AT ENTRY, once, on both books -- the live position
in its entry facts (`entry_facts_for`, the decision as it stood when the
order went out), the paper trade on two fields of its own -- and one reader,
`confidence_on_entry`, hands it to every card. Recorded, not looked up: the
current scan's idea for the same symbol is a different quantity, and a card
that printed it would print a figure the trade was never opened on.

Three absences, three sentences: a hand-typed ticket's stamp says "not
measured", a field nobody could read says the dash, and a row written before
the field existed says "not recorded". None of them is a 0, and none is in
the accent colour a measured figure wears.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.core.live_executor import LivePosition, entry_facts_for, restore_entry_facts, stamp_entry_facts
from bot.core.position_telemetry import confidence_on_entry
from bot.formatters import signal_card as SC
from bot.risk.portfolio import PortfolioTracker
from bot.skills.callback_handler import paper_close_card_data
from bot.skills.trading_commands import live_position_card_data
from bot.utils.models import Direction, TradeExecution, TradeIdea
from tests.png_text import drawn, fill_of

UTC = timezone.utc
SCORE_LABEL = "SCORE (UNCALIBRATED)"


def _idea(*, confidence=0.31, blend=0.70, source="unknown", entry=100.0):
    return TradeIdea(
        asset="LINK/USDT", direction=Direction.LONG, entry_price=entry,
        stop_loss=entry * 0.97, take_profit=entry * 1.06, confidence=confidence,
        blended_confidence_raw=blend, source=source,
        reasoning="[rule|TREND_UP|x|swing|C=0.66] test", order_type="market")


def _stamp():
    """A hand-typed ticket: `build_manual_idea` writes confidence 1.0 and no blend."""
    return _idea(confidence=1.0, blend=None, source="manual")


def _live(idea=None, **kw):
    pos = LivePosition(trade_id="TI-1", symbol="LINK/USDT:USDT", direction="LONG", entry_price=100.0,
                       quantity=10.0, cost_usd=200.0, stop_loss=97.0, take_profit=106.0, leverage=5, **kw)
    if idea is not None:
        stamp_entry_facts(pos, entry_facts_for(idea, order_type="market"))
    return pos


# ── recorded at entry ───────────────────────────────────────────────────────

class TestThePaperBookRecordsIt:
    def test_a_measured_figure_is_recorded_with_its_basis(self, tmp_path):
        book = PortfolioTracker(initial_balance=10_000.0, state_file=str(tmp_path / "p.json"))
        t = book.open_position(_idea(), 500.0, leverage=2)
        assert (t.confidence_at_entry, t.confidence_basis) == (0.70, "blend")
        assert confidence_on_entry(t).text() == "score 70 (uncalibrated)"

    def test_a_stamp_is_recorded_as_a_stamp_not_as_a_hundred(self, tmp_path):
        book = PortfolioTracker(initial_balance=10_000.0, state_file=str(tmp_path / "p.json"))
        t = book.open_position(_stamp(), 500.0, leverage=2)
        assert (t.confidence_at_entry, t.confidence_basis) == (None, "stamp")
        assert confidence_on_entry(t).text() == "not measured"

    def test_the_record_survives_the_json_round_trip(self, tmp_path):
        book = PortfolioTracker(initial_balance=10_000.0, state_file=str(tmp_path / "p.json"))
        t = book.open_position(_idea(), 500.0, leverage=2)
        back = TradeExecution.model_validate(t.model_dump(mode="json"))
        assert (back.confidence_at_entry, back.confidence_basis) == (0.70, "blend")

    def test_a_row_written_before_the_field_reads_as_not_recorded(self):
        old = TradeExecution(trade_id="old", asset="LINK/USDT", direction=Direction.LONG, entry_price=100.0,
                             quantity=10.0, stop_loss=97.0, take_profit=106.0)
        assert old.confidence_basis is None
        assert confidence_on_entry(old) is None

    def test_the_closed_trade_still_carries_it(self, tmp_path):
        book = PortfolioTracker(initial_balance=10_000.0, state_file=str(tmp_path / "p.json"))
        t = book.open_position(_idea(), 500.0, leverage=2)
        closed = book.close_position(t.trade_id, 103.0)
        data = paper_close_card_data("LINKUSDT", closed, 2.0)
        assert (data["confidence"], data["confidence_basis"]) == (0.70, "blend")
        assert data["opened_at"] == closed.opened_at


class TestTheLiveBookRecordsIt:
    def test_the_entry_facts_carry_the_one_reading(self):
        facts = entry_facts_for(_idea(), order_type="market")
        assert (facts["confidence"], facts["confidence_basis"]) == (0.70, "blend")
        stamped = entry_facts_for(_stamp(), order_type="market")
        assert (stamped["confidence"], stamped["confidence_basis"]) == (None, "stamp")

    def test_the_facts_come_back_from_the_saved_row(self):
        pos = _live(_idea())
        row = {"entry_facts": dict(pos.entry_facts)}
        fresh = _live()
        restore_entry_facts(fresh, row)
        assert confidence_on_entry(fresh).value == 0.70

    def test_a_position_with_no_facts_is_not_recorded(self):
        assert confidence_on_entry(_live()) is None
        # facts from before the keys existed: the decision was recorded, the score was not
        pos = _live()
        stamp_entry_facts(pos, {"v": 1, "intended_entry": 100.0, "fills": []})
        assert confidence_on_entry(pos) is None

    def test_the_livepositions_card_data_carries_it(self):
        now = datetime(2026, 10, 19, 20, 0, tzinfo=UTC)
        pos = _live(_idea(), opened_at=now - timedelta(hours=6))
        data = live_position_card_data(pos, 101.0, now)
        assert (data["confidence"], data["confidence_basis"]) == (0.70, "blend")
        assert data["opened_at"] == now - timedelta(hours=6)
        bare = live_position_card_data(_live(), 101.0, now)
        assert (bare["confidence"], bare["confidence_basis"]) == (None, None)


# ── printed on the cards ────────────────────────────────────────────────────

POSITION = {"symbol": "B/USDT:USDT", "direction": "SHORT", "is_live": False, "entry": 0.12479, "now": 0.1211,
            "pnl_pct": 14.78, "pnl_usd": 209.66, "net_pnl": 207.86, "fees": 1.79, "size_usd": 1418.05,
            "leverage": 5, "hold_time": "6.5h", "rr": 0.6, "sl": 0.13095, "tp": 0.11555,
            "opened_at": datetime(2026, 10, 17, 20, 12, tzinfo=UTC)}

CLOSE = {"symbol": "BTC", "direction": "LONG", "entry": 1.0, "exit": 1.1, "pnl_usd": 2.0, "reason": "tp_hit",
         "opened_at": "2026-10-17T20:12:00+00:00"}


@pytest.mark.parametrize("fn,base", [(SC.render_position_card, POSITION), (SC.render_close_card, CLOSE)])
class TestTheCardsSayIt:
    def test_a_measured_score_is_the_number_in_the_accent_colour(self, fn, base):
        rows = drawn(fn, dict(base, confidence=0.70, confidence_basis="blend"))
        assert fill_of(rows, SCORE_LABEL) == SC._GRAY
        assert fill_of(rows, "70") == SC._CYAN
        assert fill_of(rows, "17 Oct 2026 20:12 UTC") == SC._WHITE

    def test_a_stamp_says_not_measured(self, fn, base):
        rows = drawn(fn, dict(base, confidence=None, confidence_basis="stamp"))
        assert fill_of(rows, "not measured") == SC._GRAY
        assert "100" not in [t for t, _ in rows]

    def test_a_row_without_the_record_says_not_recorded(self, fn, base):
        rows = drawn(fn, base)
        assert fill_of(rows, "not recorded") == SC._GRAY
        assert "0" not in [t for t, _ in rows]

    def test_a_field_nobody_could_read_is_the_dash(self, fn, base):
        rows = drawn(fn, dict(base, confidence=None, confidence_basis="unread"))
        after_label = [t for t, _ in rows][[t for t, _ in rows].index(SCORE_LABEL):]
        assert after_label[1] == "—"
        assert fill_of(rows, "—") == SC._MUTED

    def test_an_opened_time_that_does_not_parse_is_no_date(self, fn, base):
        rows = drawn(fn, dict(base, opened_at="yesterday-ish", confidence=0.7, confidence_basis="blend"))
        texts = [t for t, _ in rows]
        assert texts[texts.index("OPENED") + 1] == "—"
        assert not [t for t in texts if "UTC" in t]
