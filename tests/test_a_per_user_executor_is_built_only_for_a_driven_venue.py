"""A per-user executor is built only for a venue whose order path was driven.

`/connect` stores keys for every venue `exchange_credentials` knows, and the
engine's resolver built an executor for whichever one the store recorded —
OKX, Gate, KuCoin and Paradex among them. The three key+secret adapters send
a quantity in COINS to venues that count a perp order in CONTRACTS. Driven
with ccxt's own request builders on fabricated markets, 3000 DOGE (a $60
margin at 5x at $0.10):

    OKX     DOGE ctVal 1000   ->  sz=3000 tdMode=cross   = 3,000,000 DOGE
    Gate    DOGE quanto 10    ->  size=3000              =    30,000 DOGE
    KuCoin  DOGE mult 100     ->  size=3000 leverage=1   =   300,000 DOGE
    all three, BTC lot 1      ->  InvalidOrder

and the executor's own minimum gate, on the OKX BTC market, answered "...but
Bitget requires >= $60000.00 notional" — Bitget named on an OKX refusal, with a
minimum computed from one contract read as one coin. The adapter's docstring
said each "MUST pass the existing /venue preflight against a real account
before being enabled for auto-trade"; no such gate existed on the per-user
path.

`PER_USER_EXECUTION_VENUES` is that gate, applied where the engine builds a
per-user executor; the contract-size conversion is filed, not attempted. A
refused venue still links, stores and reads its balance, and the connect card
says orders will not route there.
"""
from __future__ import annotations

import ast
import asyncio
import textwrap
from pathlib import Path
from unittest.mock import patch

import ccxt.async_support as ca
import pytest
from cryptography.fernet import Fernet

from bot.core import exchange_credentials as ec
from bot.core.engine import RuneClawEngine
from bot.core.exchange_credentials import ExchangeCredentialStore
from bot.core.live_executor import LiveExecutor
from bot.core.venues import (
    PER_USER_EXECUTION_VENUES,
    get_venue,
    per_user_execution_refusal,
    valid_venue_ids,
)

ROOT = Path(__file__).resolve().parents[1]

CREDS = {
    "bitget": {"api_key": "BG" * 8, "api_secret": "BS" * 8, "passphrase": "pp"},
    "bybit": {"api_key": "BY" * 8, "api_secret": "YS" * 8},
    "bingx": {"api_key": "BX" * 8, "api_secret": "XS" * 8},
    "hyperliquid": {"wallet_address": "0x" + "a" * 40, "agent_private_key": "0x" + "b" * 64},
    "okx": {"api_key": "OK" * 8, "api_secret": "OS" * 8, "passphrase": "pp"},
    "gate": {"api_key": "GA" * 8, "api_secret": "GS" * 8},
    "kucoin": {"api_key": "KC" * 8, "api_secret": "KS" * 8, "passphrase": "pp"},
    "paradex": {"wallet_address": "0x" + "c" * 40, "agent_private_key": "0x" + "d" * 64},
}


def _store(tmp_path):
    (tmp_path / ".key").write_bytes(Fernet.generate_key())
    return ExchangeCredentialStore(creds_file=str(tmp_path / "creds.enc"),
                                   key_file=str(tmp_path / ".key"))


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = object()  # the shared operator executor, a sentinel
    eng.ws_feed = None
    eng._user_executors = {}
    eng._balance_view_executors = {}
    eng.risk = object()
    eng.slippage = None
    return eng


def _resolve(tmp_path, venue, *, named=False, audits=None):
    store = _store(tmp_path)
    store.set_venue("u1", venue, CREDS[venue])
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=store), \
            patch("bot.core.engine.CONFIG") as cfg, \
            patch("bot.core.engine.audit",
                  lambda *a, **k: (audits.append((a, k)) if audits is not None else None)):
        cfg.per_user_live_enabled = True
        ex = eng._executor_for("u1", venue if named else "")
        refusal = eng.execution_refusal("u1")
    return eng, ex, refusal


# ── the list ───────────────────────────────────────────────────────────────
def test_the_list_is_the_four_venues_whose_orders_were_driven():
    assert PER_USER_EXECUTION_VENUES == {"bitget", "bybit", "bingx", "hyperliquid"}
    assert PER_USER_EXECUTION_VENUES <= set(valid_venue_ids())


@pytest.mark.parametrize("venue", sorted(set(valid_venue_ids()) - PER_USER_EXECUTION_VENUES))
def test_every_other_venue_is_refused_by_name_with_no_date(venue):
    why = per_user_execution_refusal(venue)
    assert why and get_venue(venue).display_name in why
    assert "places no order there" in why
    for promise in ("soon", "coming", "next week", "shortly", "2026", "2027"):
        assert promise not in why.lower(), (venue, promise)


@pytest.mark.parametrize("venue", ["okx", "gate", "kucoin"])
def test_the_contract_venues_say_why(venue):
    assert "contracts where this bot sends coins" in per_user_execution_refusal(venue)


def test_a_driven_venue_is_not_refused():
    for v in PER_USER_EXECUTION_VENUES:
        assert per_user_execution_refusal(v) is None
    # An unknown id is refused, not waved through.
    assert per_user_execution_refusal("ftx")


# ── the resolver ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("venue", sorted(PER_USER_EXECUTION_VENUES))
def test_a_driven_venue_gets_its_executor(tmp_path, venue):
    eng, ex, refusal = _resolve(tmp_path, venue)
    assert isinstance(ex, LiveExecutor) and ex._venue.id == venue
    assert refusal is None


@pytest.mark.parametrize("venue", ["okx", "gate", "kucoin", "paradex"])
@pytest.mark.parametrize("named", [False, True])
def test_a_refused_venue_gets_no_executor_and_never_the_operators(tmp_path, venue, named):
    eng, ex, refusal = _resolve(tmp_path, venue, named=named)
    assert ex is None, "an executor was built for a venue whose order path was never driven"
    assert ex is not eng.live_executor
    assert eng._user_executors == {}
    assert refusal and refusal.endswith("Nothing was placed.")
    assert get_venue(venue).display_name in refusal


def test_the_refusal_is_audited_once(tmp_path):
    audits: list = []
    store = _store(tmp_path)
    store.set_venue("u1", "okx", CREDS["okx"])
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=store), \
            patch("bot.core.engine.CONFIG") as cfg, \
            patch("bot.core.engine.audit", lambda *a, **k: audits.append((a, k))):
        cfg.per_user_live_enabled = True
        for _ in range(4):
            assert eng._executor_for("u1") is None
    refused = [k for _, k in audits if k.get("result") == "REFUSED"]
    assert len(refused) == 1, refused


def test_the_unnamed_readers_answer_unread_rather_than_raise(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.set_venue("u1", "okx", CREDS["okx"])
    eng = _engine()
    with patch("bot.core.exchange_credentials.get_credential_store", return_value=store), \
            patch("bot.core.engine.CONFIG") as cfg:
        cfg.per_user_live_enabled = True
        cfg.is_live = lambda: True
        rc = asyncio.run(eng._live_recheck_context("u1"))
        bal = asyncio.run(eng.get_user_live_equity("u1"))
    assert (rc.equity, rc.open_count, rc.available_usd) == (None, None, None)
    assert bal is None


def test_the_confirm_path_refuses_in_the_venues_words_before_the_recheck():
    """A 400-line confirm behind the risk gate, a venue and Telegram: the claim
    is an ORDER of two calls, so it is asserted as one. Left to the re-check,
    the None executor surfaced as "re-check failed (error logged)"."""
    src = (ROOT / "bot/core/engine.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_confirm_trade_inner")
    calls = [(c.lineno, c.func.attr) for c in ast.walk(fn)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
             and c.func.attr in ("execution_refusal", "_live_recheck_context")]
    order = [name for _, name in sorted(calls)]
    assert order[:2] == ["execution_refusal", "_live_recheck_context"], order


# ── where executors are built ──────────────────────────────────────────────
def _unchecked_constructions(source: str) -> list:
    """Functions that build a LiveExecutor WITH credentials and never ask the
    allow-list. The balance view is read-only by construction and is kept out
    of every monitoring, reconcile and close loop, so it may link any venue."""
    out = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        builds = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                  and ((isinstance(c.func, ast.Name) and c.func.id == "LiveExecutor")
                       or (isinstance(c.func, ast.Attribute) and c.func.attr == "LiveExecutor"))
                  and any(k.arg == "credentials" for k in c.keywords)]
        if not builds or fn.name == "balance_view_executor":
            continue
        asks = any(isinstance(c, ast.Call) and (
            (isinstance(c.func, ast.Name) and c.func.id == "per_user_execution_refusal")
            or (isinstance(c.func, ast.Attribute) and c.func.attr == "per_user_execution_refusal"))
            for c in ast.walk(fn))
        if not asks:
            out.append(fn.name)
    return out


def test_every_per_user_executor_construction_asks_the_list():
    found = []
    for path in (ROOT / "bot").rglob("*.py"):
        found += [f"{path.relative_to(ROOT)}:{n}" for n in _unchecked_constructions(path.read_text())]
    assert found == [], f"a trading executor built without asking the allow-list: {found}"


def test_the_construction_rule_on_planted_trees():
    assert _unchecked_constructions(textwrap.dedent("""
        def builds(user, creds, venue):
            return LiveExecutor(user_id=user, credentials=creds, venue=venue)
    """)) == ["builds"]
    assert _unchecked_constructions(textwrap.dedent("""
        def checks(user, creds, venue):
            if per_user_execution_refusal(venue):
                return None
            return LiveExecutor(user_id=user, credentials=creds, venue=venue)
    """)) == []
    assert _unchecked_constructions(textwrap.dedent("""
        def operator():
            return LiveExecutor()
    """)) == []


# ── the words on the cards ─────────────────────────────────────────────────
def test_the_minimum_gate_names_the_executors_own_venue():
    okx = ca.okx({"apiKey": "k", "secret": "s", "password": "p"})
    okx.set_markets([okx.safe_market_structure(dict(
        id="BTC-USDT-SWAP", symbol="BTC/USDT:USDT", base="BTC", quote="USDT", settle="USDT",
        baseId="BTC", quoteId="USDT", settleId="USDT", type="swap", spot=False, swap=True,
        contract=True, linear=True, inverse=False, contractSize=0.01, active=True,
        precision={"amount": 1, "price": 0.1}, limits={"amount": {"min": 1}, "cost": {"min": 0}},
        info={}))])
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._venue = get_venue("okx")
    try:
        block, _q = ex._exchange_minimum_gate(okx, okx.market("BTC/USDT:USDT"), "BTC/USDT:USDT",
                                              0.005, 60000.0, 5, 60.0)
    finally:
        asyncio.run(okx.close())
    assert block and "OKX requires" in block
    assert "Bitget" not in block


class _Store:
    def set_venue(self, tg_id, venue, fields):
        self.saved = (tg_id, venue)

    def fingerprint(self, tg_id):
        return "ok_****1234"


class _Stub:
    def __init__(self):
        self.sent: list = []
        self.engine = type("E", (), {"invalidate_user_executor": lambda self, t: None})()

    async def _guard(self, update, command="", ctx=None):
        return True

    async def _send(self, update, text, reply_markup=None, edit=False):
        self.sent.append(text)

    def _get_tg_id(self, update):
        return 4242


class _Update:
    class message:
        @staticmethod
        async def delete():
            return None

    class effective_chat:
        type = "private"


class _Ctx:
    def __init__(self, *args):
        self.args = list(args)


def _connect(monkeypatch, *args):
    from bot.skills.telegram_handler import TelegramHandler

    async def _fake_validate(venue, fields, sandbox=False):
        return True, "100.00 USDT free"

    monkeypatch.setattr(ec, "validate_venue_credentials", _fake_validate)
    monkeypatch.setattr(ec, "get_credential_store", lambda: _Store())
    stub = _Stub()
    asyncio.run(TelegramHandler._cmd_connect(stub, _Update(), _Ctx(*args)))
    return stub.sent[-1]


def test_linking_okx_says_no_order_routes_there(monkeypatch):
    out = _connect(monkeypatch, "okx", "OK" * 8, "OS" * 8, "pp")
    assert "OKX account linked" in out
    assert "places no order there" in out
    assert "Nothing was placed" not in out  # the card linked keys; it placed nothing to begin with


def test_linking_bybit_says_nothing_of_the_kind(monkeypatch):
    out = _connect(monkeypatch, "bybit", "BY" * 8, "YS" * 8)
    assert "Bybit account linked" in out
    assert "places no order there" not in out


def test_the_usage_card_names_the_balances_only_venues(monkeypatch):
    out = _connect(monkeypatch)
    for v in sorted(set(valid_venue_ids()) - PER_USER_EXECUTION_VENUES):
        assert get_venue(v).display_name in out.split("linked for balances only")[0], v
    for v in PER_USER_EXECUTION_VENUES:
        line = next(ln for ln in out.split("\n") if "linked for balances only" in ln)
        assert get_venue(v).display_name not in line, v


# ── B6: the prose says what the resolver does ──────────────────────────────
def test_no_surface_says_per_user_accounts_stay_on_bitget():
    for rel in ("bot/skills/engine_ops_commands.py", "bot/core/engine.py", "bot/core/venues.py"):
        text = " ".join((ROOT / rel).read_text().split())
        for claim in ("remain on Bitget", "stay Bitget", "always stay on Bitget"):
            assert claim not in text, (rel, claim)
