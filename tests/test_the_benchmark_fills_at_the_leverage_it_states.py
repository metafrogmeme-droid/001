"""The frozen benchmark fills every trade at 1x, and now says so.

The risk gate's ``position_size_usd`` is a MARGIN at every site that opens a
position: the live executor places ``size_usd * leverage / price`` contracts,
and the engine's practice fill opens the paper book at
``CONFIG.exchange.default_leverage`` (5x), both lowered by the idea's
margin-risk cap. The backtest's fill called
``PortfolioTracker.open_position(idea, size_usd)`` with no leverage at all, so
it took the tracker's default of 1 and opened the size as the WHOLE notional.
Driven on one $100 fill at $100:

    backtest (default)   quantity 1.0   margin $100   +1% -> +$1.00
    live / practice      quantity 5.0   margin $100   +1% -> +$5.00

So the record every sizing decision in `docs/FROZEN_BENCHMARK.md` was made on
measures a bot risking a fifth of what live risks per trade, and the
MAX_LEVERAGE chapter of CLAUDE.md had claimed the benchmark ran at the live
leverage. `BacktestConfig.leverage` is the fill's leverage now, threaded from
the runner's ``--leverage`` into every config the runner builds and lowered by
the same ``apply_margin_risk_cap`` the practice fill uses; the default STAYS 1
so the record on file reproduces line for line, ``--honest`` does not touch
it, the artefact records it, and both documents say which leverage the record
was measured at. Which leverage the record SHOULD be measured at is the
operator's decision and is filed with the 5x arm's numbers.
"""
from __future__ import annotations

import ast
import inspect
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.backtest import benchmark_record as br
from bot.backtest import engine as bt_engine
from bot.backtest import parity, runner
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.compat import UTC
from bot.core.leverage import RISK_CAP_ATTR
from bot.utils.models import Direction, TradeIdea
from tests.source_scan import code_only
from tests.test_the_parity_card_reads_the_benchmark_on_record import _artefact, _card_shaped_rows, _plant

ROOT = Path(__file__).resolve().parents[1]


def _engine(**cfg):
    return BacktestEngine(BacktestConfig(
        symbol="BTC/USDT", timeframe="1h", initial_balance=1_000.0,
        slippage_pct=0.0, commission_pct=0.0, **cfg))


def _bar():
    return BacktestBar(timestamp=datetime(2025, 1, 1, 5, tzinfo=UTC),
                       open=100.0, high=101.0, low=99.0, close=100.5,
                       volume=1000.0, symbol="BTC/USDT")


def _risk_check(size_usd=100.0):
    return SimpleNamespace(position_size_usd=size_usd,
                           verdict=SimpleNamespace(value="APPROVED"))


def _idea(cap=None):
    idea = TradeIdea(
        id="TI-LEV", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=98.0, take_profit=110.0,
        confidence=0.7, reasoning="x", source="t",
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
    )
    if cap is not None:
        object.__setattr__(idea, RISK_CAP_ATTR, cap)
    return idea


def _fill(eng, idea=None):
    eng._execute_fill(idea or _idea(), _risk_check(100.0), fill_price=100.0, bar=_bar())
    (pos,) = eng.portfolio.open_positions
    return pos


class TestTheFillOpensAtTheConfiguredLeverage:

    def test_the_default_is_1x_and_the_size_is_the_whole_notional(self):
        eng = _engine()
        try:
            pos = _fill(eng)
            assert pos.leverage == 1
            assert pos.quantity == pytest.approx(1.0), "size $100 at $100 is one unit at 1x"
            assert eng.portfolio.balance == pytest.approx(900.0), "the whole size is the margin"
            closed = eng.portfolio.close_position(pos.trade_id, 101.0)
            assert closed.pnl == pytest.approx(1.0), "+1% on a $100 notional"
        finally:
            eng.cleanup()

    def test_five_x_opens_five_times_the_notional_for_the_same_margin(self):
        """What live and the practice fill do with the same $100."""
        eng = _engine(leverage=5)
        try:
            pos = _fill(eng)
            assert pos.leverage == 5
            assert pos.quantity == pytest.approx(5.0)
            assert eng.portfolio.balance == pytest.approx(900.0), "the margin deducted is the size, once"
            closed = eng.portfolio.close_position(pos.trade_id, 101.0)
            assert closed.pnl == pytest.approx(5.0), "+1% on a $500 notional"
        finally:
            eng.cleanup()

    def test_the_ideas_margin_risk_cap_lowers_the_fill_leverage(self):
        """A 5x config under a 2x cap fills at 2x: the reading the practice
        fill takes, reduce-only, and a no-op for an idea with no cap."""
        eng = _engine(leverage=5)
        try:
            pos = _fill(eng, _idea(cap=2))
            assert pos.leverage == 2
            assert pos.quantity == pytest.approx(2.0)
        finally:
            eng.cleanup()

    def test_the_cap_never_raises_it(self):
        eng = _engine(leverage=1)
        try:
            assert _fill(eng, _idea(cap=10)).leverage == 1
        finally:
            eng.cleanup()

    def test_it_is_the_practice_fills_own_reading(self, monkeypatch):
        """Planted: the fill asks `apply_margin_risk_cap` with the config's
        leverage and the idea, and opens at what it answers -- a
        byte-identical copy of that clamp would agree with every fixture."""
        asked = []

        def _planted(target, idea):
            asked.append((target, idea.id))
            return 3

        monkeypatch.setattr(bt_engine, "apply_margin_risk_cap", _planted)
        eng = _engine(leverage=5)
        try:
            pos = _fill(eng)
            assert asked == [(5, "TI-LEV")]
            assert pos.leverage == 3 and pos.quantity == pytest.approx(3.0)
        finally:
            eng.cleanup()


class TestTheConfigRefusesWhatIsNotAFill:

    @pytest.mark.parametrize("bad", [0, -1], ids=["zero", "negative"])
    def test_a_leverage_below_one_is_refused(self, bad):
        with pytest.raises(Exception):
            BacktestConfig(leverage=bad)

    def test_the_record_on_file_is_one_x(self):
        assert BacktestConfig().leverage == 1


class TestTheRunnerThreadsTheFlag:

    def test_the_flag_defaults_to_the_records_leverage_and_honest_leaves_it(self):
        ns = runner.build_parser().parse_args(["--dataset", "x"])
        assert ns.leverage == 1
        ns = runner.build_parser().parse_args(["--dataset", "x", "--honest", "--leverage", "5"])
        runner._apply_honest_fidelity(ns)
        assert ns.leverage == 5, "--honest measures the record's exits and fees, never a leverage"
        ns = runner.build_parser().parse_args(["--dataset", "x", "--honest"])
        runner._apply_honest_fidelity(ns)
        assert ns.leverage == 1

    def test_every_config_the_runner_builds_carries_it(self):
        """A scan, stated as one: the three `BacktestConfig(...)` calls in the
        runner are inside coroutines that load data and run folds, so the
        claim is a CALL SHAPE -- each carries `leverage=args.leverage` -- and a
        fourth call added tomorrow without it fails here."""
        tree = ast.parse(inspect.getsource(runner))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "BacktestConfig"]
        assert len(calls) == 3, [n.lineno for n in calls]
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords if k.arg}
            assert "leverage" in kw, f"BacktestConfig at line {call.lineno} carries no leverage"
            assert ast.unparse(kw["leverage"]) == "args.leverage", call.lineno

    def test_the_walk_forward_base_and_the_artefact_carry_it(self):
        src = code_only(inspect.getsource(runner))
        assert '"leverage": args.leverage' in src, "the walk-forward base overrides"
        assert '"leverage": config.leverage' in src, "the artefact records the leverage it was measured at"


class TestBothDocumentsSayWhichLeverageTheRecordWasMeasuredAt:

    def test_the_benchmark_doc_states_the_records_leverage(self):
        doc = (ROOT / "docs" / "FROZEN_BENCHMARK.md").read_text()
        lev = BacktestConfig().leverage
        assert f"fills every trade at {lev}x" in doc, "the record's leverage is stated, derived"
        assert "--leverage 5" in doc, "the arm that measures what live places is named"

    def test_claude_md_no_longer_claims_the_benchmark_runs_at_the_live_leverage(self):
        text = (ROOT / "CLAUDE.md").read_text()
        assert len(re.findall(r"frozen benchmark runs at 5x", text)) == 0
        assert "the frozen benchmark fills at 1x" in text


# ── the record on file says which leverage it was measured at ────────────
class TestTheRecordSaysWhichLeverageItWasMeasuredAt:

    def test_an_artefact_written_before_the_key_is_1x_by_construction_and_says_so(self, tmp_path):
        r = br.benchmark_on_record(_plant(tmp_path, _artefact()))
        assert r.state == "read"
        assert r.leverage == 1 and r.leverage_recorded is False
        line = br.card_line(r)
        assert "fills at 1x (recorded before the artefact said" in line

    def test_a_recorded_leverage_is_read_and_printed(self, tmp_path):
        r = br.benchmark_on_record(_plant(tmp_path, _artefact(leverage=5)))
        assert r.leverage == 5 and r.leverage_recorded is True
        assert "fills at 5x" in br.card_line(r)
        assert "recorded before" not in br.card_line(r)

    @pytest.mark.parametrize("junk", ["5", 0, -2, 2.5, None], ids=["str", "zero", "neg", "float", "null"])
    def test_a_leverage_the_artefact_records_but_cannot_place_is_unreadable_by_name(self, tmp_path, junk):
        r = br.benchmark_on_record(_plant(tmp_path, _artefact(leverage=junk)))
        assert r.state == "unreadable"
        assert "leverage" in r.reason and "not a fill" in r.reason
        assert "not \"no benchmark\"" in br.card_line(r)

    def test_the_parity_card_carries_it_through_the_summary(self, tmp_path):
        p = _plant(tmp_path, _artefact(leverage=5))
        s = parity.parity_summary(_card_shaped_rows(), 0.06, benchmark=br.benchmark_on_record(p))
        assert s["benchmark"]["leverage"] == 5 and s["benchmark"]["leverage_recorded"] is True
        assert "fills at 5x" in parity.format_report(s), "the rendered card says it"
