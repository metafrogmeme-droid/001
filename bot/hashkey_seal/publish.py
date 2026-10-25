"""Publish one seal hash to HashKey testnet, or refuse before signing.

The flag, the chain, and a readable ``isAnchored`` all come before the
signer key. Mainnet is not a target: an endpoint that answers any chain
id other than the testnet id is refused, and the key is not read.
A failed read is not "not anchored".
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Mapping, Optional

from bot.fvb.keccak import keccak256
from bot.hashkey_seal.flag import flag_on
from bot.hashkey_seal.networks import mainnet_chain_id, testnet_chain_id, testnet_rpc

SIGNER_KEY = "HASHKEY_SEAL_SIGNER_KEY"
CHAIN_ID_ENV = "HASHKEY_SEAL_CHAIN_ID"
ADDRESS_ENV = "HASHKEY_SEAL_ADDRESS"
RPC_ENV = "HASHKEY_SEAL_RPC_URL"

_HEX = frozenset("0123456789abcdefABCDEF")

Transport = Callable[[str, list], object]
SenderOf = Callable[[str], str]
Signer = Callable[[dict, str], str]


class RpcFailed(Exception):
    """The endpoint did not answer a usable result. The message stays empty."""


class SignerUnavailable(Exception):
    """eth-account is not installed. Nothing was signed."""


def anchor_calldata(seal_hash: bytes, kind: bytes) -> bytes:
    return keccak256(b"anchor(bytes32,bytes32)")[:4] + seal_hash + kind


def is_anchored_calldata(seal_hash: bytes) -> bytes:
    return keccak256(b"isAnchored(bytes32)")[:4] + seal_hash


def kind_claw_guard() -> bytes:
    return keccak256(b"claw-guard")


def kind_agent_leash() -> bytes:
    return keccak256(b"agent-leash")


def _word(value: object) -> Optional[bytes]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text[:2].lower() == "0x":
        text = text[2:]
    if len(text) != 64 or any(c not in _HEX for c in text):
        return None
    return bytes.fromhex(text)


def _address(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) != 42 or text[:2].lower() != "0x":
        return None
    body = text[2:]
    if any(c not in _HEX for c in body):
        return None
    return "0x" + body.lower()


def _quantity(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith("0x"):
        return None
    body = text[2:]
    if not body or any(c not in _HEX for c in body):
        return None
    return int(body, 16)


def _bool_word(value: object) -> Optional[bool]:
    word = _word(value)
    if word is None:
        return None
    n = int.from_bytes(word, "big")
    if n == 0:
        return False
    if n == 1:
        return True
    return None


def _chain_setting(env: Mapping[str, str]) -> tuple[Optional[int], Optional[str]]:
    """(chain id or None when unset, reason when the text is not a chain id)."""
    raw = env.get(CHAIN_ID_ENV)
    if raw is None or str(raw).strip() == "":
        return None, None
    text = str(raw).strip()
    if text.startswith("+") or not text.isdigit():
        return None, "chain_unreadable"
    return int(text), None


def _key(env: Mapping[str, str]) -> tuple[Optional[str], Optional[str]]:
    raw = env.get(SIGNER_KEY)
    if raw is None or str(raw).strip() == "":
        return None, "no_key"
    text = str(raw).strip()
    body = text[2:] if text[:2].lower() == "0x" else text
    if len(body) != 64 or any(c not in _HEX for c in body):
        return None, "bad_key"
    return "0x" + body.lower(), None


def rpc_url(env: Mapping[str, str]) -> str:
    raw = env.get(RPC_ENV)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return testnet_rpc()


def http_transport(url: str) -> Transport:
    def call(method: str, params: list) -> object:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"content-type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            raise RpcFailed from exc
        if not isinstance(payload, dict) or payload.get("error") or "result" not in payload:
            raise RpcFailed
        return payload["result"]

    return call


def _refused(reason: str, **extra: object) -> dict:
    body: dict = {
        "ok": False,
        "signed": False,
        "broadcast": False,
        "reason": reason,
        "tx_hash": None,
        "readable": None,
        "anchored": None,
        "chain_id": None,
    }
    for key in ("signed", "broadcast", "readable", "anchored", "chain_id", "tx_hash"):
        if key in extra:
            body[key] = extra[key]
    return body


def read_anchor(
    env: Mapping[str, str],
    seal_hash: object,
    *,
    transport: Optional[Transport] = None,
) -> dict:
    """Ask ``isAnchored``. Does not read the signer key and does not sign."""
    word = _word(seal_hash)
    if word is None:
        return {"readable": False, "anchored": None, "reason": "bad_hash"}
    address = _address(env.get(ADDRESS_ENV))
    if address is None:
        return {"readable": False, "anchored": None, "reason": "no_contract"}
    configured, why = _chain_setting(env)
    if why:
        return {"readable": False, "anchored": None, "reason": why}
    if configured is not None and configured != testnet_chain_id():
        return {
            "readable": False,
            "anchored": None,
            "reason": "not_testnet",
            "chain_id": configured,
        }
    call = transport if transport is not None else http_transport(rpc_url(env))
    try:
        chain = _quantity(call("eth_chainId", []))
        if chain is None:
            return {"readable": False, "anchored": None, "reason": "chain_unreadable"}
        if chain != testnet_chain_id():
            return {
                "readable": False,
                "anchored": None,
                "reason": "not_testnet",
                "chain_id": chain,
            }
        data = "0x" + is_anchored_calldata(word).hex()
        result = call("eth_call", [{"to": address, "data": data}, "latest"])
    except RpcFailed:
        return {"readable": False, "anchored": None, "reason": "rpc_unreadable"}
    anchored = _bool_word(result)
    if anchored is None:
        return {"readable": False, "anchored": None, "reason": "result_unreadable", "chain_id": chain}
    return {"readable": True, "anchored": anchored, "reason": None, "chain_id": chain}


def status(env: Mapping[str, str]) -> dict:
    """Whether a publish could be attempted. The key's value is not returned."""
    present = env.get(SIGNER_KEY)
    signer_set = bool(str(present).strip()) if present is not None else False
    configured, why = _chain_setting(env)
    return {
        "flag_on": flag_on(env),
        "chain_id": testnet_chain_id(),
        "refused_chain_id": mainnet_chain_id(),
        "configured_chain_id": configured,
        "chain_reason": why,
        "contract_set": _address(env.get(ADDRESS_ENV)) is not None,
        "signer_set": signer_set,
        "kinds": {
            "claw-guard": "0x" + kind_claw_guard().hex(),
            "agent-leash": "0x" + kind_agent_leash().hex(),
        },
    }


def _default_sender(key: str) -> str:
    try:
        from eth_account import Account
    except ImportError as exc:
        raise SignerUnavailable from exc
    address = Account.from_key(key).address
    if not isinstance(address, str) or not address:
        raise SignerUnavailable
    return address


def _default_signer(tx: dict, key: str) -> str:
    try:
        from eth_account import Account
    except ImportError as exc:
        raise SignerUnavailable from exc
    signed = Account.sign_transaction(tx, key)
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = getattr(signed, "rawTransaction", None)
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise SignerUnavailable
    return "0x" + bytes(raw).hex()


def publish_anchor(
    env: Mapping[str, str],
    seal_hash: object,
    kind: object,
    *,
    transport: Optional[Transport] = None,
    sender_of: Optional[SenderOf] = None,
    signer: Optional[Signer] = None,
) -> dict:
    """Sign and submit ``anchor`` on testnet, or refuse without signing."""
    if not flag_on(env):
        return _refused("flag_off")
    word = _word(seal_hash)
    if word is None:
        return _refused("bad_hash")
    kind_word = _word(kind)
    if kind_word is None:
        return _refused("bad_kind")
    if word == bytes(32):
        return _refused("zero_hash")
    address = _address(env.get(ADDRESS_ENV))
    if address is None:
        return _refused("no_contract")
    configured, why = _chain_setting(env)
    if why:
        return _refused(why)
    if configured is not None and configured != testnet_chain_id():
        return _refused("not_testnet", chain_id=configured)
    call = transport if transport is not None else http_transport(rpc_url(env))
    try:
        chain = _quantity(call("eth_chainId", []))
    except RpcFailed:
        return _refused("rpc_unreadable")
    if chain is None:
        return _refused("chain_unreadable")
    if chain != testnet_chain_id():
        return _refused("not_testnet", chain_id=chain)
    data = "0x" + anchor_calldata(word, kind_word).hex()
    try:
        current = call("eth_call", [{"to": address, "data": "0x" + is_anchored_calldata(word).hex()}, "latest"])
    except RpcFailed:
        return _refused("rpc_unreadable", chain_id=chain)
    anchored = _bool_word(current)
    if anchored is None:
        return _refused("result_unreadable", chain_id=chain)
    if anchored is True:
        return _refused("duplicate", readable=True, anchored=True, chain_id=chain)
    key, key_reason = _key(env)
    if key is None:
        return _refused(key_reason or "no_key", chain_id=chain, readable=True, anchored=False)
    who = sender_of if sender_of is not None else _default_sender
    sign = signer if signer is not None else _default_signer
    try:
        sender = who(key)
    except SignerUnavailable:
        return _refused("signer_unavailable", chain_id=chain, readable=True, anchored=False)
    try:
        nonce = _quantity(call("eth_getTransactionCount", [sender, "pending"]))
        gas = _quantity(call(
            "eth_estimateGas",
            [{"from": sender, "to": address, "data": data, "value": "0x0"}],
        ))
        block = call("eth_getBlockByNumber", ["latest", False])
        priority = _quantity(call("eth_maxPriorityFeePerGas", []))
    except RpcFailed:
        return _refused("rpc_unreadable", chain_id=chain, readable=True, anchored=False)
    if not isinstance(block, dict):
        return _refused("rpc_unreadable", chain_id=chain, readable=True, anchored=False)
    base = _quantity(block.get("baseFeePerGas"))
    if gas == 0:
        return _refused("gas_unusable", chain_id=chain, readable=True, anchored=False)
    if nonce is None or gas is None or base is None or priority is None:
        return _refused("rpc_unreadable", chain_id=chain, readable=True, anchored=False)
    fee_cap = base * 2 + priority
    if fee_cap.bit_length() > 256 or priority.bit_length() > 256:
        return _refused("rpc_unreadable", chain_id=chain, readable=True, anchored=False)
    tx = {
        "chainId": testnet_chain_id(),
        "nonce": nonce,
        "maxFeePerGas": fee_cap,
        "maxPriorityFeePerGas": priority,
        "gas": gas,
        "to": address,
        "value": 0,
        "data": data,
        "type": 2,
    }
    try:
        raw = sign(tx, key)
    except SignerUnavailable:
        return _refused("signer_unavailable", chain_id=chain, readable=True, anchored=False)
    if not isinstance(raw, str) or not raw.startswith("0x") or len(raw) < 4:
        return _refused("signer_unavailable", chain_id=chain, readable=True, anchored=False)
    try:
        sent = call("eth_sendRawTransaction", [raw])
    except RpcFailed:
        return _refused(
            "rejected", chain_id=chain, readable=True, anchored=False, signed=True,
        )
    tx_hash = sent if isinstance(sent, str) else None
    if tx_hash is None or _word(tx_hash) is None:
        return _refused(
            "rejected", chain_id=chain, readable=True, anchored=False, signed=True,
        )
    return {
        "ok": True,
        "signed": True,
        "broadcast": True,
        "reason": None,
        "tx_hash": tx_hash,
        "readable": True,
        "anchored": None,
        "chain_id": testnet_chain_id(),
    }

