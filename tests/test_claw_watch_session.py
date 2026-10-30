"""Session and API-key anomaly alerts. The proof-of-concept cases, offline.

No test here calls the network. A live testnet read stays skipped unless
HL_SESSION_LIVE is set, and CI does not set it. The flag stays off unless
a test passes it in.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

from bot.claw_watch_heuristics import falsealarm as F
from bot.claw_watch_heuristics import hl_events as H
from bot.claw_watch_heuristics import session as S
from bot.claw_watch_heuristics.flag import FLAG, flag_on
from tests.source_scan import code_only

T0 = 1793318400  # 2026-10-30 synthetic, on an hour boundary
BASE = {
    "devices": [["mac-chrome", "AS1136"]],
    "script_origins": ["https://app.example", "https://cdn.rum-vendor.example"],
    "recipients": ["0x" + "aa" * 20],
    "outflows": [100, 120, 90, 110],
    "last_outflow_ts": T0 - 86400,
}
U = "0x" + "11" * 20
NEW = "0x" + "cc" * 20
OLD = "0x" + "aa" * 20
T = 1793318400000
ROOT = Path(__file__).resolve().parents[1]
ON = {FLAG: "1"}


def codes(alerts):
    return [row["code"] for row in alerts]


def raw(agents=(), fills=(), ledger=()):
    return {"user": U, "agents": list(agents), "fills": list(fills), "ledger": list(ledger)}


def fill(i, oid, direction="Close Long", t=T):
    return {"time": t + i * 1000, "dir": direction, "oid": oid, "coin": "BTC", "px": "70000", "sz": "0.001"}


# ── the proof-of-concept session cases ────────────────────────────────────


def test_normal_session_no_alerts():
    events = [
        {
            "type": "session_start", "ts": T0, "session": "s1", "device": "mac-chrome",
            "ip_asn": "AS1136",
            "script_origins": ["https://app.example", "https://cdn.rum-vendor.example"],
        },
        {"type": "withdrawal", "ts": T0 + 60, "session": "s1", "to": "0x" + "aa" * 20, "amount": 100},
    ]
    assert S.analyze(events, BASE) == []


def test_polymarket_style_hijacked_script_replay():
    events = [{
        "type": "session_start", "ts": T0, "session": "s1", "device": "mac-chrome",
        "ip_asn": "AS1136",
        "script_origins": [
            "https://app.example", "https://cdn.rum-vendor.example", "https://evil-cdn.example",
        ],
    }]
    events += [
        {
            "type": "order", "ts": T0 + 10 + i * 20, "session": "s1",
            "side": "sell_position", "reduce_only": True, "notional": 50,
        }
        for i in range(6)
    ]
    events += [{
        "type": "transfer", "ts": T0 + 200, "session": "s1",
        "to": "0x3f99" + "00" * 18, "amount": 2000,
    }]
    found = codes(S.analyze(events, BASE))
    for want in (
        "SCRIPT_ORIGIN_CHANGE", "POSITION_CLOSE_BURST", "FAST_OUTFLOW_AFTER_NEW_ACCESS",
        "OUTFLOW_NEW_RECIPIENT", "OUTFLOW_SPIKE", "COMPOUND_PATTERN",
    ):
        assert want in found, want
    assert found.count("POSITION_CLOSE_BURST") == 1


def test_new_api_key_with_withdraw_scope_then_fast_withdrawal():
    events = [
        {"type": "api_key_created", "ts": T0, "key_id": "k9", "scopes": ["trade", "withdraw"]},
        {"type": "withdrawal", "ts": T0 + 300, "key_id": "k9", "to": "0x" + "aa" * 20, "amount": 100},
    ]
    found = set(codes(S.analyze(events, BASE)))
    assert {"NEW_API_KEY", "API_KEY_WITHDRAW_SCOPE", "FAST_OUTFLOW_AFTER_NEW_ACCESS", "COMPOUND_PATTERN"} <= found
    assert "OUTFLOW_NEW_RECIPIENT" not in found


def test_slow_withdrawal_after_key_is_not_fast():
    events = [
        {"type": "api_key_created", "ts": T0, "key_id": "k9", "scopes": ["trade"]},
        {"type": "withdrawal", "ts": T0 + 3 * 3600, "key_id": "k9", "to": "0x" + "aa" * 20, "amount": 100},
    ]
    assert codes(S.analyze(events, BASE)) == ["NEW_API_KEY"]


def test_new_device_alone_is_info_only():
    events = [{
        "type": "session_start", "ts": T0, "session": "s2", "device": "win-edge",
        "ip_asn": "AS9009", "script_origins": ["https://app.example"],
    }]
    alerts = S.analyze(events, BASE)
    assert codes(alerts) == ["NEW_DEVICE_SESSION"]
    assert alerts[0]["level"] == "info"


def test_dormant_wallet_reactivated_coindcx_style():
    book = dict(BASE, last_outflow_ts=T0 - 200 * 86400)
    events = [{"type": "transfer", "ts": T0, "key_id": "sweeper", "to": "0x" + "aa" * 20, "amount": 100}]
    alerts = S.analyze(events, book)
    assert codes(alerts) == ["DORMANT_WALLET_ACTIVE"]
    assert alerts[0]["detail"]["idle_days"] == 200


def test_four_closes_is_not_a_burst():
    events = [
        {"type": "order", "ts": T0 + i * 30, "session": "s1", "reduce_only": True, "notional": 10}
        for i in range(4)
    ]
    assert S.analyze(events, BASE) == []


def test_wording_has_no_verdicts():
    assert set(S.TEXT) == set(S.LEVEL)
    for sentence in S.TEXT.values():
        low = sentence.lower()
        assert "safe" not in low
        assert "scam" not in low
        assert "malicious" not in low
    assert "safe" not in S.DISCLAIMER.lower()
    assert "scam" not in S.DISCLAIMER.lower()


# ── the proof-of-concept adapter cases ────────────────────────────────────


def test_new_agent_becomes_api_key_event_only_if_unknown():
    body = raw(agents=[{"address": NEW, "name": "x"}, {"address": OLD, "name": "bot"}])
    events = H.to_events(body, known_agents={OLD}, now_ms=T)
    assert [event["key_id"] for event in events if event["type"] == "api_key_created"] == [NEW]


def test_close_fills_map_to_reduce_only_orders_open_fills_ignored():
    events = H.to_events(raw(fills=[fill(0, 1), fill(1, 2, "Open Long"), fill(2, 3, "Close Short")]))
    assert [event["oid"] for event in events] == [1, 3]
    assert all(event["reduce_only"] for event in events)


def test_outbound_ledger_mapped_inbound_skipped():
    ledger = [
        {"time": T, "hash": "h1", "delta": {"type": "withdraw", "usdc": "250.0"}},
        {"time": T + 1, "hash": "h2", "delta": {
            "type": "send", "user": U, "destination": "0x" + "dd" * 20, "amount": "10", "usdcValue": "10",
        }},
        {"time": T + 2, "hash": "h3", "delta": {
            "type": "send", "user": "0x" + "ee" * 20, "destination": U, "amount": "5",
        }},
        {"time": T + 3, "hash": "h4", "delta": {"type": "deposit", "usdc": "100"}},
    ]
    events = H.to_events(raw(ledger=ledger))
    assert [(event["type"], event["amount"]) for event in events] == [("withdrawal", 250.0), ("transfer", 10.0)]


def test_hl_takeover_replay_new_agent_burst_then_withdraw():
    fills = [fill(i * 20, 100 + i) for i in range(6)]
    ledger = [{"time": T + 200_000, "hash": "w", "delta": {"type": "withdraw", "usdc": "5000"}}]
    events = H.to_events(raw(agents=[{"address": NEW}], fills=fills, ledger=ledger), known_agents=set(), now_ms=T)
    found = {alert["code"] for alert in S.analyze(events, {"recipients": ["bridge"], "outflows": [100, 100, 100]})}
    assert {"NEW_API_KEY", "POSITION_CLOSE_BURST", "OUTFLOW_SPIKE", "COMPOUND_PATTERN"} <= found


def test_partial_fills_of_one_order_are_not_a_burst():
    events = H.to_events(raw(fills=[fill(i, 7) for i in range(12)]))
    assert S.analyze(events, {}) == []


def test_own_bot_patterns():
    traces = F.own_bot_traces(T0)

    def hot(name):
        return [
            alert["code"] for alert in S.analyze(traces[name], {})
            if alert["level"] in ("attention", "urgent")
        ]
    assert hot("tp_ladder_partial_fills") == []
    assert hot("kill_switch_flatten_tagged") == []
    assert hot("api_wallet_rotation") == []
    assert hot("five_stops_on_a_wick") == ["POSITION_CLOSE_BURST"]
    assert hot("kill_switch_flatten_untagged") == ["POSITION_CLOSE_BURST"]


def test_busy_account_baseline_raises_threshold_and_cooldown():
    hist = H.to_events(raw(fills=[fill(i * 10, 500 + i, t=T - 86400_000) for i in range(20)]))
    base = H.baseline_from_events(hist)
    assert base["max_closes_5m"] == 20
    today = H.to_events(raw(fills=[fill(i * 10, 900 + i) for i in range(20)]))
    assert S.analyze(today, base) == []
    burst = H.to_events(raw(fills=[fill(i * 5, 1000 + i) for i in range(60)]))
    found = codes(S.analyze(burst, base))
    assert found.count("POSITION_CLOSE_BURST") == 1


# ── the doors the proof of concept did not have ───────────────────────────


def test_the_flag_stays_off_by_default(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert flag_on() is False
    assert flag_on({}) is False
    assert flag_on({FLAG: ""}) is False
    assert flag_on({FLAG: "on"}) is False
    assert flag_on({FLAG: "yes"}) is False
    events = [{"type": "api_key_created", "ts": T0, "key_id": "k9", "scopes": ["withdraw"]}]
    held = S.consider_session(events, BASE, {})
    assert held["enabled"] is False
    assert held["scored"] is False
    assert held["alerts"] is None
    opened = S.consider_session(events, BASE, ON)
    assert opened["scored"] is True
    assert codes(opened["alerts"]) == codes(S.analyze(events, BASE))
    assert flag_on({FLAG: "1"}) is True
    assert flag_on({FLAG: " true "}) is True


def test_unreadable_events_are_not_an_empty_clear():
    out = S.consider_session(None, BASE, ON)
    assert out["scored"] is False
    assert out["alerts"] is None
    bad_base = S.consider_session([], "nope", ON)
    assert bad_base["alerts"] is None
    assert bad_base["sentence"] == S.BASELINE_SENTENCE


def test_an_unreadable_amount_is_not_a_zero_outflow():
    events = [{
        "type": "withdrawal", "ts": T0, "key_id": "k", "to": "0x" + "bb" * 20, "amount": "nope",
    }]
    assert S.analyze(events, BASE) == []


def test_a_zero_amount_is_a_reading():
    events = [{
        "type": "withdrawal", "ts": T0, "key_id": "main", "to": "0x" + "bb" * 20, "amount": 0.0,
    }]
    found = codes(S.analyze(events, BASE))
    assert "OUTFLOW_NEW_RECIPIENT" in found
    assert "OUTFLOW_SPIKE" not in found


def test_a_private_key_is_not_scored_or_echoed(monkeypatch, capsys):
    key = "ab" * 32
    events = [{"type": "api_key_created", "ts": T0, "key_id": key, "scopes": ["withdraw"]}]
    out = S.consider_session(events, BASE, ON)
    assert out["alerts"] is None
    assert out["error_class"] == "Refused"
    assert key not in json.dumps(out)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"events": events, "baseline": BASE})))
    S.main()
    raw_out = capsys.readouterr().out
    assert key not in raw_out
    assert json.loads(raw_out)["alerts"] is None


def test_the_cli_scores_a_quiet_window(monkeypatch, capsys):
    events = [{
        "type": "session_start", "ts": T0, "session": "s1", "device": "mac-chrome",
        "ip_asn": "AS1136",
        "script_origins": ["https://app.example", "https://cdn.rum-vendor.example"],
    }]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"events": events, "baseline": BASE})))
    S.main()
    body = json.loads(capsys.readouterr().out)
    assert body["scored"] is True
    assert body["alerts"] == []
    assert body["disclaimer"] == S.DISCLAIMER


def test_an_off_read_is_not_an_empty_event_list():
    with pytest.raises(RuntimeError):
        H.to_events({"user": U, "status": "off", "agents": None, "fills": None, "ledger": None})


def test_fetch_while_the_flag_is_off_does_not_call(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    called = {"n": 0}

    def reader(_payload):
        called["n"] += 1
        return []

    out = H.fetch(U, 0, 1, fetch_fn=reader, env={})
    assert called["n"] == 0
    assert out["status"] == "off"
    assert out["agents"] is None
    assert out["fills"] is None
    assert out["ledger"] is None
    bare = H.fetch(U, 0, 1, env={})
    assert bare["status"] == "off"


def test_info_refuses_any_host_but_testnet(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(H.RefusedHost):
        H.info({"type": "meta"}, url="https://api.hyperliquid.xyz/info")
    with pytest.raises(H.RefusedHost):
        H.info({"type": "meta"}, url="http://127.0.0.1/info")


def test_falsealarm_runner_is_off_without_the_flag_and_scores_our_bot_offline(tmp_path):
    def boom(*_a, **_k):
        raise AssertionError("network")

    held = F.run(["BTC"], 4, 5, str(tmp_path / "no.json"), info_fn=boom, env={}, sleep_s=0)
    assert held["enabled"] is False
    assert held["sampled"] is None
    assert held["accounts"] is None
    assert not (tmp_path / "no.json").exists()

    def quiet(payload):
        assert payload["type"] == "recentTrades"
        return []

    opened = F.run(
        ["BTC"], 4, 5, str(tmp_path / "yes.json"), info_fn=quiet, env=ON, sleep_s=0, now_ms=T,
    )
    assert opened["enabled"] is True
    assert opened["sampled"] == 0
    assert opened["own_bot"]["tp_ladder_partial_fills"] == []
    assert opened["own_bot"]["five_stops_on_a_wick"] == ["POSITION_CLOSE_BURST"]
    assert opened["own_bot"]["kill_switch_flatten_tagged"] == []
    assert opened["own_bot"]["kill_switch_flatten_untagged"] == ["POSITION_CLOSE_BURST"]
    assert opened["own_bot"]["api_wallet_rotation"] == []
    saved = json.loads((tmp_path / "yes.json").read_text(encoding="utf-8"))
    assert saved["sampled"] == 0


def test_importing_the_adapter_does_not_open_a_socket(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    import importlib

    import bot.claw_watch_heuristics.hl_events as hl
    importlib.reload(hl)


def test_the_new_modules_do_not_sign_or_name_mainnet():
    names = ("session.py", "hl_events.py", "falsealarm.py")
    blob = "\n".join(
        code_only(path.read_text(encoding="utf-8"))
        for path in (ROOT / "bot" / "claw_watch_heuristics").glob("*.py")
        if path.name in names
    )
    for needle in (
        "private_key", "eth_account", "sign_message", "HYPERLIQUID_PRIVATE_KEY",
        "api.hyperliquid.xyz", "requests",
    ):
        assert needle not in blob
    for sentence in S.TEXT.values():
        assert sentence in blob


@pytest.mark.skipif(
    not os.environ.get("HL_SESSION_LIVE"),
    reason="set HL_SESSION_LIVE=1 to read the public testnet info API",
)
def test_live_testnet_falsealarm(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    result = F.run(["BTC"], 2, 1, str(tmp_path / "live.json"), sleep_s=0)
    assert result["enabled"] is True
    assert result["sampled"] is not None
