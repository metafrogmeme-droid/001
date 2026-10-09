"""A strategy card says how often its breaker tripped and what reset it modelled.

The house card (Full Scan) read -4.15% on 9 trades: a five-loss streak tripped
the breaker after the ninth trade and it refused the next 86 ideas, because a
replay has no operator to reset it. The owner chose that the cards model an
operator's reset after 24 bars (`CARD_BREAKER_RESET_BARS`). A figure measured
with that assumption says so beside itself, with the trip count, so a reader
can see how often the run would have stood still without one.

One reading per quantity: the count is the risk engine's own
(`RiskEngine.stats["circuit_breaker_trips"]`), carried on the backtest result,
written to the card by the generator as the runner reported it, and passed
through the catalogue. A card recorded before the block says nothing about it.
"""
from __future__ import annotations

import pytest

from bot.backtest.data_loader import DataLoader
from bot.backtest.models import BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester
from bot.core import strategy_catalog as cat
from scripts.gen_agent_scorecards import (
    CARD_BREAKER_RESET_BARS,
    breaker_args,
    breaker_block,
)

# ── the runner's result carries the risk engine's count ─────────────────────

async def _run(reset_bars: int):
    data = {
        "BTC/USDT": DataLoader.generate_synthetic(bars=700, seed=3),
        "ETH/USDT": DataLoader.generate_synthetic(bars=700, seed=6, start_price=3000.0),
    }
    pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0,
                                            breaker_reset_bars=reset_bars),
                             symbols=list(data))
    try:
        pb._risk._trip_circuit_breaker("test: tripped before the run", cause="streak")
        res = await pb.run(data)
        return res, pb._risk.stats["circuit_breaker_trips"]
    finally:
        pb.cleanup()


@pytest.mark.asyncio
async def test_a_halted_run_reports_its_one_trip_and_no_reset():
    res, trips = await _run(0)
    assert (res.breaker_trips, res.breaker_reset_bars) == (1, 0)
    assert res.breaker_trips == trips


@pytest.mark.asyncio
async def test_a_reset_run_reports_the_reset_and_every_trip():
    res, trips = await _run(24)
    assert res.breaker_reset_bars == 24
    assert res.breaker_trips == trips >= 1
    dumped = res.model_dump()
    assert dumped["breaker_trips"] == trips and dumped["breaker_reset_bars"] == 24


# ── the generator writes what the runner reported, or refuses ───────────────

def test_the_card_records_the_runner_s_breaker():
    assert breaker_block({"breaker_reset_bars": 24, "breaker_trips": 3}, 24) == {
        "reset_bars": 24, "trips": 3}
    assert breaker_block({"breaker_reset_bars": 0, "breaker_trips": 0}, 0) == {
        "reset_bars": 0, "trips": 0}


@pytest.mark.parametrize("runner", [
    {},                                                  # an older runner
    {"breaker_reset_bars": 24},                          # no trip count
    {"breaker_reset_bars": 24, "breaker_trips": None},
    {"breaker_reset_bars": 24, "breaker_trips": True},   # not a count
    {"breaker_reset_bars": 24, "breaker_trips": -1},
])
def test_a_runner_that_did_not_report_the_breaker_writes_no_card(runner):
    with pytest.raises(ValueError):
        breaker_block(runner, 24)


def test_a_runner_that_ran_another_reset_writes_no_card():
    # The portfolio loop once accepted the flag and ran 0.
    with pytest.raises(ValueError, match="asked for 24"):
        breaker_block({"breaker_reset_bars": 0, "breaker_trips": 1}, 24)


def test_the_cards_model_a_day_of_1h_bars_and_the_flag_says_so():
    assert CARD_BREAKER_RESET_BARS == 24
    assert breaker_args(24) == ["--breaker-reset-bars", "24"]
    assert breaker_args(0) == []
    with pytest.raises(ValueError):
        breaker_args(-1)


# ── the catalogue passes it through; absent is not zero ─────────────────────

def test_the_catalogue_passes_the_block_through():
    assert cat._breaker({"breaker": {"reset_bars": 24, "trips": 3}}) == {
        "reset_bars": 24, "trips": 3}
    # A measured 0 is a count.
    assert cat._breaker({"breaker": {"reset_bars": 24, "trips": 0}}) == {
        "reset_bars": 24, "trips": 0}


@pytest.mark.parametrize("card", [
    {},                                                  # recorded before the block
    {"breaker": None},
    {"breaker": {"reset_bars": 24}},
    {"breaker": {"reset_bars": 24, "trips": "3"}},
    {"breaker": {"reset_bars": True, "trips": 3}},
])
def test_a_card_without_a_readable_block_says_nothing_about_it(card):
    assert cat._breaker(card) is None


def test_every_committed_card_carries_its_breaker():
    for entry in cat.catalog():
        sc = entry.get("scorecard") or {}
        if not sc.get("metrics"):
            continue
        assert sc.get("breaker") is not None, entry.get("id")
        assert sc["breaker"]["reset_bars"] == CARD_BREAKER_RESET_BARS, entry.get("id")
