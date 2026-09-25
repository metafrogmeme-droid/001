"""The Hyperliquid /connect probe reads the key's role before storing it.

The probe is a balance fetch, and Hyperliquid's balance is a PUBLIC read keyed
on the wallet address: the agent key is not signed with at all. The docstring
over it said it "proves the agent key authenticates for the wallet before we
store it". Driven with real ccxt and the transport stubbed:

    key 0xff…ff (above the curve order, cannot sign)  ->  (True, "123.00 USDC free")
    the wallet's own MASTER key                        ->  (True, "123.00 USDC free")

no signed request in either, and both keys encrypted and stored — the first to
fail at the first signed action on the money path (a stop), the second refused
only when a client was built. `hyperliquid_key_role` already answered both; it
was applied in `HyperliquidVenue.create_exchange` and nowhere at the door.

Three outcomes now, the /setsigner shape: confirmed (an agent key, derived),
well-formed but unconfirmed (no signing library installed — the CI install —
said, not implied), and rejected (not a signing key, or the master key), in the
constructor's own words and quoting no part of the key. Authentication for the
wallet is still proven only at the first signed action, and the docstring says
so.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import ccxt.async_support as ca
import pytest

import bot.web.web3_signer as ws
from bot.core import exchange_credentials as ec
from bot.core.venues import HyperliquidVenue, hyperliquid_key_refusal

WALLET = "0x" + "ab" * 20
AGENT_ADDR = "0x" + "cd" * 20
GOOD_KEY = "0x" + "01" * 32
BAD_KEY = "0x" + "ff" * 32          # a scalar above the secp256k1 order


@pytest.fixture
def venue(monkeypatch):
    """Real ccxt.hyperliquid with the transport stubbed; records requests."""
    calls: list = []
    real = ca.hyperliquid

    class _Rec(real):
        async def fetch(self, url, method="GET", headers=None, body=None):
            calls.append((method, url, body))
            b = json.loads(body or "{}")
            if b.get("type") == "userAbstraction":
                return "default"
            if b.get("type") == "clearinghouseState":
                summary = {"accountValue": "123.0", "totalMarginUsed": "0",
                           "totalNtlPos": "0", "totalRawUsd": "123.0"}
                return {"marginSummary": summary, "crossMarginSummary": summary,
                        "withdrawable": "123.0", "assetPositions": [], "time": 0}
            if b.get("type") == "spotClearinghouseState":
                return {"balances": []}
            return {}

        async def load_markets(self, reload=False, params={}):
            return {}

    monkeypatch.setattr(ca, "hyperliquid", _Rec)
    return calls


def _validate(key):
    return asyncio.run(ec.validate_venue_credentials(
        "hyperliquid", {"wallet_address": WALLET, "agent_private_key": key}))


def _derives(address):
    return patch.object(ws, "check_signing_key",
                        lambda raw: {"ok": True, "address": address, "reason": ""})


def test_a_key_that_cannot_sign_is_refused_before_anything_is_asked(venue):
    ok, detail = _validate(BAD_KEY)
    assert ok is False
    assert detail == hyperliquid_key_refusal("rejected")
    assert "ff" * 8 not in detail.lower()
    assert venue == [], "the probe asked the venue about a key it had already refused"


def test_the_master_key_is_refused_at_the_door(venue):
    with _derives(WALLET):
        ok, detail = _validate(GOOD_KEY)
    assert ok is False
    assert detail == hyperliquid_key_refusal("master")
    assert venue == []


def test_an_agent_key_is_confirmed_and_stored(venue):
    with _derives(AGENT_ADDR):
        ok, detail = _validate(GOOD_KEY)
    assert ok is True
    assert detail == "123.00 USDC free"
    assert not any("signature" in (b or "") for _, _, b in venue)


def test_an_unconfirmed_key_is_stored_and_says_it_was_not_confirmed(venue):
    # The CI install: no signing library, so no address can be derived.
    ok, detail = _validate(GOOD_KEY)
    assert ok is True
    assert detail.startswith("123.00 USDC free")
    assert "no signing library" in detail and "first order" in detail


def test_the_door_and_the_constructor_refuse_in_the_same_words():
    v = HyperliquidVenue()
    with pytest.raises(RuntimeError) as bad:
        v.create_exchange(None, {"wallet_address": WALLET, "agent_private_key": BAD_KEY})
    assert str(bad.value) == hyperliquid_key_refusal("rejected")
    with _derives(WALLET), pytest.raises(RuntimeError) as master:
        v.create_exchange(None, {"wallet_address": WALLET, "agent_private_key": GOOD_KEY})
    assert str(master.value) == hyperliquid_key_refusal("master")


def test_only_the_two_refusing_roles_have_a_sentence():
    assert hyperliquid_key_refusal("agent") is None
    assert hyperliquid_key_refusal("unconfirmed") is None
    assert hyperliquid_key_refusal("master") and hyperliquid_key_refusal("rejected")


def test_the_docstring_no_longer_claims_the_probe_authenticates_the_key():
    # The retraction has to NAME the claim it corrects, so the phrase may
    # appear exactly once — as the quotation — and a second, live claim fails.
    doc = " ".join((ec.validate_hyperliquid_credentials.__doc__ or "").split())
    assert doc.count("proves the agent key authenticates for the wallet") == 1
    assert 'used to say the probe "proves the agent key' in doc
    assert "proven only at the first signed action" in doc
