"""The build step before any signature — and the three ways it could lose money.

The signing slice, decided non-custodial and simulation-first. This half turns
an allowed plan into an UNSIGNED Jupiter transaction the user signs in their own
wallet. Nothing here signs and nothing here holds a key: the repo has no Solana
signing library, `solana_wallet.js` exposes only connect/signMessage, and
`/linkwallet` verifies ed25519 signatures without ever producing one.

Most of these tests are about refusals, because on this path a wrong "yes" costs
real money and a wrong "no" costs a retry.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core import meme_swap as ms

USER = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKEN = "So11111111111111111111111111111111111111112"
NOW = 1_700_000_000.0


def plan(side="buy", allowed=True, would_execute=False, size_usd=25.0):
    return {
        "allowed": allowed, "would_execute": would_execute, "side": side,
        "jupiter_request": {
            "inputMint": USDC if side == "buy" else TOKEN,
            "outputMint": TOKEN if side == "buy" else USDC,
            "amount_usd": size_usd, "slippageBps": 100},
    }


def transport(quote=None, swap=None, fail=None):
    calls = []

    # Dispatch on the PATH, not on a substring: the swap endpoint is
    # `https://quote-api.jup.ag/v6/swap`, which contains "quote" — matching
    # loosely sent every swap call down the quote branch and made the module
    # look broken. A fixture with a blind spot accuses the code it tests.
    def _is_quote(url):
        return url.rstrip("/").endswith("/quote")

    async def _t(method, url, params, body):
        calls.append((method, url, params, body))
        if fail == "quote" and _is_quote(url):
            raise RuntimeError("upstream 503")
        if fail == "swap" and not _is_quote(url):
            raise RuntimeError("upstream 503")
        if _is_quote(url):
            return quote if quote is not None else {
                "inAmount": "25000000", "outAmount": "1234567",
                "otherAmountThreshold": "1210000", "priceImpactPct": "0.0031"}
        return swap if swap is not None else {"swapTransaction": "BASE64TX=="}

    _t.calls = calls
    return _t


def build(p=None, **kw):
    kw.setdefault("user_public_key", USER)
    kw.setdefault("transport", transport())
    kw.setdefault("now", lambda: NOW)
    kw.setdefault("env", {})
    return asyncio.run(ms.build_swap(plan() if p is None else p, **kw))


# ── nothing here signs, and the output says so ────────────────────────────

def test_the_build_is_never_signed_or_broadcast():
    b = build()
    assert b["buildable"] is True
    assert b["signed"] is False and b["broadcast"] is False
    assert "NOT signed, NOT broadcast" in ms.human_readable(b)


def test_a_private_key_is_not_a_parameter():
    """The custody model, asserted rather than trusted to the docstring."""
    import inspect
    sig = inspect.signature(ms.build_swap).parameters
    for banned in ("private_key", "secret_key", "keypair", "signer", "mnemonic"):
        assert banned not in sig
    src = inspect.getsource(ms)
    assert "sign(" not in src and "sign_transaction" not in src


def test_a_public_key_is_required():
    b = build(user_public_key="")
    assert b["buildable"] is False
    assert "signs nothing" in b["reason"]


# ── hazard 1: a stale quote ───────────────────────────────────────────────

def test_terms_carry_an_expiry_and_expire():
    b = build()
    t = b["terms"]
    assert t["expires_at"] == NOW + ms.QUOTE_TTL_S
    assert ms.terms_expired(t, now=lambda: NOW) is False
    assert ms.terms_expired(t, now=lambda: NOW + ms.QUOTE_TTL_S + 1) is True


def test_undateable_terms_are_expired_terms():
    # A quote we cannot date is one we cannot honour. The damaging default here
    # is "still valid", so the safe answer is the opposite one.
    for junk in (None, {}, {"expires_at": None}, {"expires_at": "soon"}):
        assert ms.terms_expired(junk, now=lambda: NOW) is True


# ── hazard 2: a stale authorisation ───────────────────────────────────────

def test_an_old_plan_cannot_authorise_a_swap_now():
    """`/memeplan` read the market as it was. Liquidity and exits move."""
    b = build(plan_created_at=NOW - (ms.MAX_PLAN_AGE_S + 1))
    assert b["buildable"] is False
    assert "re-run the preflight" in b["reason"]


def test_a_fresh_plan_still_builds():
    b = build(plan_created_at=NOW - 5)
    assert b["buildable"] is True


def test_a_plan_that_was_not_allowed_builds_nothing():
    b = build(plan(allowed=False))
    assert b["buildable"] is False
    assert b["unsigned_transaction"] is None


def test_a_plan_claiming_it_would_execute_is_refused():
    """A contract violation, and the safe reading of one is to stop.

    This module exists BECAUSE the planner cannot execute; a plan asserting
    otherwise is not one this code understands.
    """
    b = build(plan(would_execute=True))
    assert b["buildable"] is False
    assert "refusing to build" in b["reason"]


# ── hazard 3: double execution ────────────────────────────────────────────

def test_identical_terms_produce_an_identical_intent_id():
    a, b = build(), build()
    assert a["intent_id"] == b["intent_id"], (
        "a signing surface cannot dedupe what it cannot recognise")


def test_a_requote_is_a_new_intent_not_a_repeat():
    # New expiry means a new price. Treating it as a duplicate would suppress a
    # legitimate retry after the first quote went stale.
    a = build()
    b = build(now=lambda: NOW + 10)
    assert a["intent_id"] != b["intent_id"]


def test_building_twice_sends_nothing():
    t = transport()
    build(transport=t)
    build(transport=t)
    assert all(m in ("GET", "POST") for m, _u, _p, _b in t.calls)
    assert not any("send" in u or "broadcast" in u for _m, u, _p, _b in t.calls)


# ── the network is simulation unless mainnet is named ─────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("", ms.SIMULATE), ("nonsense", ms.SIMULATE), ("MAINNET", ms.MAINNET),
    ("devnet", ms.DEVNET), ("mainnet", ms.MAINNET), (" simulate ", ms.SIMULATE),
])
def test_network_is_fail_closed(raw, expected):
    assert ms.network({"MEME_EXECUTION_NETWORK": raw}) == expected


def test_the_default_build_says_it_cannot_touch_mainnet():
    b = build()
    assert b["network"] == ms.SIMULATE
    assert "cannot move mainnet funds" in ms.human_readable(b)


def test_mainnet_requires_naming_it():
    b = build(env={"MEME_EXECUTION_NETWORK": "mainnet"})
    assert b["network"] == ms.MAINNET
    assert "mainnet transaction ready to sign" in b["reason"]


# ── sizing without guessing ───────────────────────────────────────────────

def test_a_buy_converts_usd_with_usdc_decimals():
    b = build(plan(size_usd=25.0))
    assert b["terms"]["in_amount"] == "25000000"      # 25 USDC, 6dp


def test_a_sell_refuses_to_guess_token_decimals():
    """Guessing decimals sizes the trade wrong by orders of magnitude."""
    b = build(plan(side="sell"))
    assert b["buildable"] is False
    assert "does not know the token's decimals" in b["reason"]


def test_a_sell_builds_when_base_units_are_supplied():
    b = build(plan(side="sell"), amount_base_units=1_000_000_000)
    assert b["buildable"] is True


def test_a_non_positive_amount_is_refused():
    assert build(amount_base_units=0)["buildable"] is False
    assert build(amount_base_units=-5)["buildable"] is False


# ── upstream failures are refusals, never empty successes ─────────────────

@pytest.mark.parametrize("stage", ["quote", "swap"])
def test_an_upstream_failure_refuses_rather_than_returning_a_blank(stage):
    b = build(transport=transport(fail=stage))
    assert b["buildable"] is False
    assert b["unsigned_transaction"] is None


def test_no_route_is_a_named_refusal():
    b = build(transport=transport(quote={}))
    assert b["buildable"] is False and "no route" in b["reason"]


def test_a_transactionless_response_is_refused():
    for bad in ({}, {"swapTransaction": ""}, {"swapTransaction": None}, {"x": 1}):
        b = build(transport=transport(swap=bad))
        assert b["buildable"] is False, bad
        assert b["unsigned_transaction"] is None


def test_the_transaction_is_passed_through_untouched():
    b = build(transport=transport(swap={"swapTransaction": "AAABBBCCC=="}))
    assert b["unsigned_transaction"] == "AAABBBCCC==", (
        "this module must never decode or rewrite what the user will sign")
