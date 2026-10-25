"""Claw Watch heuristics. Fixtures only: nothing in this file opens a socket."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

from bot.claw_watch_heuristics.alert import alerts_of, render_alert
from bot.claw_watch_heuristics.allowlist import (
    ALLOW_SPENDERS,
    CEX_SWEEP,
    GOOD_7702,
    PERMIT2,
    RECIPIENT_QUIET,
)
from bot.claw_watch_heuristics.chains import chain_allowed
from bot.claw_watch_heuristics.decode import (
    TOPIC_APPROVAL,
    TOPIC_PERMIT2_PERMIT,
    TOPIC_TRANSFER,
)
from bot.claw_watch_heuristics.flag import (
    ETHERSCAN_KEY,
    FLAG,
    INTERNAL_FLAG,
    flag_on,
    internal_enabled,
)
from bot.claw_watch_heuristics.internal import read_internal
from bot.claw_watch_heuristics.reader import fetch_window
from bot.claw_watch_heuristics.score import consider, score_request
from bot.claw_watch_heuristics.store import budget_charge, budget_spent, mark_polled, poll_block
from bot.skills.claw_heuristics_commands import ClawHeuristicsCommands

V = "0x" + "11" * 20
OTHER = "0x" + "22" * 20
UNKNOWN = "0x" + "33" * 20
TOKEN = "0x" + "44" * 20
PAYEE = "0xab12" + "00" * 16 + "cd34"
LOOK = "0xab12" + "ff" * 16 + "cd34"
KUCOIN = "0x58edf78281334335effa23101bbe3371b6a36a51"
BITVAVO = "0xab782bc7d4a2b306825de5a7730034f8f63ee1bc"
PARASWAP = "0x0000000000bbf5c5fd284e657f01bd000933c96d"
DELEGATION_MANAGER = "0xdb9b1e94b5b69df7e401ddbede43491141047db3"
GAS_STATION = "0xc066ac5d385419b1a8c43a0e146fa439837a8b8c"
ROUTER = "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad"
GOOD = "0x63c0c19a282a1b52b07dd5a65b58948a07dae32b"
BAD = "0x6f7c7b0129afd6172dd36891b7048c6cf2d29c7f"
CHAIN = 11155111
_UNLIMITED_ERC20 = 2 ** 255
_UNLIMITED_P2 = 2 ** 159


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


def _word(n: int) -> str:
    return f"{n:064x}"


def _transfer(token, frm, to, value):
    return {
        "address": token,
        "topics": [TOPIC_TRANSFER, _pad(frm), _pad(to)],
        "data": "0x" + _word(value),
    }


def _approval(token, owner, spender, value):
    return {
        "address": token,
        "topics": [TOPIC_APPROVAL, _pad(owner), _pad(spender)],
        "data": "0x" + _word(value),
    }


def _permit(owner, token, spender, amount):
    return {
        "address": PERMIT2,
        "topics": [TOPIC_PERMIT2_PERMIT, _pad(owner), _pad(token), _pad(spender)],
        "data": "0x" + _word(amount) + _word(0),
    }


def _tx(digest, frm, to, logs, **extra):
    raw = {
        "hash": digest, "from": frm, "to": to, "input": "0x",
        "block": extra.pop("block", 10), "value": "0x0", "logs": logs,
    }
    raw.update(extra)
    return raw


def _window(txs, **extra):
    raw = {
        "address": V, "chain_id": CHAIN, "transactions": txs,
        "code": "0x", "code_status": "read", "internal": {"status": "off"},
    }
    raw.update(extra)
    return raw


def _medium(payload):
    out = score_request(payload)
    findings = out["findings"]
    assert findings is not None
    return [row for row in findings if row["signal_strength"] in ("medium", "high")]


def _rules(payload):
    out = score_request(payload)
    assert out["findings"] is not None
    found = []
    for row in out["findings"]:
        for sig in row["signals"]:
            found.append(sig["rule"])
    return out, found


def test_the_lists_the_quiet_rule_uses_are_the_ones_in_the_allowlist():
    assert KUCOIN in CEX_SWEEP and KUCOIN in RECIPIENT_QUIET and KUCOIN not in ALLOW_SPENDERS
    assert BITVAVO in RECIPIENT_QUIET
    assert PARASWAP in ALLOW_SPENDERS
    assert DELEGATION_MANAGER in ALLOW_SPENDERS
    assert GAS_STATION in ALLOW_SPENDERS
    assert ROUTER in ALLOW_SPENDERS
    assert GOOD in GOOD_7702
    assert BAD not in GOOD_7702


def test_flag_words_and_chain_refusal():
    assert flag_on({}) is False
    assert flag_on({FLAG: "yes"}) is False
    assert flag_on({FLAG: "on"}) is False
    assert flag_on({FLAG: "true"}) is True
    assert flag_on({FLAG: " 1 "}) is True
    assert chain_allowed(1) is False
    assert chain_allowed(True) is False
    assert chain_allowed(CHAIN) is True
    assert chain_allowed(31337) is True
    refused = score_request(_window([] , chain_id=1))
    assert refused["findings"] is None
    assert refused["chain_allowed"] is False
    assert score_request({"address": V, "chain_id": True, "transactions": []})["findings"] is None


def test_false_positive_rate_on_the_benign_fixtures_is_zero():
    """Ten quiet fixtures. A medium or high finding would be a false positive."""
    benign = [
        _window([_tx("0x" + "01" * 32, V, UNKNOWN, [_transfer(TOKEN, V, UNKNOWN, 10 ** 18)])]),
        _window([_tx("0x" + "02" * 32, OTHER, UNKNOWN, [
            _permit(V, TOKEN, UNKNOWN, _UNLIMITED_P2),
            _transfer(TOKEN, V, KUCOIN, 10 ** 18),
        ])]),
        _window([_tx("0x" + "03" * 32, OTHER, PARASWAP, [
            _permit(V, TOKEN, UNKNOWN, _UNLIMITED_P2),
            _transfer(TOKEN, V, UNKNOWN, 10 ** 18),
        ])]),
        _window([_tx("0x" + "04" * 32, OTHER, DELEGATION_MANAGER, [
            _transfer(TOKEN, V, UNKNOWN, 10 ** 18),
        ])]),
        _window([_tx("0x" + "05" * 32, OTHER, GAS_STATION, [
            _transfer(TOKEN, V, UNKNOWN, 10 ** 18),
        ])]),
        _window([_tx("0x" + "06" * 32, OTHER, UNKNOWN, [
            _transfer(TOKEN, V, BITVAVO, 10 ** 18),
        ])]),
        _window([_tx("0x" + "07" * 32, OTHER, UNKNOWN, [], authorizations=[
            {"address": GOOD, "authority": V},
        ])]),
        _window([_tx("0x" + "08" * 32, OTHER, V, [_transfer(TOKEN, UNKNOWN, V, 10 ** 18)])]),
        _window([_tx("0x" + "09" * 32, OTHER, ROUTER, [
            _transfer(TOKEN, V, UNKNOWN, 10 ** 18),
        ])]),
        _window([_tx("0x" + "0a" * 32, OTHER, V, [
            _transfer(TOKEN, V, UNKNOWN, 10 ** 18),
        ])], code="0xef0100" + GOOD[2:], code_status="read"),
    ]
    flagged = 0
    for payload in benign:
        if _medium(payload):
            flagged += 1
    assert len(benign) == 10
    assert flagged / len(benign) == 0


def test_the_patterns_the_note_alerts_on():
    permit = _window([_tx("0x" + "11" * 32, OTHER, UNKNOWN, [
        _permit(V, TOKEN, UNKNOWN, _UNLIMITED_P2),
    ])])
    out, rules = _rules(permit)
    assert out["findings"][0]["score"] == 40
    assert out["findings"][0]["signal_strength"] == "medium"
    assert "PERMIT_SUBMITTED_BY_THIRD_PARTY" in rules
    assert "UNLIMITED_AMOUNT" in rules
    revoke = out["findings"][0]["revokes"][0]
    assert revoke["data"].startswith("0xcc53287f")
    assert revoke["signature"] is None

    swept = _window([_tx("0x" + "12" * 32, OTHER, UNKNOWN, [
        _approval(TOKEN, V, UNKNOWN, _UNLIMITED_ERC20),
        _transfer(TOKEN, V, UNKNOWN, 10 ** 18),
    ])])
    out, rules = _rules(swept)
    assert out["findings"][0]["score"] >= 70
    assert out["findings"][0]["signal_strength"] == "high"
    assert "PERMIT_AND_PULL_SAME_TX" in rules
    assert out["findings"][0]["revokes"][0]["data"].startswith("0x095ea7b3")

    split = _window([_tx("0x" + "13" * 32, OTHER, UNKNOWN, [
        _transfer(TOKEN, V, "0x" + "55" * 20, 200_000),
        _transfer(TOKEN, V, "0x" + "66" * 20, 800_000),
    ])])
    out, rules = _rules(split)
    assert "SPLIT_PAYOUT_PATTERN" in rules
    assert "THIRD_PARTY_PULL" in rules
    assert out["findings"][0]["score"] == 50

    crime = _window([_tx("0x" + "14" * 32, OTHER, UNKNOWN, [], authorizations=[
        {"address": BAD, "authority": V},
    ])])
    out, rules = _rules(crime)
    assert out["findings"][0]["score"] == 80
    assert "DELEGATION_TO_LISTED_SWEEPER" in rules
    clear = out["findings"][0]["revokes"][0]
    assert clear["signature"] is None
    assert clear["authorization"]["signature"] is None
    assert clear["authorization"]["nonce"] is None
    assert clear["authorization"]["address"] == "0x" + "0" * 40

    memory = {
        "trusted": [PAYEE],
        "tainted": {},
        "keys": {PAYEE[2:6] + PAYEE[-4:]: [PAYEE]},
    }
    bait = _window([_tx("0x" + "15" * 32, OTHER, UNKNOWN, [
        _transfer(TOKEN, V, LOOK, 0),
    ])], memory=memory)
    out, rules = _rules(bait)
    assert out["findings"][0]["score"] == 60
    assert "ZERO_VALUE_PULL" in rules
    assert "LOOKALIKE_OF_PAST_RECIPIENT" in rules
    assert out["findings"][0]["revokes"] == []


def test_low_signals_are_stored_and_not_alerted():
    lone = _window([_tx("0x" + "21" * 32, OTHER, UNKNOWN, [
        _transfer(TOKEN, V, UNKNOWN, 10 ** 18),
    ])])
    out, rules = _rules(lone)
    assert out["findings"][0]["score"] == 25
    assert out["findings"][0]["signal_strength"] == "low"
    assert alerts_of(out["findings"]) == []
    assert rules == ["THIRD_PARTY_PULL"]

    unknown = "0x" + "99" * 20
    deleg = _window([_tx("0x" + "22" * 32, OTHER, UNKNOWN, [], authorizations=[
        {"address": unknown, "authority": V},
    ])])
    out, rules = _rules(deleg)
    assert out["findings"][0]["score"] == 15
    assert out["findings"][0]["signal_strength"] == "low"
    assert "DELEGATION_TO_UNKNOWN_CONTRACT" in rules
    assert alerts_of(out["findings"]) == []


def test_an_unreadable_sender_or_code_is_not_a_clear_reading():
    hidden = _window([_tx("0x" + "31" * 32, "", UNKNOWN, [
        _transfer(TOKEN, V, UNKNOWN, 10 ** 18),
    ])])
    out, rules = _rules(hidden)
    assert "THIRD_PARTY_PULL" not in rules
    unread = score_request(_window([], code=None, code_status="unreadable"))
    assert unread["code_status"] == "unreadable"
    assert unread["findings"] == []
    assert all("DELEGATION" not in sig["rule"] for row in unread["findings"] for sig in row["signals"])


def test_alert_text_names_a_signal_and_not_a_verdict():
    finding = {
        "chain_id": CHAIN,
        "tx_hash": "0x" + "ab" * 32,
        "score": 40,
        "signal_strength": "medium",
        "signals": [
            {"rule": "PERMIT_SUBMITTED_BY_THIRD_PARTY", "weight": 30, "evidence": {}},
            {"rule": "UNLIMITED_AMOUNT", "weight": 10, "evidence": {}},
        ],
        "revokes": [{
            "kind": "transaction", "to": TOKEN, "data": "0x095ea7b3" + "00" * 32,
            "signature": None,
        }],
    }
    text = render_alert(finding)
    low = text.lower()
    assert "safe" not in low
    assert "scam" not in low
    assert "$" not in text
    assert "PERMIT_SUBMITTED_BY_THIRD_PARTY" in text
    assert "does not sign" in text


def test_the_budget_refuses_the_221st_call_and_an_unreadable_book(tmp_path):
    path = str(tmp_path / "book.db")
    assert budget_spent(path, V, CHAIN, 1_000) == 0
    assert poll_block(path, V, CHAIN, 1_000, 0, 1) is None
    assert budget_charge(path, V, CHAIN, 1_000, 220) is True
    assert poll_block(path, V, CHAIN, 1_000, 0, 1) == "budget"
    mark_polled(path, V, CHAIN, 5_000)
    fresh = str(tmp_path / "interval.db")
    budget_charge(fresh, V, CHAIN, 5_000, 0)
    mark_polled(fresh, V, CHAIN, 5_000)
    assert poll_block(fresh, V, CHAIN, 5_010, 100, 1) == "interval"
    assert poll_block(fresh, V, CHAIN, 5_200, 100, 1) is None
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    bad = str(blocked / "nested.db")
    assert budget_spent(bad, V, CHAIN, 1) is None
    assert poll_block(bad, V, CHAIN, 1, 0, 1) == "unreadable"


def test_internal_reads_are_off_and_the_key_never_leaves(caplog):
    assert internal_enabled({}) is False
    off = read_internal(V, CHAIN, 1, {})
    assert off["status"] == "off"
    assert off["transfers"] is None
    key = "cwh-test-key-zz"
    env = {INTERNAL_FLAG: "1", ETHERSCAN_KEY: key}

    def _boom(_url):
        raise RuntimeError(key)

    caplog.set_level(logging.WARNING)
    failed = read_internal(V, CHAIN, 1, env, _boom)
    blob = json.dumps(failed) + caplog.text
    assert key not in blob
    assert failed["status"] == "unreadable"
    assert failed["error_class"] == "RuntimeError"
    called = []

    def _transport(url):
        called.append(url)
        return "{}"

    mainnet = read_internal(V, 1, 1, env, _transport)
    fork = read_internal(V, 31337, 1, env, _transport)
    assert called == []
    assert mainnet["status"] == "off" and mainnet["reason"] == "chain"
    assert fork["status"] == "off" and fork["reason"] == "fork"


def test_the_reader_does_not_replay_history_or_treat_a_failed_log_as_empty():
    seen = []

    def _transport(calls):
        seen.append([method for method, _params in calls])
        rows = []
        for method, _params in calls:
            if method == "eth_chainId":
                rows.append({"result": hex(CHAIN)})
            elif method == "eth_blockNumber":
                rows.append({"result": "0x10"})
            elif method == "eth_getCode":
                rows.append({"result": "0x"})
            elif method == "eth_getLogs":
                rows.append({"error": {"code": -1}})
            else:
                rows.append({"result": None})
        return rows

    first = fetch_window("http://127.0.0.1:1/rpc", V, CHAIN, None, _transport)
    assert first["status"] == "read"
    assert first["history"] == "not_replayed"
    assert first["calls"] == 1
    assert "eth_getLogs" not in seen[0]
    failed = fetch_window("http://127.0.0.1:1/rpc", V, CHAIN, 1, _transport)
    assert failed["status"] == "unreadable"
    assert failed["transactions"] == []

    def _empty(calls):
        rows = []
        for method, _params in calls:
            if method == "eth_chainId":
                rows.append({"result": hex(CHAIN)})
            elif method == "eth_blockNumber":
                rows.append({"result": "0x10"})
            elif method == "eth_getCode":
                rows.append({"result": "0x"})
            else:
                rows.append({"result": []})
        return rows

    empty = fetch_window("http://127.0.0.1:1/rpc", V, CHAIN, 1, _empty)
    assert empty["status"] == "read"
    assert empty["transactions"] == []

    def _mismatch(calls):
        return [{"result": "0x1"} if method == "eth_chainId" else {"result": "0x10"}
                for method, _params in calls]

    mismatch = fetch_window("http://127.0.0.1:1/rpc", V, CHAIN, None, _mismatch)
    assert mismatch["status"] == "chain_mismatch"
    called = {"n": 0}

    def _no(*_a, **_k):
        called["n"] += 1
        return []

    refused = fetch_window("http://127.0.0.1:1/rpc?apikey=abc", V, CHAIN, None, _no)
    assert refused["status"] == "refused"
    assert called["n"] == 0
    assert fetch_window("http://127.0.0.1:1/rpc", V, 1, None, _no)["status"] == "refused"
    assert called["n"] == 0


def test_importing_the_package_does_not_open_a_socket(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    import importlib

    import bot.claw_watch_heuristics.internal as internal
    import bot.claw_watch_heuristics.reader as reader
    importlib.reload(reader)
    importlib.reload(internal)


def test_consider_while_the_flag_is_off_does_not_score():
    out = consider(_window([]), {})
    assert out["enabled"] is False
    assert out["findings"] is None


def test_the_command_is_hidden_until_the_flag_or_an_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAW_WATCH_HEURISTICS_ID_SALT", "s" * 16)
    monkeypatch.setenv("CLAW_WATCH_HEURISTICS_DB", str(tmp_path / "cmd.db"))
    monkeypatch.delenv(FLAG, raising=False)

    class _Chat:
        id = 42

    class _Msg:
        def __init__(self, text):
            self.text = text

    class _Update:
        def __init__(self, text):
            self.effective_chat = _Chat()
            self.message = _Msg(text)

    class _Host(ClawHeuristicsCommands):
        def __init__(self, admin):
            self.admin = admin
            self.sent = []
            self.asks = 0

        def _is_admin(self, _update):
            self.asks += 1
            return self.admin

        async def _send(self, _update, text, reply_markup=None, edit=False):
            self.sent.append(text)

    hidden = _Host(False)
    asyncio.run(hidden._cmd_clawheuristics(_Update("/clawheuristics"), None))
    assert hidden.sent == ["Not found."]
    assert hidden.asks == 1

    preview = _Host(True)
    asyncio.run(preview._cmd_clawheuristics(_Update("/clawheuristics status"), None))
    assert preview.sent[0].startswith("Admin preview. Not public.")
    assert "Not found." not in preview.sent[0]

    monkeypatch.setenv(FLAG, "1")
    opened = _Host(False)
    asyncio.run(opened._cmd_clawheuristics(_Update("/clawheuristics status"), None))
    assert opened.asks == 1
    assert not opened.sent[0].startswith("Not found.")
    assert "budget used 0" in opened.sent[0] or "No address is stored" in opened.sent[0]
