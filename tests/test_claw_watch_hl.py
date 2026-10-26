"""Hyperliquid account watch. The prototype's offline cases, plus the bot's doors.

No test here calls the network. The live case stays skipped unless both
HL_LIVE and the mainnet read flag are set, and CI sets neither.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from bot.claw_watch_hl import watch as H
from bot.claw_watch_hl.client import MAINNET_INFO, TESTNET_INFO, info_url
from bot.claw_watch_hl.copy import NOT_AVAILABLE, NOT_FOUND
from bot.claw_watch_hl.flag import FLAG, MAINNET_FLAG, flag_on, mainnet_read_on
from bot.claw_watch_hl.hook import read_claw_watch
from bot.claw_watch_hl.service import handle_command, start_poll
from tests.source_scan import code_only

NOW = 1792915200000
U = "0x" + "11" * 20
BASE = {
    "user": U, "taken_ms": NOW,
    "agents": {"0x" + "aa" * 20: {"name": "bot", "validUntil": NOW + 30 * 86400000}},
    "builders": {"0x" + "bb" * 20: 10}, "role": "user", "subAccounts": [],
    "referredBy": None, "accountValue": "1000.0", "withdrawable": "500.0",
    "ledger": [],
}
SALT = "s" * 32
ROOT = Path(__file__).resolve().parents[1]


def codes(alerts):
    return {x["code"] for x in alerts}


def _file_once(user, state_path, now_ms=None, fetch=None, env=None):
    """The prototype's file-backed one-shot. The bot stores snapshots in sqlite."""
    try:
        with open(state_path, encoding="utf-8") as fh:
            old = json.load(fh)
        if not isinstance(old, dict):
            old = None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        old = None
    new = H.snapshot(user, now_ms, fetch, env)
    alerts = H.diff(old, new)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(new, fh, indent=1)
    digest = hashlib.sha256(
        json.dumps(new, sort_keys=True, default=str).encode()
    ).hexdigest()
    return {"baseline": old is None, "alerts": alerts, "snapshot_sha256": digest}


def _env(tmp_path, **extra):
    return {
        FLAG: "1",
        "CLAW_WATCH_HL_ID_SALT": SALT,
        "CLAW_WATCH_HL_STORE": str(tmp_path / "hl.sqlite"),
        **extra,
    }


# ── the prototype's offline cases ─────────────────────────────────────────


def test_baseline_info_only():
    alerts = H.diff(None, BASE)
    assert codes(alerts) == {"HL_API_WALLET_PRESENT", "HL_BUILDER_APPROVAL_PRESENT"}
    assert all(x["level"] == "info" for x in alerts)


def test_no_change_no_alerts():
    assert H.diff(BASE, copy.deepcopy(BASE)) == []


def test_new_agent_without_expiry():
    nxt = copy.deepcopy(BASE)
    nxt["agents"]["0x" + "cc" * 20] = {"name": "", "validUntil": None}
    assert {"HL_NEW_API_WALLET", "HL_AGENT_NO_EXPIRY"} <= codes(H.diff(BASE, nxt))


def test_agent_removed_and_expiry_soon():
    nxt = copy.deepcopy(BASE)
    nxt["agents"] = {"0x" + "dd" * 20: {"name": "x", "validUntil": NOW + 86400000}}
    got = codes(H.diff(BASE, nxt))
    assert {"HL_API_WALLET_REMOVED", "HL_NEW_API_WALLET", "HL_AGENT_EXPIRES_SOON"} <= got


def test_builder_new_at_cap_and_raise():
    nxt = copy.deepcopy(BASE)
    nxt["builders"]["0x" + "ee" * 20] = 100
    nxt["builders"]["0x" + "bb" * 20] = 50
    got = codes(H.diff(BASE, nxt))
    assert {"HL_NEW_BUILDER_APPROVAL", "HL_BUILDER_FEE_AT_PERP_CAP", "HL_BUILDER_FEE_CHANGED"} <= got


def test_role_value_drop_and_withdraw():
    nxt = copy.deepcopy(BASE)
    nxt["role"] = "agent"
    nxt["accountValue"] = "100.0"
    nxt["ledger"] = [
        {"time": NOW, "hash": "0x1", "delta": {"type": "withdraw", "usdc": "900.0"}},
        {"time": NOW, "hash": "0x2", "delta": {"type": "deposit", "usdc": "1.0"}},
        {"time": NOW, "hash": "0x3", "delta": {
            "type": "send", "amount": "5", "token": "USDC",
            "destination": "0x" + "99" * 20,
        }},
    ]
    alerts = H.diff(BASE, nxt)
    assert {"HL_ROLE_CHANGED", "HL_ACCOUNT_VALUE_DROP_50PCT", "HL_FUNDS_MOVED_OUT"} <= codes(alerts)
    assert sum(1 for x in alerts if x["code"] == "HL_FUNDS_MOVED_OUT") == 2


def test_no_verdict_words():
    nxt = copy.deepcopy(BASE)
    nxt["agents"]["0x" + "cc" * 20] = {"name": "", "validUntil": None}
    text = json.dumps(H.diff(None, nxt) + H.diff(BASE, nxt)).lower()
    for word in ("scam", " safe", "malicious", "fraud"):
        assert word not in text


def test_snapshot_with_fake_fetch(tmp_path):
    resp = {
        "extraAgents": [{"name": "Mobile QR", "address": "0xAB", "validUntil": NOW + 1}],
        "approvedBuilders": ["0xBB"], "maxBuilderFee": 10, "userRole": {"role": "user"},
        "subAccounts": None, "referral": {"referredBy": None},
        "clearinghouseState": {
            "marginSummary": {"accountValue": "1"}, "withdrawable": "0",
        },
        "userNonFundingLedgerUpdates": [],
    }
    snap = H.snapshot(U, NOW, fetch=lambda payload: resp[payload["type"]])
    assert snap["agents"]["0xab"]["name"] == "Mobile QR"
    assert snap["builders"] == {"0xbb": 10}
    report = _file_once(U, str(tmp_path / "s.json"), NOW, fetch=lambda payload: resp[payload["type"]])
    assert report["baseline"]
    assert len(report["snapshot_sha256"]) == 64


@pytest.mark.skipif(
    not os.environ.get("HL_LIVE") or not mainnet_read_on(),
    reason="set HL_LIVE=1 and CLAW_WATCH_HL_MAINNET_INFO=1 for a live public-API read",
)
def test_live_public_address(tmp_path):
    report = _file_once("0x3bcae23e8c380dab4732e9a159c0456f12d866f3", str(tmp_path / "s.json"))
    assert report["baseline"]
    assert any(x["code"] == "HL_API_WALLET_PRESENT" for x in report["alerts"])
    again = _file_once("0x3bcae23e8c380dab4732e9a159c0456f12d866f3", str(tmp_path / "s.json"))
    assert not again["baseline"]


# ── host, honesty, and the doors the prototype did not have ──────────────


def test_info_host_is_testnet_unless_the_subflag_is_on():
    assert info_url({}) == TESTNET_INFO
    assert info_url({MAINNET_FLAG: "yes"}) == TESTNET_INFO
    assert info_url({MAINNET_FLAG: "on"}) == TESTNET_INFO
    assert info_url({MAINNET_FLAG: "1"}) == MAINNET_INFO
    assert info_url({MAINNET_FLAG: "true"}) == MAINNET_INFO
    assert flag_on({}) is False
    assert flag_on({FLAG: "yes"}) is False
    assert flag_on({FLAG: "1"}) is True
    assert mainnet_read_on({}) is False


def test_mainnet_url_is_only_named_in_the_client():
    for path in (ROOT / "bot" / "claw_watch_hl").rglob("*.py"):
        if path.name == "client.py":
            continue
        assert "api.hyperliquid.xyz" not in path.read_text(encoding="utf-8")


def test_the_package_does_not_sign_or_name_a_key():
    blob = "\n".join(
        code_only(path.read_text(encoding="utf-8"))
        for path in (ROOT / "bot" / "claw_watch_hl").rglob("*.py")
    )
    for needle in ("private_key", "eth_account", "sign_message", "HYPERLIQUID_PRIVATE_KEY"):
        assert needle not in blob


def test_unreadable_account_value_is_not_a_drop():
    nxt = copy.deepcopy(BASE)
    nxt["accountValue"] = None
    assert "HL_ACCOUNT_VALUE_DROP_50PCT" not in codes(H.diff(BASE, nxt))
    zero = copy.deepcopy(BASE)
    zero["accountValue"] = "0.0"
    assert "HL_ACCOUNT_VALUE_DROP_50PCT" in codes(H.diff(BASE, zero))


def test_a_transfer_to_the_account_or_a_subaccount_is_not_outbound():
    nxt = copy.deepcopy(BASE)
    sub = "0x" + "ab" * 20
    nxt["subAccounts"] = [sub]
    nxt["ledger"] = [
        {"time": NOW, "hash": "0x1", "delta": {
            "type": "send", "amount": "1", "token": "USDC", "destination": U,
        }},
        {"time": NOW, "hash": "0x2", "delta": {
            "type": "spotTransfer", "amount": "2", "token": "USDC", "destination": sub,
        }},
        {"time": NOW, "hash": "0x3", "delta": {
            "type": "send", "amount": "3", "token": "USDC",
            "destination": "0x" + "99" * 20,
        }},
    ]
    alerts = [x for x in H.diff(BASE, nxt) if x["code"] == "HL_FUNDS_MOVED_OUT"]
    assert len(alerts) == 1
    assert "99" in alerts[0]["text"]


def test_a_small_position_change_is_quiet_and_a_sudden_one_is_not():
    old = copy.deepcopy(BASE)
    old["positions"] = {"BTC": {"szi": "1.0", "marginUsed": "10"}}
    old["marginUsed"] = "100"
    small = copy.deepcopy(old)
    small["positions"] = {"BTC": {"szi": "1.1", "marginUsed": "10"}}
    small["marginUsed"] = "110"
    assert codes(H.diff(old, small)) == set()
    sudden = copy.deepcopy(old)
    sudden["positions"] = {"BTC": {"szi": "0.4", "marginUsed": "10"}}
    sudden["marginUsed"] = "40"
    got = codes(H.diff(old, sudden))
    assert "HL_POSITION_CHANGED" in got
    assert "HL_MARGIN_CHANGED" in got


def test_an_unread_margin_is_not_zero():
    old = copy.deepcopy(BASE)
    old["marginUsed"] = "100"
    old["positions"] = {}
    nxt = copy.deepcopy(old)
    nxt["marginUsed"] = None
    assert "HL_MARGIN_CHANGED" not in codes(H.diff(old, nxt))


def test_many_agents_on_a_baseline_are_counted():
    snap = copy.deepcopy(BASE)
    snap["agents"] = {
        "0x" + f"{i:040x}": {"name": "", "validUntil": NOW + 30 * 86400000}
        for i in range(20)
    }
    alerts = H.diff(None, snap)
    present = [a for a in alerts if a["code"] == "HL_API_WALLET_PRESENT"]
    assert present == []
    summary = [a for a in alerts if a["code"] == "HL_API_WALLETS_PRESENT"]
    assert summary and summary[0]["count"] == 20


def test_a_zero_transfer_amount_is_shown():
    nxt = copy.deepcopy(BASE)
    nxt["ledger"] = [{
        "time": NOW, "hash": "0x1",
        "delta": {"type": "withdraw", "usdc": "0"},
    }]
    alerts = [a for a in H.diff(BASE, nxt) if a["code"] == "HL_FUNDS_MOVED_OUT"]
    assert len(alerts) == 1
    assert " 0 " in alerts[0]["text"]


def test_an_unread_claw_watch_is_not_an_empty_list(monkeypatch):
    monkeypatch.setattr("bot.drain_watch.service.get_service", lambda: None)
    read = read_claw_watch("123")
    assert read.present is True
    assert read.addresses is None


def test_claw_watch_addresses_come_from_its_store(tmp_path, monkeypatch):
    from bot.daily_duel_store import player_key
    from bot.drain_watch.service import DrainWatch
    from bot.drain_watch.store import DrainWatchStore

    salt = "s" * 32
    store = DrainWatchStore(tmp_path / "drain.sqlite")
    svc = DrainWatch(salt=salt, store=store)
    key = player_key("123", salt)
    store.add(key, "0x" + "ab" * 20, "2026-01-01T00:00:00Z")
    monkeypatch.setattr("bot.drain_watch.service.get_service", lambda: svc)
    read = read_claw_watch("123")
    assert read.present is True
    assert read.addresses == ["0x" + "ab" * 20]


def test_poll_does_not_start_and_does_not_touch_the_network_when_the_flag_is_off(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("network")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    monkeypatch.delenv(FLAG, raising=False)
    assert start_poll(object()) is None


def test_command_is_not_found_for_a_non_admin_while_the_flag_is_off(tmp_path):
    env = _env(tmp_path)
    env.pop(FLAG)
    def fetch(_payload):
        raise AssertionError("network")

    texts = handle_command("42", U, is_admin=False, env=env, fetch=fetch)
    assert texts == [NOT_FOUND]


def test_admin_preview_stores_without_a_network_read(tmp_path):
    env = _env(tmp_path)
    env.pop(FLAG)
    calls = []

    def fetch(_payload):
        calls.append(1)
        return []

    stored = handle_command("424242", U, is_admin=True, env=env, fetch=fetch)
    assert calls == []
    assert any("Stored" in line for line in stored)
    blob = (tmp_path / "hl.sqlite").read_bytes()
    assert b"424242" not in blob
    status = handle_command("424242", "", is_admin=True, env=env, fetch=fetch)
    assert any("Admin preview" in line for line in status)
    assert calls == []


def test_now_uses_the_injected_fetch_and_the_second_read_is_quiet(tmp_path):
    env = _env(tmp_path)
    resp = {
        "extraAgents": [], "approvedBuilders": [], "userRole": {"role": "user"},
        "subAccounts": [], "referral": {},
        "clearinghouseState": {
            "marginSummary": {"accountValue": "10", "totalMarginUsed": "1"},
            "withdrawable": "1", "assetPositions": [],
        },
        "userNonFundingLedgerUpdates": [],
    }
    calls = []

    def fetch(payload):
        calls.append(payload["type"])
        if payload["type"] == "maxBuilderFee":
            return 0
        return resp[payload["type"]]

    handle_command("7", U, is_admin=True, env=env, fetch=fetch)
    first = handle_command("7", "now", is_admin=True, env=env, fetch=fetch, now_ms=NOW)
    assert calls
    assert first == ["Baseline read. Nothing further was listed."]
    n_calls = len(calls)
    second = handle_command("7", "now", is_admin=True, env=env, fetch=fetch, now_ms=NOW)
    assert len(calls) > n_calls
    assert second == ["No change since the last read."]


def test_stop_deletes_the_rows(tmp_path):
    env = _env(tmp_path)
    handle_command("7", U, is_admin=True, env=env, fetch=lambda _p: [])
    gone = handle_command("7", "stop", is_admin=True, env=env, fetch=lambda _p: [])
    assert "Removed" in gone[0]
    again = handle_command("7", "stop", is_admin=True, env=env, fetch=lambda _p: [])
    assert "Nothing was stored" in again[0]


def test_a_secret_is_not_stored(tmp_path):
    env = _env(tmp_path)
    texts = handle_command("7", "a" * 64, is_admin=True, env=env, fetch=lambda _p: [])
    assert "Nothing was stored" in texts[0]
    assert "a" * 64 not in texts[0]
    assert not (tmp_path / "hl.sqlite").exists() or b"a" * 64 not in (tmp_path / "hl.sqlite").read_bytes()


def test_missing_salt_is_not_available_even_for_an_admin(tmp_path):
    texts = handle_command("7", "", is_admin=True, env={"CLAW_WATCH_HL_STORE": str(tmp_path / "x.sqlite")})
    assert texts == [NOT_AVAILABLE]


def test_decimal_zero_is_a_reading():
    assert H._dec("0") == Decimal("0")
    assert H._dec("0.0") == Decimal("0")
    assert H._dec(None) is None
    assert H._dec("") is None
