"""The pool rehearsal. Fork accounting, a hard cap, a pause, and no broadcast.

The Solidity file is the contract. This module drives the same rules because
CI has no forge. A permitted mainnet config still does not sign.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.t2c.chain_guard import SigningRefused
from bot.t2c.mainnet_config import admin_pool_status, pool_config, public_pool_status
from bot.t2c.pool import (
    FORK,
    MAINNET,
    ChainRefused,
    DepositCap,
    NotOwner,
    Paused,
    Pool,
    Unreadable,
    WithdrawalNftRefused,
    assert_process_does_not_sign,
    fork_rehearsal,
)
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
OWNER = "0x" + "11" * 20
USER = "0x" + "22" * 20
OTHER = "0x" + "33" * 20


def _open(chain_id: int = FORK, cap: int = 100) -> Pool:
    pool = Pool(chain_id, cap, OWNER)
    pool.unpause(OWNER)
    return pool


def test_fork_rehearsal_caps_the_deposit_and_the_pause_stops_the_next():
    paused = Pool(FORK, 100, OWNER)
    with pytest.raises(Paused):
        paused.deposit(USER, 1)
    stranger = Pool(FORK, 100, OWNER)
    with pytest.raises(NotOwner):
        stranger.unpause(USER)
    pool = _open()
    assert pool.deposit(USER, 40) == 40
    assert pool.assets == 40
    assert pool.shares[USER] == 40
    with pytest.raises(DepositCap):
        pool.deposit(USER, 61)
    assert pool.assets == 40
    pool.pause(OWNER)
    with pytest.raises(Paused):
        pool.deposit(USER, 1)
    with pytest.raises(NotOwner):
        pool.pause(USER)


def test_withdrawal_nfts_move_on_a_fork_and_a_testnet_and_not_on_mainnet():
    fork = _open()
    fork.deposit_withdrawal_nft(USER, 7)
    fork.transfer_withdrawal_nft(USER, 7, OTHER)
    assert fork.nft_holder[7] == OTHER
    sepolia = _open(11155111)
    sepolia.deposit_withdrawal_nft(USER, 8)
    sepolia.transfer_withdrawal_nft(USER, 8, OTHER)
    assert sepolia.nft_holder[8] == OTHER
    mainnet = _open(MAINNET)
    mainnet.deposit(USER, 10)
    mainnet.transfer_shares(USER, OTHER, 4)
    assert mainnet.shares[USER] == 6
    assert mainnet.shares[OTHER] == 4
    with pytest.raises(WithdrawalNftRefused):
        mainnet.deposit_withdrawal_nft(USER, 9)
    with pytest.raises(WithdrawalNftRefused):
        mainnet.transfer_withdrawal_nft(USER, 9, OTHER)


def test_a_zero_is_unreadable_and_an_unknown_chain_is_refused():
    pool = _open()
    with pytest.raises(Unreadable):
        pool.deposit(USER, 0)
    with pytest.raises(Unreadable):
        Pool(FORK, 0, OWNER)
    with pytest.raises(ChainRefused):
        Pool(999, 100, OWNER)
    with pytest.raises(Unreadable):
        pool.withdraw(USER, 1)
    pool.deposit(USER, 5)
    assert pool.withdraw(USER, 2) == 2
    assert pool.assets == 3


def test_the_mainnet_config_is_not_a_deployment():
    closed = pool_config({})
    assert closed["deployed"] is False
    assert closed["broadcast"] is False
    assert closed["server_signs"] is False
    assert closed["server_holds_key"] is False
    assert closed["users_deposit_from_own_wallet"] is True
    assert closed["deposit_cap_wei"] is None
    assert closed["deposit_cap_source"] == "absent"
    assert closed["deploy_permitted"] is False
    assert closed["chain_id"] == MAINNET
    go_only = pool_config({
        "T2C_POOL_OWNER_GO": "1",
        "T2C_DEPOSIT_CAP_WEI": "100",
        "T2C_POOL_OWNER": OWNER,
    })
    assert go_only["deploy_permitted"] is False
    assert go_only["external_audit"] is False
    permitted = pool_config({
        "T2C_POOL_OWNER_GO": "true",
        "T2C_POOL_AUDIT": "1",
        "T2C_DEPOSIT_CAP_WEI": "100",
        "T2C_POOL_OWNER": OWNER,
    })
    assert permitted["deploy_permitted"] is True
    assert permitted["deployed"] is False
    assert permitted["broadcast"] is False
    assert permitted["deposit_cap_wei"] == "100"
    unread = pool_config({"T2C_DEPOSIT_CAP_WEI": "nope"})
    assert unread["deposit_cap_source"] == "unreadable"
    assert unread["deposit_cap_wei"] is None
    public = public_pool_status({})
    assert public["deposit_cap"] == "absent"
    assert "deposit_cap_wei" not in public
    admin = admin_pool_status({"T2C_DEPOSIT_CAP_WEI": "100", "T2C_POOL_OWNER": OWNER})
    assert admin["deposit_cap"] == "set"
    assert admin["deposit_cap_wei"] == "100"
    assert admin["mainnet_pool"] == "not_deployed"
    with pytest.raises(SigningRefused):
        assert_process_does_not_sign()
    rehearsal = fork_rehearsal()
    assert rehearsal["broadcast"] is False
    assert rehearsal["signed"] is False
    assert rehearsal["paused"] is True
    assert rehearsal["assets"] == 35
    assert rehearsal["nft_moved"] is True
    assert rehearsal["chain_id"] == FORK


def test_the_pool_contract_is_the_only_deposit_and_it_does_not_sign():
    src = ROOT / "contracts" / "t2c" / "src" / "T2CPool.sol"
    text = src.read_text(encoding="utf-8")
    assert "function deposit(" in text
    assert "paused = true" in text
    assert "depositCap" in text
    assert "WithdrawalNftRefused" in text
    assert "block.chainid == MAINNET" in text
    names = (
        (31337, "FORK"),
        (1, "MAINNET"),
        (11155111, "SEPOLIA"),
        (17000, "HOLESKY"),
        (560048, "HOODI"),
    )
    for chain_id, name in names:
        assert f"{name} = {chain_id}" in text
    blob = "\n".join(
        code_only(path.read_text(encoding="utf-8"))
        for path in (ROOT / "bot" / "t2c" / "pool.py", ROOT / "bot" / "t2c" / "mainnet_config.py", src)
    )
    for token in ("private_key", "eth_sendTransaction", "sign_transaction", "PRIVATE_KEY"):
        assert token not in blob
