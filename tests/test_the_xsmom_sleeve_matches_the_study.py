"""The bot's copy of the xsmom + BTC regime rule is the study's rule.

`scripts/xsmom_regime_study.py` is sealed by hash and is the reference: it read
the holdout once and `REPORT.md` records what it scored. `bot/sleeve/
xsmom_regime.py` is a second copy, written as pure functions the paper runner
can call without pandas panels. Two copies of a rule are two answers unless
something holds them to one, and this file is that something: both run on the
study's own shipped data and must hand back the same target weights, and the
bot's constants must be the script's.

The script is loaded the way its own test loads it and is never modified.
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
from pathlib import Path

import pytest

from bot.sleeve import xsmom_regime as bot_rule

ROOT = Path(__file__).resolve().parents[1]
_P = ROOT / "scripts" / "xsmom_regime_study.py"
_spec = importlib.util.spec_from_file_location("xsmom_regime_study_parity", _P)
study = importlib.util.module_from_spec(_spec)
sys.modules["xsmom_regime_study_parity"] = study
_spec.loader.exec_module(study)


@pytest.fixture(scope="module")
def panel():
    op, cl = study.load_panel(study.DATA)
    cols = list(cl.columns)
    rows = {c: [None if math.isnan(v) else float(v) for v in cl[c].to_numpy()] for c in cols}
    return op, cl, cols, rows


def _bot_decision(rows, cols, t):
    return bot_rule.decide({c: rows[c][: t + 1] for c in cols})


def test_the_data_read_is_the_data_the_study_sealed():
    """A parity over files that drifted from the sealed set would be a parity
    with a study nobody ran."""
    sealed = {}
    for line in (study.DIR / "data.sha256").read_text().splitlines():
        digest, name = line.split()
        sealed[name] = digest
    on_disk = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(study.DATA.glob("*.csv"))}
    assert on_disk == sealed


def test_every_constant_is_the_scripts():
    assert bot_rule.UNIVERSE == tuple(study.UNIVERSE)
    assert bot_rule.ANCHOR == study.ANCHOR
    assert bot_rule.FEE == study.FEE
    assert bot_rule.MIN_HISTORY == study.MIN_HISTORY
    assert bot_rule.TARGET_VOL == study.TARGET_VOL
    assert bot_rule.VOL_WIN == study.VOL_WIN
    assert bot_rule.PORT_VOL_WIN == study.PORT_VOL_WIN
    p, s = bot_rule.PRIMARY, study.PRIMARY
    assert (p.lookback, p.rebalance, p.regime_ma, p.fee) == (
        s.lookback, s.rebalance, s.regime_ma, s.fee)
    # The primary is spot: the script's funding charge is a sensitivity row.
    assert s.funding_per_day == 0.0


def test_target_weights_are_identical_on_every_scheduled_rebalance(panel):
    """Every calendar rebalance close in the shipped data, about 240 of them:
    the same coins, and the same weight to the bit.

    Both arms are in the sample: closes where the rule holds a book and
    closes where it is cash (the 2018 and 2022 bear markets, the gate off).
    """
    _op, cl, cols, rows = panel
    held = cash = 0
    for t in range(len(cl)):
        if not bot_rule.is_rebalance_day(cl.index[t].date()):
            continue
        ref = study.decide(cl, t, study.PRIMARY)
        got = _bot_decision(rows, cols, t)
        assert set(got.weights) == set(cols)
        for c in cols:
            assert got.weights[c] == float(ref[c]), (cl.index[t].date(), c)
        if float(ref.sum()) > 0:
            held += 1
        else:
            cash += 1
    assert held > 50 and cash > 50, (held, cash)


def test_the_regime_reading_is_the_gate_the_backtest_flips_on(panel):
    """The script has no regime function: its gate is a line inside
    `backtest`. Driven through `hold_btc_only=True`, the backtest holds BTC
    exactly while that gate is on, so its exposure series is the gate."""
    op, cl, _cols, rows = panel
    bt = study.backtest(op, cl, study.PRIMARY, hold_btc_only=True)
    btc = rows["BTC"]
    checked = 0
    for s in range(1, len(cl) - 1):
        t = s - 1
        if not bot_rule.is_rebalance_day(cl.index[t].date()):
            continue
        reg = bot_rule.regime_at(btc[: t + 1])
        exposure = float(bt["exposure"].iloc[s - 1])
        if reg is None:
            assert exposure == 0.0
            continue
        assert (exposure > 0) == reg.on, cl.index[t].date()
        checked += 1
    assert checked > 200


def test_every_trade_the_study_made_is_on_a_day_the_bot_calls_due(panel):
    """The schedule: a 14-day calendar from 2017-08-17 and every regime flip.
    Each row where the study's backtest paid a cost is a decision on a close
    the bot's rule also calls due, and flips are among them."""
    op, cl, _cols, rows = panel
    bt = study.backtest(op, cl, study.PRIMARY)
    btc = rows["BTC"]
    flips_seen = 0
    for s in range(1, len(cl) - 1):
        if float(bt["cost"].iloc[s - 1]) <= 0:
            continue
        t = s - 1
        day = cl.index[t].date()
        now = bot_rule.regime_at(btc[: t + 1])
        prev = bot_rule.regime_at(btc[:t])
        flip = now is not None and prev is not None and now.on != prev.on
        assert bot_rule.is_rebalance_day(day) or flip, day
        flips_seen += int(flip and not bot_rule.is_rebalance_day(day))
    assert flips_seen > 5

