"""`/memeplan` — the meme path, and what "wiring" it could and could not mean.

THE REQUEST WAS "WIRE THE MEME EXECUTION PATH". THERE IS NO EXECUTION TO WIRE.

`bot/core/meme_executor.py` is a PLANNER. `would_execute` is a hardcoded False
and its docstring says signing is "a later, separately-gated slice" that does
not exist. `bot/core/validation_gate.py` opens by calling itself a stub not
wired to the backtest harness — a different concern (recording validation runs)
that has nothing to do with trading. And the integrity veto's *enforce* half
consumes an executor that cannot execute.

So what was unreachable was a fail-closed PREFLIGHT: "would this buy clear
every precondition, and if not, which one stopped it". That is diligence, and
it is what this command exposes. Nothing here can move money, and the tests
below are mostly about keeping it that way.

THREE FAIL-CLOSED PRECONDITIONS, none of which this command can satisfy on the
user's behalf: the MEME_TRADING_ENABLED flag (default OFF), a human-set
Authority Envelope in enforce mode, and the rug/liquidity/exit safety gate.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core import meme_executor
from bot.skills.telegram_handler import TelegramHandler

MINT = "So11111111111111111111111111111111111111112"


class _Stub:
    _SOL_MINT_RE = TelegramHandler._SOL_MINT_RE

    def __init__(self, allowed=True):
        self.sent: list = []
        self.errors: list = []
        self._allowed = allowed

    async def _guard(self, update, command="", ctx=None):
        return self._allowed

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    async def _send_error(self, update, command_name, exc):
        self.errors.append((command_name, exc))

    def _get_tg_id(self, update):
        return 4242


class _Ctx:
    def __init__(self, *args):
        self.args = list(args)


def _run(stub, *args):
    return asyncio.run(TelegramHandler._cmd_memeplan(stub, object(), _Ctx(*args)))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """The command builds its own DexScreenerSource, which would really fetch.

    A test that needs a venue to be up fails for reasons that have nothing to
    do with this repository — the same argument DexScreenerSource's own
    docstring makes for injecting its transport.
    """
    import bot.core.token_sources as ts

    class _Fake:
        name = "dexscreener"
        requires_key = False

        def available(self):
            return True

        async def fetch(self, chain, address):
            return {}

    monkeypatch.setattr(ts, "DexScreenerSource", _Fake)


# ── the property that matters more than any other ─────────────────────────

def test_the_planner_can_never_say_it_would_execute():
    """`would_execute` is the whole safety boundary of this slice.

    A plan that passes every precondition still does not trade — signing is a
    separate slice that does not exist. If this ever returns True, something
    has grown the ability to move money without anyone deciding it should.
    """
    for side in ("buy", "sell"):
        plan = meme_executor.plan_swap(
            intent={"side": side, "token_mint": MINT, "size_usd": 25.0},
            envelope_authorized=True, feature_on=True,
            safety_report={"verdict": "safe", "checks": []},
            market={"liquidity_usd": 5_000_000.0, "age_hours": 5000.0,
                    "buys_24h": 900.0, "sells_24h": 800.0})
        assert plan["would_execute"] is False
    assert "would_execute: False" in meme_executor.human_readable(plan)


def test_the_command_never_reports_a_trade_as_placed(monkeypatch):
    stub = _Stub()
    _run(stub, MINT, "25")
    body = stub.sent[0] if stub.sent else ""
    for word in ("executed", "submitted", "filled", "order placed"):
        assert word not in body.lower(), f"the preflight implied it traded: {word}"


# ── the three fail-closed preconditions ───────────────────────────────────

def test_the_feature_flag_defaults_off_and_blocks(monkeypatch):
    monkeypatch.delenv("MEME_TRADING_ENABLED", raising=False)
    plan = meme_executor.plan_swap(
        intent={"side": "buy", "token_mint": MINT, "size_usd": 25.0},
        envelope_authorized=True,
        safety_report={"verdict": "safe", "checks": []},
        market={"liquidity_usd": 5_000_000.0, "age_hours": 5000.0,
                "buys_24h": 900.0, "sells_24h": 800.0})
    assert plan["allowed"] is False
    assert any(not c["ok"] and "feature" in c["name"] for c in plan["preconditions"])


def test_no_envelope_blocks_even_with_the_flag_on():
    plan = meme_executor.plan_swap(
        intent={"side": "buy", "token_mint": MINT, "size_usd": 25.0},
        envelope_authorized=False, feature_on=True,
        safety_report={"verdict": "safe", "checks": []},
        market={"liquidity_usd": 5_000_000.0, "age_hours": 5000.0,
                "buys_24h": 900.0, "sells_24h": 800.0})
    assert plan["allowed"] is False
    assert any(not c["ok"] and c["name"] == "envelope_authorized"
               for c in plan["preconditions"])


def test_an_unreadable_envelope_is_not_an_authorizing_one(monkeypatch):
    """The command reads the store in a try/except. A store that raises must
    not be read as consent — that is the direction that costs money."""
    import bot.guardian.user_authority_store as store

    def boom():
        raise RuntimeError("store unreadable")
    monkeypatch.setattr(store, "get_user_authority_store", boom)
    stub = _Stub()
    _run(stub, MINT, "25")
    assert stub.sent and "envelope" in stub.sent[0].lower()


# ── input handling: nothing checked must not read as nothing found ────────

def test_no_argument_explains_itself_and_checks_nothing():
    stub = _Stub()
    _run(stub)
    assert "Usage" in stub.sent[0]
    assert "never trades" in stub.sent[0]


@pytest.mark.parametrize("bad", [
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",   # an EVM address, wrong chain
    "not a mint",
    "short",
])
def test_a_non_solana_mint_is_refused_before_any_request(bad):
    stub = _Stub()
    _run(stub, bad)
    assert "Nothing was checked" in stub.sent[0], (
        "a rejected input must not read like a completed clean preflight")


def test_a_bad_size_is_refused_rather_than_defaulted():
    stub = _Stub()
    _run(stub, MINT, "twenty-five")
    assert "number" in stub.sent[0].lower()


def test_the_auth_gate_runs_before_anything_is_fetched():
    stub = _Stub(allowed=False)
    _run(stub, MINT, "25")
    assert stub.sent == []


# ── the gate's own fail-closed behaviour on missing market data ───────────

def test_an_unreadable_market_does_not_clear_the_gate():
    """A pool we cannot measure is not a pool we have cleared.

    DexScreener may report no txn counts at all; the gate must treat that as
    unknown and refuse, not as "nobody sold" — which is the shape of a pool you
    can buy into and never leave.
    """
    plan = meme_executor.plan_swap(
        intent={"side": "buy", "token_mint": MINT, "size_usd": 25.0},
        envelope_authorized=True, feature_on=True,
        safety_report={"verdict": "safe", "checks": []},
        market={})
    assert plan["allowed"] is False


def test_a_sell_is_never_blocked_by_the_safety_gate():
    """Being able to dump a rug IS the safety property."""
    plan = meme_executor.plan_swap(
        intent={"side": "sell", "token_mint": MINT, "size_usd": 25.0},
        envelope_authorized=True, feature_on=True,
        safety_report={"verdict": "danger", "checks": []},
        market={})
    gate_checks = [c for c in plan["preconditions"] if c["name"] == "safety_gate"]
    assert gate_checks and gate_checks[0]["ok"] is True


# ── registration, the three gates that caught /token ──────────────────────

def test_registered_catalogued_and_permitted():
    import inspect

    from bot.skills.command_catalog import all_entries
    from bot.utils.user_store import ROLE_PERMISSIONS

    assert '("memeplan", self._cmd_memeplan)' in inspect.getsource(TelegramHandler)
    assert "memeplan" in set(all_entries()), "/memeplan would be word-of-mouth only"
    for role in ("trader", "paper", "viewer"):
        assert "memeplan" in ROLE_PERMISSIONS[role], f"{role} cannot reach it"
