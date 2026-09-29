"""The per-trade risk budget is the LOSS AT THE STOP, at the leverage the order places at.

`SWING_MAX_RISK_PCT` (2% of equity by default) is the figure an operator sets
to say how much a stop-out may cost. The risk gate's base was
`risk_budget / stop_distance` -- a NOTIONAL such that the loss at the stop is
the budget -- and every site that opens a position commits that figure as
MARGIN at the leverage the order places at (the live executor's
`size_usd * leverage / price`, the practice fill at DEFAULT_LEVERAGE, the
backtest at `BacktestConfig.leverage`). So the loss at the stop was the budget
TIMES the leverage: 2% x 5 at the 5x default, with the notional cap (13% of
equity) the only thing that made it smaller, and the four `*_MAX_RISK_PCT`
knobs binding nothing under it (CLAUDE.md's #148 chapter drove the arithmetic:
the loss at the stop was `cap x MAX_MARGIN_RISK_PCT`, a product of two knobs
neither of which is called a risk budget).

Decided 2026-09-28 (delegated): the budget means what it says.

    margin = risk_budget / (stop_distance x leverage)

Driven on a $10,000 book, swing budget 2%, 5x standard, 13% cap:

    stop 3%   cap binds     $1,300 (as before)      loss at stop $195 <= $200
    stop 4%   base binds    $1,000 (was $1,300)     loss at stop $200 == budget
    stop 6%   base binds    $  667 (was $1,300)     loss at stop $200 == budget

The leverage is read ONCE, at the top of the evaluation, and the margin-risk
cap reads the same figure -- two readers of one leverage were the #201 shape.
The backtest hands its fill leverage in (`fill_leverage=`), so at its plain 1x
the base is the notional it always was and the record reproduces; under
`--honest` it fills at the operator standard, so the record measures what
live commits. Every other caller places at the operator standard and the gate
reads it for itself.
"""
from __future__ import annotations

import ast
import inspect
import os
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot.core.session_aware as session_aware
import bot.risk.risk_engine as rem
from bot.backtest import engine as bt_engine
from bot.backtest import runner
from bot.compat import UTC
from bot.config import CONFIG, RUNTIME
from bot.core.leverage import StandardLeverage, operator_standard
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
_LONDON = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
EQUITY = 10_000.0
BUDGET_PCT = 2.0      # SWING_MAX_RISK_PCT default
CAP_PCT = 13.0        # the swing notional cap, % of equity


@pytest.fixture(autouse=True)
def _measure_at_london(monkeypatch):
    """The session multiplier is x1.0 at London and x0.75 in the Asian
    session; a figure that depends on the hour is a fixture reading the wall
    clock (the live-book chapter's own 08:00 UTC failure)."""
    real = session_aware.get_current_session

    def _at(now=None):
        return real(_LONDON if now is None else now)

    monkeypatch.setattr(session_aware, "get_current_session", _at)


@pytest.fixture(autouse=True)
def _shipped_knobs():
    """The fixture is the shipped arithmetic; a box whose .env moved a knob
    must not move the numbers below."""
    assert CONFIG.strategy_types.get_max_risk_pct("swing") == BUDGET_PCT
    assert CONFIG.strategy_types.get_max_position_pct("swing", CONFIG.risk.max_position_pct) == CAP_PCT
    assert operator_standard(CONFIG.exchange, None).leverage == 5
    assert CONFIG.risk.max_margin_risk_pct == 30.0
    yield


@pytest.fixture
def no_override():
    old = RUNTIME.leverage_override
    RUNTIME.leverage_override = None
    try:
        yield
    finally:
        RUNTIME.leverage_override = old


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-budget-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=EQUITY), state_file=state)


def _idea(stop_pct: float, entry: float = 100.0):
    """A swing long at `entry` with its stop `stop_pct` below and a 2R target,
    at a confidence over the floor."""
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=entry,
        stop_loss=entry * (1 - stop_pct / 100), take_profit=entry * (1 + 2 * stop_pct / 100),
        confidence=0.72, reasoning="budget", source="scan", timestamp=datetime.now(UTC),
    )


def _size(check) -> float:
    return float(check.position_size_usd)


def _base_label(check) -> str:
    return next(p for p in check.size_path if p.startswith("fixed-fractional"))


# ── the budget bounds the loss at the stop ────────────────────────────────

class TestTheBudgetIsTheLossAtTheStop:

    @pytest.mark.parametrize("stop_pct, expected", [(4.0, 1_000.0), (6.0, 1_000.0 * 4 / 6)],
                             ids=["4pct", "6pct"])
    def test_a_wide_stop_is_sized_so_the_stop_costs_the_budget(self, no_override, stop_pct, expected):
        check = _engine().evaluate(_idea(stop_pct), atr=2.0)
        assert check.verdict.name == "APPROVED", check.checks_failed
        # The check carries the figure to the cent, so the comparison is to
        # the cent, and the loss at the stop to half a dollar of it.
        assert _size(check) == pytest.approx(expected, abs=0.01)
        loss_at_stop = _size(check) * 5 * stop_pct / 100
        assert loss_at_stop == pytest.approx(EQUITY * BUDGET_PCT / 100, abs=0.5)
        assert _base_label(check).startswith(f"fixed-fractional (swing risk 2% / stop {stop_pct:.2f}% at 5x)")
        assert check.size_basis.startswith("fixed-fractional"), check.size_basis

    def test_a_tight_stop_is_still_the_caps(self, no_override):
        """3% at 5x asks for 13.3% of equity; the 13% cap decides, and the
        loss at the stop is UNDER the budget -- the cap only ever tightens."""
        check = _engine().evaluate(_idea(3.0), atr=2.0)
        assert _size(check) == pytest.approx(EQUITY * CAP_PCT / 100)
        assert check.size_basis.startswith("notional cap")
        assert _size(check) * 5 * 0.03 < EQUITY * BUDGET_PCT / 100

    @pytest.mark.parametrize("stop_pct", [2.0, 3.0, 4.0, 5.0, 6.0])
    def test_the_loss_at_the_stop_never_exceeds_the_budget(self, no_override, stop_pct):
        check = _engine().evaluate(_idea(stop_pct), atr=2.0)
        assert check.verdict.name == "APPROVED", check.checks_failed
        assert _size(check) * 5 * stop_pct / 100 <= EQUITY * BUDGET_PCT / 100 + 0.5, "to the cent the check carries"


# ── the leverage is the one the order places at ──────────────────────────

class TestTheLeverageIsTheOrders:

    def test_the_override_lowers_the_base(self):
        """A 2% stop at 5x asks for 20% of equity (the cap decides, $1,300);
        under `/leverage set 10` the same budget over the same stop is $1,000,
        and the base decides."""
        old = RUNTIME.leverage_override
        try:
            RUNTIME.leverage_override = None
            at5 = _engine().evaluate(_idea(2.0), atr=2.0)
            RUNTIME.leverage_override = 10
            at10 = _engine().evaluate(_idea(2.0), atr=2.0)
        finally:
            RUNTIME.leverage_override = old
        assert _size(at5) == pytest.approx(1_300.0) and at5.size_basis.startswith("notional cap")
        assert _size(at10) == pytest.approx(1_000.0) and at10.size_basis.startswith("fixed-fractional")
        assert "at 10x)" in _base_label(at10)

    def test_the_base_and_the_margin_risk_cap_read_one_leverage(self, monkeypatch, no_override):
        """Plant the reading: a standard no config produces, and BOTH lines
        carry it -- the base label and the margin-risk sentence. A second
        resolution would agree with every honest fixture and disagree here."""
        planted = StandardLeverage(4, 4, "default", 10)
        monkeypatch.setattr(rem, "operator_standard", lambda cfg, override: planted)
        check = _engine().evaluate(_idea(4.0), atr=2.0)
        assert "at 4x)" in _base_label(check)
        assert _size(check) == pytest.approx(EQUITY * BUDGET_PCT / 100 / (0.04 * 4))
        mr = next(ln for ln in check.checks_passed if ln.startswith("MARGIN_RISK"))
        assert "4x" in mr and "5x" not in mr, mr

    def test_the_backtests_fill_leverage_is_what_the_gate_divides_by(self, no_override):
        """At the plain 1x the base is the notional it always was -- the
        record reproduces -- and the margin-risk cap is measured at 1x, so a
        10% stop the 5x reading refuses is admitted where the fill really
        runs at 1x."""
        eng = _engine()
        plain = eng.evaluate(_idea(20.0), atr=2.0, fill_leverage=1)
        assert _size(plain) == pytest.approx(EQUITY * BUDGET_PCT / 100 / 0.20), "2% / 20% at 1x = $1,000"
        assert "at 1x)" in _base_label(plain)
        capped = eng.evaluate(_idea(4.0), atr=2.0, fill_leverage=1)
        assert _size(capped) == pytest.approx(EQUITY * CAP_PCT / 100), "2% / 4% = 50% of equity: the cap"
        wide_at_1x = eng.evaluate(_idea(10.0), atr=2.0, fill_leverage=1)
        assert not any(ln.startswith("MARGIN_RISK") for ln in wide_at_1x.checks_failed)
        wide_at_5x = eng.evaluate(_idea(10.0), atr=2.0)
        assert any(ln.startswith("MARGIN_RISK") for ln in wide_at_5x.checks_failed), wide_at_5x.checks_passed

    def test_a_fill_leverage_under_one_reads_as_one(self, no_override):
        check = _engine().evaluate(_idea(20.0), atr=2.0, fill_leverage=0)
        assert "at 1x)" in _base_label(check)

    def test_the_backtest_engine_hands_its_config_leverage_in(self):
        """A scan, stated as one: the backtest's gate call is inside a
        400-line bar loop behind a data loader, and the claim is a CALL SHAPE."""
        src = inspect.getsource(bt_engine.BacktestEngine)
        tree = ast.parse(inspect.cleandoc(src) if src.startswith(" ") else src)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "evaluate"
                 and getattr(getattr(n.func, "value", None), "attr", None) == "risk"]
        assert len(calls) == 1, "one gate call in the backtest engine"
        kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
        assert kw.get("fill_leverage") == "int(self.config.leverage)", kw


# ── the honest run fills at the leverage live places ─────────────────────

class TestTheHonestRunFillsAtTheLiveLeverage:

    def test_honest_resolves_the_operator_standard(self):
        args = SimpleNamespace(honest=True, leverage=None, commission=None)
        runner._apply_honest_fidelity(args)
        assert args.leverage == operator_standard(CONFIG.exchange, None).leverage == 5

    def test_an_explicit_leverage_is_kept_under_honest(self):
        args = SimpleNamespace(honest=True, leverage=2, commission=None)
        runner._apply_honest_fidelity(args)
        assert args.leverage == 2

    def test_a_plain_run_still_fills_at_one(self):
        args = SimpleNamespace(honest=False, leverage=None, commission=None)
        runner._apply_honest_fidelity(args)
        assert args.leverage == 1 and args.commission == 0.1

    def test_the_flag_defaults_to_none_so_explicit_can_be_told_from_absent(self):
        src = code_only(inspect.getsource(runner))
        assert 'add_argument("--leverage", type=int, default=None' in src

    def test_the_resolution_never_reads_the_runtime_override(self):
        """A benchmark is a reproducible measurement; the /leverage override
        is a state file. Under an override of 10 the honest run still fills
        at the configured standard."""
        old = RUNTIME.leverage_override
        try:
            RUNTIME.leverage_override = 10
            args = SimpleNamespace(honest=True, leverage=None, commission=None)
            runner._apply_honest_fidelity(args)
        finally:
            RUNTIME.leverage_override = old
        assert args.leverage == 5


# ── the documents say what the code does ─────────────────────────────────

class TestTheDocumentsSayWhatTheCodeDoes:

    def test_the_readme_calls_the_budget_the_loss_at_the_stop(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "risk budget (1-2% of equity) is the loss at the stop" in text
        assert "fills the same figure at the live leverage" in text
        assert "3.9% of equity at defaults" not in text, "the superseded arithmetic, stated as current"

    def test_the_runbook_and_env_example_name_the_leverage_in_the_denominator(self):
        rb = (ROOT / "docs" / "LIVE_HARDENING_RUNBOOK.md").read_text(encoding="utf-8")
        assert "`risk_budget / (stop_distance_pct × leverage)`" in rb
        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "risk_budget / (stop_distance x leverage) sizing" in env

    def test_the_gate_spells_the_base_once(self):
        """One division in the gate: the budget over (stop x leverage). A
        second `risk_budget / stop_distance_pct` is the notional coming back."""
        src = code_only(inspect.getsource(rem.RiskEngine._evaluate_locked))
        assert src.count("risk_budget / (stop_distance_pct * _lev_std)") == 1
        assert "risk_budget / stop_distance_pct" not in src
