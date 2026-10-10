"""The Limit Entry Scanner's rules, driven: the leader's gate, the score, the
skips, and the one limit a pass places.

Ported from the playbook (`playbooks/runeclaw-limit-scanner`, v0.1.0) onto
closed 1h bars, with one change of substance: an unread dimension adds
nothing. The playbook gave it mid-scale points (12.5 momentum, 10 VWAP, 8
order book, 7.5 volume), so a symbol whose book, ranking and VWAP could not
be read started at 38 of the 70 it needed, on points nobody measured.
"""
from __future__ import annotations

import pytest

from bot.core import limit_scanner as ls

P = ls.ScannerParams()


def _bars(closes, *, spread=0.004, volume=1_000_000.0, start=1_700_000_000_000):
    """1h rows [ts, o, h, l, c, v] closing at ``closes``."""
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) * (1 + spread), min(prev, c) * (1 - spread)
        rows.append([start + i * 3_600_000, prev, hi, lo, c, volume])
        prev = c
    return rows


def _snap(symbol="ETH", **kw):
    base = dict(last=100.0, vwap=99.0, high=101.0, low=95.0, change_pct=4.0,
                quote_volume=50e6, atr=1.0, bid_volume=300.0, ask_volume=100.0)
    base.update(kw)
    return ls.Snapshot(symbol=symbol, **base)


LEADER_UP = _snap("BTC", last=101.0, vwap=100.0, change_pct=1.0)
LEADER_DOWN = _snap("BTC", last=99.0, vwap=100.0, change_pct=-1.0)


# ── the day, from closed bars ───────────────────────────────────────────────

def test_a_day_is_read_from_twenty_five_closed_bars():
    rows = _bars([100.0 + i * 0.5 for i in range(60)])
    s = ls.snapshot_from_bars("ETH/USDT:USDT", rows, P)
    day = rows[-24:]
    assert s.last == rows[-1][4]
    assert s.high == max(r[2] for r in day) and s.low == min(r[3] for r in day)
    assert s.change_pct == pytest.approx((rows[-1][4] / rows[-25][4] - 1) * 100)
    assert s.quote_volume == pytest.approx(sum(r[4] * r[5] for r in day))
    assert s.vwap is not None and s.atr is not None and s.atr > 0
    # The book, funding and taker flow are read live; the bars carry none.
    assert (s.bid_volume, s.ask_volume, s.funding_bps, s.taker_ratio) == (None,) * 4


def test_a_short_or_broken_day_is_unread_not_zero():
    short = ls.snapshot_from_bars("ETH", _bars([100.0] * 24), P)
    assert short.change_pct is None, "24 bars have no close a day before the last"
    assert short.high is not None
    rows = _bars([100.0 + i for i in range(60)])
    rows[-3][5] = None                      # a volume nobody stated
    rows[-5][2] = None                      # a high nobody stated
    s = ls.snapshot_from_bars("ETH", rows, P)
    assert s.quote_volume is None and s.high is None and s.atr is None
    assert s.change_pct is not None, "the closes still read"
    flat = _bars([100.0] * 60, volume=0.0)
    assert ls.snapshot_from_bars("ETH", flat, P).quote_volume == 0.0, \
        "a bar that traded nothing is a reading"


# ── the leader's gate ───────────────────────────────────────────────────────

def _side(regime):
    return regime.side, regime.size_factor


def test_the_leader_names_the_side_and_its_size():
    assert _side(ls.leader_regime(LEADER_UP, P)) == (ls.LONG, 1.0)
    assert _side(ls.leader_regime(LEADER_DOWN, P)) == (ls.SHORT, 1.0)
    # One vote is half size.
    up_below = _snap("BTC", last=99.0, vwap=100.0, change_pct=1.0)
    assert _side(ls.leader_regime(up_below, P)) == (ls.LONG, 0.5)
    # Taker flow, read, is the third vote; unread, it votes for neither.
    assert _side(ls.leader_regime(
        ls.Snapshot(**{**up_below.__dict__, "taker_ratio": 1.3}), P)) == (ls.LONG, 1.0)
    assert ls.leader_regime(
        ls.Snapshot(**{**up_below.__dict__, "taker_ratio": 0.7}), P).side == ls.SHORT
    no_short = ls.ScannerParams(allow_short=False)
    assert ls.leader_regime(LEADER_DOWN, no_short).side is None


def test_a_flat_leader_and_an_unread_leader_are_different_sentences():
    flat = ls.leader_regime(_snap("BTC", last=100.0, vwap=100.0, change_pct=0.0), P)
    unread = ls.leader_regime(_snap("BTC", change_pct=None), P)
    assert flat.side is None and unread.side is None
    assert flat.read is True and unread.read is False
    assert flat.reason != unread.reason


# ── the score ───────────────────────────────────────────────────────────────

def test_a_fully_read_leader_of_the_board_scores_every_dimension():
    best = _snap("ETH", change_pct=6.0, quote_volume=90e6)
    weak = _snap("ADA", change_pct=-2.0, last=98.0, vwap=99.0, quote_volume=20e6,
                 bid_volume=100.0, ask_volume=300.0)
    board = ls.score_universe([weak, best], LEADER_UP, ls.LONG, P)
    assert [r.symbol for r in board] == ["ETH", "ADA"]
    top = board[0]
    assert None not in top.dims.values()
    assert top.dims == {"momentum": 25.0, "vwap": 20.0, "range": 20.0,
                        "orderbook": 20.0, "volume": 15.0}
    assert top.points == 100.0
    # The short side mirrors every dimension.
    short = {r.symbol: r for r in ls.score_universe([weak, best], LEADER_DOWN, ls.SHORT, P)}
    assert short["ADA"].dims["momentum"] == 25.0 and short["ETH"].dims["momentum"] == 0.0
    assert short["ADA"].dims["vwap"] == 20.0 and short["ETH"].dims["vwap"] == 0.0


def test_an_unread_dimension_adds_nothing_and_is_named():
    """The playbook's mid-scale points: 12.5 + 10 + 8 + 7.5 = 38 unmeasured."""
    blind = _snap("ETH", vwap=None, bid_volume=None, ask_volume=None,
                  change_pct=None, quote_volume=None)
    other = _snap("ADA", vwap=None, bid_volume=None, ask_volume=None,
                  change_pct=None, quote_volume=None)
    (row, _), = [ls.score_universe([blind, other], LEADER_UP, ls.LONG, P)[:2]]
    assert {k for k, v in row.dims.items() if v is None} == {
        "momentum", "vwap", "orderbook", "volume"}
    assert row.points == row.dims["range"] == 20.0
    # The bar is not lowered for what could not be read.
    assert ls.scan_pass([LEADER_UP, blind, other], P).plan is None


def test_a_ranking_with_nothing_to_rank_is_unread():
    alone = ls.score_universe([_snap("ETH")], LEADER_UP, ls.LONG, P)[0]
    assert alone.dims["momentum"] is None and alone.dims["volume"] is None
    same = ls.score_universe([_snap("ETH"), _snap("ADA")], LEADER_UP, ls.LONG, P)
    assert all(r.dims["momentum"] is None for r in same), "equal readings rank nothing"


@pytest.mark.parametrize("side,kw,why", [
    (ls.LONG, {"last": 106.0, "vwap": 100.0}, "extended beyond the VWAP cap"),
    (ls.SHORT, {"last": 94.0, "vwap": 100.0}, "extended beyond the VWAP cap"),
    (ls.LONG, {"quote_volume": 5e6}, "quote volume below the floor"),
    (ls.LONG, {"bid_volume": 10.0, "ask_volume": 100.0}, "an opposing order-book wall"),
    (ls.SHORT, {"bid_volume": 100.0, "ask_volume": 10.0}, "an opposing order-book wall"),
    (ls.LONG, {"funding_bps": 30.0}, "funding at or beyond the skip threshold"),
    (ls.SHORT, {"funding_bps": -30.0}, "funding at or beyond the skip threshold"),
])
def test_each_skip_holds_a_symbol_off_the_side_it_names(side, kw, why):
    row = ls.score_universe([_snap("ETH", **kw), _snap("ADA", change_pct=1.0)],
                            LEADER_UP, side, P)
    eth = next(r for r in row if r.symbol == "ETH")
    assert eth.skip == why


@pytest.mark.parametrize("side,kw", [
    (ls.LONG, {"last": 104.9, "vwap": 100.0}),        # inside the 5% cap
    (ls.SHORT, {"last": 106.0, "vwap": 100.0}),       # extended the other way
    (ls.LONG, {"quote_volume": 10e6}),                 # at the floor
    (ls.LONG, {"funding_bps": -45.0}),                 # a long is paid this
    (ls.SHORT, {"funding_bps": 45.0}),                 # a short is paid this
    (ls.LONG, {"funding_bps": 29.9}),
])
def test_the_other_side_of_each_skip_is_not_skipped(side, kw):
    row = ls.score_universe([_snap("ETH", **kw), _snap("ADA", change_pct=1.0)],
                            LEADER_UP, side, P)
    assert next(r for r in row if r.symbol == "ETH").skip == ""


# ── the one limit ───────────────────────────────────────────────────────────

def test_the_long_limit_rests_below_vwap_with_its_stop_at_the_day_low():
    s = _snap("ADA", last=100.0, vwap=99.0, atr=1.0, low=90.0)
    plan, why = ls.plan_pullback(s, ls.LONG, 1.0, P)
    assert why == "" and plan.limit_price == pytest.approx(98.5)
    assert plan.stop == pytest.approx(90.0), "the structure is wider than the floor"
    assert plan.take_profit == pytest.approx(98.5 * 1.05)
    short = ls.plan_pullback(_snap("ADA", last=100.0, vwap=101.0, atr=1.0, high=110.0),
                             ls.SHORT, 1.0, P)[0]
    assert short.limit_price == pytest.approx(101.5) and short.stop == pytest.approx(110.0)
    assert short.take_profit == pytest.approx(101.5 * 0.95)


@pytest.mark.parametrize("symbol,floor", [("BTC", 0.015), ("ETH/USDT:USDT", 0.015),
                                          ("SOLUSDT", 0.012), ("BNB", 0.012),
                                          ("PEPE", 0.025)])
def test_a_stop_inside_the_tier_floor_is_widened_to_it(symbol, floor):
    s = _snap(symbol, last=100.0, vwap=99.0, atr=1.0, low=98.4)
    plan = ls.plan_pullback(s, ls.LONG, 1.0, P)[0]
    assert plan.stop_frac == pytest.approx(floor)
    assert plan.stop == pytest.approx(98.5 * (1 - floor))


def test_a_limit_is_never_placed_through_the_market_or_past_the_chase_cap():
    below = ls.plan_pullback(_snap("ADA", last=97.0, vwap=99.0, atr=1.0, low=90.0),
                             ls.LONG, 1.0, P)[0]
    assert below.limit_price == 97.0, "a buy limit above the market is a market buy"
    far = ls.plan_pullback(_snap("ADA", last=110.0, vwap=105.0, atr=2.0, low=90.0),
                           ls.LONG, 1.0, P)
    assert far[0] is None and "chase cap" in far[1]
    near = ls.plan_pullback(_snap("ADA", last=106.0, vwap=105.0, atr=2.0, low=90.0),
                            ls.LONG, 1.0, P)
    assert near[0] is not None


def test_a_pass_places_the_best_qualified_symbol_once():
    best = _snap("ETH", change_pct=6.0, quote_volume=90e6)
    second = _snap("SOL", change_pct=5.0, quote_volume=80e6)
    weak = _snap("ADA", change_pct=-2.0, quote_volume=20e6)
    p = ls.scan_pass([LEADER_UP, weak, second, best], P)
    assert p.plan.symbol == "ETH" and p.plan.side == ls.LONG and p.plan.points >= 70
    assert all(r.symbol != "BTC" for r in p.board), "the leader is never traded"
    # A symbol already held is scored, and the next qualified one is placed.
    p2 = ls.scan_pass([LEADER_UP, weak, second, best], P, held=frozenset({"ETH"}))
    assert p2.plan.symbol == "SOL"
    # A half-size regime carries its size to the plan.
    half = _snap("BTC", last=99.0, vwap=100.0, change_pct=1.0)
    assert ls.scan_pass([half, weak, second, best], P).plan.size_factor == 0.5
    # No leader, no order; a mixed leader, no order.
    assert ls.scan_pass([weak, second, best], P).plan is None
    flat = _snap("BTC", last=100.0, vwap=100.0, change_pct=0.0)
    assert ls.scan_pass([flat, weak, second, best], P).plan is None


def test_the_preset_numbers_are_read_or_refused():
    cfg = {"scanner": "limit_entry", "min_score": 75, "max_vwap_ext_pct": 2.5,
           "stop_floor_pct": {"BTCUSDT": 2.0}, "allow_short": False, "leader": "XAUUSDT"}
    p = ls.params_from_preset(cfg)
    assert (p.min_score, p.max_vwap_ext_pct, p.allow_short, p.leader) == (75, 2.5, False, "XAU")
    assert p.stop_floor("BTC") == 0.02 and p.stop_floor("ETH") == 0.025
    assert ls.params_from_preset({"scanner": "limit_entry"}) == ls.ScannerParams()
    for bad in ({"min_score": "70"}, {"min_score": 0}, {"min_score": 101},
                {"atr_period": 1.5}, {"allow_short": "yes"}, {"stop_floor_pct": {"BTC": -1}},
                {"leader": ""}):
        assert ls.params_from_preset({"scanner": "limit_entry", **bad}) is None, bad
    assert ls.params_from_preset({"min_score": 70}) is None, "not this scanner"


def test_the_minimum_score_is_the_bar_both_ways():
    """A fully priceable symbol at 80 (no book read) is placed under 70 and
    held off under 85: the bar, not the pricing, decides."""
    best = _snap("ETH", change_pct=6.0, quote_volume=90e6, bid_volume=None, ask_volume=None)
    weak = _snap("ADA", change_pct=-2.0, quote_volume=20e6)
    placed = ls.scan_pass([LEADER_UP, weak, best], P)
    assert placed.plan is not None and placed.plan.points == 80.0
    held_off = ls.scan_pass([LEADER_UP, weak, best], ls.ScannerParams(min_score=85))
    assert held_off.plan is None
    assert held_off.reason == "no symbol at or above the minimum score"


# ── classes ─────────────────────────────────────────────────────────────────

CRYPTO = {"name": "crypto", "leader": "BTCUSDT", "members": ["ETHUSDT", "ADAUSDT"],
          "min_quote_volume": 10_000_000}
METALS = {"name": "metals", "leader": "XAUUSDT", "members": ["XAGUSDT", "XPTUSDT"],
          "min_quote_volume": 250_000}
CP = ls.params_from_preset({"scanner": "limit_entry", "classes": [CRYPTO, METALS]})


@pytest.mark.parametrize("bad", [
    [dict(CRYPTO, members=["BTCUSDT", "ETHUSDT"])],                # trades its own leader
    [CRYPTO, dict(METALS, members=["ETHUSDT"])],                    # a member in two classes
    [CRYPTO, dict(METALS, name="crypto")],                          # two classes, one name
    [dict(CRYPTO, min_quote_volume=0)], [dict(CRYPTO, members=[])], [], "crypto",
])
def test_classes_that_do_not_read_refuse_the_preset(bad):
    assert ls.params_from_preset({"scanner": "limit_entry", "classes": bad}) is None
    assert ls.params_from_preset({"scanner": "limit_entry", "classes": [CRYPTO],
                                  "leader": "BTCUSDT"}) is None, "two answers to one question"


def test_each_class_follows_its_own_leader_and_floor():
    eth = _snap("ETH", change_pct=6.0, quote_volume=90e6)
    ada = _snap("ADA", change_pct=-2.0, quote_volume=20e6)
    # Gold down and below its VWAP: the metals class goes short while crypto goes long.
    xau = _snap("XAU", last=99.0, vwap=100.0, change_pct=-1.0)
    xag = _snap("XAG", last=98.0, vwap=99.0, high=101.0, low=95.0, change_pct=-4.0,
                quote_volume=0.9e6, bid_volume=100.0, ask_volume=300.0)
    xpt = _snap("XPT", change_pct=2.0, quote_volume=0.4e6)
    passes = dict(ls.scan_classes([LEADER_UP, eth, ada, xau, xag, xpt], CP))
    assert passes["crypto"].regime.side == ls.LONG and passes["crypto"].plan.symbol == "ETH"
    assert passes["metals"].regime.side == ls.SHORT and passes["metals"].plan.symbol == "XAG"
    assert passes["metals"].plan.side == ls.SHORT
    # The metals floor admits a 0.9M market; the crypto floor would not.
    assert not any(r.skip for r in passes["metals"].board)
    thin = ls.score_universe([xag, xpt], xau, ls.SHORT, ls.class_params(CP, CP.classes[0]))
    assert all(r.skip == "quote volume below the floor" for r in thin)
    # A class sees only its own leader and members.
    assert {r.symbol for r in passes["crypto"].board} == {"ETH", "ADA"}


def test_without_classes_one_pass_covers_everything():
    (name, p), = ls.scan_classes([LEADER_UP, _snap("ETH", change_pct=6.0, quote_volume=90e6),
                                  _snap("ADA", change_pct=-2.0, quote_volume=20e6)], P)
    assert name == "all" and p.plan.symbol == "ETH"
