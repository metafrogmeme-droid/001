"""Exit Swap accounting. Fork chain id only, and a refusal leaves the book."""
from __future__ import annotations

from decimal import Decimal

import pytest

from bot.t2c.chain_guard import (
    LOCAL_FORK,
    MainnetForbidden,
    NotLocalFork,
    SigningRefused,
    assert_may_move_funds,
    refuse_sign,
)
from bot.t2c.exit_model import (
    clamp_fee_bps,
    exit_fee_bps,
    flash_unwind,
    fork_demo,
    new_ledger,
    quote_trade,
    settle_unsteth_claim,
)

SEPOLIA = 11155111
HOLESKY = 17000
HOODI = 560048


def test_only_the_local_fork_may_move_funds_and_signing_is_always_refused():
    assert_may_move_funds(LOCAL_FORK)
    with pytest.raises(MainnetForbidden):
        assert_may_move_funds(1)
    for chain in (SEPOLIA, HOLESKY, HOODI, 5):
        with pytest.raises(NotLocalFork):
            assert_may_move_funds(chain)
    with pytest.raises(SigningRefused):
        refuse_sign()


def test_fee_clamp_and_the_formula():
    assert clamp_fee_bps(Decimal(1)) == Decimal(5)
    assert clamp_fee_bps(Decimal(150)) == Decimal(100)
    assert clamp_fee_bps(None) is None
    assert exit_fee_bps(Decimal(0)) == Decimal(10)
    assert exit_fee_bps(Decimal(1)) == Decimal(50)
    assert exit_fee_bps(Decimal(2)) == Decimal(100)
    assert exit_fee_bps(None) is None
    assert exit_fee_bps(Decimal(-1)) is None


def test_intake_refuses_the_whole_trade_and_the_next_block_starts_clear():
    ledger = new_ledger(Decimal(1000))
    first = quote_trade(ledger, LOCAL_FORK, Decimal(10), Decimal(1), 1)
    assert first["filled"] is True
    assert first["fee_bps"] == Decimal(10)
    assert ledger["lp_fee_eth"] == Decimal("0.01")
    fee_before = ledger["lp_fee_eth"]
    refused = quote_trade(ledger, LOCAL_FORK, Decimal(11), Decimal(1), 1)
    assert refused["filled"] is False
    assert refused["reason"] == "intake_cap"
    assert ledger["lp_fee_eth"] == fee_before
    nxt = quote_trade(ledger, LOCAL_FORK, Decimal(20), Decimal(1), 2)
    assert nxt["filled"] is True


def test_a_non_wsteth_asset_and_an_unread_pool_are_refusals():
    ledger = new_ledger(Decimal(1000))
    other = quote_trade(ledger, LOCAL_FORK, Decimal(1), Decimal(1), 1, asset="weETH")
    assert other["reason"] == "wsteth_only"
    assert ledger["lp_fee_eth"] == 0
    empty = new_ledger(None)
    unread = quote_trade(empty, LOCAL_FORK, Decimal(1), Decimal(1), 1)
    assert unread["reason"] == "unreadable"
    with pytest.raises(MainnetForbidden):
        quote_trade(ledger, 1, Decimal(1), Decimal(1), 1)


def test_flash_shortfall_rolls_back_and_a_claim_keeps_an_unread_steth_out():
    ledger = new_ledger(Decimal(1000))
    short = flash_unwind(
        ledger, LOCAL_FORK, debt_eth=Decimal(5), quantity=Decimal(1),
        price=Decimal(1), block_number=1,
    )
    assert short["reason"] == "shortfall"
    assert ledger["lp_fee_eth"] == 0
    assert ledger["claims"] == []
    filled = flash_unwind(
        ledger, LOCAL_FORK, debt_eth=Decimal("0.5"), quantity=Decimal(1),
        price=Decimal("0.99"), block_number=1,
    )
    assert filled["filled"] is True
    assert filled["steth_out"] is None
    assert ledger["lp_fee_eth"] > 0
    assert "shares" not in ledger
    settled = settle_unsteth_claim(ledger, filled["claim_id"])
    assert settled["settled"] is True
    assert settled["steth_out"] is None


def test_the_edge_band_and_the_claim_cycle():
    from bot.t2c.exit_model import (
        IDLE,
        QUOTING,
        claim,
        exit_cycle,
        mark_finalized,
        note_edge,
        sell,
    )
    ledger = new_ledger(Decimal(1000))
    held = note_edge(ledger, Decimal(50), Decimal(0))
    assert held["moved"] is False
    assert ledger["state"] == IDLE
    opened = note_edge(ledger, Decimal("50.1"), Decimal(0))
    assert opened["state"] == QUOTING
    stay = note_edge(ledger, Decimal(20), Decimal(0))
    assert stay["moved"] is False
    closed = note_edge(ledger, Decimal("19.9"), Decimal(0))
    assert closed["state"] == IDLE
    unread = note_edge(ledger, None, Decimal(1))
    assert unread["reason"] == "unreadable"
    assert ledger["state"] == IDLE
    refused = sell(ledger, LOCAL_FORK, "steth", Decimal(1), Decimal(0), Decimal(1), 1)
    assert refused["reason"] == "wrong_state"
    assert ledger["lp_fee_eth"] == 0
    cycle = exit_cycle()
    assert cycle["event"] == "Bought"
    assert cycle["claimed"] is True
    assert cycle["state"] == "idle"
    book = new_ledger(Decimal(1000))
    note_edge(book, Decimal(80), Decimal(0))
    sold = sell(book, LOCAL_FORK, "wsteth", Decimal(1), Decimal("0.5"), Decimal(1), 1)
    assert sold["filled"] is True
    assert mark_finalized(book, sold["request_id"])["finalized"] is True
    assert claim(book, [])["reason"] == "unreadable"
    assert book["state"] == "claimable"


def test_the_fork_demo_is_not_a_broadcast():
    demo = fork_demo()
    assert demo["broadcast"] is False
    assert demo["chain_id"] == LOCAL_FORK
    assert demo["steth_out"] is None
    assert demo["trade"]["filled"] is True
    assert Decimal(demo["lp_fee_eth"]) > 0
