"""Moving funds is a local fork, chain id 31337, and this process never signs.

Mainnet (1) is its own refusal. Sepolia, Holesky and Hoodi are refused too:
a local fork is the only chain id this model will account on.
"""
from __future__ import annotations

LOCAL_FORK = 31337
MAINNET = 1


class MainnetForbidden(Exception):
    """Chain id 1. Nothing here is broadcast there."""


class NotLocalFork(Exception):
    """Any chain id other than 31337, including the public testnets."""


class SigningRefused(Exception):
    """This process does not hold a key and does not sign."""


def assert_may_move_funds(chain_id: object) -> None:
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        raise NotLocalFork()
    if chain_id == MAINNET:
        raise MainnetForbidden()
    if chain_id != LOCAL_FORK:
        raise NotLocalFork()


def refuse_sign(*_args: object, **_kwargs: object) -> None:
    """Signing is refused on every chain, including the local fork."""
    raise SigningRefused()
