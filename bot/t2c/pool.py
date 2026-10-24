"""In-memory rehearsal of the pool the user would deposit into.

The contract is ``contracts/t2c/src/T2CPool.sol``. This module is the same
rules, driven on a fork chain id, because the runner has no forge. It does
not hold a key, does not sign, and does not broadcast.
"""
from __future__ import annotations

from typing import NoReturn

from bot.t2c.chain_guard import SigningRefused, refuse_sign

FORK = 31337
MAINNET = 1
TESTNETS = frozenset({11155111, 17000, 560048})
POOL_CHAINS = frozenset({FORK, MAINNET}) | TESTNETS


class Unreadable(Exception):
    """A zero amount, a zero cap, or an address this rehearsal cannot name."""


class Paused(Exception):
    """The pause switch is on."""


class DepositCap(Exception):
    """The deposit would put the pool's assets above the hard cap."""


class NotOwner(Exception):
    """The caller is not the address the rehearsal was given."""


class ChainRefused(Exception):
    """The chain id is not the fork, a named testnet, or mainnet."""


class WithdrawalNftRefused(Exception):
    """A withdrawal NFT moves on a testnet or a fork."""


class Pool:
    """Assets, share balances, and withdrawal-NFT holders for one rehearsal."""

    def __init__(self, chain_id: int, cap_wei: int, owner: str) -> None:
        if isinstance(chain_id, bool) or not isinstance(chain_id, int):
            raise ChainRefused()
        if chain_id not in POOL_CHAINS:
            raise ChainRefused()
        if isinstance(cap_wei, bool) or not isinstance(cap_wei, int) or cap_wei <= 0:
            raise Unreadable()
        if not _address(owner):
            raise Unreadable()
        self.chain_id = chain_id
        self.cap_wei = cap_wei
        self.owner = owner
        self.paused = True
        self.assets = 0
        self.shares: dict[str, int] = {}
        self.nft_holder: dict[int, str] = {}

    def pause(self, caller: str) -> None:
        if caller != self.owner:
            raise NotOwner()
        self.paused = True

    def unpause(self, caller: str) -> None:
        if caller != self.owner:
            raise NotOwner()
        self.paused = False

    def deposit(self, caller: str, wei: int) -> int:
        self._open()
        amount = _positive(wei)
        if self.assets + amount > self.cap_wei:
            raise DepositCap()
        self.assets += amount
        self._mint(caller, amount)
        return amount

    def withdraw(self, caller: str, shares: int) -> int:
        self._open()
        amount = _positive(shares)
        held = self.shares[caller] if caller in self.shares else None
        if held is None or held < amount:
            raise Unreadable()
        self.shares[caller] = held - amount
        self.assets -= amount
        return amount

    def transfer_shares(self, caller: str, to: str, amount: int) -> None:
        self._open()
        moved = _positive(amount)
        if not _address(to):
            raise Unreadable()
        held = self.shares[caller] if caller in self.shares else None
        if held is None or held < moved:
            raise Unreadable()
        self.shares[caller] = held - moved
        self._mint(to, moved)

    def deposit_withdrawal_nft(self, caller: str, token_id: int) -> None:
        self._open()
        self._nft_chain()
        if isinstance(token_id, bool) or not isinstance(token_id, int) or token_id <= 0:
            raise Unreadable()
        if not _address(caller):
            raise Unreadable()
        if token_id in self.nft_holder:
            raise Unreadable()
        self.nft_holder[token_id] = caller

    def transfer_withdrawal_nft(self, caller: str, token_id: int, to: str) -> None:
        self._open()
        self._nft_chain()
        if not _address(to):
            raise Unreadable()
        holder = self.nft_holder[token_id] if token_id in self.nft_holder else None
        if holder is None or holder != caller:
            raise NotOwner()
        self.nft_holder[token_id] = to

    def _open(self) -> None:
        if self.paused:
            raise Paused()

    def _nft_chain(self) -> None:
        if self.chain_id == MAINNET:
            raise WithdrawalNftRefused()
        if self.chain_id != FORK and self.chain_id not in TESTNETS:
            raise WithdrawalNftRefused()

    def _mint(self, holder: str, amount: int) -> None:
        if holder in self.shares:
            self.shares[holder] = self.shares[holder] + amount
        else:
            self.shares[holder] = amount


def _positive(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Unreadable()
    return value


def _address(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 42 or not value.startswith("0x"):
        return False
    hexdigits = set("0123456789abcdefABCDEF")
    return all(char in hexdigits for char in value[2:])


def fork_rehearsal() -> dict:
    """One deposit, one share transfer, and one NFT transfer on the fork.

    The pause is put back on at the end. Signing is attempted and refused.
    """
    owner = "0x" + "11" * 20
    user = "0x" + "22" * 20
    other = "0x" + "33" * 20
    pool = Pool(FORK, 100, owner)
    pool.unpause(owner)
    pool.deposit(user, 40)
    pool.transfer_shares(user, other, 10)
    pool.deposit_withdrawal_nft(user, 1)
    pool.transfer_withdrawal_nft(user, 1, other)
    pool.withdraw(other, 5)
    pool.pause(owner)
    signed = True
    try:
        assert_process_does_not_sign()
    except SigningRefused:
        signed = False
    return {
        "chain_id": FORK,
        "broadcast": False,
        "signed": signed,
        "assets": pool.assets,
        "paused": pool.paused,
        "nft_moved": pool.nft_holder.get(1) == other,
    }


def assert_process_does_not_sign() -> NoReturn:
    """There is no success path. Signing is refused, including on mainnet."""
    refuse_sign()
    raise AssertionError("refuse_sign returned")
