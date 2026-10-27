"""Publish one seal hash to HashKey testnet, or refuse before signing.

The flag, the inputs, the chain, the RPC URL's shape and a readable
``isAnchored`` all come before the signer key. Mainnet is not a target: an
endpoint that answers any chain id other than the testnet id is refused, the
key is not read, and the signed transaction carries the testnet chain id
whatever the endpoint said, so a mainnet node rejects it.

Three values, not two. A failed read is not "not anchored". A send whose
answer was lost is not "rejected": ``broadcast`` is ``None`` and the
locally computed transaction hash is returned so the chain can be asked. A
send the node accepted is not "ok" until a receipt says so: ``ok`` is
``None`` with reason ``submitted`` while the chain has not answered.

The whole publish runs inside one deadline. Every RPC call is bounded by
the time left on a wall clock (a worker thread is abandoned when it runs
over, so a trickling endpoint cannot hold a call open) as well as per
socket operation, and a publish that cannot cover signing and sending
refuses before it signs. The route that spawns this process allows more
than the deadline, so the parent never kills a child that may already
have sent.
"""
from __future__ import annotations

import http.client
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Mapping, Optional

from bot.fvb.keccak import keccak256
from bot.hashkey_seal.flag import flag_on
from bot.hashkey_seal.networks import mainnet_chain_id, testnet_chain_id, testnet_rpc
from bot.web.web3_signer import _SECP256K1_N

SIGNER_KEY = "HASHKEY_SEAL_SIGNER_KEY"
CHAIN_ID_ENV = "HASHKEY_SEAL_CHAIN_ID"
ADDRESS_ENV = "HASHKEY_SEAL_ADDRESS"
RPC_ENV = "HASHKEY_SEAL_RPC_URL"
DEADLINE_ENV = "HASHKEY_SEAL_DEADLINE_SEC"
MAX_FEE_ENV = "HASHKEY_SEAL_MAX_FEE_GWEI"

#: The route gives the child process twenty seconds; the publish stays inside.
DEFAULT_DEADLINE_SEC = 14.0
#: A ceiling on both the priority fee and the fee cap, in gwei. HashKey fees
#: are a small fraction of this; an endpoint that asks for more is refused.
DEFAULT_MAX_FEE_GWEI = 10
#: ``anchor`` stores two words and emits one log; this leaves headroom.
GAS_CEILING = 200_000
NONCE_CEILING = 2**64 - 1
#: No single call waits longer than this, whatever the deadline.
CALL_TIMEOUT_CAP_SEC = 20.0
#: Signing and sending need at least this much of the budget left.
MIN_SIGN_BUDGET_SEC = 1.0
RECEIPT_POLL_SEC = 0.5

_HEX = frozenset("0123456789abcdefABCDEF")

Transport = Callable[[str, list], object]
SenderOf = Callable[[str], str]
Signer = Callable[[dict, str], str]
Clock = Callable[[], float]


class RpcFailed(Exception):
    """The transport did not deliver a usable answer. The message stays empty."""

    pass  # a docstring-only body does not parse after code_only blanks it


class RpcRejected(RpcFailed):
    """The node answered an error object. Only its numeric code and the first
    four bytes of a string ``data`` field are kept; never the message."""

    def __init__(self, code: Optional[int] = None, selector: Optional[str] = None) -> None:
        super().__init__()
        self.code = code
        self.selector = selector


class SignerUnavailable(Exception):
    """eth-account is not installed, or returned no raw transaction. Nothing was signed."""

    pass


class SignerRejected(Exception):
    """eth-account is installed and refused the key or the transaction.
    Nothing of its message is kept."""

    pass


def _selector(signature: bytes) -> bytes:
    return keccak256(signature)[:4]


DUPLICATE_HASH_SELECTOR = "0x" + _selector(b"DuplicateHash()").hex()
NOT_PUBLISHER_SELECTOR = "0x" + _selector(b"NotPublisher()").hex()


def anchor_calldata(seal_hash: bytes, kind: bytes) -> bytes:
    return _selector(b"anchor(bytes32,bytes32)") + seal_hash + kind


def is_anchored_calldata(seal_hash: bytes) -> bytes:
    return _selector(b"isAnchored(bytes32)") + seal_hash


def anchor_of_calldata(seal_hash: bytes) -> bytes:
    return _selector(b"anchorOf(bytes32)") + seal_hash


def kind_claw_guard() -> bytes:
    return keccak256(b"claw-guard")


def kind_agent_leash() -> bytes:
    return keccak256(b"agent-leash")


def known_kinds() -> dict[str, bytes]:
    """The kinds this publisher anchors. The contract accepts any non-zero
    kind; the server anchors only the two it advertises."""
    return {"claw-guard": kind_claw_guard(), "agent-leash": kind_agent_leash()}


def tx_hash_of(raw: str) -> str:
    """The hash a node will report for this raw transaction: keccak of its bytes."""
    return "0x" + keccak256(bytes.fromhex(raw[2:])).hex()


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


def _anchor_of_words(value: object) -> Optional[tuple[str, str]]:
    """``(publisher, kind)`` from an ``anchorOf`` answer, or None when unreadable."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text[:2].lower() != "0x":
        return None
    body = text[2:]
    if len(body) != 128 or any(c not in _HEX for c in body):
        return None
    first, second = body[:64], body[64:]
    if any(c != "0" for c in first[:24]):
        return None
    return "0x" + first[24:].lower(), "0x" + second.lower()


def _raw_ok(raw: object) -> bool:
    if not isinstance(raw, str) or raw[:2] != "0x":
        return False
    body = raw[2:]
    return len(body) >= 2 and len(body) % 2 == 0 and all(c in _HEX for c in body)


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
    scalar = int(body, 16)
    if not 0 < scalar < _SECP256K1_N:
        return None, "bad_key"
    return "0x" + body.lower(), None


def _positive_number(env: Mapping[str, str], name: str, default: float) -> Optional[float]:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip()
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or not value > 0:
        return None
    return value


def deadline_seconds(env: Mapping[str, str]) -> Optional[float]:
    """The publish budget, or None when the setting is not a positive number."""
    return _positive_number(env, DEADLINE_ENV, DEFAULT_DEADLINE_SEC)


def max_fee_wei(env: Mapping[str, str]) -> Optional[int]:
    """The ceiling on the priority fee and the fee cap, in wei, or None when unreadable."""
    gwei = _positive_number(env, MAX_FEE_ENV, float(DEFAULT_MAX_FEE_GWEI))
    if gwei is None:
        return None
    return int(gwei * 10**9)


def rpc_url(env: Mapping[str, str]) -> str:
    raw = env.get(RPC_ENV)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return testnet_rpc()


def rpc_url_reason(url: str) -> Optional[str]:
    """None when the URL is https, or http on the loopback host; a reason otherwise."""
    low = url.strip().lower()
    if low.startswith("https://") and len(low) > len("https://"):
        return None
    for host in ("127.0.0.1", "localhost", "[::1]"):
        prefix = "http://" + host
        rest = low[len(prefix):] if low.startswith(prefix) else None
        if rest is not None and (rest == "" or rest[0] in ":/"):
            return None
    return "bad_rpc_url"


def _rejected_from(error: object) -> RpcRejected:
    code: Optional[int] = None
    selector: Optional[str] = None
    if isinstance(error, dict):
        raw_code = error.get("code")
        if isinstance(raw_code, int) and not isinstance(raw_code, bool):
            code = raw_code
        data = error.get("data")
        if isinstance(data, str) and data[:2].lower() == "0x" and len(data) >= 10:
            head = data[2:10]
            if all(c in _HEX for c in head):
                selector = "0x" + head.lower()
    return RpcRejected(code, selector)


def _within(fn: Callable[[], object], seconds: float) -> object:
    """Run ``fn`` in a daemon thread and wait at most ``seconds`` of wall
    clock. A socket timeout bounds each read, not the whole exchange, so a
    node that answers in a trickle could otherwise hold one call open past
    the deadline. A thread that runs over is abandoned; its socket timeout
    ends it on its own."""
    out: list = []
    err: list = []

    def run() -> None:
        try:
            out.append(fn())
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
            err.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise RpcFailed
    if err:
        raise err[0]
    return out[0]


def http_transport(
    url: str,
    *,
    deadline: Optional[float] = None,
    clock: Clock = time.monotonic,
) -> Transport:
    """One JSON-RPC call per invocation. Each call waits at most the time
    left on the deadline (or the per-call cap without one), on a wall clock
    and per socket operation; a call with no time left fails without
    connecting."""

    def call(method: str, params: list) -> object:
        timeout = CALL_TIMEOUT_CAP_SEC
        if deadline is not None:
            left = deadline - clock()
            if left <= 0:
                raise RpcFailed
            timeout = min(CALL_TIMEOUT_CAP_SEC, left)
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"content-type": "application/json"}
        )

        def fetch() -> object:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            payload = _within(fetch, timeout)
        except (
            OSError,
            urllib.error.URLError,
            json.JSONDecodeError,
            TimeoutError,
            http.client.HTTPException,
        ) as exc:
            raise RpcFailed from exc
        if not isinstance(payload, dict):
            raise RpcFailed
        if "error" in payload and payload["error"] is not None:
            raise _rejected_from(payload["error"])
        if "result" not in payload:
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


def _unsettled(reason: str, *, tx_hash: str, chain_id: int, broadcast: Optional[bool]) -> dict:
    """A signed transaction whose fate the chain has not told us. ``ok`` is None."""
    return {
        "ok": None,
        "signed": True,
        "broadcast": broadcast,
        "reason": reason,
        "tx_hash": tx_hash,
        "readable": True,
        "anchored": None,
        "chain_id": chain_id,
    }


def read_anchor(
    env: Mapping[str, str],
    seal_hash: object,
    *,
    transport: Optional[Transport] = None,
) -> dict:
    """Ask ``isAnchored`` and, when stored, ``anchorOf``. Does not read the
    signer key and does not sign. ``attribution`` is ``read``, ``absent`` or
    ``unreadable``; ``publisher`` and ``kind`` are None unless read."""
    word = _word(seal_hash)
    if word is None:
        return _unread("bad_hash")
    address = _address(env.get(ADDRESS_ENV))
    if address is None:
        return _unread("no_contract")
    configured, why = _chain_setting(env)
    if why:
        return _unread(why)
    if configured is not None and configured != testnet_chain_id():
        return _unread("not_testnet", chain_id=configured)
    url = rpc_url(env)
    bad = rpc_url_reason(url)
    if bad:
        return _unread(bad)
    call = transport if transport is not None else http_transport(url)
    try:
        chain = _quantity(call("eth_chainId", []))
        if chain is None:
            return _unread("chain_unreadable")
        if chain != testnet_chain_id():
            return _unread("not_testnet", chain_id=chain)
        data = "0x" + is_anchored_calldata(word).hex()
        result = call("eth_call", [{"to": address, "data": data}, "latest"])
    except RpcFailed:
        return _unread("rpc_unreadable")
    anchored = _bool_word(result)
    if anchored is None:
        return _unread("result_unreadable", chain_id=chain)
    body = {
        "readable": True,
        "anchored": anchored,
        "reason": None,
        "chain_id": chain,
        "publisher": None,
        "kind": None,
        "attribution": "absent",
    }
    if anchored:
        try:
            who = call("eth_call", [{"to": address, "data": "0x" + anchor_of_calldata(word).hex()}, "latest"])
        except RpcFailed:
            body["attribution"] = "unreadable"
            return body
        parsed = _anchor_of_words(who)
        if parsed is None or parsed[0] == "0x" + "00" * 20:
            body["attribution"] = "unreadable"
            return body
        body["publisher"], body["kind"] = parsed
        body["attribution"] = "read"
    return body


def _unread(reason: str, **extra: object) -> dict:
    body: dict = {
        "readable": False,
        "anchored": None,
        "reason": reason,
        "publisher": None,
        "kind": None,
        "attribution": "unreadable",
    }
    if "chain_id" in extra:
        body["chain_id"] = extra["chain_id"]
    return body


def status(env: Mapping[str, str]) -> dict:
    """Whether a publish could be attempted. The key's value is not returned."""
    present = env.get(SIGNER_KEY)
    signer_set = bool(str(present).strip()) if present is not None else False
    configured, why = _chain_setting(env)
    fee = max_fee_wei(env)
    return {
        "flag_on": flag_on(env),
        "chain_id": testnet_chain_id(),
        "refused_chain_id": mainnet_chain_id(),
        "configured_chain_id": configured,
        "chain_reason": why,
        "contract_set": _address(env.get(ADDRESS_ENV)) is not None,
        "signer_set": signer_set,
        "rpc_url_ok": rpc_url_reason(rpc_url(env)) is None,
        "deadline_sec": deadline_seconds(env),
        "max_fee_gwei": None if fee is None else fee / 10**9,
        "gas_ceiling": GAS_CEILING,
        "kinds": {name: "0x" + value.hex() for name, value in known_kinds().items()},
    }


def _default_sender(key: str) -> str:
    try:
        from eth_account import Account
    except ImportError as exc:
        raise SignerUnavailable from exc
    try:
        address = Account.from_key(key).address
    except (TypeError, ValueError) as exc:
        raise SignerRejected from exc
    if not isinstance(address, str) or not address:
        raise SignerUnavailable
    return address


def _default_signer(tx: dict, key: str) -> str:
    """The one place eth-account sees the transaction. It validates ``to`` as
    a checksum address or raw bytes, so the lowercase address the JSON-RPC
    calls use is handed over as bytes; the raw transaction is the same."""
    try:
        from eth_account import Account
    except ImportError as exc:
        raise SignerUnavailable from exc
    try:
        signed = Account.sign_transaction(dict(tx, to=bytes.fromhex(tx["to"][2:])), key)
    except (TypeError, ValueError) as exc:
        raise SignerRejected from exc
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = getattr(signed, "rawTransaction", None)
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise SignerUnavailable
    return "0x" + bytes(raw).hex()


def _stderr(line: str) -> None:
    sys.stderr.write(line)
    sys.stderr.flush()


def publish_anchor(
    env: Mapping[str, str],
    seal_hash: object,
    kind: object,
    *,
    transport: Optional[Transport] = None,
    sender_of: Optional[SenderOf] = None,
    signer: Optional[Signer] = None,
    clock: Clock = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    report: Optional[Callable[[str], None]] = None,
    deadline_sec: Optional[float] = None,
) -> dict:
    """Sign and submit ``anchor`` on testnet, or refuse without signing.

    ``report`` receives one line with the transaction hash, nonce and sender
    before the send, so a parent that kills this process still has the hash.
    """
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
    if kind_word == bytes(32):
        return _refused("zero_kind")
    if kind_word not in known_kinds().values():
        return _refused("unknown_kind")
    address = _address(env.get(ADDRESS_ENV))
    if address is None:
        return _refused("no_contract")
    configured, why = _chain_setting(env)
    if why:
        return _refused(why)
    if configured is not None and configured != testnet_chain_id():
        return _refused("not_testnet", chain_id=configured)
    url = rpc_url(env)
    bad = rpc_url_reason(url)
    if bad:
        return _refused(bad)
    budget = deadline_sec if deadline_sec is not None else deadline_seconds(env)
    if budget is None or not budget > 0:
        return _refused("deadline_unreadable")
    fee_ceiling = max_fee_wei(env)
    if fee_ceiling is None:
        return _refused("fee_cap_unreadable")
    deadline = clock() + budget

    def left() -> float:
        return deadline - clock()

    call = transport if transport is not None else http_transport(url, deadline=deadline, clock=clock)
    tell = report if report is not None else _stderr

    try:
        chain = _quantity(call("eth_chainId", []))
    except RpcFailed:
        return _refused("rpc_unreadable")
    if chain is None:
        return _refused("chain_unreadable")
    if chain != testnet_chain_id():
        return _refused("not_testnet", chain_id=chain)
    data = "0x" + anchor_calldata(word, kind_word).hex()
    is_anchored_call = [{"to": address, "data": "0x" + is_anchored_calldata(word).hex()}, "latest"]
    try:
        current = call("eth_call", is_anchored_call)
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
    except SignerRejected:
        return _refused("signer_rejected", chain_id=chain, readable=True, anchored=False)

    def refuse(reason: str, **extra: object) -> dict:
        fields: dict = {"chain_id": chain, "readable": True, "anchored": False}
        fields.update(extra)
        return _refused(reason, **fields)

    try:
        nonce = _quantity(call("eth_getTransactionCount", [sender, "pending"]))
    except RpcRejected:
        return refuse("node_error")
    except RpcFailed:
        return refuse("rpc_unreadable")
    try:
        gas = _quantity(call(
            "eth_estimateGas",
            [{"from": sender, "to": address, "data": data, "value": "0x0"}],
        ))
    except RpcRejected as rejected:
        if rejected.selector == DUPLICATE_HASH_SELECTOR:
            # The pre-read said "not anchored"; the node's execution says the
            # hash is stored or about to be. Read once more and say which.
            try:
                again = _bool_word(call("eth_call", is_anchored_call))
            except RpcFailed:
                again = None
            if again is True:
                return refuse("duplicate", anchored=True)
            return refuse("duplicate_pending", anchored=None)
        if rejected.selector == NOT_PUBLISHER_SELECTOR:
            return refuse("not_publisher")
        if rejected.selector is not None:
            return refuse("estimate_reverted")
        return refuse("estimate_rejected")
    except RpcFailed:
        return refuse("rpc_unreadable")
    try:
        block = call("eth_getBlockByNumber", ["latest", False])
        priority = _quantity(call("eth_maxPriorityFeePerGas", []))
    except RpcRejected:
        return refuse("node_error")
    except RpcFailed:
        return refuse("rpc_unreadable")
    if not isinstance(block, dict):
        return refuse("rpc_unreadable")
    base = _quantity(block.get("baseFeePerGas"))
    if gas == 0:
        return refuse("gas_unusable")
    if nonce is None or gas is None or base is None or priority is None:
        return refuse("rpc_unreadable")
    if gas > GAS_CEILING:
        return refuse("gas_cap_exceeded")
    if nonce > NONCE_CEILING:
        return refuse("nonce_unusable")
    fee_cap = base * 2 + priority
    if priority > fee_ceiling or fee_cap > fee_ceiling:
        return refuse("fee_cap_exceeded")
    if left() < MIN_SIGN_BUDGET_SEC:
        return refuse("budget_exhausted")
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
        return refuse("signer_unavailable")
    except SignerRejected:
        return refuse("signer_rejected")
    if not _raw_ok(raw):
        return refuse("signer_unavailable")
    expected = tx_hash_of(raw)
    tell(f"tx_hash={expected} nonce={nonce} sender={sender}\n")
    try:
        sent = call("eth_sendRawTransaction", [raw])
    except RpcRejected:
        # The node refused it; the hash names the transaction it refused.
        return _refused(
            "rejected", chain_id=chain, readable=True, anchored=False, signed=True, tx_hash=expected,
        )
    except RpcFailed:
        return _unsettled("send_unconfirmed", tx_hash=expected, chain_id=chain, broadcast=None)
    if not isinstance(sent, str) or _word(sent) is None or sent.strip().lower() != expected:
        return _unsettled("hash_mismatch", tx_hash=expected, chain_id=chain, broadcast=None)
    return _await_receipt(call, is_anchored_call, expected, chain, left, sleep)


def _await_receipt(
    call: Transport,
    is_anchored_call: list,
    tx_hash: str,
    chain: int,
    left: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict:
    """Poll for the receipt while the budget lasts. ``anchored`` is re-read
    from the contract, never derived from the receipt: a reverted anchor's
    hash may well be stored, by someone else."""
    while left() > 0:
        try:
            receipt = call("eth_getTransactionReceipt", [tx_hash])
        except RpcFailed:
            return _unsettled("submitted", tx_hash=tx_hash, chain_id=chain, broadcast=True)
        if isinstance(receipt, dict):
            status_word = _quantity(receipt.get("status"))
            try:
                anchored = _bool_word(call("eth_call", is_anchored_call))
            except RpcFailed:
                anchored = None
            if status_word == 1:
                return {
                    "ok": True, "signed": True, "broadcast": True, "reason": None,
                    "tx_hash": tx_hash, "readable": True, "anchored": anchored, "chain_id": chain,
                }
            if status_word == 0:
                return {
                    "ok": False, "signed": True, "broadcast": True, "reason": "reverted",
                    "tx_hash": tx_hash, "readable": True, "anchored": anchored, "chain_id": chain,
                }
            return _unsettled("receipt_unreadable", tx_hash=tx_hash, chain_id=chain, broadcast=True)
        remaining = left()
        if remaining <= 0:
            break
        sleep(min(RECEIPT_POLL_SEC, remaining))
    return _unsettled("submitted", tx_hash=tx_hash, chain_id=chain, broadcast=True)
