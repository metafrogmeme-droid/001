"""The journal's R was P&L over the PRICE distance, negated for shorts.

`r_multiple_for` divided by `|entry - stop|` — the risk per UNIT — with no
quantity, so it equalled R only for a trade of exactly one coin: a 0.1 ETH
trade risking $10 that made +$11.40 read +0.11R; a 5,000 DOGE trade over-read
by 5,000x. And the divisor was negated for a SHORT, so a losing short printed
a POSITIVE R and a winning short a negative one — on the weekly review's Avg
R, Best and Worst, in `_generate_lessons` ("Full stop hit" on r < -0.8) and
`_generate_tags` ("runner" on r >= 3.0). The post-mortem leaf computed its own
R over dollar risk and declined to print the journal's, which is how this was
found; it is one arithmetic now.

R is net P&L over `|entry - stop| * quantity`, with the sign of the P&L. An
entry with no quantity on record has no R — and a legacy entry's STORED R is
not loaded, because it is known-wrong, not unknown-but-plausible.
"""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from bot.core import engine as engine_mod
from bot.core import trade_postmortem as pm
from bot.core.trade_journal import TradeJournal, r_multiple_for, r_unknown_reason
from bot.skills.engine_ops_commands import _r_tag
from tests.source_scan import code_only

# ── the arithmetic ──────────────────────────────────────────────────────────

def test_r_scales_with_quantity_because_risk_is_in_dollars():
    # 0.1 ETH, entry 2000, stop 1900: $10 at risk. +$11.40 is +1.14R, not +0.114R.
    assert r_multiple_for(2000.0, 1900.0, 11.40, 0.1) == pytest.approx(1.14)
    assert r_multiple_for(100.0, 95.0, 20.0, 2.0) == pytest.approx(2.0)
    # the old per-unit reading, for the record
    assert r_multiple_for(2000.0, 1900.0, 11.40, 0.1) != pytest.approx(0.114)


def test_the_sign_is_the_pnls_for_either_direction():
    assert r_multiple_for(100.0, 105.0, -10.0, 1.0) == pytest.approx(-2.0), "a losing short is negative R"
    assert r_multiple_for(100.0, 105.0, 10.0, 1.0) == pytest.approx(2.0), "a winning short is positive R"
    assert r_multiple_for(100.0, 95.0, -5.0, 1.0) == pytest.approx(-1.0)


def test_no_quantity_is_no_r_not_zero_r():
    for qty in (None, 0, 0.0, -1, "x"):
        out = r_multiple_for(100.0, 95.0, 10.0, qty)
        assert out is None, qty
        assert out is not 0.0  # noqa: F632 - identity is the point


def test_the_post_mortem_and_the_journal_are_one_arithmetic():
    for args in ((100.0, 95.0, 2.0, 20.0), (100.0, 105.0, 1.0, -10.0), (2000.0, 1900.0, 0.1, 11.4)):
        entry, sl, qty, pnl = args
        assert pm.realized_r(entry, sl, qty, pnl) == r_multiple_for(entry, sl, pnl, qty)
    assert pm.realized_r(None, 95.0, 1.0, 10.0) is None
    assert pm.realized_r(100.0, 95.0, None, 10.0) is None
    src = code_only(inspect.getsource(pm.realized_r))
    assert "r_multiple_for(" in src and "abs(" not in src, \
        "the post-mortem grew its own copy of the arithmetic again"


# ── through the journal ─────────────────────────────────────────────────────

def _rec(j, tid, **kw):
    base = dict(trade_id=tid, symbol="ETH/USDT", direction="LONG", strategy_type="swing",
                entry_price=2000.0, exit_price=2114.0, stop_loss=1900.0, take_profit=2300.0,
                pnl=11.40, holding_hours=2.0)
    base.update(kw)
    return j.record_trade(**base)


def test_record_trade_scores_r_against_the_size_it_is_given(tmp_path):
    j = TradeJournal(journal_file=str(tmp_path / "j.json"))
    e = _rec(j, "T1", quantity=0.1)
    assert e.r_multiple == pytest.approx(1.14)
    assert e.quantity == 0.1
    assert r_unknown_reason(e) is None


def test_no_size_on_record_is_no_r_and_says_which_half_is_missing(tmp_path):
    j = TradeJournal(journal_file=str(tmp_path / "j.json"))
    no_qty = _rec(j, "T2")
    assert no_qty.r_multiple is None
    assert r_unknown_reason(no_qty) == "no_quantity"
    no_stop = _rec(j, "T3", stop_loss=0.0, quantity=0.1)
    assert no_stop.r_multiple is None
    assert r_unknown_reason(no_stop) == "no_stop", "a missing stop outranks a missing size"


def test_a_short_that_lost_is_journaled_as_a_negative_r(tmp_path):
    j = TradeJournal(journal_file=str(tmp_path / "j.json"))
    e = _rec(j, "S1", direction="SHORT", entry_price=100.0, stop_loss=105.0, exit_price=110.0,
             pnl=-10.0, quantity=1.0)
    assert e.r_multiple == pytest.approx(-2.0)
    assert "full_stop" in e.tags or e.r_multiple < 0  # the tags see the true sign


def test_a_legacy_entry_loads_with_no_r_because_its_stored_one_is_known_wrong(tmp_path):
    f = tmp_path / "j.json"
    row = {"trade_id": "L1", "symbol": "DOGE/USDT", "direction": "SHORT", "strategy_type": "swing",
           "entry": 0.10, "exit": 0.11, "sl": 0.105, "tp": 0.09, "pnl": -50.0, "pnl_pct": -10.0,
           "r_mult": 10000.0,   # pnl / price distance, negated: the shape this file is about
           "hold_hrs": 3.0, "ts": 1_700_000_000, "venue": "bitget", "uid": "7"}
    sized = dict(row, trade_id="L2", r_mult=-2.0, qty=5000.0)
    f.write_text(json.dumps([row, sized]))
    j = TradeJournal(journal_file=str(f))
    by = {e.trade_id: e for e in j._entries}
    assert by["L1"].r_multiple is None and by["L1"].quantity is None
    assert r_unknown_reason(by["L1"]) == "no_quantity"
    assert by["L2"].r_multiple == pytest.approx(-2.0) and by["L2"].quantity == 5000.0
    # and the review counts the legacy one as unscored rather than averaging it in
    rev = j.get_weekly_review(lookback_days=10_000)
    assert rev["r_scored"] == 1 and rev["r_unscored"] == 1
    assert rev["avg_r_multiple"] == pytest.approx(-2.0)


def test_the_review_says_why_its_best_and_worst_carry_no_r(tmp_path):
    j = TradeJournal(journal_file=str(tmp_path / "j.json"))
    _rec(j, "A", pnl=40.0)                              # no size
    _rec(j, "B", pnl=-5.0, stop_loss=0.0, quantity=1.0)  # no stop
    rev = j.get_weekly_review()
    assert rev["best_trade"]["r"] is None and rev["best_trade"]["r_reason"] == "no_quantity"
    assert rev["worst_trade"]["r"] is None and rev["worst_trade"]["r_reason"] == "no_stop"
    assert "size not on record" in _r_tag(None, "no_quantity")
    assert "no stop on record" in _r_tag(None, "no_stop")
    assert "no stop on record" in _r_tag(None), "the one-argument form keeps its old sentence"
    assert _r_tag(1.14) == "1.1R"


# ── the engine hands the size over, three-valued ───────────────────────────

def test_the_engine_reads_the_size_off_either_book_and_never_as_zero():
    q = engine_mod._journal_quantity
    assert q(SimpleNamespace(quantity=0.5)) == 0.5
    assert q(SimpleNamespace(quantity="0.25")) == 0.25
    for bad in (0, 0.0, -1, None, "x"):
        assert q(SimpleNamespace(quantity=bad)) is None, bad
    assert q(SimpleNamespace()) is None
    src = code_only(inspect.getsource(engine_mod))
    assert src.count("quantity=_journal_quantity(") == 2, \
        "both journal callers (the live close and the paper close) must pass the size"
