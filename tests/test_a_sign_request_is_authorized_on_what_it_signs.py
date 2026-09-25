"""`/web3/sign` asks the Authority Envelope about the transaction it signs.

It used to authorize one number and sign another: the envelope was asked about
a client-supplied ``amount_usd`` (the shipped dashboard always sends ``null``)
and a client-supplied ``asset``, while whatever ``value_wei`` the client sent
went into the signature — and the day's spend was the literal 0.0, with
nothing recorded afterwards. Driven under an envelope capped at $1 a trade and
$2 a day, 1000 test ETH were signed and broadcast, twice.

The notional is computed bot-side now, from the signed ``value_wei`` and a
native-coin mark the bot reads; the asset is the network's own coin; the day's
spend is read from the one authority ledger and this transfer is recorded into
it before the signature. The key is planted through the signer's own resolver,
never through the process environment.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.guardian import authority as auth
from bot.web import onchain_value as ov
from bot.web import web3_signer as sg

TG = "42"
DEST = "0x" + "ab" * 20
ETH = 10 ** 18


class _Users:
    def get(self, tg):
        return {"authorized": True, "role": "admin"} if str(tg) == TG else None


class _Exchange:
    def __init__(self, marks):
        self.marks = marks
        self.asked = []

    async def fetch_ticker(self, sym):
        self.asked.append(sym)
        m = self.marks.get(sym)
        if isinstance(m, Exception):
            raise m
        return {"last": m}


class _Engine:
    _pending_ideas: dict = {}

    def __init__(self, marks):
        self.exchange = _Exchange(marks)
        self.calls = 0

    async def get_exchange(self, category="Crypto"):
        self.calls += 1
        return self.exchange


@pytest.fixture
def rig(monkeypatch, tmp_path):
    import bot.guardian.review_queue as rq
    import bot.guardian.user_authority_store as us
    from bot.guardian.authority_ledger import AuthoritySpendLedger
    from bot.web import user_gateway as ug

    monkeypatch.setattr(ug, "_GATEWAY_SECRET", "s" * 32)
    store = us.UserAuthorityStore(str(tmp_path / "ua.json"))
    monkeypatch.setattr(us, "_STORE", store)
    ledger = AuthoritySpendLedger(state_file=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", ledger)
    rows: list = []
    monkeypatch.setattr(rq, "get_review_queue",
                        lambda: SimpleNamespace(record=lambda row: rows.append(row)))
    audits: list = []
    real_audit = ug.audit

    def _audit(logger, msg, **kw):
        audits.append(kw)
        return real_audit(logger, msg, **kw)

    monkeypatch.setattr(ug, "audit", _audit)

    signed: list = []
    broadcast: list = []

    class _Account:
        @staticmethod
        def from_key(_key):
            a = SimpleNamespace(address="0x" + "cd" * 20)

            def sign_transaction(tx):
                signed.append(dict(tx))
                return SimpleNamespace(raw_transaction=b"\x02\xaa", hash=b"\x11" * 32)

            a.sign_transaction = sign_transaction
            return a

    # The key goes in through the signer's resolver — never os.environ, which
    # the secrets vault would mirror to disk at the next import of bot.config.
    monkeypatch.setattr(sg, "_resolve_key", lambda env=None: "0x" + "11" * 32)
    monkeypatch.setattr(sg, "_signing_lib", lambda: _Account)
    monkeypatch.setattr(sg, "rpc_url_for", lambda network, env=None: "http://rpc.stub.invalid")

    async def _no_estimate(**_kw):
        return None

    monkeypatch.setattr(sg, "estimate_tx_gas", _no_estimate)

    async def _bcast(raw, url, chain_id):
        broadcast.append(chain_id)
        return {"ok": True, "tx_hash": "0x" + "22" * 32}

    monkeypatch.setattr(sg, "broadcast", _bcast)

    def bind(**spec):
        base = {"mode": "enforce", "withdraw_allowed": True, "withdraw_allowlist": [DEST]}
        base.update(spec)
        store.bind(TG, auth.compile_envelope(base))

    def app(marks=None):
        engine = _Engine(marks if marks is not None else {"ETH/USDT": 3000.0})
        a = ug.build_gateway(engine, SimpleNamespace(users=_Users()))
        return a, engine

    return SimpleNamespace(ug=ug, ledger=ledger, rows=rows, audits=audits, signed=signed,
                           broadcast=broadcast, bind=bind, app=app)


async def _sign(app, **over):
    body = {"telegram_id": TG, "network": "sepolia", "to": DEST, "value_wei": str(1000 * ETH),
            "nonce": 7, "gas": 30000}
    body.update(over)
    body = {k: v for k, v in body.items() if v is not _ABSENT}
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        r = await c.post("/web3/sign", json=body, headers={"X-Gateway-Secret": "s" * 32})
        return r.status, await r.json()
    finally:
        await c.close()


_ABSENT = object()


# ── the driven figure ───────────────────────────────────────────────────

async def test_a_thousand_eth_against_a_one_dollar_cap_is_refused_and_nothing_signed(rig):
    rig.bind(max_notional_per_trade_usd=1.0, max_notional_daily_usd=2.0)
    app, _engine = rig.app()
    status, d = await _sign(app)
    assert status == 403 and d["error"] == "authority_denied"
    assert "transfer notional $3,000,000.00 exceeds per-trade cap $1.00" in d["reasons"]
    assert rig.signed == [] and rig.broadcast == []
    assert rig.ledger.spent(TG, time.time()) == 0.0


async def test_the_wires_amount_and_asset_are_not_what_is_authorized(rig):
    """The second request of the survey: `amount_usd: 0.5`, `asset: USDC`, and
    the same 1000 ETH. Both fields are ignored; the envelope sees the ETH."""
    rig.bind(max_notional_per_trade_usd=1000.0, symbol_blocklist=["ETH"])
    app, _engine = rig.app()
    status, d = await _sign(app, value_wei=str(ETH // 10_000), amount_usd=0.5, asset="USDC")
    assert status == 403
    assert d["reasons"] == ["ETH is on the authority's blocklist"]
    assert rig.signed == []


# ── the allow path records what it signed ───────────────────────────────

async def test_an_in_cap_transfer_is_signed_recorded_and_reviewed(rig):
    rig.bind(max_notional_per_trade_usd=1.0, max_notional_daily_usd=2.0)
    app, engine = rig.app()
    wei = ETH // 10_000                                   # 0.0001 ETH = $0.30 at $3000
    status, d = await _sign(app, value_wei=str(wei))
    assert status == 200 and d["signed"] is True, d
    assert [t["value"] for t in rig.signed] == [wei], "the envelope's value is the signed value"
    assert engine.exchange.asked == ["ETH/USDT"]
    assert d["notional_usd"] == 0.3 and d["asset"] == "ETH" and d["value_wei"] == str(wei)
    assert rig.ledger.spent(TG, time.time()) == pytest.approx(0.3)

    row = rig.rows[-1]["action"]
    assert row["value_wei"] == str(wei) and row["asset"] == "ETH"
    assert row["amount_usd"] == 0.3 and row["chain_id"] == 11155111
    assert "mainnet mark" in row["amount_basis"]           # the testnet decision, stated
    audit = [a for a in rig.audits if a.get("action") == "web3_sign"][-1]["data"]
    assert audit["value_wei"] == str(wei) and audit["notional_usd"] == 0.3
    assert audit["asset"] == "ETH" and audit["network"] == "sepolia"


async def test_an_exact_retry_is_not_counted_twice_and_a_new_one_is(rig):
    rig.bind(max_notional_per_trade_usd=1.0, max_notional_daily_usd=2.0)
    app, _engine = rig.app()
    wei = ETH // 10_000
    await _sign(app, value_wei=str(wei), nonce=7)
    await _sign(app, value_wei=str(wei), nonce=7)          # same tx: deduped
    assert rig.ledger.spent(TG, time.time()) == pytest.approx(0.3)
    await _sign(app, value_wei=str(wei), nonce=8)
    assert rig.ledger.spent(TG, time.time()) == pytest.approx(0.6)


async def test_the_day_is_read_from_the_ledger_and_the_cap_binds(rig):
    rig.bind(max_notional_per_trade_usd=1.0, max_notional_daily_usd=2.0)
    rig.ledger.record(TG, 1.9, time.time(), ref="earlier")
    app, _engine = rig.app()
    status, d = await _sign(app, value_wei=str(ETH // 10_000))
    assert status == 403
    assert any("would exceed the daily cap $2.00 (already spent $1.90)" in x for x in d["reasons"])
    assert rig.signed == []


# ── unpriced is refused, zero is a measurement ──────────────────────────

@pytest.mark.parametrize("mark", [RuntimeError("venue down"), 0, None, float("nan")])
async def test_an_unpriced_transfer_is_refused_by_name(rig, mark):
    rig.bind(max_notional_per_trade_usd=None, max_notional_daily_usd=None)
    app, _engine = rig.app({"ETH/USDT": mark})
    status, d = await _sign(app, value_wei=str(ETH))
    assert status == 503 and d["error"] == "unpriced"
    assert "ETH" in d["reason"] and d["reason"].endswith("Nothing was signed.")
    assert rig.signed == [] and rig.ledger.spent(TG, time.time()) == 0.0


async def test_a_zero_value_call_is_a_measured_zero_and_needs_no_mark(rig):
    rig.bind(max_notional_per_trade_usd=1.0, max_notional_daily_usd=2.0)
    app, engine = rig.app({"ETH/USDT": RuntimeError("never asked")})
    status, d = await _sign(app, value_wei="0")
    assert status == 200 and d["notional_usd"] == 0.0, d
    assert engine.calls == 0, "a 0-wei call asked the venue for a price it does not need"
    assert rig.ledger.spent(TG, time.time()) == 0.0


async def test_a_testnet_coin_is_priced_at_its_own_mainnet_mark(rig):
    rig.bind(max_notional_per_trade_usd=1.0)
    app, engine = rig.app({"POL/USDT": 0.5})
    status, d = await _sign(app, network="polygon-amoy", value_wei=str(ETH))  # 1 POL
    assert status == 200 and d["asset"] == "POL" and d["notional_usd"] == 0.5, d
    assert engine.exchange.asked == ["POL/USDT"]


@pytest.mark.parametrize("value", [_ABSENT, "-1", "lots"])
async def test_a_value_that_is_not_a_readable_wei_is_refused(rig, value):
    rig.bind()
    app, _engine = rig.app()
    status, _d = await _sign(app, value_wei=value)
    assert status == 400 and rig.signed == []


# ── the ledger's two failures ───────────────────────────────────────────

async def test_an_unreadable_ledger_is_refused_under_a_daily_cap(rig, monkeypatch):
    rig.bind(max_notional_daily_usd=2.0)

    def boom(*_a, **_k):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(rig.ledger, "spent", boom)
    app, _engine = rig.app()
    status, d = await _sign(app, value_wei=str(ETH // 10_000))
    assert status == 403
    assert any("the day's spend under this authority could not be read" in x for x in d["reasons"])
    assert rig.signed == []


async def test_a_spend_that_cannot_be_recorded_is_not_signed(rig, monkeypatch):
    rig.bind(max_notional_per_trade_usd=1.0)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(rig.ledger, "record", boom)
    app, _engine = rig.app()
    status, d = await _sign(app, value_wei=str(ETH // 10_000))
    assert status == 503 and d["error"] == "spend_unrecorded"
    assert rig.signed == [] and rig.broadcast == []


# ── the pure reading ────────────────────────────────────────────────────

def test_the_notional_reading():
    assert ov.notional_usd(0, None) == 0.0
    assert ov.notional_usd(ETH, None) is None
    assert ov.notional_usd(ETH, 3000.0) == 3000.0
    assert ov.notional_usd(-1, 3000.0) is None


def test_every_network_names_its_coin():
    from bot.web import web3_exec_gate as gate
    for name in gate.NETWORKS:
        assert ov.native_asset(name), name
    assert ov.native_asset("polygon-amoy") == "POL"
    assert ov.native_asset("nope") is None
