"""The smart-money line on the Telegram cards prints what the website read.

The website holds the one Flovia client and remembers Nansen's 24h net flow
per symbol for a day (`app/lib/smart_money.js`); the bot asks it over the bot
channel (`bot/utils/context_pull.py`) and prints the answer as given. Three
absences, three words on the signal card: a read figure in the text colour
(a market fact, never a tint that would make the line a verdict), "not read"
when the website said so, the dash when the caller passed nothing. The duel
card prints the line only when there is a read value, never a zero.

The line is context: `test_smart_money_is_a_line_and_never_a_verdict.py`
holds that nothing in the engine, the risk gate or the settlement reads it.
"""
from __future__ import annotations

import pytest

import bot.utils.context_pull as cp
from bot.formatters import duel_card
from bot.formatters.money import compact_signed_usd
from bot.formatters.signal_card import _GRAY, _MUTED, _WHITE, _smart_money_cell


class TestTheCompactFigure:
    @pytest.mark.parametrize("v,want", [
        (1234567.8, "+$1.2M"), (-340000, "-$340K"), (12, "+$12"), (0, "+$0"),
        (2.1e9, "+$2.1B"), (-999, "-$999"), (1000, "+$1K"),
    ])
    def test_sign_before_the_currency_at_a_cells_size(self, v, want):
        assert compact_signed_usd(v) == want


class TestTheSignalCardCell:
    def test_a_read_flow_is_the_figure_in_the_text_colour(self):
        assert _smart_money_cell({"smart_money": {"read": True, "net_flow_usd": 1234567.8}}) == ("+$1.2M net in",
            _WHITE)
        assert _smart_money_cell({"smart_money": {"read": True, "net_flow_usd": -5000}}) == ("-$5K net out", _WHITE)
        assert _smart_money_cell({"smart_money": {"read": True, "net_flow_usd": 0}}) == ("+$0 net in", _WHITE)

    def test_not_read_is_grey_and_names_a_usable_reason(self):
        assert _smart_money_cell({"smart_money": {"read": False, "reason": "refused"}}) == ("not read", _GRAY)
        assert _smart_money_cell({"smart_money": {"read": False,
            "reason": "not covered"}}) == ("not read (not covered)", _GRAY)
        assert _smart_money_cell({"smart_money": {"read": False,
            "reason": "unconfigured"}}) == ("not read (unconfigured)", _GRAY)
        assert _smart_money_cell({"smart_money": {"read": True, "net_flow_usd": "junk"}}) == ("not read", _GRAY)

    def test_nothing_passed_is_the_dash(self):
        assert _smart_money_cell({}) == ("—", _MUTED)
        assert _smart_money_cell({"smart_money": None}) == ("—", _MUTED)


class TestTheDuelCardLine:
    def test_a_read_value_is_a_line_and_nothing_else_is(self):
        assert duel_card.smart_money_line({"symbol": "SOL", "smart_money": {"read": True, "net_flow_usd": 1234567.8}}) \
            == "📊 Smart money 24h: +$1.2M in (Nansen)"
        assert duel_card.smart_money_line({"symbol": "SOL"}) is None
        assert duel_card.smart_money_line({"symbol": "SOL", "smart_money": {"read": False,
            "reason": "refused"}}) is None
        assert duel_card.smart_money_line({"symbol": "SOL", "smart_money": {"read": False, "reason": "refused",
            "net_flow_usd": 5}}) is None, \
            "a figure beside read False is not a reading"
        assert duel_card.smart_money_line({"symbol": "SOL", "smart_money": {"read": True,
            "net_flow_usd": None}}) is None

    def test_a_mover_names_both_legs(self):
        line = duel_card.smart_money_line({
            "symbol": "ARB", "symbol_b": "SOL",
            "smart_money": {"read": True, "net_flow_usd": 7}, "smart_money_b": {"read": True, "net_flow_usd": -8},
        })
        assert line == "📊 Smart money 24h: ARB: +$7 in · SOL: -$8 out (Nansen)"

    def test_the_card_prints_it_under_the_question(self):
        data = {"day": "2026-10-20", "horizon_hours": 24, "rounds": [{
            "round_id": 1, "kind": "direction", "symbol": "SOL", "callable": True,
            "smart_money": {"read": True, "net_flow_usd": 1234567.8},
        }], "accuracy": None, "marks": None}
        text = duel_card.render_card(data)
        assert "📊 Smart money 24h: +$1.2M in (Nansen)" in text
        data["rounds"][0].pop("smart_money")
        assert "Smart money" not in duel_card.render_card(data)


class TestThePull:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        cp.forget()
        monkeypatch.setattr(cp, "is_configured", lambda: True)
        yield
        cp.forget()

    def test_the_websites_reading_is_returned_as_given(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cp, "_call", lambda path, data=None: (seen.append(path), (200, {
            "symbol": "SOL", "reading": {"read": True, "net_flow_usd": 5.0, "provider": "Nansen"}}))[1])
        r = cp.smart_money_reading("SOL/USDT:USDT")
        assert r == {"read": True, "net_flow_usd": 5.0, "provider": "Nansen"}
        assert seen == ["/api/bot/sync/context/smart-money?symbol=SOL"]
        monkeypatch.setattr(cp, "_call", lambda path, data=None: (200, {"reading": {"read": False,
            "reason": "not covered"}}))
        assert cp.smart_money_reading("BTC") == {"read": False, "reason": "not covered"}

    def test_a_dead_website_is_unreachable_and_remembered_for_a_minute(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cp, "_call", lambda path, data=None: (calls.append(path), (None, None))[1])
        r = cp.smart_money_reading("SOL")
        assert r["read"] is False and r["reason"] == "unreachable"
        cp.smart_money_reading("SOL")
        assert len(calls) == 1, "one timeout per symbol per minute"
        monkeypatch.setattr(cp, "_call", lambda path, data=None: (503, {"error": "context_unavailable"}))
        r = cp.smart_money_reading("ARB")
        assert r["reason"] == "unreachable" and "503" in r["detail"]
        # A reading inside a non-200 body is not believed: the status is the
        # website's word on whether its answer stands.
        monkeypatch.setattr(cp, "_call", lambda path, data=None: (503, {"reading": {"read": True,
            "net_flow_usd": 1.0}}))
        r = cp.smart_money_reading("XRP")
        assert r["read"] is False and r["reason"] == "unreachable"

    def test_no_channel_is_unconfigured_without_a_call(self, monkeypatch):
        monkeypatch.setattr(cp, "is_configured", lambda: False)
        monkeypatch.setattr(cp, "_call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")))
        assert cp.smart_money_reading("SOL") == {"read": False, "reason": "unconfigured"}
        assert cp.smart_money_reading("") == {"read": False, "reason": "malformed"}


def test_the_signal_card_data_carries_the_reading_once(monkeypatch):
    """`signal_card_from_idea` asks the pull once per card and hands the
    reading to the renderer unchanged; a caller that already has one passes
    it in `scan_data` and the pull is not asked."""
    import bot.formatters.signal_card as sc
    from tests.test_live_website_sync import _idea

    seen = {}
    monkeypatch.setattr(sc, "render_signal_card", lambda data: seen.setdefault("data", data) or b"png")
    monkeypatch.setattr("bot.utils.context_pull.smart_money_reading",
                        lambda symbol: {"read": True, "net_flow_usd": 42.0, "symbol": symbol})
    sc.signal_card_from_idea(_idea("TI-1", "SOL/USDT:USDT"))
    assert seen["data"]["smart_money"] == {"read": True, "net_flow_usd": 42.0, "symbol": "SOL/USDT:USDT"}
    seen.clear()
    monkeypatch.setattr("bot.utils.context_pull.smart_money_reading",
                        lambda symbol: (_ for _ in ()).throw(AssertionError("the pull must not be asked twice")))
    sc.signal_card_from_idea(_idea("TI-2", "SOL/USDT:USDT"), scan_data={"smart_money": {"read": False,
        "reason": "refused"}})
    assert seen["data"]["smart_money"] == {"read": False, "reason": "refused"}


class TestTheDisputedWord:
    """A settled call a second source disagreed with carries the word; a call
    the second source agreed with, or that was not checked, does not."""

    def _round(self, state, disputed):
        return {"round_id": 1, "kind": "direction", "symbol": "SOL", "callable": False,
                "my_call": {"pick": "long", "outcome": "up", "move_pct": 5.0, "disputed": disputed},
                "my_result": {"state": state, "marks": 1}}

    def test_true_adds_the_word_and_nothing_else_does(self):
        assert duel_card.call_line(self._round("correct", True)).endswith("(+5.00%) · disputed")
        assert "disputed" not in duel_card.call_line(self._round("correct", False))
        assert "disputed" not in duel_card.call_line(self._round("correct", None))
        assert "disputed" not in duel_card.call_line(self._round("wrong", None))
        assert duel_card.call_line(self._round("wrong", True)).endswith("· disputed")

    def test_a_running_call_is_never_disputed(self):
        r = self._round("pending", True)
        r["my_call"]["pending"] = True
        assert "disputed" not in duel_card.call_line(r)
