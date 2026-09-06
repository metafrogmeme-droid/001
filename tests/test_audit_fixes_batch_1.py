"""Audit batch 1: five surfaces that acted on, or asserted, a read never made.

The rule at the top of CLAUDE.md, in the places where breaking it costs money
rather than credibility:

1. `_cmd_confirm`'s drift retry rebuilt the idea at the new price and CALLED
   `confirm_trade` on it — a market order for a thesis the user never saw,
   authorised by a button they pressed for a different one.
2. An untracked exchange close fell back to `fill_price = entry_price`, which
   books a round trip whose PnL is exactly the fees: a measured-looking number
   for a fill the venue never reported.
3. A paper close fell back to `pos.entry_price`, RETIRING the position at a
   price nothing quoted — the trade leaves the book at a fabricated flat.
4. The emergency card counted accounts ATTEMPTED as accounts flattened. On the
   screen an operator reads to find out whether they still have exposure.
5. `/status` divided by an unreadable equity to 0.0, printing "0.0%" beside a
   daily-loss cap; `/check risk` printed "Breaker: CLEAR" off the NARROW gate
   while the warning-rate breaker refused every entry.
"""

from __future__ import annotations

import inspect

import pytest

from bot.formatters.drift_offer import (
    STOP_PCT,
    TARGET_PCT,
    atr_from_ohlcv,
    flatten_account_ok,
    flatten_headline,
    paper_close_price,
    reanalyzed_idea,
    render_reanalyzed_offer,
    venue_fill_price,
)
from bot.skills.skill_registry import entry_gate, gate_words
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only


def _idea(price: float = 100.0, direction: Direction = Direction.LONG) -> TradeIdea:
    # SHORT geometry is stop ABOVE / target BELOW; the model validates it.
    is_long = direction == Direction.LONG
    return TradeIdea(
        id="TI-orig", asset="BTC/USDT", direction=direction, entry_price=price,
        stop_loss=price * (0.95 if is_long else 1.05),
        take_profit=price * (1.10 if is_long else 0.90),
        confidence=0.8, reasoning="original thesis")


# ── the re-analysed idea ────────────────────────────────────────────────

def test_an_unreadable_price_produces_no_idea_at_all():
    assert reanalyzed_idea(_idea(), 0.0) is None
    assert reanalyzed_idea(_idea(), -1.0) is None


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_the_rebuilt_idea_is_protective_on_both_sides(direction):
    new = reanalyzed_idea(_idea(direction=direction), 200.0)
    assert new is not None
    assert new.entry_price == 200.0
    if direction == Direction.LONG:
        assert new.stop_loss < 200.0 < new.take_profit
    else:
        assert new.take_profit < 200.0 < new.stop_loss
    is_long = direction == Direction.LONG
    assert new.stop_loss == pytest.approx(
        200.0 * ((1 - STOP_PCT) if is_long else (1 + STOP_PCT)), rel=1e-6)
    assert new.take_profit == pytest.approx(
        200.0 * ((1 + TARGET_PCT) if is_long else (1 - TARGET_PCT)), rel=1e-6)
    assert new.source == "auto_reanalyze"


def test_the_rebuilt_reasoning_does_not_claim_an_analysis_happened():
    new = reanalyzed_idea(_idea(), 103.0)
    assert new is not None
    reasoning = new.reasoning.lower()
    assert "placeholder" in reasoning
    assert "not a fresh analysis" in reasoning


def test_the_offer_card_says_it_was_not_executed_and_is_not_the_same_setup():
    original = _idea(100.0)
    new = reanalyzed_idea(original, 103.0)
    card = render_reanalyzed_offer(original, new)
    assert "not executed" in card.lower()
    assert "+3.0%" in card
    assert "not</b> the setup you confirmed" in card
    # It must not read like a fill report.
    assert "executed" not in card.replace("not executed", "")


# ── prices that were never read ─────────────────────────────────────────

@pytest.mark.parametrize("order", [
    {}, {"average": 0}, {"average": None, "price": 0},
    {"average": "", "price": ""}, None, "not a dict",
])
def test_a_venue_that_returned_no_fill_price_reads_as_none(order):
    assert venue_fill_price(order) is None


def test_a_real_fill_price_is_returned_preferring_the_average():
    assert venue_fill_price({"average": 101.5, "price": 100.0}) == 101.5
    assert venue_fill_price({"price": 100.0}) == 100.0


@pytest.mark.parametrize("ticker", [{}, {"last": 0}, {"last": None}, None, 7])
def test_a_ticker_with_no_last_price_reads_as_none(ticker):
    assert paper_close_price(ticker) is None


def test_a_real_last_price_is_returned():
    assert paper_close_price({"last": 42.5}) == 42.5


def test_atr_is_zero_only_when_there_are_too_few_bars():
    assert atr_from_ohlcv([]) == 0.0
    assert atr_from_ohlcv(None) == 0.0
    assert atr_from_ohlcv([[0, 0, 10, 8, 9]]) == 0.0
    assert atr_from_ohlcv([[0, 0, 10, 8, 9], [0, 0, 11, 9, 10]]) == 2.0


# ── the flatten count ───────────────────────────────────────────────────

def test_a_failed_close_makes_the_account_not_ok():
    assert flatten_account_ok(["Closed BTC", "Closed ETH"]) is True
    assert flatten_account_ok([]) is True
    assert flatten_account_ok(["Closed BTC", "Failed to close ETH: 502"]) is False
    assert flatten_account_ok(["close_all_positions failed: timeout"]) is False


def test_the_headline_counts_closes_not_attempts():
    line = flatten_headline([
        {"account": "operator", "messages": [], "ok": True},
        {"account": "alice", "messages": [], "ok": False},
    ])
    assert "1 of 2" in line
    assert "alice" in line and "exposure may remain" in line


def test_an_all_ok_flatten_says_so_without_a_warning():
    line = flatten_headline([{"account": "operator", "messages": [], "ok": True}])
    assert "1 of 1" in line
    assert "FAILED" not in line


def test_no_live_accounts_is_not_reported_as_flattened():
    line = flatten_headline([])
    assert "none (no live accounts)" in line
    assert ": 0" not in line


# ── the entry gate ──────────────────────────────────────────────────────

class _Risk:
    def __init__(self, blocked="", raises=False):
        self._blocked = blocked
        self._raises = raises

    @property
    def trading_blocked_by(self):
        if self._raises:
            raise RuntimeError("risk state unreadable")
        return self._blocked


class _Engine:
    def __init__(self, risk):
        self.risk = risk


def test_a_raised_gate_read_is_unread_not_clear():
    assert entry_gate(_Engine(_Risk(raises=True))) is None
    assert gate_words(None) == "⚪ UNREAD"
    assert "CLEAR" not in gate_words(None)


def test_an_open_gate_is_clear():
    assert entry_gate(_Engine(_Risk(""))) == ""
    assert gate_words("") == "🟢 CLEAR"


def test_the_warning_rate_breaker_is_named_not_shown_as_clear():
    gate = entry_gate(_Engine(_Risk("warning_rate:engine_tick_failure")))
    words = gate_words(gate)
    assert "REFUSING ENTRIES" in words
    assert "engine_tick_failure" in words
    assert "CLEAR" not in words


def test_the_loss_streak_gate_is_named():
    assert "loss-streak" in gate_words("loss_streak")


def test_gate_words_splits_into_an_icon_and_a_label_for_every_state():
    # `_risk` does `icon, label = gate_words(gate).split(" ", 1)`.
    for gate in (None, "", "warning_rate:x", "loss_streak", "manual"):
        icon, label = gate_words(gate).split(" ", 1)
        assert len(icon) == 1 and label


# ── the wiring a unit test cannot reach ─────────────────────────────────

def _handler_code() -> str:
    # Every file the handler class is made of: the trade paths live in the
    # trading mixin since the handler split, and a scan of one file reads
    # the move as the offer block vanishing.
    from tests.source_scan import handler_sources
    return "\n".join(code_only(p.read_text(encoding="utf-8")) for p in handler_sources())


def test_the_drift_retry_offers_and_never_confirms():
    code = _handler_code()
    # Anchored on CODE: the section comment above it is blanked by code_only.
    start = code.index('if "price drifted" in result.lower()')
    block = code[start:start + 4000]
    assert "render_reanalyzed_offer(" in block
    assert "confirm_trade(retry_id" not in block
    assert "confirm:{new_idea.id}" in block


def test_the_untracked_close_never_books_the_entry_price_as_a_fill():
    code = _handler_code()
    start = code.index('close_side = "sell" if side == "LONG" else "buy"')
    block = code[start:start + 9000]
    assert "fill_price = entry_price" not in block
    assert "venue_fill_price(order)" in block
    assert "Net PnL: unread" in block


def test_the_paper_close_refuses_rather_than_closing_at_the_entry_price():
    code = _handler_code()
    start = code.index("for pos in list(portfolio.open_positions)")
    block = code[start:start + 2500]
    assert "close_position(pos.trade_id, pos.entry_price)" not in block
    assert "paper_close_price(ticker)" in block
    assert "NOT</b> closed" in block


def test_the_emergency_card_reads_the_headline_helper():
    code = _handler_code()
    assert "flatten_headline(summary.get('accounts', []))" in code
    assert "Accounts flattened: {len(summary" not in code


def test_status_daily_pnl_is_none_when_the_equity_is_unreadable():
    code = _handler_code()
    start = code.index("daily_pnl_pct = (None if daily_pnl is None")
    block = code[start:start + 200]
    assert "else None)" in block
    assert "else 0.0)" not in block


def test_the_engine_records_whether_each_flatten_actually_closed():
    from bot.core.engine import RuneClawEngine
    code = code_only(inspect.getsource(RuneClawEngine.flatten_all_positions))
    assert '"ok": flatten_account_ok(list(msgs))' in code
    halt = code_only(inspect.getsource(RuneClawEngine.emergency_halt_all))
    assert "accounts_failed" in halt
    assert 'a.get("ok")' in halt


def test_every_breaker_line_in_the_registry_reads_the_wide_gate():
    from bot.skills import skill_registry
    code = code_only(open(skill_registry.__file__, encoding="utf-8").read())
    assert code.count("gate_words(gate)") >= 3
    assert "'TRIPPED' if cb else 'CLEAR'" not in code
