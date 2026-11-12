"""Funding in the limit scanner's forward tests: a price that was read, or "unpriced".

Both forward tests refuse a pass while funding is unpriced, and the shadow
book's trades recorded no exit time, so funding could never be priced. Now a
trade records when it left (and when each part of it left), and
`price_funding` charges it the prints it held through. A missing print
leaves the trade unpriced: absent is never a measured zero. A window the
prints cover with no print inside it is a measured zero.
"""
from __future__ import annotations

import json

import pytest

from bot.core import limit_shadow as ls
from bot.core.limit_scanner import LONG, SHORT, ScannerParams

H = 3_600_000
F = 1_700_000_000_000 - (1_700_000_000_000 % (8 * H))   # a fill bar on a funding boundary


def _trade(side=LONG, fill=F, exit_=F + 5 * H, exits=None, net=10.0, symbol="ETH"):
    return {"symbol": symbol, "side": side, "fill_ms": fill, "decision_ms": fill - H,
            "exit_ms": exit_, "exits": exits if exits is not None else [[exit_, 1.0]],
            "gross_bp": net + 4.0, "fee_bp": 4.0, "net_bp": net, "funding": "unpriced"}


def _prints(*times, rate=0.0001):
    return {"ETH": [(t, rate) for t in times]}


# A four-hour funding interval around the default trade (fill F, exit F + 5 h):
# one print inside its window, at F + 4 h.
GRID = _prints(F - 4 * H, F, F + 4 * H, F + 8 * H)


def test_a_long_pays_the_print_it_held_through_and_a_short_receives_it():
    prints = GRID
    (long_,) = ls.price_funding([_trade(LONG)], prints)
    assert long_["funding"] == "priced"
    assert long_["funding_bp"] == pytest.approx(1.0)
    assert long_["net_bp"] == pytest.approx(9.0)
    (short,) = ls.price_funding([_trade(SHORT)], prints)
    assert short["funding_bp"] == pytest.approx(-1.0)
    assert short["net_bp"] == pytest.approx(11.0)


def test_only_the_prints_inside_the_holding_window_are_charged():
    # The fill bar's own open is before the fill; the exit bar's open is
    # still held; the print after the exit bar is not.
    prints = _prints(F, F + 3 * H, F + 4 * H, F + 8 * H)
    (t,) = ls.price_funding([_trade(exit_=F + 3 * H)], prints)
    assert t["funding_bp"] == pytest.approx(1.0)


def test_a_share_that_already_left_does_not_pay():
    exits = [[F + H, 0.5], [F + 5 * H, 0.5]]
    (held,) = ls.price_funding([_trade(exits=exits)], _prints(F, F + H, F + 8 * H))
    assert held["funding_bp"] == pytest.approx(1.0), "at its own exit bar's open the half was held"
    (left,) = ls.price_funding([_trade(exits=exits)], _prints(F, F + 2 * H, F + 8 * H))
    assert left["funding_bp"] == pytest.approx(0.5)


def test_a_covered_window_with_no_print_in_it_is_a_measured_zero():
    (t,) = ls.price_funding([_trade(fill=F + H, exit_=F + 3 * H)], _prints(F, F + 8 * H))
    assert t["funding"] == "priced" and t["funding_bp"] == 0.0 and t["net_bp"] == 10.0


@pytest.mark.parametrize("prints", [
    {},                                                        # no prints read for the symbol
    _prints(F - 8 * H, F + 16 * H),                            # a 24 h gap: a print was not read
    _prints(F - 8 * H, F + H),                                 # nothing at or after the exit bar
    _prints(F + H, F + 8 * H),                                 # nothing at or before the fill bar
])
def test_a_missing_print_leaves_the_trade_unpriced_never_zero(prints):
    (t,) = ls.price_funding([_trade()], prints)
    assert t["funding"] == "unpriced"
    assert "funding_bp" not in t
    assert t["net_bp"] == 10.0


def test_a_trade_with_no_exit_time_is_unpriced():
    old = _trade()
    del old["exit_ms"], old["exits"]
    (t,) = ls.price_funding([old], GRID)
    assert t["funding"] == "unpriced"
    (same_with_exit,) = ls.price_funding([_trade()], GRID)
    assert same_with_exit["funding"] == "priced"


def test_priced_means_every_trade_and_at_least_one():
    a, b = ls.price_funding([_trade(), _trade(symbol="SOL")], GRID)
    assert (a["funding"], b["funding"]) == ("priced", "unpriced")
    assert ls.all_funding_priced([a]) is True
    assert ls.all_funding_priced([a, b]) is False
    assert ls.all_funding_priced([]) is False


def test_the_forward_verdict_reads_funding_from_the_trades():
    after = ls.RECLAIM_FORWARD_CUTOFF_MS + 8 * H
    kw = dict(cutoff_ms=ls.RECLAIM_FORWARD_CUTOFF_MS, cutoff=ls.RECLAIM_FORWARD_CUTOFF,
              n_trials=ls.N_RECLAIM_FORWARD)
    covered = {"ETH": [(after, 0.0001), (after + 4 * H, 0.0001), (after + 8 * H, 0.0001)]}
    priced = ls.forward_verdict([_trade(fill=after, exit_=after + 5 * H)], covered, **kw)
    assert priced["funding"] == "priced"
    assert priced["mean_net_bp"] == pytest.approx(9.0)
    assert "fewer than" in priced["reason"]
    unpriced = ls.forward_verdict([_trade(fill=after, exit_=after + 5 * H)], {}, **kw)
    assert unpriced["funding"] == "unpriced" and unpriced["reason"] == "funding is unpriced"
    # Trades on or before the cutoff are not this test's: they are left out, not refused.
    before = ls.forward_verdict([_trade(fill=ls.RECLAIM_FORWARD_CUTOFF_MS)], covered, **kw)
    assert before["n"] == 0


def test_the_shadow_book_records_when_each_part_of_a_position_left():
    params = ScannerParams()
    spec = ls.REALISTIC
    order = {"symbol": "ETH", "side": LONG, "px": 100.0, "stop": 99.0, "take_profit": 105.0,
             "atr": 1.0, "points": 80.0, "placed_ts": F / 1000.0, "placed_ms": F,
             "placed_close": 100.6, "class": "crypto", "bars_waited": 0}
    positions, meta, trades = {}, {}, []
    fill_bar = (F + H, 100.5, 100.8, 99.8, 100.4, 1.0)        # 20 bp through: a sure fill
    stop_bar = (F + 3 * H, 100.2, 100.3, 98.0, 98.4, 1.0)
    ls._mark_to_market([order], positions, meta, {"ETH": fill_bar}, params, spec, ls._Cfg(spec), trades)
    assert trades == [] and "ETH" in positions
    ls._mark_to_market([], positions, meta, {"ETH": stop_bar}, params, spec, ls._Cfg(spec), trades)
    (trade,) = trades
    assert trade["fill_ms"] == F + H and trade["exit_ms"] == F + 3 * H
    assert sum(share for _, share in trade["exits"]) == pytest.approx(1.0)
    assert all(ms == F + 3 * H for ms, _ in trade["exits"])


def _forward_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "limit_scanner_forward.py"
    spec = importlib.util.spec_from_file_location("limit_scanner_forward", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_forward_script_scores_each_test_on_its_own_terms():
    mod = _forward_script()
    bars = {"ETH/USDT:USDT": [(F + i * H, 100.0, 101.0, 99.0, 100.0, 1.0) for i in range(6)]}
    reclaim = mod.score(bars, {}, "reclaim", ScannerParams())
    assert [r["variant"] for r in reclaim["variants"]] == ["V1", "V3"]
    for row in reclaim["variants"]:
        assert row["verdict"]["cutoff"] == ls.RECLAIM_FORWARD_CUTOFF
        assert row["verdict"]["n_trials"] == 2
        assert "verdict_taker_exits" in row and row["survives"] is False
    original = mod.score(bars, {}, "original", ScannerParams())
    assert [r["variant"] for r in original["variants"]] == ["V0", "V2"]
    assert original["n_trials"] == 4 and "pre-fix" in original["note"]
    assert all("verdict_taker_exits" not in r for r in original["variants"])


def test_funding_files_are_read_per_dataset_symbol_and_a_missing_one_is_absent(tmp_path):
    bars = tmp_path / "bars"
    bars.mkdir()
    (bars / "manifest.json").write_text(json.dumps({"symbols": {
        "ETH/USDT:USDT": {"file": "ETH_USDT_USDT.csv.gz"},
        "SOL/USDT:USDT": {"file": "SOL_USDT_USDT.csv.gz"}}}))
    funding = tmp_path / "funding"
    funding.mkdir()
    (funding / "ETH_USDT_USDT.json").write_text(json.dumps(
        [{"ms": F, "rate": 0.0001}, {"ms": F + 8 * H, "rate": "-0.0002"}, {"ms": "bad"}]))
    prints = ls.load_funding(funding, bars)
    assert prints == {"ETH/USDT:USDT": [(F, 0.0001), (F + 8 * H, -0.0002)]}
