"""HashKey seal publisher, startup refusal, and the owner deploy script.

The publisher signs HashKey testnet only. A chain that is not that testnet,
a failed read, and a hash already stored are all refused before the signer
key is read. The deploy script is the owner's, and it refuses the server.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from bot.fvb.keccak import keccak256
from bot.hashkey_seal.flag import flag_on
from bot.hashkey_seal.networks import mainnet_chain_id as hashkey_mainnet_chain_id
from bot.hashkey_seal.networks import testnet_chain_id as hashkey_testnet_chain_id
from bot.hashkey_seal.networks import testnet_rpc as hashkey_testnet_rpc
from bot.hashkey_seal.publish import (
    RpcFailed,
    anchor_calldata,
    publish_anchor,
    read_anchor,
    status,
)
from bot.web.mainnet_guard import startup_refusal

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "contracts" / "hashkey" / "script" / "deploy.sh"
NETWORKS = ROOT / "contracts" / "hashkey" / "networks.json"
SOL = ROOT / "contracts" / "hashkey" / "script" / "DeployHashKeySeal.s.sol"
CONTRACT = ROOT / "contracts" / "hashkey" / "src" / "HashKeySeal.sol"
DOC = ROOT / "docs" / "hackathons" / "hashkey.md"

ADDRESS = "0x" + "11" * 20
SEAL = "0x" + "22" * 32
KIND = "0x" + "33" * 32
SENDER = "0x" + "aa" * 20
FALSE_WORD = "0x" + "00" * 32
TRUE_WORD = "0x" + ("00" * 31) + "01"
TX_HASH = "0x" + "cd" * 32
KEY_BODY = "ab" * 32
RAW = "0x" + "ee" * 8

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


class _Endpoint:
    def __init__(self):
        self.chain = "0x85"
        self.anchored = FALSE_WORD
        self.gas = "0x5208"
        self.base = "0x10"
        self.priority = "0x2"
        self.nonce = "0x3"
        self.sent = TX_HASH
        self.fail_chain = False
        self.fail_call = False
        self.fail_send = False
        self.omit_base = False
        self.calls: list[tuple] = []

    def __call__(self, method, params):
        self.calls.append((method, list(params)))
        if method == "eth_chainId":
            if self.fail_chain:
                raise RpcFailed()
            return self.chain
        if method == "eth_call":
            if self.fail_call:
                raise RpcFailed()
            return self.anchored
        if method == "eth_getTransactionCount":
            return self.nonce
        if method == "eth_estimateGas":
            return self.gas
        if method == "eth_getBlockByNumber":
            if self.omit_base:
                return {}
            return {"baseFeePerGas": self.base}
        if method == "eth_maxPriorityFeePerGas":
            return self.priority
        if method == "eth_sendRawTransaction":
            if self.fail_send:
                raise RpcFailed()
            return self.sent
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


def _publish(env, endpoint, wallet, seal=SEAL, kind=KIND):
    return publish_anchor(
        env, seal, kind,
        transport=endpoint, sender_of=wallet.sender, signer=wallet.sign,
    )


def _script_env(**extra: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    env.update(extra)
    return env


def _run_script(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT, env=_script_env(**extra),
        capture_output=True, text=True, check=False,
    )


def test_flag_off_does_not_read_the_key():
    env = _env(HASHKEY_SEAL_ENABLED="0")
    endpoint, wallet = _Endpoint(), _Wallet()
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "flag_off"
    assert out["signed"] is False and out["broadcast"] is False
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    assert wallet.keys == []
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
    env = _env(HASHKEY_SEAL_CHAIN_ID="nope")
    endpoint, wallet = _Endpoint(), _Wallet()
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "chain_unreadable"
    assert out["chain_id"] is None
    assert "HASHKEY_SEAL_SIGNER_KEY" not in env.reads
    assert wallet.keys == []


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


def test_publish_sends_a_zero_value_testnet_transaction():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    out = _publish(env, endpoint, wallet)
    assert out["ok"] is True
    assert out["signed"] is True and out["broadcast"] is True
    assert out["reason"] is None
    assert out["tx_hash"] == TX_HASH
    assert out["anchored"] is None
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
    sent = [params for method, params in endpoint.calls if method == "eth_sendRawTransaction"]
    assert sent == [[RAW]]


def test_a_rejected_send_stays_signed_and_unbroadcast():
    env = _env()
    endpoint, wallet = _Endpoint(), _Wallet()
    endpoint.fail_send = True
    out = _publish(env, endpoint, wallet)
    assert out["reason"] == "rejected"
    assert out["signed"] is True
    assert out["broadcast"] is False
    assert out["anchored"] is False
    dumped = json.dumps(out)
    assert RAW not in dumped
    assert KEY_BODY not in dumped


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

    bad_body = "zz" * 16
    endpoint, wallet = _Endpoint(), _Wallet()
    bad = _env(HASHKEY_SEAL_SIGNER_KEY=bad_body)
    out = _publish(bad, endpoint, wallet)
    assert out["reason"] == "bad_key"
    assert out["signed"] is False
    assert wallet.keys == []
    assert bad_body not in json.dumps(out)


def test_read_does_not_touch_the_key_and_keeps_three_answers():
    env = _env()
    endpoint = _Endpoint()
    false = read_anchor(env, SEAL, transport=endpoint)
    assert false == {"readable": True, "anchored": False, "reason": None, "chain_id": 133}
    endpoint.anchored = TRUE_WORD
    true = read_anchor(env, SEAL, transport=endpoint)
    assert true["anchored"] is True and true["readable"] is True
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
    assert KEY_BODY not in json.dumps(body)
    assert hashkey_testnet_rpc() == "https://testnet.hsk.xyz"


def test_startup_refuses_a_seal_key_aimed_at_any_chain_but_testnet():
    key = "0x" + KEY_BODY
    refused = startup_refusal({
        "HASHKEY_SEAL_SIGNER_KEY": key,
        "HASHKEY_SEAL_CHAIN_ID": "177",
    })
    assert refused is not None
    assert "does not sign" in refused
    assert KEY_BODY not in refused
    assert "177" not in refused

    other = startup_refusal({
        "HASHKEY_SEAL_SIGNER_KEY": key,
        "HASHKEY_SEAL_CHAIN_ID": "1",
    })
    assert other is not None
    assert "does not start" in other
    assert KEY_BODY not in other

    assert startup_refusal({
        "HASHKEY_SEAL_SIGNER_KEY": key,
        "HASHKEY_SEAL_CHAIN_ID": "133",
    }) is None
    assert startup_refusal({"HASHKEY_SEAL_SIGNER_KEY": key}) is None
    assert startup_refusal({"HASHKEY_SEAL_CHAIN_ID": "177"}) is None


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


def test_cli_usage_does_not_sign(capsys):
    from bot.hashkey_seal.__main__ import main

    assert main(["nope"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "usage"


def test_deploy_script_refuses_the_server_before_any_chain():
    planted = "0x" + KEY_BODY
    syntax = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert syntax.returncode == 0, syntax.stderr
    assert os.access(SCRIPT, os.X_OK)

    refused = _run_script("--network", "testnet", RUNECLAW_SERVER="1", ETH_PRIVATE_KEY=planted)
    assert refused.returncode != 0
    assert "RUNECLAW_SERVER" in refused.stderr
    assert planted not in refused.stderr and KEY_BODY not in refused.stderr
    assert "endpoint answered" not in refused.stderr

    leaked = _run_script(
        "--network", "testnet",
        HASHKEY_SEAL_SIGNER_KEY=planted,
        HASHKEY_SEAL_PUBLISHER=ADDRESS,
    )
    assert leaked.returncode != 0
    assert "HASHKEY_SEAL_SIGNER_KEY" in leaked.stderr
    assert planted not in leaked.stderr and KEY_BODY not in leaked.stderr
    assert "endpoint answered" not in leaked.stderr

    both = _run_script(
        "--network", "testnet",
        HASHKEY_WALLET="ledger",
        ETH_PRIVATE_KEY=planted,
        HASHKEY_SEAL_PUBLISHER=ADDRESS,
    )
    assert both.returncode != 0
    assert "both set" in both.stderr
    assert planted not in both.stderr

    unknown = _run_script("--network", "other")
    assert unknown.returncode != 0
    assert "testnet or mainnet" in unknown.stderr

    missing = _run_script("--network", "testnet")
    assert missing.returncode != 0
    assert "HASHKEY_SEAL_PUBLISHER" in missing.stderr
    assert "endpoint answered" not in missing.stderr

    help_text = _run_script("--help")
    assert help_text.returncode == 0
    assert "HASHKEY_SEAL_PUBLISHER" in help_text.stdout
    assert "--broadcast" in help_text.stdout


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
    after = text.split("ARGS=(forge script", 1)[1]
    assert "ETH_PRIVATE_KEY" not in after
    assert "HASHKEY_SEAL_SIGNER_KEY" not in after


def test_chain_ids_agree_and_the_contract_holds_nothing():
    table = json.loads(NETWORKS.read_text(encoding="utf-8"))
    assert table["testnet"]["chain_id"] == 133
    assert table["testnet"]["chain_id_hex"] == "0x85"
    assert table["testnet"]["rpc"] == "https://testnet.hsk.xyz"
    assert table["mainnet"]["chain_id"] == 177
    assert table["mainnet"]["chain_id_hex"] == "0xb1"
    assert table["mainnet"]["rpc"] == "https://mainnet.hsk.xyz"
    sol = SOL.read_text(encoding="utf-8")
    assert f"TESTNET_CHAIN_ID = {table['testnet']['chain_id']};" in sol
    assert f"MAINNET_CHAIN_ID = {table['mainnet']['chain_id']};" in sol
    src = CONTRACT.read_text(encoding="utf-8")
    for word in ("payable", "selfdestruct", "delegatecall"):
        assert word not in src
    route = (ROOT / "app" / "routes" / "hashkey_seal.js").read_text(encoding="utf-8")
    assert "optionalAuth" in route
    assert "177" not in route
    assert "eth_sendRawTransaction" not in route
    assert "new Wallet(" not in route


def test_the_doc_and_the_ci_job_name_the_same_chains():
    doc = DOC.read_text(encoding="utf-8")
    assert "internal review" in doc.lower()
    assert "https://testnet.hsk.xyz" in doc
    assert "https://mainnet.hsk.xyz" in doc
    assert "forge test" in doc
    assert "--verify" in doc
    assert "by hand" in doc.lower()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "forge test" in ci
    assert "foundry/releases/download/v1.8.6/" in ci
    assert "58850106984038f3a7200f38a4182c8cb423d3a4f4c7848eacbc2453e6bc35fc" in ci


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
