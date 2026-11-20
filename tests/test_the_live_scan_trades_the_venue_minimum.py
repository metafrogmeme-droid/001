"""The scan's live orders go out at the venue's minimum, and nothing else changes.

Owner decision, 2026-11-20. The hourly scan's live record measured a negative
edge (README, read 2026-10-17: 249 strategy exits, mean result per trade below
zero with its 95% interval below zero). It keeps placing live orders, for the
fills, fees and close prices the cost model reads, at the smallest size a
market takes. `LIVE_SCAN_SIZE` is the setting, "venue_min" is what ships, and
`LiveExecutor._scan_order_at_venue_minimum` is the one place it is applied.

What this file holds, each with both arms:

  * The order that reaches the venue is the market's minimum, and the margin
    recorded for it is that order's margin, not the risk-sized one.
  * It only ever shrinks. An order already at or under the minimum is left to
    the round-up gate, which decides it as it did before.
  * A minimum that cannot be read refuses the order. Placing the risk-sized
    amount instead would be the outcome the setting exists to prevent.
  * An order a person typed, an order on a venue testnet, and every order
    under "risk_sized" are placed at the size they came with.

Driven through the real `execute` against a planted venue. The suite's
harness sets "risk_sized" for every other file (tests/conftest.py says why);
this file sets the shipped value back and reads the shipped default from a
fresh process.
"""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import bot.core.live_executor as le
from bot.config import CONFIG
from bot.core import bounds_shadow
from bot.core.live_executor import (
    LiveExecutor,
    execution_indicates_failure,
    venue_minimum_quantity,
    venue_minimums,
)
from bot.utils.models import Direction, TradeIdea
from tests.test_the_placed_order_is_the_checked_order import _market, _Sent, _Venue

ROOT = Path(__file__).resolve().parent.parent
PRICE = 2_000.0
#: 0.01 ETH at $2,000 is $20 of notional: over the $5 minimum cost, so the
#: amount floor is what binds. At 5x that is $4 of margin.
MIN_QTY = 0.01
MIN_MARGIN_AT_5X = MIN_QTY * PRICE / 5
#: $50 of margin at 5x is $250, 0.125 ETH, which the venue's 0.01 grid
#: truncates to 0.12: twelve times the minimum.
RISK_SIZED_QTY = 0.125
RISK_SIZED_SENT = 0.12


@pytest.fixture(autouse=True)
def _the_shipped_setting():
    """The harness's fixture runs first and restores what it found, so this
    one only sets the value under test."""
    object.__setattr__(CONFIG.execution, "live_scan_size", "venue_min")
    assert not CONFIG.exchange.sandbox and not CONFIG.exchange.hyperliquid_testnet, (
        "the premise: these orders are on a mainnet venue")
    yield


def _idea(source=None):
    extra = {} if source is None else {"source": source}
    return TradeIdea(id="TI-MIN-1", asset="ETH/USDT", direction=Direction.LONG,
                     entry_price=PRICE, stop_loss=PRICE * 0.98, take_profit=PRICE * 1.06,
                     confidence=0.8, reasoning="fixture", **extra)


def _place(idea, size_usd=50.0, market=None):
    """Drive `execute`. Returns (result, the orders the venue was sent, audit rows)."""
    venue = _Venue(market if market is not None else _market(), PRICE)
    ex = LiveExecutor(state_dir=tempfile.mkdtemp())
    ex._exchange = venue
    audits: list[dict] = []
    with patch.object(le, "audit", lambda log, msg, **kw: audits.append({"message": msg, **kw})), \
         patch.object(bounds_shadow.BOUNDS_LEDGER, "record", lambda *a, **k: None), \
         patch.object(type(CONFIG), "is_live", return_value=True):
        try:
            result = asyncio.run(ex.execute(idea, size_usd=size_usd, order_type="market"))
        except _Sent:
            result = "<sent>"
    return result, venue.orders, audits


def steps_leverage(idea) -> int:
    """The leverage the shrunk margin is stated at: the venue's, which is the
    unit `size_usd` is in by the time the order's quantity exists."""
    _, _, audits = _place(_idea())
    return [a for a in audits if a.get("result") == "SHRUNK_TO_VENUE_MIN"][0]["data"]["leverage"]


def _results(audits):
    return [row.get("result") for row in audits if row.get("action") == "live_execute"]


# ── the order that reaches the venue ──────────────────────────────────────

class TestTheOrderSent:

    def test_the_premise_the_same_order_is_twelve_times_the_minimum_when_risk_sized(self):
        """The fixture can tell the two apart: risk-sized, this order goes out
        at 0.12 ETH against a 0.01 minimum. Without that gap a shrink and a
        no-op would send the same order."""
        object.__setattr__(CONFIG.execution, "live_scan_size", "risk_sized")
        result, orders, audits = _place(_idea())
        assert result == "<sent>"
        assert orders[0]["amount"] == pytest.approx(RISK_SIZED_SENT)
        assert "SHRUNK_TO_VENUE_MIN" not in _results(audits)

    def test_a_scan_order_is_placed_at_the_venue_minimum(self):
        result, orders, audits = _place(_idea())
        assert result == "<sent>", result
        assert len(orders) == 1 and orders[0]["amount"] == MIN_QTY
        row = [a for a in audits if a.get("result") == "SHRUNK_TO_VENUE_MIN"][0]
        assert row["data"]["qty"] == MIN_QTY
        assert row["data"]["risk_sized_qty"] == pytest.approx(RISK_SIZED_QTY)
        # The margin that moves with it is the placed order's, in the unit the
        # caps and the position record read.
        assert row["data"]["margin"] == pytest.approx(
            row["data"]["risk_sized_margin"] * MIN_QTY / RISK_SIZED_QTY)

    def test_the_minimum_cost_binds_when_it_is_the_larger_minimum(self):
        """A market whose amount floor is tiny and whose minimum notional is
        not: $5 at $2,000 is 0.0025, snapped up the 0.001 grid to 0.003."""
        market = _market(amount_step=0.001, amount_min=0.001, cost_min=5.0)
        result, orders, _ = _place(_idea(), market=market)
        assert result == "<sent>"
        assert orders[0]["amount"] == pytest.approx(0.003)
        assert orders[0]["amount"] * PRICE >= 5.0, "the order clears the venue's minimum notional"

    def test_the_shrunk_order_is_not_then_rounded_up_or_refused(self):
        """The minimum gate reads the shrunk order and has nothing to do: no
        round-up, no "too small", and the caps are asked about less, not more."""
        result, _, audits = _place(_idea())
        assert result == "<sent>"
        seen = _results(audits)
        for unwanted in ("ROUNDED_TO_MIN", "BELOW_EXCHANGE_MIN", "ROUNDUP_OVER_CAP", "ROUNDUP_OVER_AUTHORIZED"):
            assert unwanted not in seen, seen

    def test_the_size_trace_records_the_step(self):
        """The executor works on a copy of the idea that shares the trace's
        list, so the caller's idea carries the step once a trace is started."""
        from bot.core.size_trace import reset_size_trace, size_steps
        idea = _idea()
        reset_size_trace(idea)
        _place(idea)
        steps = [step for step in size_steps(idea) if "venue minimum" in step.label]
        assert len(steps) == 1, size_steps(idea)
        assert steps[0].usd == pytest.approx(MIN_MARGIN_AT_5X * 5 / steps_leverage(idea))


# ── it only ever shrinks ──────────────────────────────────────────────────

class TestItNeverRaisesAnOrder:

    def test_an_order_already_at_the_minimum_is_left_alone(self):
        result, orders, audits = _place(_idea(), size_usd=MIN_MARGIN_AT_5X)
        assert result == "<sent>" and orders[0]["amount"] == MIN_QTY
        assert "SHRUNK_TO_VENUE_MIN" not in _results(audits)

    def test_an_order_under_the_minimum_is_still_the_round_up_gates_to_decide(self):
        """$3 at 5x is 0.0075 ETH, under the 0.01 minimum by 1.33x: inside the
        shipped 1.5x round-up, so the gate rounds it up exactly as before."""
        assert CONFIG.exchange.exchange_min_roundup_enabled is True
        result, orders, audits = _place(_idea(), size_usd=3.0)
        assert result == "<sent>" and orders[0]["amount"] == MIN_QTY
        seen = _results(audits)
        assert "ROUNDED_TO_MIN" in seen and "SHRUNK_TO_VENUE_MIN" not in seen

    def test_an_order_too_far_under_the_minimum_is_still_refused_by_that_gate(self):
        result, orders, audits = _place(_idea(), size_usd=1.0)   # 0.0025 ETH, 4x under
        assert orders == [] and result.startswith("BLOCKED:")
        assert "BELOW_EXCHANGE_MIN" in _results(audits)

    @pytest.mark.parametrize("quantity", [0.01, 0.009, 0.0001])
    def test_the_step_returns_what_it_was_given_at_or_under_the_minimum(self, quantity):
        ex = LiveExecutor(state_dir=tempfile.mkdtemp())
        block, q, margin = ex._scan_order_at_venue_minimum(
            _idea(), "ETH/USDT:USDT", _market(), quantity, PRICE, 5, 7.0)
        assert (block, q, margin) == (None, quantity, 7.0)


# ── a minimum that cannot be read ─────────────────────────────────────────

class TestAnUnreadMinimumRefuses:

    def test_a_market_that_states_no_minimum_is_refused_and_nothing_is_sent(self):
        market = _market(amount_step=None, amount_min=None, cost_min=None)
        assert venue_minimums(market) == (0.0, 0.0, 0.0), "the premise: nothing is stated"
        result, orders, audits = _place(_idea(), market=market)
        assert orders == [], "an order reached the venue at the risk-sized amount"
        assert result.startswith("BLOCKED:") and "states no minimum order" in result
        assert "Nothing was placed at the risk-sized amount" in result
        assert execution_indicates_failure(result), "a handler must read it as placed nothing"
        assert "VENUE_MIN_UNREAD" in _results(audits)

    def test_no_market_data_is_refused_by_the_step(self):
        ex = LiveExecutor(state_dir=tempfile.mkdtemp())
        with patch.object(le, "audit", lambda *a, **k: None):
            block, q, margin = ex._scan_order_at_venue_minimum(
                _idea(), "ETH/USDT:USDT", None, 0.125, PRICE, 5, 50.0)
        assert block is not None and "returned no market data" in block
        assert (q, margin) == (0.125, 50.0), "a refusal hands back the order unchanged"

    def test_the_same_market_places_at_its_risk_size_when_the_setting_is_off(self):
        """The refusal belongs to the setting. Without it this market is what
        it was before: an order with no minimum to check."""
        object.__setattr__(CONFIG.execution, "live_scan_size", "risk_sized")
        ex = LiveExecutor(state_dir=tempfile.mkdtemp())
        block, q, margin = ex._scan_order_at_venue_minimum(
            _idea(), "ETH/USDT:USDT", None, 0.125, PRICE, 5, 50.0)
        assert (block, q, margin) == (None, 0.125, 50.0)


# ── what is not shrunk ────────────────────────────────────────────────────

class TestWhatIsLeftAtItsOwnSize:

    def test_an_order_a_person_typed_keeps_the_size_they_gave(self):
        result, orders, audits = _place(_idea(source="manual"))
        assert result == "<sent>" and orders[0]["amount"] == pytest.approx(RISK_SIZED_SENT)
        assert "SHRUNK_TO_VENUE_MIN" not in _results(audits)

    @pytest.mark.parametrize("flag", ["sandbox", "hyperliquid_testnet"])
    def test_an_order_on_a_venue_testnet_is_not_shrunk(self, flag):
        previous = getattr(CONFIG.exchange, flag)
        object.__setattr__(CONFIG.exchange, flag, True)
        try:
            ex = LiveExecutor(state_dir=tempfile.mkdtemp())
            block, q, margin = ex._scan_order_at_venue_minimum(
                _idea(), "ETH/USDT:USDT", _market(), 0.125, PRICE, 5, 50.0)
        finally:
            object.__setattr__(CONFIG.exchange, flag, previous)
        assert (block, q, margin) == (None, 0.125, 50.0)

    @pytest.mark.parametrize("source", ["unknown", "scan_skill", "getclaw", "auto_reanalyze"])
    def test_every_producer_but_a_typed_ticket_is_shrunk(self, source):
        """The scan does not stamp its ideas ("unknown" is the field's
        default), so the rule is the negative one the engine already uses:
        everything that is not a hand-typed ticket."""
        ex = LiveExecutor(state_dir=tempfile.mkdtemp())
        with patch.object(le, "audit", lambda *a, **k: None):
            block, q, margin = ex._scan_order_at_venue_minimum(
                _idea(source=source), "ETH/USDT:USDT", _market(), 0.125, PRICE, 5, 50.0)
        assert block is None and q == MIN_QTY
        assert margin == pytest.approx(50.0 * MIN_QTY / 0.125)


# ── the arithmetic ────────────────────────────────────────────────────────

@pytest.mark.parametrize("floor,step,min_cost,price,expected", [
    (0.001, 0.001, 5.0, 63_000.0, 0.001),     # the amount floor binds
    (0.001, 0.001, 5.0, 2_000.0, 0.003),      # the minimum notional binds, snapped up the grid
    (1.0, 1.0, 5.0, 0.5, 10.0),               # whole contracts: $5 at $0.50
    (0.0, 0.0, 5.0, 2.0, 2.5),                # a notional minimum and no grid
    (0.01, 0.01, 0.0, 2_000.0, 0.01),         # an amount floor and no notional minimum
])
def test_the_minimum_is_the_larger_of_the_floor_and_the_minimum_notional(floor, step, min_cost, price, expected):
    assert venue_minimum_quantity(floor, step, min_cost, price) == pytest.approx(expected)


@pytest.mark.parametrize("floor,step,min_cost,price", [
    (0.0, 0.0, 0.0, 2_000.0),     # nothing stated
    (0.0, 0.001, 0.0, 2_000.0),   # a grid is not a minimum
    (0.0, 0.0, 5.0, 0.0),         # a notional minimum and no price to turn it into a quantity
])
def test_nothing_stated_is_none_and_never_zero(floor, step, min_cost, price):
    """None, so a caller cannot shrink an order to a quantity of zero and
    read the refusal that follows as the venue's."""
    assert venue_minimum_quantity(floor, step, min_cost, price) is None


def test_it_is_the_figure_the_round_up_uses():
    """One reading of "the venue's minimum": the round-up's `q_min` and this
    are the same number for the same market, so an order the step shrinks is
    never one the gate then rounds."""
    floor, step, min_cost = venue_minimums(_market(amount_step=0.001, amount_min=0.001, cost_min=5.0))
    _, q_min, _ = le.resolve_exchange_min_quantity(0.0005, floor, step, min_cost, PRICE, True, 1.5)
    assert venue_minimum_quantity(floor, step, min_cost, PRICE) == q_min


# ── the setting ───────────────────────────────────────────────────────────

def _fresh_config(value=None):
    """The setting as a new process reads it. The config is read at import,
    so only a fresh interpreter can say what ships."""
    env = {k: v for k, v in os.environ.items() if k != "LIVE_SCAN_SIZE"}
    env["RUNECLAW_SKIP_DOTENV"] = "1"
    if value is not None:
        env["LIVE_SCAN_SIZE"] = value
    return subprocess.run(
        [sys.executable, "-c",
         "from bot.config import CONFIG; print('SIZE=' + CONFIG.execution.live_scan_size)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_what_ships_is_the_venue_minimum():
    run = _fresh_config()
    assert run.returncode == 0, run.stderr[-400:]
    assert "SIZE=venue_min" in run.stdout


def test_the_other_value_is_read_and_a_misspelling_refuses_to_start():
    run = _fresh_config("RISK_SIZED ")
    assert run.returncode == 0 and "SIZE=risk_sized" in run.stdout, run.stderr[-400:]
    typo = _fresh_config("venue-min")
    assert typo.returncode != 0, "a misspelt value must not quietly become either size"
    assert "LIVE_SCAN_SIZE" in typo.stderr and "venue_min" in typo.stderr


# ── where it sits ─────────────────────────────────────────────────────────

def _execute_calls() -> list[str]:
    tree = ast.parse((ROOT / "bot/core/live_executor.py").read_text(encoding="utf-8"))
    execute = next(node for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef) and cls.name == "LiveExecutor"
                   for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute")
    calls = [(node.lineno, node.func.attr) for node in ast.walk(execute)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    return [name for _, name in sorted(calls)]


def test_the_shrink_runs_before_the_minimum_gate_and_the_caps_and_once():
    """A scan cannot see this by driving one order: applied AFTER the gate, a
    shrunk order would never have had its caps asked at the placed margin.
    The order of the three calls inside `execute` is the property."""
    calls = _execute_calls()
    assert calls.count("_scan_order_at_venue_minimum") == 1
    shrink = calls.index("_scan_order_at_venue_minimum")
    assert shrink < calls.index("_exchange_minimum_gate") < calls.index("_notional_boundary_gate")
    assert shrink < calls.index("_submit_entry_order")


def test_nothing_about_arming_live_is_read_or_written_by_the_step():
    """The setting sizes an order that is already being placed. Whether one
    is placed at all is decided elsewhere, and this step must not grow a
    second answer to that."""
    tree = ast.parse((ROOT / "bot/core/live_executor.py").read_text(encoding="utf-8"))
    step = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "_scan_order_at_venue_minimum")
    names = {node.attr for node in ast.walk(step) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(step) if isinstance(node, ast.Name)}
    for forbidden in ("is_live", "live_suspended", "RUNTIME", "live_trading_enabled",
                      "simulation_mode", "auto_confirm_live_enabled"):
        assert forbidden not in names, forbidden
