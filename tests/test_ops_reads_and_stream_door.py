"""The bridge's new doors: /parity, /attestation, /redteam and /ws/telemetry.

Two layers, both driven:

  * `bot/web/ops_reads.py` directly: what each payload holds and, as important,
    what it refuses to hold (a dollar, a private seed, a pass it did not earn).
  * `api_bridge.app` through Starlette's test client: that each door is behind
    the dashboard token and the per-IP limiter, and that the WebSocket
    authenticates before it subscribes, streams a real frame end to end, and
    leaves nothing subscribed behind it.

Nothing here reaches a venue: the order-flow read is a stand-in that returns a
real `OrderFlowSignal`, and the red-team child is stubbed except for the one test
that runs the real script (it makes no network call).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))

import api_bridge  # noqa: E402
from bot.web import ops_reads  # noqa: E402
from bot.web import telemetry_stream as ts  # noqa: E402

TOKEN = "bridge-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

#: Words that would mean money in a key of a public payload.
_MONEY_KEY_PARTS = ("usd", "pnl", "net_", "gross", "notional", "fees", "equity", "(?<!im)balance", "cost")


def _walk_keys(obj, out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _walk_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_keys(v, out)
    return out


def _assert_dollar_free(body):
    text = json.dumps(body)
    assert "$" not in text
    for key in _walk_keys(body):
        low = key.lower()
        for part in _MONEY_KEY_PARTS:
            # fee RATES and COUNTS of fee records are published
            # (realized_fee_rate, fees_read...); a bare `fees`/`total_fees` is money.
            if part == "fees" and low in ("fees_read", "fees_stated", "fees_close_leg",
                                          "fees_estimated", "fees_basis_unrecorded"):
                continue
            if low == "fee_drag_of_gross":     # a RATIO: the share of gross profit fees took
                continue
            assert not re.search(part, low), f"{key!r} looks like an amount"


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api_bridge, "_DASHBOARD_TOKEN", TOKEN)
    api_bridge._rate_limits.clear()
    hub = ts.TelemetryHub()
    monkeypatch.setattr(api_bridge, "_telemetry_hub", hub)
    monkeypatch.setattr(api_bridge, "_telemetry_feeds", None)
    ops_reads._reset_red_team_cache()
    # No `with`: the lifespan builds a RuneClawEngine, which is not under test.
    c = TestClient(api_bridge.app)
    c.hub = hub
    yield c
    ops_reads._reset_red_team_cache()
    api_bridge._rate_limits.clear()


# ── the gates ────────────────────────────────────────────────────────────────

ROUTES = ("/parity", "/attestation", "/redteam")


@pytest.mark.parametrize("route", ROUTES)
def test_each_route_refuses_a_missing_or_wrong_token_and_an_unconfigured_bridge(client, monkeypatch, route):
    assert client.get(route).status_code == 401
    assert client.get(route, headers={"Authorization": "Bearer nope"}).status_code == 401
    monkeypatch.setattr(api_bridge, "_DASHBOARD_TOKEN", "")
    assert client.get(route, headers=AUTH).status_code == 503       # same as every gated route


@pytest.mark.parametrize("route", ROUTES)
def test_each_route_is_rate_limited(client, monkeypatch, route):
    """The limiter runs after the token check (a 401 spends no budget), so the
    budget here is spent by a valid caller."""
    async def never_run(_timeout):
        return {"ran": False, "error": "Stubbed"}

    monkeypatch.setattr(ops_reads, "_run_red_team_child", never_run)
    monkeypatch.setattr(api_bridge, "engine", None)
    monkeypatch.setattr(api_bridge, "_RATE_LIMIT", 2)
    codes = [client.get(route, headers=AUTH).status_code for _ in range(4)]
    assert 429 not in codes[:2] and codes[2:] == [429, 429]


def test_every_new_http_route_declares_the_token_gate_and_the_limiter():
    """Reachability is driven above; this pins that a route added to the same
    block cannot forget the gate, by reading the dependencies the app holds."""
    from fastapi.routing import APIRoute

    routes = {r.path: r for r in api_bridge.app.routes if isinstance(r, APIRoute)}
    for path in ROUTES:
        deps = {d.call for d in routes[path].dependant.dependencies}
        assert api_bridge.require_dashboard_token in deps, path
        assert api_bridge._require_rate_limit in deps, path


# ── /parity ──────────────────────────────────────────────────────────────────


def _trade(net, sym="BTC/USDT", fees=0.12):
    return {"symbol": sym, "entry_price": 100.0, "quantity": 1.0, "cost_usd": 10.0,
            "leverage": 10, "pnl_usd": net, "gross_pnl": net + fees, "commission": fees,
            "signal_type": "regime_trend", "strategy_type": "swing", "close_reason": "TP",
            "fill_source": "exchange_fill_history", "fee_basis": "venue"}


def test_parity_publishes_counts_and_ratios_and_not_one_dollar(client, tmp_path, monkeypatch):
    path = tmp_path / "closed.json"
    rows = [_trade(5.0) for _ in range(8)] + [_trade(-3.0) for _ in range(4)]
    path.write_text(json.dumps(rows))
    monkeypatch.setattr("bot.backtest.parity.DEFAULT_TRADES_FILE", str(path))
    r = client.get("/parity", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "read" and body["trades"] == 12
    assert body["wins"] == 8 and body["losses"] == 4
    assert abs(body["win_rate"] - 8 / 12) < 1e-9
    assert body["verdict"]["n"] == 12 and "edge" in body["verdict"]
    _assert_dollar_free(body)
    for forbidden in ("net_pnl", "gross_pnl", "total_fees", "notional", "edge_sentence", "mean_net"):
        assert forbidden not in json.dumps(body)


def test_parity_is_a_whitelist_a_new_summary_key_does_not_leak(monkeypatch):
    """Both arms of the guard: the headline survives, and a dollar key the
    summary grows tomorrow (or a sentence that prints one) does not."""
    import bot.backtest.parity as parity

    real = parity.parity_summary

    def spiced(trades, commission, benchmark=None):
        s = real(trades, commission, benchmark)
        s["brand_new_pnl_usd"] = 123.45
        s["verdict"]["edge_sentence"] = "mean net +$4.20 per trade"
        s["benchmark"]["pooled_net_usd"] = 999.0
        return s

    monkeypatch.setattr(parity, "parity_summary", spiced)
    body = ops_reads.parity_payload("read", [_trade(1.0)] * 3, 0.06)
    assert body["trades"] == 3 and "verdict" in body
    text = json.dumps(body)
    for leaked in ("123.45", "4.20", "999", "brand_new", "edge_sentence", "pooled_net_usd"):
        assert leaked not in text


def test_an_absent_trade_file_is_absent_and_an_unparseable_one_is_a_503(client, tmp_path, monkeypatch):
    monkeypatch.setattr("bot.backtest.parity.DEFAULT_TRADES_FILE", str(tmp_path / "none.json"))
    r = client.get("/parity", headers=AUTH)
    assert r.status_code == 200 and r.json()["state"] == "absent" and "trades" not in r.json()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr("bot.backtest.parity.DEFAULT_TRADES_FILE", str(bad))
    r = client.get("/parity", headers=AUTH)
    assert r.status_code == 503 and r.json()["state"] == "unreadable"
    assert "trades" not in r.json() and "win_rate" not in r.json()

    scalar = tmp_path / "scalar.json"
    scalar.write_text("42")
    assert ops_reads.read_closed_trades(scalar) == ("unreadable", [])


def test_the_wrapper_forms_are_read(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"closed": [_trade(1.0)]}))
    assert ops_reads.read_closed_trades(p)[0] == "read"
    p.write_text(json.dumps({"trades": [_trade(1.0), "junk"]}))
    assert ops_reads.read_closed_trades(p) == ("read", [_trade(1.0)])
    p.write_text("[]")
    assert ops_reads.read_closed_trades(p) == ("read", [])       # a real empty record is a read


# ── /attestation ─────────────────────────────────────────────────────────────


def _seed_file(tmp_path):
    seed = secrets.token_bytes(32)
    p = tmp_path / "attestation_key.bin"
    p.write_bytes(seed)
    return p, seed


def _public_of(seed):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def test_the_public_key_is_the_seeds_and_the_seed_is_never_published(tmp_path):
    p, seed = _seed_file(tmp_path)
    key = ops_reads.read_public_key(p)
    assert key == {"algorithm": "ed25519", "public_key_hex": _public_of(seed), "state": "present"}
    assert seed.hex() not in json.dumps(key)


def test_a_missing_key_is_absent_and_is_not_created_by_the_read(tmp_path):
    p = tmp_path / "attestation_key.bin"
    key = ops_reads.read_public_key(p)
    assert key["state"] == "absent" and key["public_key_hex"] is None
    assert not p.exists()                                   # a read route is not a writer


def test_a_key_file_that_is_not_a_seed_is_unreadable_not_absent(tmp_path):
    p = tmp_path / "k.bin"
    p.write_bytes(b"short")
    assert ops_reads.read_public_key(p)["state"] == "unreadable"


def _chain(tmp_path, n=3):
    from bot.utils.audit_chain import AuditChain
    chain = AuditChain(str(tmp_path / "chain.jsonl"))
    for i in range(n):
        chain.append("TEST", {"i": i})
    return chain


def test_an_intact_chain_is_intact_with_its_tip(tmp_path):
    chain = _chain(tmp_path)
    out = ops_reads.verify_chain(chain)
    assert out["state"] == "intact" and out["entries"] == 3
    assert out["tip"]["sequence"] == 2 and len(out["tip"]["entry_hash"]) == 64


def test_a_tampered_chain_is_broken_and_names_the_line(tmp_path):
    chain = _chain(tmp_path)
    lines = chain.path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["payload"]["i"] = 999                                # edit a recorded payload
    lines[1] = json.dumps(rec)
    chain.path.write_text("\n".join(lines) + "\n")
    out = ops_reads.verify_chain(chain)
    assert out["state"] == "broken" and out["problem_count"] >= 1
    assert any("line 2" in p for p in out["problems"])
    assert len(out["problems"]) <= 10


def test_an_absent_chain_is_absent_and_a_huge_one_is_not_called_intact(tmp_path, monkeypatch):
    from bot.utils.audit_chain import AuditChain
    chain = AuditChain(str(tmp_path / "none.jsonl"))
    assert ops_reads.verify_chain(chain) == {"state": "absent", "entries": 0}
    full = _chain(tmp_path)
    monkeypatch.setattr(ops_reads, "CHAIN_VERIFY_MAX_BYTES", 1)
    out = ops_reads.verify_chain(full)
    assert out["state"] == "too_large" and "entries" not in out


def test_a_verifier_that_raises_is_unreadable_not_intact(tmp_path):
    chain = _chain(tmp_path)

    class Boom:
        path = chain.path

        def verify(self, _p):
            raise RuntimeError("secret=abc123")

    out = ops_reads.verify_chain(Boom())
    assert out == {"state": "unreadable"}


def test_the_chain_verdict_is_cached_until_the_log_changes(tmp_path):
    chain = _chain(tmp_path)
    calls = []
    real = chain.verify
    chain.verify = lambda p: (calls.append(1), real(p))[1]
    ops_reads.verify_chain(chain)
    ops_reads.verify_chain(chain)
    assert len(calls) == 1
    time.sleep(0.01)
    chain.append("TEST", {"i": 9})
    out = ops_reads.verify_chain(chain)
    assert len(calls) == 2 and out["entries"] == 4


def test_the_attestation_route_end_to_end(client, tmp_path, monkeypatch):
    p, seed = _seed_file(tmp_path)
    monkeypatch.setattr("bot.utils.attestation._KEY_PATH", str(p))
    monkeypatch.setattr(api_bridge, "engine", SimpleNamespace(audit_chain=_chain(tmp_path)))
    r = client.get("/attestation", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["key"]["public_key_hex"] == _public_of(seed)
    assert body["chain"]["state"] == "intact"
    assert seed.hex() not in r.text
    _assert_dollar_free(body)


def test_the_attestation_route_without_an_engine_is_a_503(client, monkeypatch):
    monkeypatch.setattr(api_bridge, "engine", None)
    assert client.get("/attestation", headers=AUTH).status_code == 503


# ── /redteam ─────────────────────────────────────────────────────────────────


def _report(passed=2, failed=0):
    scenarios = ([{"name": f"ok{i}", "category": "flash_crash", "passed": True,
                   "trade_idea": {"entry_price": 50000.0}} for i in range(passed)]
                 + [{"name": f"bad{i}", "category": "rr_gaming", "passed": False,
                     "trade_idea": {"entry_price": 50000.0}} for i in range(failed)])
    return {"scenarios": scenarios}


def test_the_summary_is_counts_and_names_and_holds_no_idea():
    out = ops_reads.summarize_red_team(_report(passed=3, failed=1))
    assert out["ran"] is True and out["total_scenarios"] == 4 and out["passed"] == 3
    assert out["failed"] == 1 and out["pass_rate"] == 75.0
    assert out["failed_scenarios"] == ["bad0"]
    assert out["by_category"]["rr_gaming"] == {"passed": 0, "failed": 1}
    assert "50000" not in json.dumps(out) and "trade_idea" not in json.dumps(out)


def test_a_malformed_report_is_not_a_pass():
    assert ops_reads.summarize_red_team({"scenarios": "x"}) == {"ran": False, "error": "MalformedReport"}


def test_the_answer_is_cached_and_concurrent_callers_share_one_run():
    async def go():
        ops_reads._reset_red_team_cache()
        calls = []

        async def runner(_timeout):
            calls.append(1)
            await asyncio.sleep(0.05)
            return ops_reads.summarize_red_team(_report())

        results = await asyncio.gather(*[ops_reads.red_team_payload(runner) for _ in range(5)])
        assert len(calls) == 1
        assert [r["cached"] for r in results].count(False) == 1
        again = await ops_reads.red_team_payload(runner)
        assert len(calls) == 1 and again["cached"] is True
    asyncio.run(go())
    ops_reads._reset_red_team_cache()


def test_a_failed_run_is_retried_after_its_short_ttl_not_its_long_one():
    async def go():
        ops_reads._reset_red_team_cache()
        calls = []

        async def runner(_timeout):
            calls.append(1)
            return {"ran": False, "error": "TimeoutError"}

        first = await ops_reads.red_team_payload(runner, ttl_s=1000, failure_ttl_s=0.05)
        again = await ops_reads.red_team_payload(runner, ttl_s=1000, failure_ttl_s=0.05)
        assert first["ran"] is False and again["cached"] is True and len(calls) == 1
        await asyncio.sleep(0.08)
        await ops_reads.red_team_payload(runner, ttl_s=1000, failure_ttl_s=0.05)
        assert len(calls) == 2
    asyncio.run(go())
    ops_reads._reset_red_team_cache()


def test_the_child_does_not_inherit_a_credential():
    env = {"PATH": "/bin", "DASHBOARD_TOKEN": "t", "BITGET_API_KEY": "k", "WEB_CREDS_KEY": "c",
           "HOME": "/root", "DB_PASSWORD": "p", "TELEGRAM_BOT_TOKEN": "b", "LANG": "C"}
    assert ops_reads.child_env(env) == {"PATH": "/bin", "HOME": "/root", "LANG": "C"}


def test_the_redteam_route_is_a_503_when_the_run_could_not_complete(client, monkeypatch):
    async def broken(_timeout):
        return {"ran": False, "error": "TimeoutError"}

    monkeypatch.setattr(ops_reads, "_run_red_team_child", broken)
    r = client.get("/redteam", headers=AUTH)
    assert r.status_code == 503 and r.json()["ran"] is False and r.json()["error"] == "TimeoutError"


def test_the_redteam_route_reports_a_clean_run(client, monkeypatch):
    async def fine(_timeout):
        return ops_reads.summarize_red_team(_report(passed=5))

    monkeypatch.setattr(ops_reads, "_run_red_team_child", fine)
    r = client.get("/redteam", headers=AUTH)
    assert r.status_code == 200 and r.json()["failed"] == 0 and r.json()["total_scenarios"] == 5
    _assert_dollar_free(r.json())


def test_the_real_script_runs_in_a_child_and_every_scenario_is_refused():
    """The one real run: `scripts/red_team.py --json` as a child process, the
    way the route runs it. It reaches no network."""
    body = asyncio.run(ops_reads._run_red_team_child(120.0))
    assert body["ran"] is True, body
    assert body["total_scenarios"] >= 30
    assert body["failed"] == 0 and body["failed_scenarios"] == []


def test_a_child_that_cannot_start_or_times_out_is_not_a_pass(monkeypatch):
    async def go():
        monkeypatch.setattr(ops_reads, "RED_TEAM_SCRIPT", ops_reads.REPO_ROOT / "scripts" / "no_such.py")
        missing = await ops_reads._run_red_team_child(30.0)
        assert missing == {"ran": False, "error": "HarnessError"}

        real_spawn = asyncio.create_subprocess_exec

        async def spawn_sleeper(*_a, **_k):
            return await real_spawn("sleep", "30", stdout=asyncio.subprocess.PIPE)

        monkeypatch.setattr(ops_reads.asyncio, "create_subprocess_exec", spawn_sleeper)
        slow = await ops_reads._run_red_team_child(0.1)
        assert slow == {"ran": False, "error": "TimeoutError"}
    asyncio.run(go())


# ── /ws/telemetry, through the real app ──────────────────────────────────────


def _recv(ws, want, limit=20):
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg["type"] == want:
            return msg
    raise AssertionError(f"no {want!r} frame")


def test_the_socket_refuses_an_unconfigured_bridge_and_a_wrong_token(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect("/ws/telemetry", headers={"Authorization": "Bearer nope"}) as ws:
        with pytest.raises(WebSocketDisconnect) as gone:
            ws.receive_text()
    assert gone.value.code == ts.CLOSE_POLICY
    assert client.hub.subscribers == 0

    monkeypatch.setattr(api_bridge, "_DASHBOARD_TOKEN", "")
    with client.websocket_connect("/ws/telemetry", headers=AUTH) as ws:
        with pytest.raises(WebSocketDisconnect) as gone:
            ws.receive_text()
    assert gone.value.code == ts.CLOSE_POLICY


def test_the_socket_is_rate_limited_before_it_is_accepted(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(api_bridge, "_check_rate_limit", lambda _ip: False)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/telemetry", headers=AUTH):
            pass


def test_an_authenticated_socket_says_hello_answers_a_ping_and_cleans_up(client):
    with client.websocket_connect("/ws/telemetry", headers=AUTH) as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["topics"] == ["risk"]
        ws.send_text(json.dumps({"op": "ping"}))
        assert _recv(ws, "pong")["type"] == "pong"
    deadline = time.monotonic() + 3
    while client.hub.subscribers and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.hub.subscribers == 0


def test_the_first_message_authenticates_when_there_is_no_header(client):
    with client.websocket_connect("/ws/telemetry") as ws:
        ws.send_text(json.dumps({"op": "auth", "token": TOKEN}))
        assert json.loads(ws.receive_text())["type"] == "hello"


def test_an_orderbook_frame_arrives_end_to_end_with_ratios_and_no_amounts(client, monkeypatch):
    from bot.core.order_flow import OrderFlowSignal

    async def fake_fetch(symbol):
        return OrderFlowSignal(
            symbol=symbol, components_ok=["book", "trades"], spread_bps=2.5,
            book_imbalance=0.4, bid_depth_usd=900000.0, ask_depth_usd=300000.0,
            bid_depth_top_usd=90000.0, ask_depth_top_usd=30000.0, mid_price=64000.0,
            aggressor_ratio=0.55, buy_volume_usd=777777.0, confidence=0.9)

    monkeypatch.setattr(api_bridge, "_orderbook_fetch", fake_fetch)
    feeds = ts.TelemetryFeeds(client.hub, lambda s: api_bridge._orderbook_fetch(s),
                              ts.RiskLogTailer("/nonexistent/risk.jsonl"),
                              orderbook_interval_s=0.05, risk_interval_s=0.05)
    monkeypatch.setattr(api_bridge, "_telemetry_feeds", feeds)
    url = "/ws/telemetry?topics=orderbook&symbols=BTC/USDT"
    with client.websocket_connect(url, headers=AUTH) as ws:
        frame_text = None
        for _ in range(20):
            raw = ws.receive_text()
            if json.loads(raw)["type"] == "orderbook":
                frame_text = raw
                break
    assert frame_text is not None
    frame = json.loads(frame_text)
    assert frame["symbol"] == "BTC/USDT" and frame["read"] is True
    assert frame["spread_bps"] == 2.5 and frame["book_imbalance"] == 0.4
    assert frame["depth_ratio_top"] == 3.0 and frame["aggressor_ratio"] == 0.55
    for amount in ("900000", "300000", "64000", "777777"):
        assert amount not in frame_text
    _assert_dollar_free(frame)


def test_a_risk_alert_the_bot_logs_reaches_a_connected_socket(client, tmp_path, monkeypatch):
    log = tmp_path / "risk.jsonl"
    log.write_text("")
    feeds = ts.TelemetryFeeds(client.hub, lambda s: api_bridge._orderbook_fetch(s),
                              ts.RiskLogTailer(log), orderbook_interval_s=0.05, risk_interval_s=0.02)
    monkeypatch.setattr(api_bridge, "_telemetry_feeds", feeds)
    line = json.dumps({
        "ts": "2026-10-27T10:00:00.000000Z", "level": "INFO", "channel": "runeclaw.risk",
        "message": "Risk REJECTED for ETH/USDT [3P/1F]", "action": "risk_check",
        "result": "REJECTED",
        "data": {"checks_failed": ["Drawdown: equity $9,100 under the $9,500 floor"]}}) + "\n"
    with client.websocket_connect("/ws/telemetry", headers=AUTH) as ws:
        assert json.loads(ws.receive_text())["type"] == "hello"   # the first read has happened
        with open(log, "a") as fh:
            fh.write(line)
        alert_text = None
        for _ in range(40):
            raw = ws.receive_text()
            if json.loads(raw).get("kind") == "rejection":
                alert_text = raw
                break
    assert alert_text is not None
    alert = json.loads(alert_text)
    assert alert["gates"] == ["Drawdown"] and alert["asset"] == "ETH/USDT"
    assert "9,100" not in alert_text and "9,500" not in alert_text
