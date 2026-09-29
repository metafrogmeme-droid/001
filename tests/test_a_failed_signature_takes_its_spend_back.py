"""A signature that FAILED hands its notional back to the 24h ledger.

`/web3/sign` records the transfer against the day BEFORE the signature is
made -- an allow is what lets the sign proceed, and a spend that cannot be
recorded is not signed -- so a `build_and_sign` that answered `ok: False`
(no key, the wrong network, a gas below the chain's floor) left the day
counting a transfer that was never handed to the network. Driven on the
unfixed tree under a $2,000 day: one failed signature of a $1,000 transfer
and the next $1,000 transfer was refused as over the cap.

The refused web-live confirm's rule, one door over: nothing was handed on,
so the spend is released; a release that could not land keeps counting and
says so with the exception's class. The BROADCAST failure one step down keeps
its spend on purpose -- a transaction handed to the network is neither
confirmed nor refused.
"""
from __future__ import annotations

import time

import pytest

from bot.web import web3_signer as sg
from tests import test_a_sign_request_is_authorized_on_what_it_signs as _suite

rig = _suite.rig                     # the sign suite's rig, re-registered by name
_sign = _suite._sign
TG, DEST, ETH = _suite.TG, _suite.DEST, _suite.ETH


def _failing_signer(monkeypatch, error="signing library refused"):
    calls = []

    def _build(**kw):
        calls.append(kw)
        return {"ok": False, "error": error}

    monkeypatch.setattr(sg, "build_and_sign", _build)
    return calls


@pytest.mark.asyncio
async def test_a_failed_signature_releases_the_days_spend(rig, monkeypatch):
    rig.bind(max_notional_per_trade_usd=5_000, max_notional_daily_usd=5_000)
    app, _ = rig.app()
    calls = _failing_signer(monkeypatch)
    status, body = await _sign(app, value_wei=str(1 * ETH))       # $3,000 at the $3,000 mark
    assert status == 400 and body["error"] == "sign_failed"
    assert body["spend_released"] is True
    assert calls, "the signer was asked"
    assert rig.ledger.spent(TG, time.time()) == 0.0
    assert any(a.get("action") == "web3_spend" and a.get("result") == "RELEASED" for a in rig.audits)


@pytest.mark.asyncio
async def test_the_next_transfer_is_not_refused_on_a_signature_that_never_happened(rig, monkeypatch):
    """The driven figure: under a $5,000 day a failed $3,000 signature used
    to leave $2,000 of room, and the next $3,000 was refused as over the cap."""
    rig.bind(max_notional_per_trade_usd=5_000, max_notional_daily_usd=5_000)
    app, _ = rig.app()
    _failing_signer(monkeypatch)
    status, _ = await _sign(app, value_wei=str(1 * ETH), nonce=7)
    assert status == 400
    monkeypatch.setattr(sg, "build_and_sign", _real_build)         # the real signer again
    status, body = await _sign(app, value_wei=str(1 * ETH), nonce=8)
    assert status == 200 and body["signed"] is True, body
    assert rig.ledger.spent(TG, time.time()) == pytest.approx(3_000.0)


_real_build = sg.build_and_sign


@pytest.mark.asyncio
async def test_a_release_that_could_not_land_keeps_the_spend_and_says_the_class(rig, monkeypatch):
    rig.bind(max_notional_per_trade_usd=5_000, max_notional_daily_usd=5_000)
    app, _ = rig.app()
    _failing_signer(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("apiKey=SECRETVALUE /var/lib/ledger.json")

    monkeypatch.setattr(rig.ledger, "release", _boom)
    status, body = await _sign(app, value_wei=str(1 * ETH))
    assert status == 400 and body["spend_released"] is None
    assert rig.ledger.spent(TG, time.time()) == pytest.approx(3_000.0), "kept: the strict direction"
    kept = [a for a in rig.audits if a.get("action") == "web3_spend"]
    assert kept and kept[-1]["result"] == "KEPT" and kept[-1]["data"]["error"] == "RuntimeError"
    assert "SECRETVALUE" not in str(rig.audits) and "SECRETVALUE" not in str(body)


@pytest.mark.asyncio
async def test_a_broadcast_that_failed_keeps_its_spend(rig, monkeypatch):
    """Handed to the network is neither confirmed nor refused: the spend
    stands, and the audit says SIGNED rather than OK."""
    rig.bind(max_notional_per_trade_usd=5_000, max_notional_daily_usd=5_000)
    app, _ = rig.app()

    async def _bcast(raw, url, chain_id):
        return {"ok": False, "error": "rpc refused"}

    monkeypatch.setattr(sg, "broadcast", _bcast)
    status, body = await _sign(app, value_wei=str(1 * ETH))
    assert status == 200 and body["signed"] is True and body["broadcast"] is False
    assert rig.ledger.spent(TG, time.time()) == pytest.approx(3_000.0)
    assert not any(a.get("action") == "web3_spend" for a in rig.audits)
    assert any(a.get("action") == "web3_sign" and a.get("result") == "SIGNED" for a in rig.audits)


@pytest.mark.asyncio
async def test_a_zero_value_failed_signature_has_nothing_to_release(rig, monkeypatch):
    rig.bind(max_notional_per_trade_usd=5_000, max_notional_daily_usd=5_000)
    app, _ = rig.app()
    _failing_signer(monkeypatch)
    status, body = await _sign(app, value_wei="0")
    assert status == 400 and body["spend_released"] is None
    assert not any(a.get("action") == "web3_spend" for a in rig.audits)


def test_the_release_helper_answers_three_ways(monkeypatch):
    """And the audit names which: a row the ledger did not hold is NOT_HELD,
    never RELEASED -- an operator reading RELEASED over a retry's spend that
    the earlier attempt still holds would think the day had room it has not."""
    from bot.web import user_gateway as ug
    from bot.web.user_gateway import _release_web3_spend

    class _Ledger:
        def __init__(self, held):
            self.held = held

        def release(self, key, ref, now):
            return self.held

    audits = []
    monkeypatch.setattr(ug, "audit", lambda log, msg, **kw: audits.append(kw))
    assert _release_web3_spend(_Ledger(True), TG, "web3:1:1:1:x", 0.0) is True
    assert audits[-1]["result"] == "RELEASED"
    assert _release_web3_spend(_Ledger(False), TG, "web3:1:1:1:x", 0.0) is False
    assert audits[-1]["result"] == "NOT_HELD"
    assert _release_web3_spend(None, TG, "web3:1:1:1:x", 0.0) is False
