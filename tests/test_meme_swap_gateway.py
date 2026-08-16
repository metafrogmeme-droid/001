"""POST /gateway/meme/swap/build — the bot end of the signing path.

Driven over a real aiohttp client rather than grepped, because the claims worth
making here are about what comes back: that a refused plan yields no build at
all, that an unreadable market is a 503 rather than an empty plan, and that the
response never says a transaction may be signed unless the operator explicitly
named mainnet.

Nothing in this file supplies a private key, because nothing in the path takes
one — asserted below rather than left to the docstrings.
"""
from __future__ import annotations

import contextlib
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.web import user_gateway as ug

SECRET = "s" * 32
HDRS = {"X-Gateway-Secret": SECRET}
MINT = "So11111111111111111111111111111111111111112"
WALLET = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
TG = "7"


class FakeUsers:
    def __init__(self):
        self._u = {TG: {"authorized": True, "role": "trader"}}

    def get(self, tg):
        return self._u.get(str(tg))

    def register(self, tg, name="", auto_role=""):
        return self._u.setdefault(str(tg), {"authorized": True, "role": "trader"})

    def permission_denial(self, tg, cmd):
        return None

    def get_tier(self, tg):
        return "basic"

    def is_admitted(self, tg):
        return True


class FakeHandler:
    def __init__(self):
        self.users = FakeUsers()
        self._limiter = SimpleNamespace(allow=lambda uid: True)

    def _allowlist_ids(self):
        return set()


class FakeEngine:
    _pending_ideas: dict = {}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(ug, "_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("WEB_GATEWAY_SECRET", SECRET)
    # Simulation is the default, and the point of several tests below.
    monkeypatch.delenv("MEME_EXECUTION_NETWORK", raising=False)


@contextlib.asynccontextmanager
async def client():
    app = ug.build_gateway(FakeEngine(), FakeHandler())
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        yield c
    finally:
        await c.close()


def plan_stub(allowed=True):
    return {
        "allowed": allowed, "would_execute": False, "side": "buy",
        "reason": "ok" if allowed else "liquidity too thin",
        "preconditions": [{"name": "safety", "ok": allowed, "detail": "…"}],
        "gate": {}, "market": {"liquidity_usd": 250_000.0, "age_hours": 48.0},
        # NOW, not a fixed stamp: build_swap refuses a plan older than
        # MAX_PLAN_AGE_S, so a frozen timestamp makes every build fail as
        # "stale" and the test reads as a broken builder.
        "created_at": time.time(),
        "jupiter_request": {"inputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                            "outputMint": MINT, "amount_usd": 25.0,
                            "slippageBps": 100},
    }


@pytest.fixture()
def stub_preflight(monkeypatch):
    """Replace the market read. Returns a setter for what it should do."""
    state = {"plan": plan_stub(), "raise": None}

    async def _pf(mint, size_usd=25.0, **kw):
        if state["raise"]:
            raise state["raise"]
        return state["plan"]

    import bot.core.meme_preflight as mpf
    monkeypatch.setattr(mpf, "preflight", _pf)
    return state


@pytest.fixture()
def stub_jupiter(monkeypatch):
    """Jupiter, without the network. The swap URL CONTAINS 'quote'."""
    async def _t(method, url, params, body):
        if url.rstrip("/").endswith("/quote"):
            return {"inAmount": "25000000", "outAmount": "1234567",
                    "otherAmountThreshold": "1210000", "priceImpactPct": "0.0031"}
        return {"swapTransaction": "BASE64TX=="}

    import bot.core.meme_swap as ms
    real = ms.build_swap

    async def _build(plan, **kw):
        kw.setdefault("transport", _t)
        return await real(plan, **kw)

    monkeypatch.setattr(ms, "build_swap", _build)


async def post(c, **over):
    body = {"telegram_id": TG, "mint": MINT, "user_public_key": WALLET,
            "size_usd": 25.0}
    body.update(over)
    return await c.post("/meme/swap/build", json=body, headers=HDRS)


# ── malformed input never reaches Jupiter ─────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mint", ["", "nope", "0x" + "a" * 40, MINT[:20]])
async def test_a_bad_mint_is_refused(mint):
    async with client() as c:
        r = await post(c, mint=mint)
        assert r.status == 400
        assert (await r.json())["error"] == "bad_mint"


@pytest.mark.asyncio
@pytest.mark.parametrize("wallet", ["", "nope", "0x" + "a" * 40])
async def test_a_bad_wallet_is_refused_before_a_transaction_exists(wallet):
    """A typo here would bind a transaction to an account nobody controls."""
    async with client() as c:
        r = await post(c, user_public_key=wallet)
        assert r.status == 400
        assert (await r.json())["error"] == "bad_wallet"


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, -1, "abc", ""])
async def test_a_present_but_unusable_size_is_refused(size):
    """0 is falsy and 0 is a number the user typed.

    The first draft read `body.get("size_usd") or 25.0`, which turned a request
    for $0 into a $25 trade — the exact shape CLAUDE.md lists, on the one field
    that decides how much money moves.
    """
    async with client() as c:
        r = await post(c, size_usd=size)
        assert r.status == 400
        assert (await r.json())["error"] == "bad_size"


@pytest.mark.asyncio
async def test_an_absent_size_uses_the_default(stub_preflight, stub_jupiter):
    """Absent is the one case where substituting is right — and it is a
    different case from present-but-zero, which is why they are separate."""
    async with client() as c:
        r = await post(c, size_usd=None)
        assert r.status == 200


@pytest.mark.asyncio
async def test_the_secret_is_required():
    async with client() as c:
        r = await c.post("/meme/swap/build", json={"telegram_id": TG})
        assert r.status == 403


# ── a refused plan yields no build at all ─────────────────────────────────

@pytest.mark.asyncio
async def test_a_disallowed_plan_returns_no_build(stub_preflight):
    # Not a build object full of nulls — a page could render that as a
    # reviewable swap. `build: null` cannot be mistaken for one.
    stub_preflight["plan"] = plan_stub(allowed=False)
    async with client() as c:
        r = await post(c)
        assert r.status == 200
        data = await r.json()
        assert data["build"] is None
        assert data["plan"]["allowed"] is False
        assert "not allowed" in data["reason"]


# ── an unreadable market is a 503, not an empty verdict ───────────────────

@pytest.mark.asyncio
async def test_an_unreadable_market_is_a_503(stub_preflight):
    """"No preconditions failed" would read as "everything passed"."""
    stub_preflight["raise"] = RuntimeError("dexscreener 503")
    async with client() as c:
        r = await post(c)
        assert r.status == 503
        data = await r.json()
        assert data["error"] == "preflight_unavailable"
        assert "build" not in data or data["build"] is None


# ── the happy path is review-only unless mainnet was named ────────────────

@pytest.mark.asyncio
async def test_a_built_swap_is_not_signable_by_default(stub_preflight, stub_jupiter):
    async with client() as c:
        data = await (await post(c)).json()
        b = data["build"]
        assert b["buildable"] is True
        assert b["signable"] is False, "simulate must never be signable"
        assert b["not_signable_reason"]
        assert b["signed"] is False and b["broadcast"] is False


@pytest.mark.asyncio
async def test_naming_mainnet_makes_it_signable(stub_preflight, stub_jupiter,
                                                monkeypatch):
    monkeypatch.setenv("MEME_EXECUTION_NETWORK", "mainnet")
    async with client() as c:
        b = (await (await post(c)).json())["build"]
        assert b["signable"] is True
        assert b["network"] == "mainnet"


@pytest.mark.asyncio
async def test_the_transaction_is_relayed_untouched(stub_preflight, stub_jupiter):
    async with client() as c:
        b = (await (await post(c)).json())["build"]
        assert b["unsigned_transaction"] == "BASE64TX=="


@pytest.mark.asyncio
async def test_the_response_carries_the_market_the_verdict_used(stub_preflight,
                                                                stub_jupiter):
    async with client() as c:
        data = await (await post(c)).json()
        assert data["plan"]["market"]["liquidity_usd"] == 250_000.0


# ── the custody model, asserted ───────────────────────────────────────────

def test_the_handler_takes_no_key_and_calls_nothing_that_signs():
    import inspect

    src = inspect.getsource(ug.handle_meme_swap_build)
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))
    for banned in ("private_key", "secret_key", "keypair", "signer",
                   "mnemonic", "sign(", "sendRawTransaction"):
        assert banned not in code, f"{banned} must not appear in this handler"
    assert "user_public_key" in code, "it needs the wallet that WILL sign"
