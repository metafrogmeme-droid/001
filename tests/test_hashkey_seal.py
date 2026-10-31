"""HashKey seal publisher, startup refusal, and the owner deploy script.

The publisher signs HashKey testnet only. A chain that is not that testnet,
a failed read, a bad RPC URL and a hash already stored are all refused before
the signer key is read. After signing, three values: ok True on a receipt
that says so, ok False with a reason, ok None while the chain has not
answered. The deploy script is the owner's, and it refuses the server.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from bot.fvb.keccak import keccak256
from bot.hashkey_seal.flag import flag_on
from bot.hashkey_seal.networks import mainnet_chain_id as hashkey_mainnet_chain_id
from bot.hashkey_seal.networks import testnet_chain_id as hashkey_testnet_chain_id
from bot.hashkey_seal.networks import testnet_rpc as hashkey_testnet_rpc
from bot.hashkey_seal.publish import (
    DEFAULT_DEADLINE_SEC,
    DEFAULT_MAX_FEE_GWEI,
    DUPLICATE_HASH_SELECTOR,
    GAS_CEILING,
    NOT_PUBLISHER_SELECTOR,
    RpcFailed,
    RpcRejected,
    SignerRejected,
    SignerUnavailable,
    _default_sender,
    _default_signer,
    anchor_calldata,
    anchor_of_calldata,
    http_transport,
    is_anchored_calldata,
    kind_agent_leash,
    kind_claw_guard,
    publish_anchor,
    read_anchor,
    rpc_url_reason,
    status,
    tx_hash_of,
)
from bot.web.mainnet_guard import mainnet_chain_ids, startup_refusal

ROOT = Path(__file__).resolve().parents[1]
HASHKEY = ROOT / "contracts" / "hashkey"
SCRIPT = HASHKEY / "script" / "deploy.sh"
NETWORKS = HASHKEY / "networks.json"
SOL = HASHKEY / "script" / "DeployHashKeySeal.s.sol"
CONTRACT = HASHKEY / "src" / "HashKeySeal.sol"
FOUNDRY = HASHKEY / "foundry.toml"
DOC = ROOT / "docs" / "hackathons" / "hashkey.md"
ROUTE = ROOT / "app" / "routes" / "hashkey_seal.js"

ADDRESS = "0x" + "11" * 20
LETTERED = "0x" + "ab" * 20
SEAL = "0x" + "22" * 32
KIND = "0x" + kind_claw_guard().hex()
LEASH = "0x" + kind_agent_leash().hex()
SENDER = "0x" + "aa" * 20
PUBLISHER_WORD = "0x" + ("00" * 12) + ("bb" * 20)
FALSE_WORD = "0x" + "00" * 32
TRUE_WORD = "0x" + ("00" * 31) + "01"
KEY_BODY = "ab" * 32
RAW = "0x" + "ee" * 8
TX_HASH = tx_hash_of(RAW)
ZERO_ADDRESS = "0x" + "00" * 20
# What the deploy-script fixture records as networks.json testnet.publisher.
TESTNET_PUBLISHER = "0x" + "cc" * 20
# Runtime bytecode as the shims report it: executable 0x6001, one metadata
# byte, then the two-byte metadata length the comparison strips.
CODE_BUILT = "0x6001aa0001"

_BANNED = re.compile(
    r"\b(" + "to" + "ken|" + "sa" + "le|" + "pri" + "ce" + r")\b",
    re.IGNORECASE,
)


class _Env(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads: list[str] = []

    def get(self, key, default=None):
        self.reads.append(str(key))
        return super().get(key, default)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Endpoint:
    """A scripted node. A send flips ``anchored`` to true, like inclusion."""

    def __init__(self, clock=None):
        self.chain = "0x85"
        self.anchored = FALSE_WORD
        self.anchor_of = PUBLISHER_WORD + KIND[2:]
        self.gas = "0x5208"
        self.base = "0x10"
        self.priority = "0x2"
        self.nonce = "0x3"
        self.sent = None  # None: answer with the hash of the raw transaction
        self.receipt: object = {"status": "0x1", "blockNumber": "0x5"}
        self.receipts_before_answer = 0
        self.include = True
        self.fail_chain = False
        self.fail_call = False
        self.fail_anchor_of = False
        self.fail_send = False
        self.reject_send = False
        self.fail_receipt = False
        self.reject_nonce = False
        self.estimate_error: object = None
        self.omit_base = False
        self.cost = 0.0
        self.clock = clock
        self.calls: list[tuple] = []

    def __call__(self, method, params):
        self.calls.append((method, list(params)))
        if self.clock is not None:
            self.clock.now += self.cost
        if method == "eth_chainId":
            if self.fail_chain:
                raise RpcFailed()
            return self.chain
        if method == "eth_call":
            if self.fail_call:
                raise RpcFailed()
            data = params[0]["data"]
            if data.startswith("0x" + anchor_of_calldata(bytes(32))[:4].hex()):
                if self.fail_anchor_of:
                    raise RpcFailed()
                return self.anchor_of
            return self.anchored
        if method == "eth_getTransactionCount":
            if self.reject_nonce:
                raise RpcRejected(-32000, None)
            return self.nonce
        if method == "eth_estimateGas":
            if isinstance(self.estimate_error, RpcRejected):
                raise self.estimate_error
            return self.gas
        if method == "eth_getBlockByNumber":
            if self.omit_base:
                return {}
            return {"baseFeePerGas": self.base}
        if method == "eth_maxPriorityFeePerGas":
            return self.priority
        if method == "eth_sendRawTransaction":
            if self.reject_send:
                raise RpcRejected(-32000, None)
            if self.fail_send:
                raise RpcFailed()
            if self.include:
                self.anchored = TRUE_WORD
            return self.sent if self.sent is not None else tx_hash_of(params[0])
        if method == "eth_getTransactionReceipt":
            if self.fail_receipt:
                raise RpcFailed()
            if self.receipts_before_answer > 0:
                self.receipts_before_answer -= 1
                return None
            return self.receipt
        raise AssertionError(method)


class _Wallet:
    def __init__(self):
        self.keys: list[str] = []
        self.txs: list[dict] = []

    def sender(self, key):
        self.keys.append(key)
        return SENDER

    def sign(self, tx, key):
        self.keys.append(key)
        self.txs.append(tx)
        return RAW


def _env(**over: str) -> _Env:
    base = {
        "HASHKEY_SEAL_ENABLED": "1",
        "HASHKEY_SEAL_ADDRESS": ADDRESS,
        "HASHKEY_SEAL_CHAIN_ID": "133",
        "HASHKEY_SEAL_SIGNER_KEY": KEY_BODY,
    }
    base.update(over)
    return _Env(base)


def _publish(env, endpoint, wallet, seal=SEAL, kind=KIND, **extra):
    reports: list[str] = []
    out = publish_anchor(
        env, seal, kind,
        transport=endpoint, sender_of=wallet.sender, signer=wallet.sign,
        report=reports.append, sleep=lambda _s: None, **extra,
    )
    out["_reports"] = reports
    return out


def _sent(endpoint):
    return [params for method, params in endpoint.calls if method == "eth_sendRawTransaction"]


# ---------------------------------------------------------------------------
# refusals before the key


def test_flag_off_does_not_read_the_key():
    env = _env(HASHKEY_SEAL_ENABLED="0")
    endpoint, wallet = _Endpoint(), _Wallet()
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "flag_off"
    assert out["ok"] is False and out["signed"] is False and out["broadcast"] is False
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    assert wallet.keys == []
    assert endpoint.calls == []


def test_malformed_inputs_are_refused_before_the_endpoint_and_the_key():
    endpoint, wallet = _Endpoint(), _Wallet()
    cases = [
        (dict(seal="0x1234"), "bad_hash"),
        (dict(kind="0x1234"), "bad_kind"),
        (dict(seal=FALSE_WORD), "zero_hash"),
        (dict(kind=FALSE_WORD), "zero_kind"),
        (dict(kind="0x" + "33" * 32), "unknown_kind"),
    ]
    for over, reason in cases:
        env = _env()
        out = _publish(env, endpoint, wallet, **over)
        assert out["reason"] == reason, over
        assert out["signed"] is False and out["anchored"] is None
        assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    env = _env(HASHKEY_SEAL_ADDRESS="0x12")
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "no_contract"
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    assert endpoint.calls == []
    assert wallet.keys == []
    for bad_hash in ("0x1234", 7, None):
        assert read_anchor(_env(), bad_hash, transport=endpoint)["reason"] == "bad_hash"
    assert read_anchor(_env(HASHKEY_SEAL_ADDRESS="nope"), SEAL, transport=endpoint)["reason"] == "no_contract"
    assert endpoint.calls == []


def test_a_configured_mainnet_chain_does_not_read_the_key():
    env = _env(HASHKEY_SEAL_CHAIN_ID="177")
    endpoint, wallet = _Endpoint(), _Wallet()
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "not_testnet"
    assert out["chain_id"] == 177
    assert out["signed"] is False
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    assert wallet.keys == []
    assert endpoint.calls == []


def test_unreadable_chain_text_does_not_read_the_key():
    for text in ("nope", "0x85", "0xb1", "+133", "133.0"):
        env = _env(HASHKEY_SEAL_CHAIN_ID=text)
        endpoint, wallet = _Endpoint(), _Wallet()
        out = _publish(env, endpoint, wallet)
        assert out["reason"] == "chain_unreadable", text
        assert out["chain_id"] is None
        assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
        assert wallet.keys == []
        assert status(env)["chain_reason"] == "chain_unreadable"
        assert read_anchor(env, SEAL, transport=endpoint)["reason"] == "chain_unreadable"


def test_a_bad_rpc_url_is_refused_before_any_call():
    assert rpc_url_reason("https://testnet.hsk.xyz") is None
    assert rpc_url_reason("http://127.0.0.1:8545") is None
    assert rpc_url_reason("http://localhost/rpc") is None
    for bad in ("http://evil.example", "ftp://x", "https://", "http://127.0.0.1.evil.example", "testnet.hsk.xyz"):
        assert rpc_url_reason(bad) == "bad_rpc_url", bad
    env = _env(HASHKEY_SEAL_RPC_URL="http://evil.example")
    endpoint, wallet = _Endpoint(), _Wallet()
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "bad_rpc_url" and out["signed"] is False
    assert endpoint.calls == [] and wallet.keys == []
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    read = read_anchor(env, SEAL, transport=endpoint)
    assert read["readable"] is False and read["anchored"] is None and read["reason"] == "bad_rpc_url"
    assert status(env)["rpc_url_ok"] is False
    assert status(_env())["rpc_url_ok"] is True


def test_an_endpoint_on_another_chain_does_not_read_the_key():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.chain = "0xb1"
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "not_testnet"
    assert out["chain_id"] == 177
    assert out["signed"] is False
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    assert wallet.keys == []


def test_an_unreadable_endpoint_does_not_read_the_key():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.fail_chain = True
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "rpc_unreadable"
    assert out["anchored"] is None
    assert out["signed"] is False
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    assert wallet.keys == []


def test_a_stored_hash_does_not_read_the_key():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.anchored = TRUE_WORD
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "duplicate"
    assert out["readable"] is True
    assert out["anchored"] is True
    assert out["signed"] is False
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    assert wallet.keys == []


def test_a_missing_key_and_a_bad_key_are_different_and_neither_is_echoed():
    endpoint, wallet = _Endpoint(), _Wallet()
    missing = _env()
    del missing["HASHKEY_SEAL_SIGNER_KEY"]
    out = _publish(missing, endpoint, wallet)
    assert out["reason"] == "no_key"
    assert out["signed"] is False
    assert out["readable"] is True
    assert out["anchored"] is False
    assert wallet.keys == []

    for bad_body in ("zz" * 16, "00" * 32, "ff" * 32):
        endpoint, wallet = _Endpoint(), _Wallet()
        bad = _env(HASHKEY_SEAL_SIGNER_KEY=bad_body)
        out = _publish(bad, endpoint, wallet)
        assert out["reason"] == "bad_key", bad_body
        assert out["signed"] is False
        assert wallet.keys == []
        assert bad_body not in json.dumps(out)


# ---------------------------------------------------------------------------
# the signed transaction and its three fates


def test_publish_sends_a_zero_value_testnet_transaction_and_reads_the_receipt():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    out = _publish(env, endpoint, wallet)
    assert out["ok"] is True
    assert out["signed"] is True and out["broadcast"] is True
    assert out["reason"] is None
    assert out["tx_hash"] == TX_HASH
    assert out["anchored"] is True
    assert out["chain_id"] == 133
    assert wallet.txs, "the signer was not asked"
    tx = wallet.txs[0]
    assert tx["value"] == 0
    assert tx["chainId"] == hashkey_testnet_chain_id() == 133
    assert tx["type"] == 2
    assert tx["to"] == ADDRESS
    assert tx["maxFeePerGas"] == 0x10 * 2 + 0x2
    assert tx["maxPriorityFeePerGas"] == 0x2
    assert tx["gas"] == 0x5208
    assert tx["data"] == "0x" + anchor_calldata(bytes.fromhex(SEAL[2:]), bytes.fromhex(KIND[2:])).hex()
    assert wallet.keys == ["0x" + KEY_BODY, "0x" + KEY_BODY]
    dumped = json.dumps(out)
    assert KEY_BODY not in dumped
    assert RAW not in dumped
    assert _sent(endpoint) == [[RAW]]
    methods = [m for m, _ in endpoint.calls]
    assert methods.index("eth_sendRawTransaction") < methods.index("eth_getTransactionReceipt")
    assert out["_reports"] == [f"tx_hash={TX_HASH} nonce=3 sender={SENDER}\n"]
    assert KEY_BODY not in out["_reports"][0] and RAW not in out["_reports"][0]


def test_the_report_line_comes_before_the_send():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    seen: list[str] = []

    def report(line):
        seen.append(line)
        assert _sent(endpoint) == [], "the hash is written before the send, not after"

    publish_anchor(env, SEAL, KIND, transport=endpoint, sender_of=wallet.sender,
                   signer=wallet.sign, report=report, sleep=lambda _s: None)
    assert seen and seen[0].startswith(f"tx_hash={TX_HASH}")


def test_a_node_that_refuses_the_send_is_rejected():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.reject_send = True
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "rejected"
    assert out["ok"] is False and out["signed"] is True and out["broadcast"] is False
    assert out["anchored"] is False
    assert out["tx_hash"] == TX_HASH, "the refused transaction is named by the hash computed before the send"
    assert RAW not in json.dumps(out) and KEY_BODY not in json.dumps(out)


def test_a_send_whose_answer_is_lost_is_unconfirmed_with_the_local_hash():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.fail_send = True
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "send_unconfirmed"
    assert out["ok"] is None and out["signed"] is True and out["broadcast"] is None
    assert out["anchored"] is None
    assert out["tx_hash"] == TX_HASH
    assert RAW not in json.dumps(out)


def test_a_node_that_answers_another_hash_is_a_mismatch():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.sent = "0x" + "cd" * 32
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "hash_mismatch"
    assert out["ok"] is None and out["broadcast"] is None and out["anchored"] is None
    assert out["tx_hash"] == TX_HASH
    endpoint = _Endpoint()
    endpoint.sent = "0x1234"
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "hash_mismatch" and out["tx_hash"] == TX_HASH


def test_a_reverted_receipt_is_ok_false_and_anchored_is_re_read_not_derived():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.receipt = {"status": "0x0"}
    endpoint.include = False
    out = _publish(env, endpoint, wallet)
    assert out["ok"] is False and out["reason"] == "reverted"
    assert out["signed"] is True and out["broadcast"] is True
    assert out["tx_hash"] == TX_HASH
    assert out["anchored"] is False
    endpoint = _Endpoint()
    endpoint.receipt = {"status": "0x0"}
    endpoint.include = True  # a rival's anchor landed first
    out = _publish(env, endpoint, _Wallet())
    assert out["ok"] is False and out["reason"] == "reverted" and out["anchored"] is True


def test_no_receipt_within_the_budget_is_submitted():
    clock = _Clock()
    env = _env()
    endpoint, wallet = _Endpoint(clock), _Wallet()
    endpoint.receipt = None
    endpoint.cost = 0.3
    slept: list[float] = []
    out = publish_anchor(
        env, SEAL, KIND, transport=endpoint, sender_of=wallet.sender, signer=wallet.sign,
        report=lambda _l: None, clock=clock, sleep=slept.append, deadline_sec=5.0,
    )
    assert out["reason"] == "submitted"
    assert out["ok"] is None and out["signed"] is True and out["broadcast"] is True
    assert out["anchored"] is None and out["tx_hash"] == TX_HASH
    assert slept and all(0 < s <= 0.5 for s in slept)
    assert [m for m, _ in endpoint.calls].count("eth_getTransactionReceipt") >= 2


def test_a_receipt_that_arrives_after_a_few_polls_is_read():
    clock = _Clock()
    env = _env()
    endpoint, wallet = _Endpoint(clock), _Wallet()
    endpoint.receipts_before_answer = 3
    endpoint.cost = 0.1
    out = publish_anchor(
        env, SEAL, KIND, transport=endpoint, sender_of=wallet.sender, signer=wallet.sign,
        report=lambda _l: None, clock=clock, sleep=lambda _s: None, deadline_sec=10.0,
    )
    assert out["ok"] is True and out["anchored"] is True
    assert [m for m, _ in endpoint.calls].count("eth_getTransactionReceipt") == 4


def test_a_receipt_read_failure_or_an_unreadable_status_is_not_a_verdict():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.fail_receipt = True
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "submitted" and out["ok"] is None and out["broadcast"] is True
    endpoint = _Endpoint()
    endpoint.receipt = {"status": "zz"}
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "receipt_unreadable" and out["ok"] is None and out["broadcast"] is True
    assert out["tx_hash"] == TX_HASH


# ---------------------------------------------------------------------------
# what the node says before signing


def test_estimate_reverts_are_named_and_only_a_duplicate_contradicts_the_pre_read():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.estimate_error = RpcRejected(3, DUPLICATE_HASH_SELECTOR)
    endpoint.anchored = FALSE_WORD
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "duplicate_pending" and out["anchored"] is None and out["signed"] is False
    assert wallet.txs == []
    assert [m for m, _ in endpoint.calls].count("eth_call") == 2, "read once more, not forever"

    class _Flips(_Endpoint):
        def __call__(self, method, params):
            if method == "eth_estimateGas":
                self.anchored = TRUE_WORD
            return super().__call__(method, params)

    endpoint = _Flips()
    endpoint.estimate_error = RpcRejected(3, DUPLICATE_HASH_SELECTOR)
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "duplicate" and out["anchored"] is True

    endpoint = _Endpoint()
    endpoint.estimate_error = RpcRejected(3, NOT_PUBLISHER_SELECTOR)
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "not_publisher" and out["anchored"] is False

    endpoint = _Endpoint()
    endpoint.estimate_error = RpcRejected(3, "0xdeadbeef")
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "estimate_reverted" and out["anchored"] is False

    endpoint = _Endpoint()
    endpoint.estimate_error = RpcRejected(-32000, None)
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "estimate_rejected" and out["anchored"] is False

    endpoint = _Endpoint()
    endpoint.reject_nonce = True
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "node_error" and out["signed"] is False


def test_a_measured_zero_gas_is_not_called_unreadable():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.gas = "0x0"
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "gas_unusable"
    assert out["signed"] is False
    assert out["readable"] is True
    assert out["anchored"] is False


def test_a_missing_fee_is_unreadable():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.omit_base = True
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "rpc_unreadable"
    assert out["signed"] is False
    assert out["anchored"] is False


def test_ceilings_refuse_before_signing_and_a_raised_ceiling_admits():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.gas = hex(GAS_CEILING + 1)
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "gas_cap_exceeded" and wallet.txs == [] and out["signed"] is False
    endpoint = _Endpoint()
    endpoint.gas = hex(GAS_CEILING)
    assert _publish(env, endpoint, _Wallet())["ok"] is True

    gwei = 10**9
    endpoint = _Endpoint()
    endpoint.priority = hex(DEFAULT_MAX_FEE_GWEI * gwei + 1)
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "fee_cap_exceeded" and out["signed"] is False
    endpoint = _Endpoint()
    endpoint.base = hex(DEFAULT_MAX_FEE_GWEI * gwei)  # base * 2 + priority is above the ceiling
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "fee_cap_exceeded"
    raised = _env(HASHKEY_SEAL_MAX_FEE_GWEI=str(DEFAULT_MAX_FEE_GWEI * 3))
    endpoint = _Endpoint()
    endpoint.base = hex(DEFAULT_MAX_FEE_GWEI * gwei)
    assert _publish(raised, endpoint, _Wallet())["ok"] is True

    endpoint = _Endpoint()
    endpoint.nonce = hex(2**64)
    out = _publish(env, endpoint, _Wallet())
    assert out["reason"] == "nonce_unusable"

    for text in ("lots", "-1", "0", "inf", "-inf", "1e400", "nan"):
        endpoint, wallet = _Endpoint(), _Wallet()
        out = _publish(_env(HASHKEY_SEAL_MAX_FEE_GWEI=text), endpoint, wallet)
        assert out["reason"] == "fee_cap_unreadable" and endpoint.calls == [] and wallet.keys == [], text
        assert status(_env(HASHKEY_SEAL_MAX_FEE_GWEI=text))["max_fee_gwei"] is None, text
        endpoint, wallet = _Endpoint(), _Wallet()
        out = _publish(_env(HASHKEY_SEAL_DEADLINE_SEC=text), endpoint, wallet)
        assert out["reason"] == "deadline_unreadable" and endpoint.calls == [], text
        assert status(_env(HASHKEY_SEAL_DEADLINE_SEC=text))["deadline_sec"] is None, text


def test_the_budget_refuses_before_signing_when_little_time_is_left():
    clock = _Clock()
    env = _env()
    endpoint, wallet = _Endpoint(clock), _Wallet()
    endpoint.cost = 0.9  # six reads before signing spend 5.4 s of a 6 s budget
    out = publish_anchor(
        env, SEAL, KIND, transport=endpoint, sender_of=wallet.sender, signer=wallet.sign,
        report=lambda _l: None, clock=clock, sleep=lambda _s: None, deadline_sec=6.0,
    )
    assert out["reason"] == "budget_exhausted"
    assert out["signed"] is False and wallet.txs == [] and _sent(endpoint) == []
    assert out["readable"] is True and out["anchored"] is False
    assert status(_env())["deadline_sec"] == DEFAULT_DEADLINE_SEC


def test_a_signer_that_is_missing_refuses_or_returns_garbage_is_not_a_signature():
    env, endpoint = _env(), _Endpoint()

    def unavailable(*_a):
        raise SignerUnavailable

    def refused(*_a):
        raise SignerRejected

    cases = [
        (unavailable, _Wallet().sign, "signer_unavailable"),
        (_Wallet().sender, unavailable, "signer_unavailable"),
        (refused, _Wallet().sign, "signer_rejected"),
        (_Wallet().sender, refused, "signer_rejected"),
        (_Wallet().sender, lambda _tx, _k: "nope", "signer_unavailable"),
        (_Wallet().sender, lambda _tx, _k: "0xabc", "signer_unavailable"),
        (_Wallet().sender, lambda _tx, _k: "0x", "signer_unavailable"),
    ]
    for sender_of, signer, reason in cases:
        out = publish_anchor(env, SEAL, KIND, transport=endpoint, sender_of=sender_of,
                             signer=signer, report=lambda _l: None, sleep=lambda _s: None)
        assert out["reason"] == reason
        assert out["signed"] is False and out["broadcast"] is False and out["ok"] is False
    assert _sent(endpoint) == []


def test_the_real_signer_accepts_the_lowercase_lettered_address():
    pytest.importorskip("eth_account")
    key = "0x" + KEY_BODY
    sender = _default_sender(key)
    assert sender.startswith("0x") and len(sender) == 42
    tx = {
        "chainId": 133, "nonce": 3, "maxFeePerGas": 34, "maxPriorityFeePerGas": 2,
        "gas": 21000, "to": LETTERED, "value": 0, "data": "0x" + anchor_calldata(bytes(32), bytes(32)).hex(),
        "type": 2,
    }
    raw = _default_signer(tx, key)
    assert raw.startswith("0x02") and len(tx_hash_of(raw)) == 66
    assert tx["to"] == LETTERED, "the transaction the publisher holds keeps the lowercase form"
    with pytest.raises(SignerRejected):
        _default_signer(dict(tx, to="0x1234"), key)
    with pytest.raises(SignerRejected):
        _default_signer(dict(tx, nonce="three"), key)
    env = _env(HASHKEY_SEAL_ADDRESS=LETTERED, HASHKEY_SEAL_SIGNER_KEY=key)
    endpoint = _Endpoint()
    out = publish_anchor(env, SEAL, KIND, transport=endpoint, report=lambda _l: None, sleep=lambda _s: None)
    assert out["ok"] is True and out["tx_hash"] == tx_hash_of(_sent(endpoint)[0][0])


# ---------------------------------------------------------------------------
# reads and status


def test_read_does_not_touch_the_key_and_keeps_three_answers():
    env = _env()
    endpoint = _Endpoint()
    false = read_anchor(env, SEAL, transport=endpoint)
    assert false == {
        "readable": True, "anchored": False, "reason": None, "chain_id": 133,
        "publisher": None, "kind": None, "attribution": "absent",
    }
    assert endpoint.calls[-1][1][0]["to"] == ADDRESS
    assert endpoint.calls[-1][1][0]["data"] == "0x" + is_anchored_calldata(bytes.fromhex(SEAL[2:])).hex()
    endpoint.anchored = TRUE_WORD
    true = read_anchor(env, SEAL, transport=endpoint)
    assert true["anchored"] is True and true["readable"] is True
    assert true["publisher"] == "0x" + "bb" * 20 and true["kind"] == KIND and true["attribution"] == "read"
    assert endpoint.calls[-1][1][0]["data"] == "0x" + anchor_of_calldata(bytes.fromhex(SEAL[2:])).hex()
    endpoint.fail_anchor_of = True
    unattributed = read_anchor(env, SEAL, transport=endpoint)
    assert unattributed["anchored"] is True and unattributed["attribution"] == "unreadable"
    assert unattributed["publisher"] is None and unattributed["kind"] is None
    endpoint.fail_anchor_of = False
    endpoint.anchor_of = FALSE_WORD + KIND[2:]
    assert read_anchor(env, SEAL, transport=endpoint)["attribution"] == "unreadable"
    endpoint.anchor_of = "0x1234"
    assert read_anchor(env, SEAL, transport=endpoint)["attribution"] == "unreadable"
    endpoint.fail_call = True
    unread = read_anchor(env, SEAL, transport=endpoint)
    assert unread["readable"] is False
    assert unread["anchored"] is None
    assert unread["reason"] == "rpc_unreadable"
    endpoint.fail_call = False
    endpoint.anchored = "0x" + ("00" * 31) + "02"
    garbled = read_anchor(env, SEAL, transport=endpoint)
    assert garbled["anchored"] is None
    assert garbled["reason"] == "result_unreadable"
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads


def test_read_refuses_a_mainnet_configuration_and_a_mainnet_endpoint():
    endpoint = _Endpoint()
    out = read_anchor(_env(HASHKEY_SEAL_CHAIN_ID="177"), SEAL, transport=endpoint)
    assert out["readable"] is False and out["anchored"] is None
    assert out["reason"] == "not_testnet" and out["chain_id"] == 177
    assert endpoint.calls == []
    endpoint.chain = "0xb1"
    out = read_anchor(_env(), SEAL, transport=endpoint)
    assert out["reason"] == "not_testnet" and out["chain_id"] == 177 and out["anchored"] is None
    assert [m for m, _ in endpoint.calls] == ["eth_chainId"]
    endpoint = _Endpoint()
    endpoint.chain = "zz"
    out = read_anchor(_env(), SEAL, transport=endpoint)
    assert out["reason"] == "chain_unreadable" and out["anchored"] is None


def test_status_names_the_two_receipt_kinds_and_not_the_key():
    env = _env()
    body = status(env)
    assert body["flag_on"] is True
    assert body["chain_id"] == hashkey_testnet_chain_id() == 133
    assert body["refused_chain_id"] == hashkey_mainnet_chain_id() == 177
    assert body["signer_set"] is True
    assert body["contract_set"] is True
    assert body["kinds"]["claw-guard"] == "0x" + keccak256(b"claw-guard").hex()
    assert body["kinds"]["agent-leash"] == "0x" + keccak256(b"agent-leash").hex()
    assert body["deadline_sec"] == DEFAULT_DEADLINE_SEC
    assert body["max_fee_gwei"] == DEFAULT_MAX_FEE_GWEI
    assert body["gas_ceiling"] == GAS_CEILING
    assert KEY_BODY not in json.dumps(body)
    assert hashkey_testnet_rpc() == "https://testnet.hsk.xyz"
    assert status(_env(HASHKEY_SEAL_MAX_FEE_GWEI="x"))["max_fee_gwei"] is None


# ---------------------------------------------------------------------------
# the real transport, against a loopback node


class _Node(http.server.BaseHTTPRequestHandler):
    answers: dict = {}
    seen: list = []

    def do_POST(self):  # noqa: N802 — http.server's name
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append(body["method"])
        raw = type(self).answers.get(body["method"], {"jsonrpc": "2.0", "id": 1, "result": "0x85"})
        out = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_a):
        return


@pytest.fixture
def node(monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _Node.answers = {}
    _Node.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Node)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", _Node
    finally:
        server.shutdown()
        server.server_close()


def test_http_transport_maps_answers_to_three_outcomes(node):
    url, handler = node
    call = http_transport(url)
    assert call("eth_chainId", []) == "0x85"
    handler.answers["eth_x"] = {"jsonrpc": "2.0", "id": 1, "result": None}
    assert call("eth_x", []) is None
    handler.answers["eth_err"] = {
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": 3, "message": "execution reverted", "data": DUPLICATE_HASH_SELECTOR + "00"},
    }
    with pytest.raises(RpcRejected) as caught:
        call("eth_err", [])
    assert caught.value.code == 3 and caught.value.selector == DUPLICATE_HASH_SELECTOR
    assert "reverted" not in str(caught.value)
    handler.answers["eth_empty_err"] = {"jsonrpc": "2.0", "id": 1, "error": {}}
    with pytest.raises(RpcRejected) as caught:
        call("eth_empty_err", [])
    assert caught.value.code is None and caught.value.selector is None
    handler.answers["eth_both"] = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000}, "result": "0x1"}
    with pytest.raises(RpcRejected):
        call("eth_both", [])
    handler.answers["eth_noresult"] = {"jsonrpc": "2.0", "id": 1}
    with pytest.raises(RpcFailed) as plain:
        call("eth_noresult", [])
    assert not isinstance(plain.value, RpcRejected)
    handler.answers["eth_list"] = [1, 2]
    with pytest.raises(RpcFailed):
        call("eth_list", [])
    handler.answers["eth_garbage"] = b"not json"
    with pytest.raises(RpcFailed):
        call("eth_garbage", [])
    assert handler.seen == [
        "eth_chainId", "eth_x", "eth_err", "eth_empty_err", "eth_both", "eth_noresult", "eth_list", "eth_garbage",
    ]


def test_http_transport_refuses_a_spent_deadline_without_connecting(node):
    url, handler = node
    clock = _Clock()
    call = http_transport(url, deadline=clock.now - 1, clock=clock)
    with pytest.raises(RpcFailed):
        call("eth_chainId", [])
    assert handler.seen == []
    call = http_transport(url, deadline=clock.now + 5, clock=clock)
    assert call("eth_chainId", []) == "0x85"


class _Trickle(http.server.BaseHTTPRequestHandler):
    """Answers the status line at once and then holds the body open."""

    hold = 5.0

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["content-length"]))
        out = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x85"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out[:3])
        self.wfile.flush()
        time.sleep(type(self).hold)
        try:
            self.wfile.write(out[3:])
        except OSError:
            pass

    def log_message(self, *_a):
        return


def test_http_transport_is_bounded_by_the_wall_clock_not_only_per_socket_read(monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _Trickle.hold = 5.0
    server = http.server.HTTPServer(("127.0.0.1", 0), _Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        call = http_transport(url, deadline=time.monotonic() + 1.0)
        started = time.monotonic()
        with pytest.raises(RpcFailed):
            call("eth_chainId", [])
        assert time.monotonic() - started < 3.0, "the call outlived the deadline"
    finally:
        server.shutdown()
        server.server_close()


def test_http_transport_against_a_closed_port_is_unreadable(monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    probe = http.server.HTTPServer(("127.0.0.1", 0), _Node)
    port = probe.server_address[1]
    probe.server_close()
    with pytest.raises(RpcFailed):
        http_transport(f"http://127.0.0.1:{port}")("eth_chainId", [])


def test_the_rpc_url_override_wins_and_a_blank_falls_back():
    from bot.hashkey_seal.publish import rpc_url
    assert rpc_url({}) == "https://testnet.hsk.xyz"
    assert rpc_url({"HASHKEY_SEAL_RPC_URL": "   "}) == "https://testnet.hsk.xyz"
    assert rpc_url({"HASHKEY_SEAL_RPC_URL": " http://127.0.0.1:1 "}) == "http://127.0.0.1:1"


# ---------------------------------------------------------------------------
# the startup guard and the CLI


def test_startup_refuses_a_seal_key_aimed_at_any_chain_but_testnet():
    key = "0x" + KEY_BODY
    refused = startup_refusal({
        "HASHKEY_SEAL_SIGNER_KEY": key,
        "HASHKEY_SEAL_CHAIN_ID": "177",
    })
    assert refused is not None
    # The generic reading now knows HashKey mainnet, so it speaks first;
    # the HashKey-specific sentence stands behind it for any other chain.
    assert "does not start" in refused or "does not sign" in refused
    assert KEY_BODY not in refused

    other = startup_refusal({
        "HASHKEY_SEAL_SIGNER_KEY": key,
        "HASHKEY_SEAL_CHAIN_ID": "1",
    })
    assert other is not None
    assert "does not start" in other
    assert KEY_BODY not in other

    for text in ("0x85", "0xb1", "0XB1", "133.0", "+133"):
        hexed = startup_refusal({"HASHKEY_SEAL_SIGNER_KEY": key, "HASHKEY_SEAL_CHAIN_ID": text})
        assert hexed is not None and "does not sign" in hexed and KEY_BODY not in hexed, text

    assert startup_refusal({
        "HASHKEY_SEAL_SIGNER_KEY": key,
        "HASHKEY_SEAL_CHAIN_ID": "133",
    }) is None
    assert startup_refusal({"HASHKEY_SEAL_SIGNER_KEY": key}) is None
    assert startup_refusal({"HASHKEY_SEAL_CHAIN_ID": "177"}) is None


def test_the_generic_guard_knows_hashkey_mainnet():
    key = "0x" + KEY_BODY
    assert 177 in mainnet_chain_ids()
    assert 133 not in mainnet_chain_ids()
    for name in ("WEB3_CHAIN_ID", "CHAIN_ID", "ANY_CHAIN_ID"):
        refused = startup_refusal({"WEB3_SIGNER_PRIVATE_KEY": key, name: "177"})
        assert refused is not None and "does not start" in refused, name
        assert KEY_BODY not in refused
    assert startup_refusal({"WEB3_SIGNER_PRIVATE_KEY": key, "WEB3_CHAIN_ID": "133"}) is None


def test_the_flag_readers_agree():
    script = (
        "const flag = require('./app/lib/hashkey_seal_flag');\n"
        "const raw = process.env.SAMPLE;\n"
        "const env = process.env.OMIT === '1' ? {} : { HASHKEY_SEAL_ENABLED: raw };\n"
        "process.stdout.write(flag.flagOn(env) ? 'yes' : 'no');\n"
    )
    cases = [
        ({}, None),
        ({"HASHKEY_SEAL_ENABLED": "1"}, "1"),
        ({"HASHKEY_SEAL_ENABLED": "true"}, "true"),
        ({"HASHKEY_SEAL_ENABLED": "TRUE"}, "TRUE"),
        ({"HASHKEY_SEAL_ENABLED": " 1 "}, " 1 "),
        ({"HASHKEY_SEAL_ENABLED": "on"}, "on"),
        ({"HASHKEY_SEAL_ENABLED": "yes"}, "yes"),
        ({"HASHKEY_SEAL_ENABLED": "0"}, "0"),
        ({"HASHKEY_SEAL_ENABLED": "false"}, "false"),
    ]
    for mapping, sample in cases:
        child = dict(os.environ)
        child.pop("HASHKEY_SEAL_ENABLED", None)
        if sample is None:
            child["OMIT"] = "1"
            child.pop("SAMPLE", None)
        else:
            child["OMIT"] = "0"
            child["SAMPLE"] = sample
        done = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT, env=child, capture_output=True, text=True, check=False,
        )
        assert done.returncode == 0, done.stderr
        lines = [line for line in done.stdout.splitlines() if line]
        assert lines, done.stdout
        assert flag_on(mapping) is (lines[-1] == "yes")


def test_cli_usage_does_not_sign(capsys, monkeypatch):
    from bot.hashkey_seal.__main__ import main

    monkeypatch.delenv("HASHKEY_SEAL_SIGNER_KEY", raising=False)
    monkeypatch.delenv("HASHKEY_SEAL_CHAIN_ID", raising=False)
    assert main(["nope"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "usage"


def test_cli_wires_read_publish_and_status_and_hides_a_crash(capsys, monkeypatch):
    import bot.hashkey_seal.__main__ as cli

    monkeypatch.delenv("HASHKEY_SEAL_SIGNER_KEY", raising=False)
    monkeypatch.delenv("HASHKEY_SEAL_CHAIN_ID", raising=False)
    monkeypatch.setenv("HASHKEY_SEAL_ADDRESS", ADDRESS)
    seen: dict = {}
    monkeypatch.setattr(
        cli, "read_anchor",
        lambda env, h: seen.setdefault("read", (h, env["HASHKEY_SEAL_ADDRESS"])) and {"r": 1},
    )
    monkeypatch.setattr(cli, "publish_anchor", lambda env, h, k: seen.setdefault("publish", (h, k)) and {"p": 1})
    monkeypatch.setattr(cli, "status", lambda env: {"s": 1})
    assert cli.main(["read", SEAL]) == 0
    assert json.loads(capsys.readouterr().out) == {"r": 1}
    assert seen["read"] == (SEAL, ADDRESS)
    assert cli.main(["publish", SEAL, KIND]) == 0
    assert json.loads(capsys.readouterr().out) == {"p": 1}
    assert seen["publish"] == (SEAL, KIND)
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"s": 1}

    def crash(env, h, k):
        raise RuntimeError("secret " + KEY_BODY)

    monkeypatch.setattr(cli, "publish_anchor", crash)
    assert cli.main(["publish", SEAL, KIND]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "RuntimeError\n"
    assert KEY_BODY not in captured.err


def test_cli_asks_the_startup_guard_before_anything_else(capsys, monkeypatch):
    import bot.hashkey_seal.__main__ as cli

    monkeypatch.setenv("HASHKEY_SEAL_SIGNER_KEY", "0x" + KEY_BODY)
    monkeypatch.setenv("HASHKEY_SEAL_CHAIN_ID", "177")
    called: list = []
    monkeypatch.setattr(cli, "status", lambda env: called.append("status") or {"s": 1})
    monkeypatch.setattr(cli, "publish_anchor", lambda env, h, k: called.append("publish") or {"p": 1})
    assert cli.main(["status"]) == 1
    assert cli.main(["publish", SEAL, KIND]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "startup refused\nstartup refused\n"
    assert KEY_BODY not in captured.err and "177" not in captured.err
    assert called == []
    monkeypatch.setenv("HASHKEY_SEAL_CHAIN_ID", "133")
    assert cli.main(["status"]) == 0
    assert called == ["status"]


def test_the_real_cli_runs_as_a_subprocess_from_the_repo_root():
    env = {k: v for k, v in os.environ.items() if not k.startswith("HASHKEY_SEAL")}
    env["HASHKEY_SEAL_ADDRESS"] = ADDRESS
    done = subprocess.run(
        ["python3", "-m", "bot.hashkey_seal", "status"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    body = json.loads(done.stdout)
    assert body["flag_on"] is False and body["signer_set"] is False and body["contract_set"] is True


# ---------------------------------------------------------------------------
# the deploy script


def _script_env(**extra: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    env.update(extra)
    return env


def _run_script(*args: str, script: Path = SCRIPT, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=ROOT, env=_script_env(**extra),
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )


def _no_chain_probe(done: subprocess.CompletedProcess[str]) -> None:
    for text in ("endpoint answered", "network testnet chain", "chain id unreadable", "stopped before any deploy"):
        assert text not in done.stderr, done.stderr


def test_deploy_script_refuses_the_server_before_any_chain():
    planted = "0x" + KEY_BODY
    syntax = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert syntax.returncode == 0, syntax.stderr
    assert os.access(SCRIPT, os.X_OK)

    refused = _run_script(
        "--network", "testnet", RUNECLAW_SERVER="1", ETH_PRIVATE_KEY=planted, HASHKEY_SEAL_PUBLISHER=ADDRESS,
    )
    assert refused.returncode != 0
    assert "RUNECLAW_SERVER" in refused.stderr
    assert planted not in refused.stderr and KEY_BODY not in refused.stderr
    _no_chain_probe(refused)

    leaked = _run_script(
        "--network", "testnet",
        HASHKEY_SEAL_SIGNER_KEY=planted,
        HASHKEY_SEAL_PUBLISHER=ADDRESS,
    )
    assert leaked.returncode != 0
    assert "HASHKEY_SEAL_SIGNER_KEY" in leaked.stderr
    assert planted not in leaked.stderr and KEY_BODY not in leaked.stderr
    _no_chain_probe(leaked)

    both = _run_script(
        "--network", "testnet",
        HASHKEY_WALLET="ledger",
        ETH_PRIVATE_KEY=planted,
        HASHKEY_SEAL_PUBLISHER=ADDRESS,
    )
    assert both.returncode != 0
    assert "both set" in both.stderr
    assert planted not in both.stderr
    _no_chain_probe(both)

    unknown = _run_script("--network", "other")
    assert unknown.returncode != 0
    assert "testnet or mainnet" in unknown.stderr

    missing = _run_script("--network", "testnet")
    assert missing.returncode != 0
    assert "HASHKEY_SEAL_PUBLISHER" in missing.stderr
    _no_chain_probe(missing)

    help_text = _run_script("--help")
    assert help_text.returncode == 0
    assert "HASHKEY_SEAL_PUBLISHER" in help_text.stdout
    assert "--broadcast" in help_text.stdout
    assert "owner" in help_text.stdout


def test_the_operator_broadcast_also_refuses_the_seal_key():
    text = (ROOT / "scripts" / "anchor_roots.sh").read_text(encoding="utf-8")
    loop = text.split("for leaked in ", 1)[1].split(";", 1)[0]
    assert "HASHKEY_SEAL_SIGNER_KEY" in loop


def test_deploy_script_does_not_source_env_or_put_the_key_on_argv():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "source .env" not in text
    assert "source web.env" not in text
    assert ". .env" not in text
    assert "--private-key" not in text
    verify_block = text.split("exec forge verify-contract", 1)[1].split("\nfi\n", 1)[0]
    assert "--chain \"$EXPECT_CHAIN\"" in verify_block and "--evm-version" in verify_block
    after = text.split("ARGS=(forge script", 1)[1]
    assert "ETH_PRIVATE_KEY" not in after
    assert "HASHKEY_SEAL_SIGNER_KEY" not in after
    assert "--chain" not in after, "--chain overrides block.chainid in a forge script simulation"
    assert "unset ETH_PRIVATE_KEY" not in text


_FORGE_SHIM = """#!/usr/bin/env bash
printf '%s\\n' "$@" >> "$SHIM_LOG"
if [ "$1" = "verify-contract" ]; then exit 0; fi
if [ "$1" = "inspect" ]; then echo "$CODE_SHOWN"; exit 0; fi
echo "== Logs =="
SEAL_SHOWN=0x5fbdb2315678afecb367f032d93f642f64180aa3
if [ -n "$OWNER_SHOWN" ]; then
  echo "  hashkey-seal: chain $CHAIN_SHOWN seal $SEAL_SHOWN owner $OWNER_SHOWN publisher $3"
fi
"""

_CAST_SHIM = """#!/usr/bin/env bash
printf 'cast %s\\n' "$*" >> "$SHIM_LOG"
case "$1" in
  to-check-sum-address) echo "$CHECKSUM_SHOWN" ;;
  abi-encode) echo "0xencoded" ;;
  code) echo "${CODE_ONCHAIN:-$CODE_SHOWN}" ;;
  chain-id)
    # The real cast asks the endpoint for eth_chainId; so does the shim, so
    # the mock node, not the shim, decides the answer.
    shift
    url=""
    while [ $# -gt 0 ]; do
      case "$1" in --rpc-url) url="$2"; shift 2 ;; *) shift ;; esac
    done
    python3 - "$url" <<'CHAIN'
import json, sys, urllib.request
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
req = urllib.request.Request(sys.argv[1], data=body, headers={"content-type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as resp:
    print(int(json.load(resp)["result"], 16))
CHAIN
    ;;
  *) exit 1 ;;
esac
"""


class _ChainNode(http.server.BaseHTTPRequestHandler):
    chain = "0x85"
    #: When True the explorer API path answers a 301, as a moved host does.
    redirect = False

    def do_GET(self):  # noqa: N802
        if not self.path.endswith("/v2/stats"):
            self.send_response(404)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if type(self).redirect:
            self.send_response(301)
            self.send_header("location", "http://moved.invalid" + self.path)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        out = b'{"total_blocks":"1"}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["content-length"]))
        assert b"eth_chainId" in body
        out = json.dumps({"jsonrpc": "2.0", "id": 1, "result": type(self).chain}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_a):
        return


@pytest.fixture
def deploy_tree(tmp_path, monkeypatch):
    """A copy of the deploy script in a tree whose testnet RPC is a loopback
    mock, with forge and cast shims that record their argv."""
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    tree = tmp_path / "tree" / "contracts" / "hashkey"
    (tree / "script").mkdir(parents=True)
    shutil.copy(SCRIPT, tree / "script" / "deploy.sh")
    (tree / "script" / "deploy.sh").chmod(0o755)
    _ChainNode.chain = "0x85"
    _ChainNode.redirect = False
    server = http.server.HTTPServer(("127.0.0.1", 0), _ChainNode)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    table = json.loads(NETWORKS.read_text(encoding="utf-8"))
    mock = f"http://127.0.0.1:{server.server_address[1]}"
    for name in ("testnet", "mainnet"):
        table[name]["rpc"] = mock
        table[name]["explorer_api"] = mock + "/api"
    table["testnet"]["publisher"] = TESTNET_PUBLISHER
    (tree / "networks.json").write_text(json.dumps(table), encoding="utf-8")
    shim = tmp_path / "bin"
    shim.mkdir()
    for name, body in (("forge", _FORGE_SHIM), ("cast", _CAST_SHIM)):
        path = shim / name
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "shim.log"
    log.write_text("", encoding="utf-8")
    owner = "0x" + "aa" * 20

    def run(*args: str, **extra: str):
        env = dict(
            PATH=str(shim) + os.pathsep + os.environ.get("PATH", ""),
            HOME=os.environ.get("HOME", ""),
            SHIM_LOG=str(log),
            CHAIN_SHOWN="133",
            OWNER_SHOWN=owner,
            CHECKSUM_SHOWN="0x5FbDB2315678afecb367f032d93F642f64180aa3",
            CODE_SHOWN=CODE_BUILT,
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
        )
        env.update(extra)
        return subprocess.run(
            ["bash", str(tree / "script" / "deploy.sh"), *args],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
            stdin=subprocess.DEVNULL,
        )

    def forge_calls() -> list[list[str]]:
        calls: list[list[str]] = []
        current: list[str] = []
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.startswith("cast "):
                continue
            if line in ("script", "verify-contract", "inspect"):
                if current:
                    calls.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            calls.append(current)
        return calls

    try:
        yield run, forge_calls, log, tree, owner
    finally:
        server.shutdown()
        server.server_close()


def test_deploy_script_stops_on_a_mismatched_chain_before_forge(deploy_tree):
    run, forge_calls, _log, _tree, _owner = deploy_tree
    _ChainNode.chain = "0xb1"
    done = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY)
    assert done.returncode != 0
    assert "endpoint answered chain 177, expected 133" in done.stderr
    assert "stopped before any deploy" in done.stderr
    assert forge_calls() == []
    assert KEY_BODY not in done.stderr
    rpc = json.loads((_tree / "networks.json").read_text())["testnet"]["rpc"]
    assert f"cast chain-id --rpc-url {rpc}" in _log.read_text(encoding="utf-8"), \
        "cast, not a second HTTP client, asks the chain"
    assert "urllib" not in SCRIPT.read_text(encoding="utf-8")


def test_deploy_script_simulates_with_the_expected_chain_and_shows_the_owner(deploy_tree):
    run, forge_calls, _log, _tree, owner = deploy_tree
    done = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY)
    assert done.returncode == 0, done.stderr
    calls = forge_calls()
    assert len(calls) == 1
    assert calls[0] == ["script", "script/DeployHashKeySeal.s.sol:DeployHashKeySeal", "--sig",
                        "run(uint256,address,address)", "133", ADDRESS, ZERO_ADDRESS,
                        "--rpc-url", json.loads((_tree / "networks.json").read_text())["testnet"]["rpc"]]
    assert "--chain" not in calls[0] and "--broadcast" not in calls[0]
    assert f"owner-to-be {owner}" in done.stderr
    assert "simulation only" in done.stderr
    assert "network testnet chain 133" in done.stderr
    assert KEY_BODY not in done.stderr and KEY_BODY not in done.stdout


def test_deploy_script_broadcasts_on_testnet_after_the_simulation(deploy_tree):
    run, forge_calls, _log, _tree, owner = deploy_tree
    done = run("--network", "testnet", "--broadcast", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY)
    assert done.returncode == 0, done.stderr
    assert f"owner-to-be {owner}" in done.stderr
    calls = forge_calls()
    assert len(calls) == 2
    assert "--broadcast" not in calls[0]
    assert calls[1][-1] == "--broadcast"
    assert calls[1][4:7] == ["133", ADDRESS, owner], "the broadcast carries the owner the simulation showed"


def test_deploy_script_stops_when_the_simulation_names_no_owner(deploy_tree):
    run, forge_calls, _log, _tree, _owner = deploy_tree
    done = run("--network", "testnet", "--broadcast", HASHKEY_SEAL_PUBLISHER=ADDRESS,
               ETH_PRIVATE_KEY="0x" + KEY_BODY, OWNER_SHOWN="")
    assert done.returncode != 0
    assert "did not report the owner-to-be" in done.stderr
    assert "owner-to-be 0x" not in done.stderr
    assert len(forge_calls()) == 1, "the simulation ran, the broadcast did not"


def _forge_toolchain_or_skip(tmp_path):
    if shutil.which("forge") is None or shutil.which("anvil") is None:
        pytest.skip("forge and anvil are not on PATH")
    copy = tmp_path / "real" / "contracts" / "hashkey"
    shutil.copytree(HASHKEY, copy, ignore=shutil.ignore_patterns("cache", "out", "broadcast"))
    built = subprocess.run(["forge", "build"], cwd=copy, capture_output=True, text=True, check=False)
    if built.returncode != 0:
        pytest.skip("forge cannot compile here (no solc): " + built.stderr.strip().splitlines()[-1][:120])
    return copy


def test_the_real_forge_simulation_prints_the_owner_line_the_shell_parses(tmp_path, monkeypatch):
    """The shim prints the owner line itself; this is the one check that the
    real script contract and the shell's regex agree. Skipped without a
    working forge, which CI's Python job does not have."""
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    copy = _forge_toolchain_or_skip(tmp_path)
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    anvil = subprocess.Popen(["anvil", "--chain-id", "133", "--port", str(port), "--silent"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        table = json.loads(NETWORKS.read_text(encoding="utf-8"))
        table["testnet"]["rpc"] = f"http://127.0.0.1:{port}"
        (copy / "networks.json").write_text(json.dumps(table), encoding="utf-8")
        deadline = time.monotonic() + 20
        import urllib.request
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{port}", data=b'{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',
                    headers={"content-type": "application/json"}), timeout=2).read()
                break
            except OSError:
                time.sleep(0.2)
        env = dict(os.environ, HASHKEY_SEAL_PUBLISHER="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
                   ETH_PRIVATE_KEY="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
        for name in ("RUNECLAW_SERVER", "HASHKEY_SEAL_SIGNER_KEY", "HASHKEY_WALLET"):
            env.pop(name, None)
        done = subprocess.run(
            ["bash", str(copy / "script" / "deploy.sh"), "--network", "testnet"],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
        )
        assert done.returncode == 0, done.stderr[-1500:]
        assert "owner-to-be 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266" in done.stderr
        assert "simulation only" in done.stderr
    finally:
        anvil.kill()
        anvil.wait()


def test_deploy_script_asks_for_the_owner_on_mainnet(deploy_tree):
    run, forge_calls, _log, _tree, owner = deploy_tree
    _ChainNode.chain = "0xb1"
    no_tty = run("--network", "mainnet", "--broadcast", HASHKEY_SEAL_PUBLISHER=ADDRESS,
                 ETH_PRIVATE_KEY="0x" + KEY_BODY, CHAIN_SHOWN="177", HASHKEY_ALLOW_ENV_KEY="1")
    assert no_tty.returncode != 0
    assert "HASHKEY_SEAL_CONFIRM_OWNER" in no_tty.stderr
    assert len(forge_calls()) == 1, "the simulation ran, the broadcast did not"
    wrong = run("--network", "mainnet", "--broadcast", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY,
                CHAIN_SHOWN="177", HASHKEY_SEAL_CONFIRM_OWNER="0x" + "bb" * 20, HASHKEY_ALLOW_ENV_KEY="1")
    assert wrong.returncode != 0 and "owner not confirmed" in wrong.stderr
    assert len(forge_calls()) == 2
    right = run("--network", "mainnet", "--broadcast", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY,
                CHAIN_SHOWN="177", HASHKEY_SEAL_CONFIRM_OWNER=owner.upper().replace("0X", "0x"),
                HASHKEY_ALLOW_ENV_KEY="1")
    assert right.returncode == 0, right.stderr
    calls = forge_calls()
    assert len(calls) == 4 and calls[-1][-1] == "--broadcast" and calls[-1][4:7] == ["177", ADDRESS, owner]


def test_deploy_script_requires_the_0x_prefix_and_a_right_checksum(deploy_tree):
    run, forge_calls, _log, _tree, _owner = deploy_tree
    bare = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY=KEY_BODY)
    assert bare.returncode != 0 and "0x followed by 64 hex" in bare.stderr
    assert KEY_BODY not in bare.stderr
    assert forge_calls() == []
    short = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY[:-2])
    assert short.returncode != 0 and forge_calls() == []
    typo = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER="0x5FbDB2315678afecb367f032d93F642f64180AA3",
               ETH_PRIVATE_KEY="0x" + KEY_BODY)
    assert typo.returncode != 0 and "checksum" in typo.stderr
    assert forge_calls() == []
    bad = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER="0x1234", ETH_PRIVATE_KEY="0x" + KEY_BODY)
    assert bad.returncode != 0 and "20-byte hex address" in bad.stderr
    good = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER="0x5FbDB2315678afecb367f032d93F642f64180aa3",
               ETH_PRIVATE_KEY="0x" + KEY_BODY)
    assert good.returncode == 0, good.stderr


def test_deploy_script_uses_the_wallet_flags_and_refuses_a_stray_dotenv(deploy_tree):
    run, forge_calls, _log, tree, _owner = deploy_tree
    ledger = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger")
    assert ledger.returncode == 0, ledger.stderr
    calls = forge_calls()
    assert calls[-1][2:4] == ["--sig", "runWithCliWallet(uint256,address,address)"]
    assert "--ledger" in calls[-1]
    account = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="account",
                  HASHKEY_ACCOUNT="mykey")
    assert account.returncode == 0, account.stderr
    assert forge_calls()[-1][-2:] == ["--account", "mykey"]
    other = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="paper")
    assert other.returncode != 0 and "ledger, trezor, or account" in other.stderr
    (tree / ".env").write_text("ETH_PRIVATE_KEY=0x" + KEY_BODY + "\n", encoding="utf-8")
    stray = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger")
    assert stray.returncode != 0 and "contracts/hashkey/.env" in stray.stderr
    assert KEY_BODY not in stray.stderr


def test_deploy_script_verify_names_the_evm_version_and_signs_nothing(deploy_tree):
    run, forge_calls, _log, _tree, _owner = deploy_tree
    done = run("--network", "testnet", "--verify", "0x5fbdb2315678afecb367f032d93f642f64180aa3",
               HASHKEY_SEAL_PUBLISHER=ADDRESS)
    assert done.returncode == 0, done.stderr
    assert "matches this build" in done.stderr
    calls = forge_calls()
    assert [c[0] for c in calls] == ["inspect", "verify-contract"]
    assert calls[0] == ["inspect", "src/HashKeySeal.sol:HashKeySeal", "deployedBytecode"]
    argv = calls[1]
    assert argv[argv.index("--evm-version") + 1] == "cancun"
    assert argv[argv.index("--chain") + 1] == "133"
    assert argv[argv.index("--constructor-args") + 1] == "0xencoded"
    assert "--broadcast" not in argv and "run(" not in " ".join(argv)


def test_deploy_script_refuses_a_raw_key_on_mainnet_unless_allowed(deploy_tree):
    run, forge_calls, _log, _tree, owner = deploy_tree
    _ChainNode.chain = "0xb1"
    refused = run("--network", "mainnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY,
                  CHAIN_SHOWN="177")
    assert refused.returncode != 0
    assert "HASHKEY_ALLOW_ENV_KEY" in refused.stderr and "raw ETH_PRIVATE_KEY on mainnet" in refused.stderr
    assert KEY_BODY not in refused.stderr
    assert forge_calls() == []
    _no_chain_probe(refused)
    allowed = run("--network", "mainnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY,
                  CHAIN_SHOWN="177", HASHKEY_ALLOW_ENV_KEY="1")
    assert allowed.returncode == 0, allowed.stderr
    assert f"owner-to-be {owner}" in allowed.stderr and len(forge_calls()) == 1
    hardware = run("--network", "mainnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger", CHAIN_SHOWN="177")
    assert hardware.returncode == 0, hardware.stderr
    assert len(forge_calls()) == 2
    _ChainNode.chain = "0x85"
    testnet = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, ETH_PRIVATE_KEY="0x" + KEY_BODY)
    assert testnet.returncode == 0, testnet.stderr


def test_deploy_script_refuses_the_testnet_publisher_as_mainnet_publisher_or_owner(deploy_tree):
    run, forge_calls, _log, tree, _owner = deploy_tree
    _ChainNode.chain = "0xb1"
    as_publisher = run("--network", "mainnet", HASHKEY_SEAL_PUBLISHER=TESTNET_PUBLISHER, HASHKEY_WALLET="ledger",
                       CHAIN_SHOWN="177")
    assert as_publisher.returncode != 0
    assert "HASHKEY_SEAL_PUBLISHER is the testnet publisher" in as_publisher.stderr
    assert forge_calls() == []
    as_owner = run("--network", "mainnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger",
                   CHAIN_SHOWN="177", OWNER_SHOWN=TESTNET_PUBLISHER.upper().replace("0X", "0x"))
    assert as_owner.returncode != 0
    assert "owner-to-be is the testnet publisher" in as_owner.stderr
    assert "simulation only" not in as_owner.stderr
    assert len(forge_calls()) == 1, "the simulation ran and showed the owner; nothing more"
    other = run("--network", "mainnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger", CHAIN_SHOWN="177")
    assert other.returncode == 0, other.stderr
    _ChainNode.chain = "0x85"
    on_testnet = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=TESTNET_PUBLISHER, HASHKEY_WALLET="ledger")
    assert on_testnet.returncode == 0, "the testnet publisher is the testnet publisher"
    # Unrecorded is not "nobody": a mainnet run stops; a testnet run does not need it.
    table = json.loads((tree / "networks.json").read_text(encoding="utf-8"))
    table["testnet"]["publisher"] = None
    (tree / "networks.json").write_text(json.dumps(table), encoding="utf-8")
    _ChainNode.chain = "0xb1"
    unrecorded = run("--network", "mainnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger", CHAIN_SHOWN="177")
    assert unrecorded.returncode != 0
    assert "does not record testnet.publisher" in unrecorded.stderr
    assert "stopped before any deploy" in unrecorded.stderr
    _ChainNode.chain = "0x85"
    still_testnet = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger")
    assert still_testnet.returncode == 0, still_testnet.stderr
    table["testnet"]["publisher"] = "0x12"
    (tree / "networks.json").write_text(json.dumps(table), encoding="utf-8")
    malformed = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger")
    assert malformed.returncode != 0 and "testnet.publisher is not a 20-byte hex address" in malformed.stderr


def test_deploy_script_refuses_foundry_overrides_that_change_the_bytecode(deploy_tree):
    run, forge_calls, _log, _tree, _owner = deploy_tree
    for name, value in (("FOUNDRY_EVM_VERSION", "paris"), ("FOUNDRY_OPTIMIZER", "false"), ("FOUNDRY_PROFILE", "ci")):
        done = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger", **{name: value})
        assert done.returncode != 0, name
        assert name in done.stderr and "overrides foundry.toml" in done.stderr
        assert forge_calls() == []
        _no_chain_probe(done)
    clean = run("--network", "testnet", HASHKEY_SEAL_PUBLISHER=ADDRESS, HASHKEY_WALLET="ledger")
    assert clean.returncode == 0, clean.stderr


def test_deploy_script_verify_stops_on_a_moved_explorer_or_foreign_code(deploy_tree):
    run, forge_calls, _log, _tree, _owner = deploy_tree
    address = "0x5fbdb2315678afecb367f032d93f642f64180aa3"
    _ChainNode.redirect = True
    moved = run("--network", "testnet", "--verify", address, HASHKEY_SEAL_PUBLISHER=ADDRESS)
    assert moved.returncode != 0
    assert "a redirect" in moved.stderr and "networks.json" in moved.stderr and "nothing was submitted" in moved.stderr
    assert forge_calls() == []
    _ChainNode.redirect = False
    foreign = run("--network", "testnet", "--verify", address, HASHKEY_SEAL_PUBLISHER=ADDRESS,
                  CODE_ONCHAIN="0x6002aa0001")
    assert foreign.returncode != 0
    assert "not this tree's build" in foreign.stderr
    assert [c[0] for c in forge_calls()] == ["inspect"], "the build was read; nothing was submitted"
    empty = run("--network", "testnet", "--verify", address, HASHKEY_SEAL_PUBLISHER=ADDRESS, CODE_ONCHAIN="0x")
    assert empty.returncode != 0 and "no code at the address" in empty.stderr
    assert [c[0] for c in forge_calls()] == ["inspect", "inspect"]
    # A different metadata tail is the same executable code.
    same = run("--network", "testnet", "--verify", address, HASHKEY_SEAL_PUBLISHER=ADDRESS, CODE_ONCHAIN="0x6001bb0001")
    assert same.returncode == 0, same.stderr
    assert "matches this build" in same.stderr
    assert [c[0] for c in forge_calls()][-1] == "verify-contract"


def test_deploy_script_does_not_echo_an_unknown_argument():
    pasted = "0x" + KEY_BODY
    done = _run_script("--network", "testnet", pasted, HASHKEY_SEAL_PUBLISHER=ADDRESS)
    assert done.returncode != 0
    assert "unknown argument" in done.stderr
    assert pasted not in done.stderr and KEY_BODY not in done.stderr and KEY_BODY not in done.stdout


def test_the_server_key_lists_agree_across_the_guard_and_both_operator_scripts():
    """mainnet_guard's tuple is the reading; the two shell loops are copies
    of it. A key added to one and not the others is a key one of them does
    not refuse."""
    from bot.web.mainnet_guard import _TESTNET_CAPABLE_KEYS

    deploy = SCRIPT.read_text(encoding="utf-8")
    loop = deploy.split("for leaked in", 1)[1].split("\ndo\n", 1)[0]
    deploy_keys = set(re.findall(r"[A-Z0-9_]+_KEY\b", loop))
    roots = (ROOT / "scripts" / "anchor_roots.sh").read_text(encoding="utf-8")
    roots_loop = roots.split("for leaked in ", 1)[1].split(";", 1)[0]
    roots_keys = set(roots_loop.split())
    assert deploy_keys == set(_TESTNET_CAPABLE_KEYS), (deploy_keys, _TESTNET_CAPABLE_KEYS)
    assert roots_keys == set(_TESTNET_CAPABLE_KEYS), (roots_keys, _TESTNET_CAPABLE_KEYS)
    assert len(deploy_keys) >= 5


# ---------------------------------------------------------------------------
# one reading per quantity


def _solidity_code_only(source: str) -> str:
    """The contract with its comments blanked: a NatSpec line that names the
    thing the scan forbids is indistinguishable from code doing it."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", without_block)


def test_chain_ids_agree_and_the_contract_holds_nothing():
    table = json.loads(NETWORKS.read_text(encoding="utf-8"))
    assert table["testnet"]["chain_id"] == 133
    assert table["testnet"]["rpc"] == "https://testnet.hsk.xyz"
    assert table["mainnet"]["chain_id"] == 177
    assert table["mainnet"]["rpc"] == "https://mainnet.hsk.xyz"
    assert "chain_id_hex" not in table["testnet"] and "chain_id_hex" not in table["mainnet"]
    checked = table["rpc_checked"]
    assert checked["method"] == "eth_chainId"
    assert int(checked["testnet_answered"], 16) == table["testnet"]["chain_id"]
    assert int(checked["mainnet_answered"], 16) == table["mainnet"]["chain_id"]
    assert table["explorer_checked"] is None or isinstance(table["explorer_checked"], dict)
    sol = SOL.read_text(encoding="utf-8")
    assert f"TESTNET_CHAIN_ID = {table['testnet']['chain_id']};" in sol
    assert f"MAINNET_CHAIN_ID = {table['mainnet']['chain_id']};" in sol
    assert "envAddress" not in sol and "envOr" not in sol, "publisher and owner are script arguments"
    src = _solidity_code_only(CONTRACT.read_text(encoding="utf-8"))
    for word in ("payable", "selfdestruct", "delegatecall", "assembly", "receive", "fallback"):
        assert word not in src, word
    foundry = FOUNDRY.read_text(encoding="utf-8")
    assert "[rpc_endpoints]" not in foundry, "networks.json is the one reading of the endpoints"
    evm = re.search(r'^evm_version\s*=\s*"([a-z]+)"', foundry, re.M)
    assert evm is not None
    script = SCRIPT.read_text(encoding="utf-8")
    shell = re.search(r'^EVM_VERSION="([a-z]+)"', script, re.M)
    assert shell is not None and shell.group(1) == evm.group(1)
    route = ROUTE.read_text(encoding="utf-8")
    assert "optionalAuth" in route
    assert "177" not in route
    assert "eth_sendRawTransaction" not in route
    assert "new Wallet(" not in route
    assert "spawnSync" not in route, "the child runs off the event loop"


def test_the_route_budget_exceeds_the_child_deadline():
    route = ROUTE.read_text(encoding="utf-8")
    child = re.search(r"DEFAULT_CHILD_DEADLINE_SEC\s*=\s*(\d+)", route)
    margin = re.search(r"PARENT_MARGIN_SEC\s*=\s*(\d+)", route)
    assert child is not None and margin is not None
    assert int(child.group(1)) == DEFAULT_DEADLINE_SEC
    assert int(margin.group(1)) > 0


def test_the_doc_and_the_ci_job_name_the_same_chains():
    doc = DOC.read_text(encoding="utf-8")
    assert "internal review" in doc.lower()
    assert "https://testnet.hsk.xyz" in doc
    assert "https://mainnet.hsk.xyz" in doc
    assert "forge test" in doc
    assert "--verify" in doc
    assert "by hand" in doc.lower()
    assert "anchorOf" in doc
    assert "cancun" in doc
    assert "HASHKEY_SEAL_CONFIRM_OWNER" in doc
    assert "stores a hash and a kind" not in doc
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "forge test" in ci
    assert "run: forge snapshot --no-match-test 'testFuzz_' --check" in ci
    assert "foundry/releases/download/v1.8.6/" in ci
    assert "58850106984038f3a7200f38a4182c8cb423d3a4f4c7848eacbc2453e6bc35fc" in ci
    assert (HASHKEY / ".gas-snapshot").is_file()


def test_new_files_do_not_use_the_forbidden_words():
    roots = [
        ROOT / "contracts" / "hashkey",
        ROOT / "bot" / "hashkey_seal",
        ROOT / "docs" / "hackathons",
        ROOT / "app" / "lib" / "hashkey_seal_flag.js",
        ROOT / "app" / "routes" / "hashkey_seal.js",
        ROOT / "app" / "test" / "hashkey_seal.test.js",
        Path(__file__),
    ]
    hits = []
    for root in roots:
        files = [root] if root.is_file() else list(root.rglob("*"))
        for path in files:
            if not path.is_file():
                continue
            if any(part in {"cache", "out", "broadcast"} for part in path.parts):
                continue
            if path.suffix in {".json", ".pyc"} and "networks.json" not in path.name:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _BANNED.search(text):
                hits.append(str(path.relative_to(ROOT)))
    assert hits == []
