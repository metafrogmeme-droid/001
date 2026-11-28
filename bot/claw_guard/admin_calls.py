"""Upgrade and admin-key calls are top risk. Blocked unless your own rule allows them.

A proxy upgrade, a new admin, a new owner, a new multisig signer, a delegation
of the signing account: each one hands the keys to someone else in a single
call. The Atoshi drain (27 Nov 2026) was an EIP-1967 proxy upgrade signed with
a phished deployer key. This module reads a prepared EVM request (a list of
calls and an optional EIP-7702 authorization list), finds those calls wherever
they sit, and answers with the same three sentences as the rest of Claw Guard.

What it reads:

* direct calls: ``upgradeTo``, ``upgradeToAndCall`` (proxy, UUPS and beacon
  share these selectors), proxy-admin ``upgrade``/``upgradeAndCall``,
  ``diamondCut``, ``setImplementation``, ``changeAdmin``, ``setAdmin``,
  ``transferOwnership``, ``acceptOwnership``, ``renounceOwnership``,
  ``grantRole`` of the default admin role, multisig owner, threshold, module,
  guard and fallback-handler changes;
* the same calls nested in ``multicall``, Multicall3, ERC-7579/7821 ``execute``,
  ``executeBatch``, a multisig ``execTransaction`` and ``multiSend``, to a
  fixed depth;
* a ``delegatecall`` (multisig operation 1, ERC-7579 call type 0xff, an inner
  multiSend operation 1), unless it goes to a published MultiSend, which is read
  through instead;
* every tuple of an EIP-7702 authorization list that names a delegate.

What it does not do: it signs nothing, reads no chain, fetches nothing and reads
no key. It does not know what a contract is, so a call it has no selector for is
not an admin call, and a wrapper it has no decoder for is not opened. A part of
a recognised wrapper that does not decode is a gap and is never counted as
nothing.

Nothing here says a request is safe or a scam. The only user-facing sentences
are the three in ``bot.claw_guard.verdicts``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from bot.claw_guard.payload import order_hash
from bot.claw_guard.verdicts import ALLOW_TEXT, blocked, needs_ok
from bot.hrc.keccak import keccak256

#: How many levels of wrapper are opened. A call at this depth is classified, not opened.
MAX_DEPTH = 4
#: How many calls one request may open, all levels together.
MAX_CALLS = 128
#: A request with more top-level calls than this is refused, not truncated.
MAX_TOP_CALLS = 256
#: The most calldata one call may carry.
MAX_DATA_BYTES = 256 * 1024

#: Published Safe MultiSend and MultiSendCallOnly deployments (safe-deployments, v1.3.0 and v1.4.1).
MULTISEND_TARGETS: tuple[str, ...] = (
    "0xA238CBeb142c10Ef7Ad8442C6D1f9E89e07e7761",
    "0x40A2aCCbd92BCA938b02010E17A5b8929b49130D",
    "0x38869bf66a61cF6bDB996A6aE40D5853Fd43B526",
    "0x9641d764fc13c8B624c04430C7356C1C7C8102e2",
)
_MULTISEND_LOWER = frozenset(t.lower() for t in MULTISEND_TARGETS)

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ZERO = "0x" + "00" * 20

#: kind -> (rule id, what it is, in the user's words)
_KINDS: dict[str, tuple[str, str]] = {
    "proxy-upgrade": ("UPG-1", "an upgrade of the code behind {t}{nv}"),
    "set-implementation": ("IMPL-1", "a change of the implementation address on {t}{nv}"),
    "admin-change": ("ADM-1", "a change of the admin of {t}{nv}"),
    "ownership-transfer": ("OWN-1", "a transfer of ownership of {t}{nv}"),
    "ownership-renounce": ("OWN-2", "giving up ownership of {t}"),
    "admin-role-grant": ("OWN-3", "granting the default admin role on {t}{nv}"),
    "multisig-owner-change": ("MSIG-1", "a change of the signers on {t}{nv}"),
    "multisig-threshold-change": ("MSIG-2", "a change of the signing threshold on {t}"),
    "multisig-power-change": ("MSIG-3", "a change of modules, guard or fallback handler on {t}{nv}"),
    "delegatecall": ("DLG-1", "a delegatecall into {t}"),
    "eip7702-delegation": ("DEL-1", "an EIP-7702 delegation of the signing account to {t}"),
}

# Admin calls. signature -> (kind, word index of the address that takes the power, or None).
_ADMIN: dict[str, tuple[str, Optional[int]]] = {
    "upgradeTo(address)": ("proxy-upgrade", 0),
    "upgradeToAndCall(address,bytes)": ("proxy-upgrade", 0),
    "upgrade(address,address)": ("proxy-upgrade", 1),
    "upgradeAndCall(address,address,bytes)": ("proxy-upgrade", 1),
    "diamondCut((address,uint8,bytes4[])[],address,bytes)": ("proxy-upgrade", None),
    "setImplementation(address)": ("set-implementation", 0),
    "upgrade(address)": ("set-implementation", 0),
    "changeAdmin(address)": ("admin-change", 0),
    "changeProxyAdmin(address,address)": ("admin-change", 1),
    "setAdmin(address)": ("admin-change", 0),
    "transferOwnership(address)": ("ownership-transfer", 0),
    "setOwner(address)": ("ownership-transfer", 0),
    "acceptOwnership()": ("ownership-transfer", None),
    "renounceOwnership()": ("ownership-renounce", None),
    "grantRole(bytes32,address)": ("admin-role-grant", 1),
    "addOwnerWithThreshold(address,uint256)": ("multisig-owner-change", 0),
    "removeOwner(address,address,uint256)": ("multisig-owner-change", 1),
    "swapOwner(address,address,address)": ("multisig-owner-change", 2),
    "changeThreshold(uint256)": ("multisig-threshold-change", None),
    "enableModule(address)": ("multisig-power-change", 0),
    "disableModule(address,address)": ("multisig-power-change", 1),
    "setGuard(address)": ("multisig-power-change", 0),
    "setFallbackHandler(address)": ("multisig-power-change", 0),
}

# Wrappers that carry calls. signature -> decoder name.
_WRAPPERS: dict[str, str] = {
    "multicall(bytes[])": "multicall",
    "multicall(uint256,bytes[])": "multicall_after_word",
    "multicall(bytes32,bytes[])": "multicall_after_word",
    "aggregate((address,bytes)[])": "aggregate",
    "tryAggregate(bool,(address,bytes)[])": "try_aggregate",
    "aggregate3((address,bool,bytes)[])": "aggregate3",
    "aggregate3Value((address,bool,uint256,bytes)[])": "aggregate3_value",
    "execute(bytes32,bytes)": "execute_mode",
    "execute(address,uint256,bytes)": "account_execute",
    "executeBatch((address,uint256,bytes)[])": "execute_batch_tuples",
    "executeBatch(address[],bytes[])": "execute_batch_arrays",
    "executeBatch(address[],uint256[],bytes[])": "execute_batch_arrays_values",
    "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)": "exec_transaction",
    "execTransactionFromModule(address,uint256,bytes,uint8)": "exec_from_module",
    "multiSend(bytes)": "multi_send",
}

#: Every signature this module acts on. signature -> its role.
SIGNATURES: dict[str, str] = {
    **{signature: kind for signature, (kind, _) in _ADMIN.items()},
    **{signature: "wrapper" for signature in _WRAPPERS},
}


def _selector(signature: str) -> bytes:
    return keccak256(signature.encode("ascii"))[:4]


def _head_words(signature: str) -> int:
    """Top-level parameter count. Every parameter here takes one head word."""
    inner = signature[signature.index("(") + 1 : -1]
    if not inner:
        return 0
    depth, count = 0, 1
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return count


_ADMIN_BY_SELECTOR = {_selector(s): (s, kind, index, _head_words(s)) for s, (kind, index) in _ADMIN.items()}
_WRAPPER_BY_SELECTOR = {_selector(s): (s, name) for s, name in _WRAPPERS.items()}


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    target: str
    signature: str
    new_value: Optional[str] = None


@dataclass(frozen=True)
class Gap:
    """A part that was recognised and not read. Never the same as nothing found."""

    path: str
    reason: str
    nested: str


@dataclass(frozen=True)
class Scan:
    findings: tuple[Finding, ...]
    gaps: tuple[Gap, ...]


class _Unreadable(Exception):
    pass


class _BadRequest(Exception):
    pass


@dataclass(frozen=True)
class _Call:
    to: Optional[str]
    data: bytes
    delegate: bool = False


# ---------------------------------------------------------------- reading ABI bytes
def _word(buf: bytes, pos: int) -> bytes:
    if pos < 0 or pos + 32 > len(buf):
        raise _Unreadable
    return buf[pos : pos + 32]


def _uint(buf: bytes, pos: int) -> int:
    return int.from_bytes(_word(buf, pos), "big")


def _address(buf: bytes, pos: int) -> str:
    word = _word(buf, pos)
    if any(word[:12]):
        raise _Unreadable
    return "0x" + word[12:].hex()


def _dynamic_bytes(buf: bytes, pos: int) -> bytes:
    length = _uint(buf, pos)
    start = pos + 32
    if length > len(buf) or start + length > len(buf):
        raise _Unreadable
    return buf[start : start + length]


def _offset(buf: bytes, base: int, pos: int) -> int:
    """Absolute position of a dynamic item whose offset word sits at ``pos``, relative to ``base``."""
    rel = _uint(buf, pos)
    if rel > len(buf):
        raise _Unreadable
    return base + rel


def _array_start(buf: bytes, pos: int) -> tuple[int, int]:
    """(element count, position of the first element word) of the array at ``pos``."""
    count = _uint(buf, pos)
    if count * 32 > len(buf):
        raise _Unreadable
    return count, pos + 32


def _bytes_array(buf: bytes, pos: int) -> list[bytes]:
    count, first = _array_start(buf, pos)
    return [_dynamic_bytes(buf, _offset(buf, first, first + 32 * i)) for i in range(count)]


def _address_array(buf: bytes, pos: int) -> list[str]:
    count, first = _array_start(buf, pos)
    return [_address(buf, first + 32 * i) for i in range(count)]


def _uint_array(buf: bytes, pos: int) -> list[int]:
    count, first = _array_start(buf, pos)
    return [_uint(buf, first + 32 * i) for i in range(count)]


def _tuples(buf: bytes, pos: int) -> list[int]:
    """Absolute positions of the elements of an array of dynamic tuples."""
    count, first = _array_start(buf, pos)
    return [_offset(buf, first, first + 32 * i) for i in range(count)]


# ---------------------------------------------------------------- wrapper decoders (args = calldata after the selector)
def _multicall(args: bytes, to: Optional[str]) -> list[_Call]:
    return [_Call(to, d) for d in _bytes_array(args, _offset(args, 0, 0))]


def _multicall_after_word(args: bytes, to: Optional[str]) -> list[_Call]:
    return [_Call(to, d) for d in _bytes_array(args, _offset(args, 0, 32))]


def _aggregate(args: bytes, to: Optional[str]) -> list[_Call]:
    return [
        _Call(_address(args, p), _dynamic_bytes(args, _offset(args, p, p + 32)))
        for p in _tuples(args, _offset(args, 0, 0))
    ]


def _try_aggregate(args: bytes, to: Optional[str]) -> list[_Call]:
    return [
        _Call(_address(args, p), _dynamic_bytes(args, _offset(args, p, p + 32)))
        for p in _tuples(args, _offset(args, 0, 32))
    ]


def _aggregate3(args: bytes, to: Optional[str]) -> list[_Call]:
    return [
        _Call(_address(args, p), _dynamic_bytes(args, _offset(args, p, p + 64)))
        for p in _tuples(args, _offset(args, 0, 0))
    ]


def _aggregate3_value(args: bytes, to: Optional[str]) -> list[_Call]:
    return [
        _Call(_address(args, p), _dynamic_bytes(args, _offset(args, p, p + 96)))
        for p in _tuples(args, _offset(args, 0, 0))
    ]


def _execute_batch_tuples(args: bytes, to: Optional[str]) -> list[_Call]:
    return [
        _Call(_address(args, p), _dynamic_bytes(args, _offset(args, p, p + 64)))
        for p in _tuples(args, _offset(args, 0, 0))
    ]


def _account_execute(args: bytes, to: Optional[str]) -> list[_Call]:
    return [_Call(_address(args, 0), _dynamic_bytes(args, _offset(args, 0, 64)))]


def _execute_batch_arrays(args: bytes, to: Optional[str]) -> list[_Call]:
    targets = _address_array(args, _offset(args, 0, 0))
    datas = _bytes_array(args, _offset(args, 0, 32))
    if len(targets) != len(datas):
        raise _Unreadable
    return [_Call(t, d) for t, d in zip(targets, datas)]


def _execute_batch_arrays_values(args: bytes, to: Optional[str]) -> list[_Call]:
    targets = _address_array(args, _offset(args, 0, 0))
    values = _uint_array(args, _offset(args, 0, 32))
    datas = _bytes_array(args, _offset(args, 0, 64))
    if not len(targets) == len(values) == len(datas):
        raise _Unreadable
    return [_Call(t, d) for t, d in zip(targets, datas)]


def _execute_mode(args: bytes, to: Optional[str]) -> list[_Call]:
    """ERC-7579 / ERC-7821 ``execute(bytes32 mode, bytes executionData)``."""
    mode = _word(args, 0)
    payload = _dynamic_bytes(args, _offset(args, 0, 32))
    call_type = mode[0]
    if call_type == 0x01:  # batch: abi.encode(Call[]) or, with the 7821 opData selector, abi.encode(Call[], bytes)
        return [
            _Call(_address(payload, p), _dynamic_bytes(payload, _offset(payload, p, p + 64)))
            for p in _tuples(payload, _offset(payload, 0, 0))
        ]
    if call_type in (0x00, 0xFF):  # single call or delegatecall: abi.encodePacked(target, value, callData)
        if len(payload) < 52:
            raise _Unreadable
        return [_Call("0x" + payload[:20].hex(), payload[52:], delegate=call_type == 0xFF)]
    raise _Unreadable


def _safe_inner(args: bytes, data_word: int, operation_word: int) -> list[_Call]:
    operation = _uint(args, 32 * operation_word)
    if operation > 1:
        raise _Unreadable
    return [_Call(_address(args, 0), _dynamic_bytes(args, _offset(args, 0, 32 * data_word)), delegate=operation == 1)]


def _exec_transaction(args: bytes, to: Optional[str]) -> list[_Call]:
    return _safe_inner(args, 2, 3)


def _exec_from_module(args: bytes, to: Optional[str]) -> list[_Call]:
    return _safe_inner(args, 2, 3)


def _multi_send(args: bytes, to: Optional[str]) -> list[_Call]:
    packed = _dynamic_bytes(args, _offset(args, 0, 0))
    out: list[_Call] = []
    pos = 0
    while pos < len(packed):
        if pos + 85 > len(packed):
            raise _Unreadable
        operation = packed[pos]
        if operation > 1:
            raise _Unreadable
        target = "0x" + packed[pos + 1 : pos + 21].hex()
        length = int.from_bytes(packed[pos + 53 : pos + 85], "big")
        end = pos + 85 + length
        if end > len(packed):
            raise _Unreadable
        out.append(_Call(target, packed[pos + 85 : end], delegate=operation == 1))
        pos = end
    return out


_DECODERS = {
    "multicall": _multicall,
    "multicall_after_word": _multicall_after_word,
    "aggregate": _aggregate,
    "try_aggregate": _try_aggregate,
    "aggregate3": _aggregate3,
    "aggregate3_value": _aggregate3_value,
    "execute_mode": _execute_mode,
    "account_execute": _account_execute,
    "execute_batch_tuples": _execute_batch_tuples,
    "execute_batch_arrays": _execute_batch_arrays,
    "execute_batch_arrays_values": _execute_batch_arrays_values,
    "exec_transaction": _exec_transaction,
    "exec_from_module": _exec_from_module,
    "multi_send": _multi_send,
}


# ---------------------------------------------------------------- the scan
class _Walk:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.gaps: dict[str, Gap] = {}
        self.opened = 0

    def gap(self, path: str, reason: str, nested: str = "not-inspected") -> None:
        self.gaps.setdefault(path, Gap(path, reason, nested))

    def visit(self, call: _Call, path: str, depth: int) -> None:
        target = call.to
        if target is None or not call.data and not call.delegate:
            return
        if call.delegate:
            if target.lower() in _MULTISEND_LOWER and call.data[:4] == _selector("multiSend(bytes)"):
                pass  # a published MultiSend is read through, not flagged
            else:
                self.findings.append(Finding("delegatecall", path, target.lower(), "delegatecall"))
                return
        if len(call.data) < 4:
            return
        head = call.data[:4]
        args = call.data[4:]
        admin = _ADMIN_BY_SELECTOR.get(head)
        if admin is not None:
            self._admin(admin, args, target, path)
            return
        wrapper = _WRAPPER_BY_SELECTOR.get(head)
        if wrapper is None:
            return
        if depth >= MAX_DEPTH:
            self.gap(path, "depth-limit")
            return
        try:
            inner = _DECODERS[wrapper[1]](args, target)
        except _Unreadable:
            self.gap(path, "undecodable-data")
            return
        self.expand(inner, path, depth + 1)

    def expand(self, calls: list[_Call], parent: str, depth: int) -> None:
        for index, call in enumerate(calls):
            if self.opened >= MAX_CALLS:
                self.gap(parent or "tx", "not-inspected")
                return
            self.opened += 1
            self.visit(call, f"{parent}.inner[{index}]" if parent != "tx" else f"tx.calls[{index}]", depth)

    def _admin(self, admin: tuple[str, str, Optional[int], int], args: bytes, target: str, path: str) -> None:
        signature, kind, index, words = admin
        if len(args) < 32 * words:
            self.gap(path, "undecodable-data")
            return
        try:
            new_value = _address(args, 32 * index) if index is not None else None
            if kind == "admin-role-grant" and any(_word(args, 0)):
                return  # a role other than the default admin role
        except _Unreadable:
            self.gap(path, "undecodable-data")
            return
        self.findings.append(Finding(kind, path, target.lower(), signature, new_value))


def scan(request: Any) -> Scan:
    """Find admin calls in a request. Raises ``ValueError`` when the request is not one."""
    try:
        calls, authorizations = _read_request(request)
    except _BadRequest as exc:
        raise ValueError("request") from exc
    walk = _Walk()
    walk.expand(calls, "tx", 0)
    for index, raw in enumerate(authorizations):
        path = f"tx.authorizationList[{index}]"
        delegate = raw.get("address") if isinstance(raw, Mapping) else None
        if not isinstance(delegate, str) or not _ADDRESS.match(delegate):
            walk.gap(path, "undecodable-data")
        elif delegate.lower() != _ZERO:
            walk.findings.append(Finding("eip7702-delegation", path, delegate.lower(), "authorizationList"))
    return Scan(tuple(walk.findings), tuple(walk.gaps.values()))


def _read_request(request: Any) -> tuple[list[_Call], list[Any]]:
    if not isinstance(request, Mapping):
        raise _BadRequest
    raw_calls = request.get("calls")
    if not isinstance(raw_calls, list) or len(raw_calls) > MAX_TOP_CALLS:
        raise _BadRequest
    authorizations = request.get("authorizationList", [])
    if not isinstance(authorizations, list) or len(authorizations) > MAX_TOP_CALLS:
        raise _BadRequest
    if not raw_calls and not authorizations:
        raise _BadRequest
    calls: list[_Call] = []
    for raw in raw_calls:
        if not isinstance(raw, Mapping):
            raise _BadRequest
        to = raw.get("to")
        if to is not None and (not isinstance(to, str) or not _ADDRESS.match(to)):
            raise _BadRequest
        data = raw.get("data", "0x")
        if not isinstance(data, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", data):
            raise _BadRequest
        if len(data) > 2 + 2 * MAX_DATA_BYTES:
            raise _BadRequest
        value = raw.get("value", "0")
        if isinstance(value, bool) or not (
            (isinstance(value, int) and value >= 0) or (isinstance(value, str) and value.isdecimal())
        ):
            raise _BadRequest
        calls.append(_Call(to.lower() if isinstance(to, str) else None, bytes.fromhex(data[2:])))
    return calls, authorizations


# ---------------------------------------------------------------- the verdict
def evaluate_admin_calls(request: Any, rules: Any, *, now: float) -> dict[str, Any]:
    """Block beats confirm. Otherwise allow. Same answer shape as ``evaluate``.

    ``rules["admin_calls"]`` is the user's own rule:

    * ``allow``: entries ``{"target": address, "kinds": [kind, ...], "new_value": address?}``. An entry lets a
      call through only when its target and kind match (and its ``new_value`` when it names one).
    * ``confirm``: the same entries; a match asks instead of blocking.
    * ``default``: ``"block"`` (the default) or ``"confirm"`` for a call no entry names. Any other value blocks.
    * ``unreadable``: ``"confirm"`` (the default) or ``"block"`` for a part that could not be read. Nothing allows it.
    """
    identity = _identity(request)
    try:
        found = scan(request)
    except ValueError:
        hit = _hit("REQ-1", "block", "that request could not be read.")
        return _answer([hit], identity, now, [], ())
    policy = _policy(rules)
    hits: list[dict[str, str]] = []
    for finding in found.findings:
        level = _level(finding, policy)
        if level is not None:
            hits.append(_hit(_KINDS[finding.kind][0], level, _sentence(finding, level, policy)))
    unreadable_level = "block" if policy["unreadable"] == "block" else "confirm"
    for gap in found.gaps:
        reason = f"part of this request ({gap.path}) could not be read, so what it changes is not known."
        hits.append(_hit("READ-1", unreadable_level, reason))
    return _answer(hits, identity, now, list(found.findings), found.gaps)


def _identity(request: Any) -> dict[str, Any]:
    if isinstance(request, Mapping):
        return {str(k): v for k, v in request.items()}
    return {"type": "evmCalls", "unreadable": True}


def _answer(
    hits: list[dict[str, str]],
    identity: Mapping[str, Any],
    now: float,
    findings: list[Finding],
    gaps: tuple[Gap, ...],
) -> dict[str, Any]:
    blocks = [h for h in hits if h["level"] == "block"]
    confirms = [h for h in hits if h["level"] == "confirm"]
    if blocks:
        decision, text = "BLOCK", blocks[0]["text"]
    elif confirms:
        decision, text = "CONFIRM", confirms[0]["text"]
    else:
        decision, text = "ALLOW", ALLOW_TEXT
    try:
        digest = order_hash(identity)
    except (TypeError, ValueError):
        digest = order_hash({"type": "evmCalls", "unreadable": True})
    return {
        "decision": decision,
        "rules": blocks + confirms,
        "order_hash": digest,
        "ts": now,
        "text": text,
        "out_of_scope": None,
        "findings": [
            {"kind": f.kind, "path": f.path, "target": f.target, "signature": f.signature, "new_value": f.new_value}
            for f in findings
        ],
        "gaps": [{"path": g.path, "reason": g.reason, "nested": g.nested} for g in gaps],
    }


def _hit(rule_id: str, level: str, reason: str) -> dict[str, str]:
    return {"id": rule_id, "level": level, "text": blocked(reason) if level == "block" else needs_ok(reason)}


def _policy(rules: Any) -> dict[str, Any]:
    admin = rules.get("admin_calls") if isinstance(rules, Mapping) else None
    admin = admin if isinstance(admin, Mapping) else {}
    return {
        "allow": _entries(admin.get("allow")),
        "confirm": _entries(admin.get("confirm")),
        "default": "confirm" if admin.get("default") == "confirm" else "block",
        "unreadable": "block" if admin.get("unreadable") == "block" else "confirm",
    }


def _entries(raw: Any) -> list[dict[str, Any]]:
    """Entries that can match something. Anything else is dropped, so it can never open the gate."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        target, kinds, new_value = item.get("target"), item.get("kinds"), item.get("new_value")
        if not isinstance(target, str) or not _ADDRESS.match(target):
            continue
        if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
            continue
        if new_value is not None and (not isinstance(new_value, str) or not _ADDRESS.match(new_value)):
            continue
        out.append(
            {
                "target": target.lower(),
                "kinds": set(kinds),
                "new_value": new_value.lower() if isinstance(new_value, str) else None,
            }
        )
    return out


def _matches(entry: Mapping[str, Any], finding: Finding) -> bool:
    if entry["target"] != finding.target or finding.kind not in entry["kinds"]:
        return False
    return entry["new_value"] is None or entry["new_value"] == finding.new_value


def _level(finding: Finding, policy: Mapping[str, Any]) -> Optional[str]:
    """``None`` means the user's own rule allows this call."""
    if any(_matches(e, finding) for e in policy["confirm"]):
        return "confirm"
    if any(_matches(e, finding) for e in policy["allow"]):
        return None
    return str(policy["default"])


def _sentence(finding: Finding, level: str, policy: Mapping[str, Any]) -> str:
    template = _KINDS[finding.kind][1]
    new_value = f" to {finding.new_value}" if finding.new_value else ""
    what = template.format(t=finding.target, nv=new_value)
    if level == "block":
        return f"{what} at {finding.path} is not on your allow list."
    return f"{what} at {finding.path}: your rules ask for your OK before it is signed."
