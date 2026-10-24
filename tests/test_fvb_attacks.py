"""The four ways the bridge is pushed, and the collateral spike."""
from __future__ import annotations

from bot.fvb.band import band_around
from bot.fvb.pricer import Pricer
from bot.fvb.replay import deep_book


def _warm(pricer: Pricer, symbol: str, price: float, session: str = "closed"):
    book = deep_book(price)
    pricer.ingest_print(symbol, ts=0.0, session=session, external=price,
                        books=[book], volume=1_000.0)
    return pricer.ingest_print(symbol, ts=40.0, session=session, external=price,
                               books=[book], volume=1_000.0)


def test_a_flash_mid_does_not_move_fair_value():
    pricer = Pricer()
    held = _warm(pricer, "EQ", 100.0)
    assert held.fv is not None
    flash = pricer.ingest_print(
        "EQ", ts=50.0, session="closed", external=100.0, books=[deep_book(140.0)], volume=500.0,
    )
    still = pricer.ingest_print(
        "EQ", ts=70.0, session="closed", external=100.0, books=[deep_book(100.0)], volume=500.0,
    )
    assert flash.fv is not None and abs(flash.fv - held.fv) / held.fv < 0.02
    assert still.fv is not None and abs(still.fv - held.fv) / held.fv < 0.02


def test_a_thin_book_widens_the_band():
    wide = band_around(100.0, "regular", 2.0, quantile=0.95, n_target=8.0)
    narrow = band_around(100.0, "regular", 12.0, quantile=0.95, n_target=8.0)
    unread = band_around(100.0, "regular", None, quantile=0.95, n_target=8.0)
    assert wide[0] is not None and narrow[0] is not None and unread[0] is not None
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])
    assert (unread[1] - unread[0]) > (narrow[1] - narrow[0])
    missing = band_around(None, "regular", 12.0, quantile=0.95, n_target=8.0)
    assert missing == (None, None)


def test_closed_session_book_share_stays_capped():
    pricer = Pricer()
    reading = _warm(pricer, "EQ", 100.0, session="closed")
    # The warm book is at the same price as the print, so rebuild a richer case.
    book = deep_book(130.0)
    pricer.ingest_print("EQ2", ts=0.0, session="closed", external=100.0, books=[book], volume=500.0)
    reading = pricer.ingest_print(
        "EQ2", ts=40.0, session="closed", external=100.0, books=[book], volume=500.0,
    )
    assert reading.w_ext is not None
    book_share = 1.0 - reading.w_ext
    assert book_share <= 0.6 + 1e-9


def test_true_gap_with_volume_is_not_held():
    pricer = Pricer()
    book = deep_book(100.0)
    pricer.ingest_print("EQ", ts=0.0, session="regular", external=100.0, books=[book], volume=500.0)
    pricer.ingest_print("EQ", ts=40.0, session="regular", external=100.0, books=[book], volume=500.0)
    reading = pricer.ingest_print(
        "EQ", ts=400.0, session="pre", external=112.0, books=[book], volume=250.0, venue="primary",
    )
    assert reading.confirmed is True
    assert reading.confirm_delay_s == 0.0
    assert reading.confirm_delay_s <= 60.0


def test_without_evidence_the_accept_window_does_not_mark_the_print_confirmed():
    pricer = Pricer()
    book = deep_book(100.0)
    pricer.ingest_print("EQ", ts=0.0, session="regular", external=100.0, books=[book], volume=500.0)
    pricer.ingest_print("EQ", ts=40.0, session="regular", external=100.0, books=[book], volume=500.0)
    pricer.ingest_print("EQ", ts=400.0, session="regular", external=140.0, books=[book], volume=1.0)
    reading = pricer.ingest_print(
        "EQ", ts=700.0, session="regular", external=140.0, books=[book], volume=1.0,
    )
    assert reading.confirmed is False
    assert reading.forced is True
    assert reading.w_ext is not None and reading.w_ext >= 0.9


def test_two_signatures_do_not_clear_three_of_five():
    # The threshold itself is asserted with real keys in the signer tests.
    # This is the consumer side: an unconfirmed packet is a halt, not a price.
    from bot.fvb.adapters import oracle_proposal
    halted = oracle_proposal(
        mark=140.0, confirmed=False, reading_ts=700.0, now=700.0,
        cadence_s=3.0, stale_s=10.0, last_emit_ts=None,
    )
    assert halted is not None and halted["action"] == "halt"


def test_upward_spike_through_the_pricer_and_a_crash_do_not_cross():
    pricer = Pricer()
    book = deep_book(100.0)
    pricer.ingest_print("UP", ts=0.0, session="regular", external=100.0, books=[book], volume=500.0)
    pricer.ingest_print("UP", ts=40.0, session="regular", external=100.0, books=[book], volume=500.0)
    up = pricer.ingest_print(
        "UP", ts=400.0, session="regular", external=180.0, books=[book], volume=1.0,
    )
    assert up.borrow_power == 100.0
    assert up.liquidation == 100.0
    assert up.external is not None and up.borrow_power < up.external

    pricer.ingest_print("DN", ts=0.0, session="regular", external=100.0, books=[book], volume=500.0)
    pricer.ingest_print("DN", ts=40.0, session="regular", external=100.0, books=[book], volume=500.0)
    down = pricer.ingest_print(
        "DN", ts=400.0, session="regular", external=60.0, books=[book], volume=1.0,
    )
    assert down.borrow_power == 60.0
    assert down.liquidation is not None and down.liquidation > down.borrow_power
    assert down.fv is not None and abs(down.fv - 100.0) / 100.0 < 0.05
