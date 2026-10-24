"""Fair-value mechanism: blend, handoff, collateral, cadence."""
from __future__ import annotations

import random

from bot.fvb.adapters import aggregator_round, clamp_to_external, oracle_proposal
from bot.fvb.collateral import borrowed_asset_marks, collateral_marks, invariant_holds
from bot.fvb.config import FvbConfig
from bot.fvb.pricer import Pricer
from bot.fvb.replay import deep_book


def _warm(pricer: Pricer, symbol: str, price: float, session: str = "regular"):
    book = deep_book(price)
    pricer.ingest_print(symbol, ts=0.0, session=session, external=price,
                        books=[book], volume=1_000.0, venue="primary")
    pricer.ingest_print(symbol, ts=40.0, session=session, external=price,
                        books=[book], volume=1_000.0, venue="primary")
    return book


def test_cadence_defaults_are_the_hip3_intervals():
    cfg = FvbConfig()
    assert cfg.oracle_cadence_s == 3.0
    assert cfg.oracle_stale_s == 10.0
    assert cfg.borrow_power_ramp_s == 900.0
    assert cfg.hyperevm_tick_s == 1.0
    assert cfg.solana_tick_s == 0.4
    assert cfg.oracle_cadence_s != cfg.hyperevm_tick_s


def test_keeper_respects_cadence_and_staleness():
    fresh = oracle_proposal(
        mark=100.0, confirmed=True, reading_ts=100.0, now=102.0,
        cadence_s=3.0, stale_s=10.0, last_emit_ts=100.0,
    )
    assert fresh is None
    emitted = oracle_proposal(
        mark=100.0, confirmed=True, reading_ts=100.0, now=103.0,
        cadence_s=3.0, stale_s=10.0, last_emit_ts=100.0,
    )
    assert emitted is not None
    assert emitted["action"] == "setOracle"
    assert emitted["stale"] is False
    stale = oracle_proposal(
        mark=100.0, confirmed=True, reading_ts=90.0, now=103.0,
        cadence_s=3.0, stale_s=10.0, last_emit_ts=100.0,
    )
    assert stale is not None
    assert stale["action"] == "fallback"
    assert "mark" not in stale
    halted = oracle_proposal(
        mark=100.0, confirmed=False, reading_ts=100.0, now=103.0,
        cadence_s=3.0, stale_s=10.0, last_emit_ts=None,
    )
    assert halted is not None
    assert halted["action"] == "halt"
    assert halted["confirmed"] is False


def test_unconfirmed_up_move_stays_at_last_confirmed():
    marks = collateral_marks(
        last_confirmed=100.0, new_print=180.0, band_lo=97.0,
        confirmed=False, fv=101.0,
    )
    assert marks.borrow_power == 100.0
    assert marks.liquidation == 100.0
    assert marks.borrow_power < 180.0
    assert marks.liquidation < 180.0


def test_unconfirmed_down_move_tightens_borrow_and_floors_liquidation():
    marks = collateral_marks(
        last_confirmed=100.0, new_print=70.0, band_lo=96.0,
        confirmed=False, fv=99.0,
    )
    assert marks.borrow_power == 70.0
    assert marks.liquidation == 96.0
    assert marks.borrow_power <= marks.liquidation
    above = collateral_marks(
        last_confirmed=100.0, new_print=90.0, band_lo=80.0,
        confirmed=False, fv=99.0,
    )
    assert above.borrow_power == 90.0
    assert above.liquidation == 90.0


def test_forced_up_move_ramps_borrow_power_under_liquidation():
    start = collateral_marks(
        last_confirmed=100.0, new_print=180.0, band_lo=97.0, confirmed=False,
        fv=180.0, forced=True, ramp_elapsed_s=0.0, ramp_s=900.0,
    )
    mid = collateral_marks(
        last_confirmed=100.0, new_print=180.0, band_lo=97.0, confirmed=False,
        fv=180.0, forced=True, ramp_elapsed_s=450.0, ramp_s=900.0,
    )
    done = collateral_marks(
        last_confirmed=100.0, new_print=180.0, band_lo=97.0, confirmed=False,
        fv=180.0, forced=True, ramp_elapsed_s=900.0, ramp_s=900.0,
    )
    assert start.borrow_power == 100.0
    assert start.liquidation == 180.0
    assert mid.borrow_power == 140.0
    assert mid.liquidation == 180.0
    assert done.borrow_power == 180.0
    assert done.liquidation == 180.0
    assert start.borrow_power <= start.liquidation
    assert mid.borrow_power <= mid.liquidation


def test_borrowed_asset_mirror():
    up = borrowed_asset_marks(
        last_confirmed=100.0, new_print=180.0, band_hi=150.0, confirmed=False, fv=140.0,
    )
    assert up.borrow_check == 180.0
    assert up.liquidation == 150.0
    assert up.liquidation <= up.borrow_check
    down = borrowed_asset_marks(
        last_confirmed=100.0, new_print=70.0, band_hi=110.0, confirmed=False, fv=90.0,
    )
    assert down.borrow_check == 100.0
    assert down.liquidation == 100.0


def test_missing_inputs_are_omitted():
    marks = collateral_marks(
        last_confirmed=None, new_print=180.0, band_lo=97.0, confirmed=False, fv=None,
    )
    assert marks.borrow_power is None
    assert marks.liquidation is None
    down = collateral_marks(
        last_confirmed=100.0, new_print=70.0, band_lo=None, confirmed=False, fv=99.0,
    )
    assert down.borrow_power is None
    assert down.liquidation is None
    assert aggregator_round(None) is None
    assert aggregator_round(0.0) is None
    assert clamp_to_external(None, 100.0, 99.0, 101.0) is None


def test_confirmed_marks_are_the_fair_value():
    marks = collateral_marks(
        last_confirmed=100.0, new_print=180.0, band_lo=97.0, confirmed=True, fv=175.0,
    )
    assert marks.borrow_power == 175.0
    assert marks.liquidation == 175.0


def test_lending_invariant_holds_across_draws():
    rng = random.Random(20261023)
    for _ in range(300):
        last = rng.uniform(1.0, 1_000_000.0)
        new = last * rng.uniform(0.4, 1.8)
        band_lo = last * rng.uniform(0.5, 1.3)
        band_hi = last * rng.uniform(0.7, 1.6)
        fv = last * rng.uniform(0.5, 1.8)
        confirmed = rng.choice((True, False))
        forced = rng.choice((True, False))
        elapsed = rng.uniform(0.0, 2_000.0)
        collateral = collateral_marks(
            last_confirmed=last, new_print=new, band_lo=band_lo, confirmed=confirmed,
            fv=fv, forced=forced, ramp_elapsed_s=elapsed, ramp_s=900.0,
        )
        borrowed = borrowed_asset_marks(
            last_confirmed=last, new_print=new, band_hi=band_hi, confirmed=confirmed, fv=fv,
        )
        assert invariant_holds(collateral, borrowed)
        if collateral.borrow_power is not None and collateral.liquidation is not None:
            assert collateral.borrow_power <= collateral.liquidation
        if borrowed.borrow_check is not None and borrowed.liquidation is not None:
            assert borrowed.liquidation <= borrowed.borrow_check


def test_pricer_upward_spike_does_not_raise_collateral():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    reading = pricer.ingest_print(
        "EQ", ts=400.0, session="regular", external=180.0,
        books=[book], volume=1.0, venue="primary",
    )
    assert reading.confirmed is False
    assert reading.borrow_power == 100.0
    assert reading.liquidation == 100.0
    assert reading.borrow_power < reading.external
    assert reading.perp_mark == 100.0
    assert reading.oi_scale is not None and reading.oi_scale <= 0.5


def test_pricer_down_move_tightens_and_stays_ordered():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    reading = pricer.ingest_print(
        "EQ", ts=400.0, session="regular", external=70.0,
        books=[book], volume=1.0, venue="primary",
    )
    assert reading.confirmed is False
    assert reading.borrow_power == 70.0
    assert reading.liquidation is not None
    assert reading.liquidation > 70.0
    assert reading.borrow_power <= reading.liquidation
    assert reading.fv is not None
    assert abs(reading.fv - 100.0) / 100.0 < 0.05


def test_borrow_power_ramps_after_a_forced_up_move():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    last = None
    for step in range(0, 16):
        last = pricer.ingest_print(
            "EQ", ts=400.0 + step * 100.0, session="regular", external=180.0,
            books=[book], volume=1.0, venue="primary",
        )
        assert last.borrow_power is not None and last.liquidation is not None
        assert last.borrow_power <= last.liquidation + 1e-9
    assert last is not None and last.forced is True
    assert last.confirmed is False
    assert last.borrow_power is not None and last.liquidation is not None
    assert last.borrow_power == last.liquidation


def test_volume_confirms_on_the_same_tick():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    reading = pricer.ingest_print(
        "EQ", ts=400.0, session="pre", external=110.0, books=[book],
        volume=100.0, venue="primary",
    )
    assert reading.confirmed is True
    assert reading.confirm_delay_s == 0.0
    assert reading.w_ext is not None and reading.w_ext >= 0.7


def test_one_share_does_not_confirm():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    reading = pricer.ingest_print(
        "EQ", ts=400.0, session="pre", external=110.0, books=[book],
        volume=1.0, venue="primary",
    )
    assert reading.confirmed is False
    assert reading.confirm_delay_s is None


def test_volume_accumulates_and_a_missing_volume_adds_nothing():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    first = pricer.ingest_print(
        "EQ", ts=400.0, session="pre", external=110.0, books=[book], volume=40.0,
    )
    assert first.confirmed is False
    second = pricer.ingest_print(
        "EQ", ts=410.0, session="pre", external=110.0, books=[book], volume=None,
    )
    assert second.confirmed is False
    third = pricer.ingest_print(
        "EQ", ts=420.0, session="pre", external=110.0, books=[book], volume=40.0,
    )
    fourth = pricer.ingest_print(
        "EQ", ts=430.0, session="pre", external=110.0, books=[book], volume=40.0,
    )
    assert third.confirmed is False
    assert fourth.confirmed is True
    assert fourth.confirm_delay_s == 30.0


def test_second_venue_confirms_immediately():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    reading = pricer.ingest_print(
        "EQ", ts=400.0, session="pre", external=110.0, books=[book], volume=1.0,
        corroborations=[("other", 110.0)],
    )
    assert reading.confirmed is True
    assert reading.confirm_delay_s == 0.0


def test_book_follow_through_is_bounded_by_the_confirm_window():
    pricer = Pricer()
    _warm(pricer, "EQ", 100.0)
    early = pricer.ingest_print(
        "EQ", ts=400.0, session="pre", external=110.0,
        books=[deep_book(105.0)], volume=1.0,
    )
    assert early.confirmed is False
    followed = pricer.ingest_print(
        "EQ", ts=430.0, session="pre", external=110.0,
        books=[deep_book(105.0)], volume=1.0,
    )
    assert followed.confirmed is True
    assert followed.confirm_delay_s == 30.0

    late = Pricer()
    _warm(late, "EQ", 100.0)
    late.ingest_print(
        "EQ", ts=400.0, session="pre", external=110.0, books=[deep_book(105.0)], volume=1.0,
    )
    too_late = late.ingest_print(
        "EQ", ts=470.0, session="pre", external=110.0, books=[deep_book(105.0)], volume=1.0,
    )
    assert too_late.confirmed is False


def test_closed_book_cap_and_the_candidate_exception():
    pricer = Pricer()
    book = deep_book(110.0)
    pricer.ingest_print("EQ", ts=0.0, session="closed", external=100.0, books=[book], volume=500.0)
    held = pricer.ingest_print("EQ", ts=40.0, session="closed", external=100.0, books=[book], volume=500.0)
    assert held.confirmed is True
    assert held.w_ext is not None
    assert abs(held.w_ext - 0.4) < 1e-9
    assert held.fv is not None
    assert abs(held.fv - 106.0) < 1e-6

    spike = Pricer()
    steady = _warm(spike, "EQ", 100.0)
    candidate = spike.ingest_print(
        "EQ", ts=400.0, session="closed", external=50.0, books=[steady], volume=1.0,
    )
    assert candidate.confirmed is False
    assert candidate.w_ext is not None and candidate.w_ext < 0.05
    assert candidate.fv is not None and abs(candidate.fv - 100.0) / 100.0 < 0.05


def test_regular_unconfirmed_gap_does_not_inherit_the_floor():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    reading = pricer.ingest_print(
        "EQ", ts=400.0, session="regular", external=70.0, books=[book], volume=1.0,
    )
    assert reading.confirmed is False
    assert reading.w_ext is not None and reading.w_ext < 0.05
    assert reading.fv is not None and abs(reading.fv - 100.0) / 100.0 < 0.05


def test_after_the_accept_window_the_print_is_in_and_still_unconfirmed():
    pricer = Pricer()
    book = _warm(pricer, "EQ", 100.0)
    early = pricer.ingest_print(
        "EQ", ts=400.0, session="regular", external=130.0, books=[book], volume=1.0,
    )
    assert early.confirmed is False
    assert early.w_ext is not None and early.w_ext < 0.05
    forced = pricer.ingest_print(
        "EQ", ts=700.0, session="regular", external=130.0, books=[book], volume=1.0,
    )
    assert forced.confirmed is False
    assert forced.forced is True
    assert forced.w_ext is not None and forced.w_ext >= 0.9
