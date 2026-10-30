"""Audit M-07: under Bitget demo trading the v3 client must send the demo header.

The ccxt half of a trade followed BITGET_SANDBOX while the v3 half (strategy stops,
position reads, flash close) always went to the live account. Both constructors the
order path uses are driven here, with the venue's own sandbox reading on and off.
"""
from __future__ import annotations

import pytest

from bot.config import CONFIG
from bot.core.bitget_v3_client import BitgetV3Client


def _header(client: BitgetV3Client):
    req = client.build_request("GET", "/api/v3/position/current-position?category=USDT-FUTURES")
    # urllib stores header names capitalised.
    return {k.lower(): v for k, v in req.header_items()}.get("paptrading")


@pytest.fixture
def sandbox():
    # CONFIG.exchange is a frozen dataclass: set through object.__setattr__ and
    # restore by hand, as tests/conftest.py does for frozen config fields.
    before = CONFIG.exchange.sandbox

    def set_(on: bool):
        object.__setattr__(CONFIG.exchange, "sandbox", on)
    yield set_
    object.__setattr__(CONFIG.exchange, "sandbox", before)


@pytest.mark.parametrize("build", [
    lambda: BitgetV3Client.from_config(),
    lambda: BitgetV3Client.for_account({"api_key": "k", "api_secret": "s", "passphrase": "p"}),
    lambda: BitgetV3Client.for_account(None),
])
def test_demo_header_follows_the_venue_sandbox_reading(sandbox, build):
    sandbox(True)
    on = build()
    assert on.demo is True and _header(on) == "1"
    sandbox(False)
    off = build()
    assert off.demo is False and _header(off) is None


def test_an_explicit_choice_wins_over_the_config(sandbox):
    sandbox(False)
    assert _header(BitgetV3Client("k", "s", "p", demo=True)) == "1"
    sandbox(True)
    assert _header(BitgetV3Client("k", "s", "p", demo=False)) is None
