"""An expectancy up-nudge is withheld when the setup lost money per trade.

The nudge is scored by win count, and a win count flatters a setup that loses
money: fourteen small wins beside six full stops read as a 70% setup, and the
nudge raised the confidence of exactly the setups whose record said not to.
Each sample carries its net P&L now, and a boost from a tier that did not make
money per trade is withheld and audited. Only the boost: a down-nudge stands.
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace as NS

import pytest

from bot.learning.setup_expectancy import SetupExpectancy, validate_oos

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _s(wins, losses, win_pnl=0.3, loss_pnl=-1.0, sym="SOL", regime="RANGE", d="LONG"):
    return ([(sym, regime, d, True, win_pnl)] * wins
            + [(sym, regime, d, False, loss_pnl)] * losses)


def _nudge(samples, sym="SOL", regime="RANGE", d="LONG"):
    return SetupExpectancy(min_samples=10).ingest(samples).nudge_for(sym, regime, d)


def test_a_setup_that_wins_most_trades_and_loses_money_gets_no_boost():
    n = _nudge(_s(14, 6))                          # 70% wins, -0.09 per trade
    assert n.value == 0.0 and n.tier == "setup" and n.n == 20
    assert n.withheld_net == pytest.approx((14 * 0.3 - 6) / 20)


def test_the_same_win_count_with_money_made_keeps_its_boost():
    n = _nudge(_s(14, 6, win_pnl=1.0))            # 70% wins, +0.40 per trade
    assert n.value > 0 and n.withheld_net is None


def test_a_setup_that_broke_even_gets_no_boost():
    n = _nudge(_s(10, 5, win_pnl=0.5, loss_pnl=-1.0))
    assert n.value == 0.0 and n.withheld_net == pytest.approx(0.0)


@pytest.mark.parametrize("win_pnl", [0.1, 5.0])
def test_a_down_nudge_stands_whatever_the_money_said(win_pnl):
    # 20% wins: the nudge is down, and the guard only ever removes a boost.
    n = _nudge(_s(3, 12, win_pnl=win_pnl))
    assert n.value < 0 and n.withheld_net is None


def test_a_record_with_no_pnl_is_judged_on_its_win_count_as_before():
    four = [s[:4] for s in _s(14, 6)]
    assert _nudge(four).value > 0


def test_the_mean_is_over_the_samples_that_carried_a_pnl():
    # Ten winners whose P&L is unreadable, five losers that are: the readable
    # half lost, so no boost -- the unreadable half is not a flat 0.
    rows = ([("SOL", "RANGE", "LONG", True, float("nan"))] * 10
            + [("SOL", "RANGE", "LONG", True, True)] * 2
            + [("SOL", "RANGE", "LONG", False, -1.0)] * 3)
    n = _nudge(rows)
    assert n.value == 0.0 and n.withheld_net == pytest.approx(-1.0)


def test_a_planted_two_field_cell_behaves_as_it_always_did():
    exp = SetupExpectancy(min_samples=10)
    exp._table = {("SOL", "RANGE", "LONG"): [8, 10]}
    exp._loaded = True
    assert exp.nudge_for("SOL", "RANGE", "LONG").value > 0


def test_the_guard_reads_the_tier_that_answered():
    # SOL has no record; longs in RANGE lost money: the coarse boost is withheld.
    n = _nudge(_s(14, 6, sym="BTC"))
    assert n.tier == "regime" and n.value == 0.0 and n.withheld_net < 0


def test_the_samples_carry_the_pnl_and_the_store_path_reads_it():
    rows = [NS(symbol="SOL", market_regime="RANGE", direction="LONG", pnl_result=p)
            for p in [0.3] * 14 + [-1.0] * 6]
    store = NS(get_decisions=lambda limit=100000: rows)
    exp = SetupExpectancy(min_samples=10).load(store)
    assert exp.nudge_for("SOL", "RANGE", "LONG").withheld_net is not None


def test_the_out_of_sample_test_still_reads_five_field_samples():
    # validate_oos kept only four-field samples, so carrying the P&L would
    # have emptied the readiness card's test in silence.
    samples = _s(30, 10, win_pnl=1.0)
    out = validate_oos(samples)
    assert out["n_train"] + out["n_test"] == len(samples)


def test_the_readiness_verdict_sees_the_trades():
    from bot.learning.readiness import _setup_expectancy_verdict
    pool = [NS(symbol="SOL", market_regime="RANGE", direction="LONG", pnl_result=p)
            for p in ([1.0, -1.0, 1.0] * 20)]
    _, sentence = _setup_expectancy_verdict(pool, 10)
    assert "on 18 unseen trade(s)" in sentence


def test_the_analyzer_audits_a_withheld_boost():
    # The nudge is applied inside a 600-line analysis method behind the
    # scanner, the LLM and the confluence engine; that a withheld boost is
    # audited, on its own branch, is a shape, stated as one.
    tree = ast.parse((ROOT / "bot" / "core" / "analyzer.py").read_text())
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.If)
            and "withheld_net" in ast.unparse(n.test)]
    assert len(hits) == 1
    # The condition itself, not a name in it: the first draft of this check
    # found the branch by name, and `False and _n.withheld_net is not None`
    # survived it with the audit dead.
    assert ast.unparse(hits[0].test) == "_n.withheld_net is not None"
    assert "result='WITHHELD'" in ast.unparse(hits[0])
