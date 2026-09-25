"""The Authority Envelope's ceilings bind every value movement, not only a trade.

`authorize` used to return from its withdraw/transfer branch right after the
destination check, so the per-trade cap, the daily cap and the symbol lists
were read for a trade and for nothing else. Driven, under an envelope capped
at $1 a trade and $2 a day with ETH blocklisted and the destination
allowlisted, a transfer of $1,000,000 of ETH on a day already $1,000,000 in
came back `allow` with one check made. Every on-chain producer in the bot asks
as a `transfer` — the testnet signer, the execution preview and the yield
plan's first leg — so the envelope capped none of them.

The trade branch had the other half: under a daily cap it read an UNKNOWN
notional as 0.0, so an auto-sized web order ($99 already spent against a
$100 cap) was authorized and then recorded nothing, because the ledger only
records a notional it has.

`authority._bounds` is the one copy both branches read now, and it refuses an
unknown notional under ANY ceiling, an unread day's spend under the daily cap,
and an unnamed asset under a symbol list.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.guardian import authority as auth

DEST = "0x" + "ab" * 20


def _env(**over):
    spec = {"mode": "enforce", "allowed_venues": ["bitget"],
            "max_notional_per_trade_usd": 1.0, "max_notional_daily_usd": 2.0,
            "withdraw_allowed": True, "withdraw_allowlist": [DEST],
            "symbol_blocklist": ["ETH"]}
    spec.update(over)
    return auth.compile_envelope(spec)


def _xfer(**over):
    a = {"kind": "transfer", "asset": "USDC", "notional_usd": 0.5, "dest": DEST}
    a.update(over)
    return a


# ── the driven figures ──────────────────────────────────────────────────

def test_a_million_dollar_transfer_of_a_blocklisted_asset_is_refused():
    r = auth.authorize(_env(), _xfer(asset="ETH", notional_usd=1_000_000.0),
                       now_ts=1000, spent_today_usd=1_000_000.0)
    assert r["decision"] == "deny"
    joined = " | ".join(r["reasons"])
    assert "blocklist" in joined
    assert "exceeds per-trade cap $1.00" in joined
    assert "would exceed the daily cap $2.00" in joined


@pytest.mark.parametrize("kind", ["transfer", "withdraw"])
def test_each_ceiling_binds_an_outflow_on_its_own(kind):
    # One breach per case, so the kill for a dropped check is that check's.
    env = _env(symbol_blocklist=["PEPE"], max_notional_per_trade_usd=1000,
               max_notional_daily_usd=5000)
    per = auth.authorize(env, _xfer(kind=kind, notional_usd=1500), now_ts=1000,
                         spent_today_usd=0.0)
    assert per["decision"] == "deny"
    assert per["reasons"] == [f"{kind} notional $1,500.00 exceeds per-trade cap $1,000.00"]

    day = auth.authorize(env, _xfer(kind=kind, notional_usd=800), now_ts=1000,
                         spent_today_usd=4500.0)
    assert day["decision"] == "deny"
    assert len(day["reasons"]) == 1 and "daily cap $5,000.00" in day["reasons"][0]

    blk = auth.authorize(env, _xfer(kind=kind, asset="PEPE", notional_usd=100),
                         now_ts=1000, spent_today_usd=0.0)
    assert blk["reasons"] == ["PEPE is on the authority's blocklist"]

    alw = auth.authorize(_env(symbol_blocklist=[], symbol_allowlist=["USDC"],
                              max_notional_per_trade_usd=1000,
                              max_notional_daily_usd=5000),
                         _xfer(kind=kind, asset="ETH", notional_usd=100),
                         now_ts=1000, spent_today_usd=0.0)
    assert alw["decision"] == "deny"
    assert "not in the authorized symbol set" in alw["reasons"][0]

    # The control: an in-bounds outflow to the allowlisted address passes, so
    # the ceilings are ceilings and not a deny-everything.
    ok = auth.authorize(env, _xfer(kind=kind, notional_usd=500), now_ts=1000,
                        spent_today_usd=0.0)
    assert ok["decision"] == "allow", ok["reasons"]


# ── unknown is never zero ───────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["trade", "transfer", "withdraw"])
@pytest.mark.parametrize("caps", [
    {"max_notional_per_trade_usd": 100.0, "max_notional_daily_usd": None},
    {"max_notional_per_trade_usd": None, "max_notional_daily_usd": 100.0},
])
def test_an_unknown_notional_is_refused_under_any_ceiling(kind, caps):
    env = _env(symbol_blocklist=[], **caps)
    action = {"kind": kind, "venue": "bitget", "asset": "BTC", "dest": DEST}
    r = auth.authorize(env, action, now_ts=1000, spent_today_usd=99.0)
    assert r["decision"] == "deny"
    assert any(f"{kind} notional is unknown" in x for x in r["reasons"]), r["reasons"]


def test_the_daily_only_case_was_the_hole():
    """$99 spent against $100, notional None: the old `n = notional or 0.0`
    read that as $99 + $0 and allowed an order of any size."""
    env = _env(symbol_blocklist=[], max_notional_per_trade_usd=None,
               max_notional_daily_usd=100.0)
    r = auth.authorize(env, {"kind": "trade", "venue": "bitget", "asset": "BTC"},
                       now_ts=1000, spent_today_usd=99.0)
    assert r["reasons"] == ["trade notional is unknown — cannot authorize against the daily cap"]


def test_no_ceiling_means_an_unknown_notional_is_not_the_question():
    env = _env(symbol_blocklist=[], max_notional_per_trade_usd=None,
               max_notional_daily_usd=None)
    r = auth.authorize(env, _xfer(notional_usd=None), now_ts=1000)
    assert r["decision"] == "allow", r["reasons"]


def test_a_zero_notional_is_a_measurement():
    # A pure contract call moves no value; $0 is a reading and clears the caps.
    r = auth.authorize(_env(symbol_blocklist=[]), _xfer(notional_usd=0.0),
                       now_ts=1000, spent_today_usd=1.5)
    assert r["decision"] == "allow", r["reasons"]


def test_a_negative_notional_is_not_a_reading():
    """`spent + (-1e6)` would open a daily cap by exactly the amount asked."""
    r = auth.authorize(_env(symbol_blocklist=[]), _xfer(notional_usd=-1_000_000.0),
                       now_ts=1000, spent_today_usd=1.9)
    assert r["decision"] == "deny"
    assert any("notional is unknown" in x for x in r["reasons"])


def test_an_unread_days_spend_is_refused_under_the_daily_cap():
    env = _env(symbol_blocklist=[], max_notional_per_trade_usd=None)
    r = auth.authorize(env, _xfer(notional_usd=0.5), now_ts=1000, spent_today_usd=None)
    assert r["reasons"] == ["the day's spend under this authority could not be read — "
                            "cannot authorize against the daily cap"]
    # A measured zero is a day with nothing spent.
    ok = auth.authorize(env, _xfer(notional_usd=0.5), now_ts=1000, spent_today_usd=0.0)
    assert ok["decision"] == "allow"


def test_an_unnamed_asset_is_refused_under_a_symbol_list():
    r = auth.authorize(_env(), _xfer(asset=None), now_ts=1000, spent_today_usd=0.0)
    assert r["reasons"] == ["transfer asset is not named — cannot check it against "
                            "the authority's symbol list"]
    # Without a list there is nothing to check a name against.
    ok = auth.authorize(_env(symbol_blocklist=[]), _xfer(asset=None), now_ts=1000,
                        spent_today_usd=0.0)
    assert ok["decision"] == "allow"


# ── one copy ────────────────────────────────────────────────────────────

def test_both_branches_read_the_one_bounds_check(monkeypatch):
    """A byte-identical second copy agrees with every fixture and diverges on
    the first edit to either — so plant the one copy and read both kinds."""
    seen = []

    def planted(envelope, action, kind, spent, result):
        seen.append(kind)
        result["reasons"].append("PLANTED")

    monkeypatch.setattr(auth, "_bounds", planted)
    for kind in ("trade", "transfer", "withdraw"):
        r = auth.authorize(_env(), {"kind": kind, "venue": "bitget", "dest": DEST,
                                    "asset": "BTC", "notional_usd": 0.1},
                           now_ts=1000, spent_today_usd=0.0)
        assert "PLANTED" in r["reasons"], kind
    assert seen == ["trade", "transfer", "withdraw"]


# ── C4: the web-live door, driven ──────────────────────────────────────

@pytest.fixture
def web_live(monkeypatch, tmp_path):
    import bot.core.exchange_credentials as ec
    import bot.guardian.user_authority_store as us
    from bot.guardian.authority_ledger import AuthoritySpendLedger
    from bot.web import user_gateway as ug

    store = us.UserAuthorityStore(str(tmp_path / "ua.json"))
    monkeypatch.setattr(us, "_STORE", store)
    ledger = AuthoritySpendLedger(state_file=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", ledger)

    class _Creds:
        def get_venue(self, _tg):
            return "bitget"

    monkeypatch.setattr(ec, "get_credential_store", lambda: _Creds())
    store.bind("web:9", auth.compile_envelope({
        "mode": "enforce", "allowed_venues": ["bitget"],
        "allowed_market_types": ["swap"], "max_notional_daily_usd": 100.0}))
    ledger.record("web:9", 99.0, time.time(), ref="earlier")
    return ug, ledger


def test_an_auto_sized_web_order_under_a_daily_cap_is_refused(web_live):
    ug, ledger = web_live
    engine = SimpleNamespace(_pending_ideas={"T1": SimpleNamespace(asset="BTC/USDT")},
                             _manual_margin_override={})
    ok, reasons = ug._authorize_web_live_trade(None, engine, "web:9", "T1")
    assert ok is False
    assert reasons == ["trade notional is unknown — cannot authorize against the daily cap"]
    assert ledger.spent("web:9", time.time()) == 99.0, "a refusal records nothing"


def test_a_sized_web_order_under_the_cap_is_allowed_and_recorded(web_live):
    ug, ledger = web_live
    engine = SimpleNamespace(_pending_ideas={"T2": SimpleNamespace(asset="BTC/USDT")},
                             _manual_margin_override={"T2": 0.1})
    ok, reasons = ug._authorize_web_live_trade(None, engine, "web:9", "T2")
    assert ok is True, reasons
    assert ledger.spent("web:9", time.time()) > 99.0


# ── F4: the yield plan's authority gate, and the preview handler ────────

def _move(**over):
    m = {"asset": "USDC", "amount_usd": 40.0, "from_chain": "sepolia",
         "delta_apy": 3.0, "breakeven_days": 12, "net_horizon_usd": 1.2,
         "worth": "yes", "custodial": False, "lockup_days": 0}
    m.update(over)
    return m


def test_a_forty_dollar_move_against_a_one_dollar_envelope_is_skipped():
    from bot.guardian.yield_plan import evaluate_yield_move
    env = auth.compile_envelope({"mode": "enforce", "max_notional_per_trade_usd": 1.0,
                                 "withdraw_allowed": True, "withdraw_allowlist": [DEST]})
    d = evaluate_yield_move(move=_move(), to_chain="base-sepolia", dest=DEST,
                            envelope=env, now_ts=1000)
    assert d["verdict"] == "skip"
    assert d["gates"] == {"scanner": True, "policy": True, "authority": False}
    assert d["reasons"] == ["transfer notional $40.00 exceeds per-trade cap $1.00"]


def test_the_plan_says_its_figures_were_the_callers():
    from bot.guardian.yield_plan import CALLER_SUPPLIED_NOTE, evaluate_yield_move
    d = evaluate_yield_move(move=_move(), to_chain="base-sepolia", dest=DEST,
                            envelope=None, now_ts=1000)
    assert d["provenance"] == CALLER_SUPPLIED_NOTE
    assert set(d["supplied_by_caller"]) == {"amount_usd", "delta_apy", "breakeven_days",
                                            "net_horizon_usd", "worth", "custodial",
                                            "lockup_days"}
    assert "did not measure a rate, a cost or a price" in CALLER_SUPPLIED_NOTE


def test_an_unread_days_moves_is_not_a_day_with_nothing_moved():
    from bot.guardian.yield_plan import DEFAULT_YIELD_POLICY, evaluate_yield_policy
    r = evaluate_yield_policy(DEFAULT_YIELD_POLICY, _move(), spent_today_usd=None)
    assert r["verdict"] == "fail"
    assert "today's moves could not be read, so the $150.00 daily cap could not be checked" \
        in r["reasons"]


class _Users:
    def get(self, tg):
        return {"authorized": True, "role": "admin"} if str(tg) == "42" else None


async def _post(app, path, body):
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        r = await c.post(path, json=body, headers={"X-Gateway-Secret": "s" * 32})
        return r.status, await r.json()
    finally:
        await c.close()


@pytest.fixture
def plan_gateway(monkeypatch, tmp_path):
    import bot.guardian.user_authority_store as us
    from bot.guardian.authority_ledger import AuthoritySpendLedger
    from bot.web import user_gateway as ug

    monkeypatch.setattr(ug, "_GATEWAY_SECRET", "s" * 32)
    store = us.UserAuthorityStore(str(tmp_path / "ua.json"))
    monkeypatch.setattr(us, "_STORE", store)
    ledger = AuthoritySpendLedger(state_file=str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ug, "_WEB_LIVE_LEDGER", ledger)
    store.bind("42", auth.compile_envelope({
        "mode": "enforce", "max_notional_daily_usd": 150.0,
        "withdraw_allowed": True, "withdraw_allowlist": [DEST]}))
    app = ug.build_gateway(SimpleNamespace(_pending_ideas={}),
                           SimpleNamespace(users=_Users()))
    return app, ledger


async def test_the_preview_reads_the_days_spend_from_the_ledger(plan_gateway):
    app, ledger = plan_gateway
    body = {"telegram_id": "42", "move": _move(), "to_chain": "base-sepolia", "dest": DEST}
    status, d = await _post(app, "/cross/plan", body)
    assert status == 200 and d["verdict"] == "execute", d["reasons"]
    assert "supplied by the caller" in d["note"]

    # $130 already moved today: $40 more breaks both the policy's $150/day and
    # the envelope's. It used to be measured against the literal 0.0.
    ledger.record("42", 130.0, time.time(), ref="earlier-move")
    status, d = await _post(app, "/cross/plan", body)
    assert d["verdict"] == "skip"
    assert d["gates"]["policy"] is False and d["gates"]["authority"] is False
    assert any("daily total $170.00" in x for x in d["reasons"])
    assert any("daily cap $150.00" in x for x in d["reasons"])


async def test_an_unreadable_ledger_refuses_the_daily_caps_by_name(plan_gateway, monkeypatch):
    app, ledger = plan_gateway

    def boom(*_a, **_k):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(ledger, "spent", boom)
    body = {"telegram_id": "42", "move": _move(), "to_chain": "base-sepolia", "dest": DEST}
    status, d = await _post(app, "/cross/plan", body)
    assert d["verdict"] == "skip"
    assert any("today's moves could not be read" in x for x in d["reasons"])
    assert any("the day's spend under this authority could not be read" in x
               for x in d["reasons"])
