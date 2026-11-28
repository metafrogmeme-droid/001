"""An ERC-8481 Agent Pre-Sign Risk Report from Claw Guard's check results.

ERC-8481 (a draft, ethereum/ERCs#2134) has a source answer "what does this request touch, and what did you read
about it" in a report that is bound to the exact request, typed finding by typed finding, and signed. It has no
score, no verdict and no recommended action. This module turns the answer of ``admin_calls.evaluate_admin_calls``
into that report. It does not decide anything.

What it states, and does not:

* It reads the request's calls and nothing else. It reads no chain state, so every core address finding is
  "not read: unsupported", never a made-up zero.
* Whatever the check did not decode is a coverage gap. A batch it opened is an ``unverified-wrapper`` gap, since no
  code was read to show the target runs the batch as encoded; a call with a selector it has no rule for is an
  ``unsupported-selector`` gap.
* The check's decision and rule ids stay out of the report. The report holds facts; the user's rule decides.

Off by default: ``CLAW_GUARD_ERC8481`` must be exactly ``1`` or ``true``. Signing goes through a signer the
caller injects. This module holds no key, reads no environment but the flag, and signs nothing it has not
checked conforms.

Not in this module: reading the ERC-8004 registry to check that the signer is the source's (step 3 of the draft's
verification), ERC-1271 signers, the on-chain anchor, and ``prepared-tx-envelope`` subjects.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Protocol

from coincurve import PublicKey

from bot.claw_guard.erc8481_jcs import digest, jcs
from bot.claw_guard.erc8481_lists import KIND_ON_LIST, eip55, read_on_list
from bot.hrc.keccak import keccak256

__all__ = [
    "FLAG",
    "KIND_ADMIN_CALL",
    "METHOD",
    "ReportSigner",
    "Source",
    "approval_errors",
    "build_report",
    "context_digest",
    "conformance_errors",
    "coverage_errors",
    "digest",
    "eip712_digest",
    "emit_enabled",
    "emit_report",
    "jcs",
    "path_error",
    "recover_authority",
    "recover_signer",
    "report_errors",
    "report_hash",
    "subject_digest",
    "verify_report",
]

FLAG = "CLAW_GUARD_ERC8481"
_ON = frozenset({"1", "true"})
VERSION = "erc-presign/1"
METHOD = "claw-guard-calldata/1"
KIND_ADMIN_CALL = "com.humanoid-traders.claw-guard.admin-call"
ZERO32 = "0x" + "00" * 32

_DOMAIN_TYPE = b"EIP712Domain(string name,string version,uint256 chainId)"
_REPORT_TYPE = b"PresignReport(bytes32 subjectDigest,bytes32 contextDigest,bytes32 reportHash)"
_DOMAIN_NAME = b"Agent Pre-Sign Risk Report"

STATUSES = ("complete", "partial")
REASONS = ("unsupported-selector", "unverified-wrapper", "depth-limit", "undecodable-data", "not-inspected")
NESTED = ("inspected", "not-inspected")
_NESTED_BY_REASON = {
    "unverified-wrapper": "inspected",
    "depth-limit": "not-inspected",
    "not-inspected": "not-inspected",
    "unsupported-selector": None,
    "undecodable-data": None,
}
_PATH = re.compile(r"^(?:typedData|tx(?:\.calls\[(0|[1-9][0-9]*)\](?:\.inner\[(?:0|[1-9][0-9]*)\])*(?:\.inner)?)?)$")
_PATH_INDEXES = re.compile(r"\[(\d+)\]")
_EVM = re.compile(r"(?:eip155:[0-9]+:)?(0x[0-9a-fA-F]{40})")
_CHAIN = re.compile(r"eip155:[1-9][0-9]*")
_CAIP_EVM = re.compile(r"eip155:([1-9][0-9]*):(0x[0-9a-fA-F]{40})")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z")
_HEX32 = re.compile(r"0x[0-9a-f]{64}")
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

_FORBIDDEN_KEYS = frozenset(
    {"score", "verdict", "severity", "action", "recommendation", "decision", "risk", "level", "rating"}
)
_UNREAD_REASONS = ("unsupported", "unavailable", "incomplete", "not-requested", "payment-required", "timeout")
_NOTE_UNREAD = "No chain state is read; only the request's calls are decoded."
_NOTE_CALLDATA = "Decoded from the request's calldata; no chain state is read."

K = "erc.presign."
CORE_ADDRESS = (K + "address.code", K + "address.first-seen-block", K + "address.prior-transfers")
K_DELEGATION = K + "address.delegation"
K_UNSIGNED = K + "account.unsigned-actors"
K_POWER = K + "account.power-change"
# Optional address kinds this module never reads. Asked for, each is "not read: unsupported" per address.
_ADDRESS_OPTIONAL = frozenset(
    K + name
    for name in (
        "address.source-verified",
        "address.delegate-authorizations",
        "address.delegate-single-caller",
        "address.delegate-unbounded-outflow",
        "address.lookalike",
        "address.on-list",
    )
)

# Safe calls the check decodes. signature -> ((change, grantee is read from the call?), ...)
_POWER: dict[str, tuple[tuple[str, bool], ...]] = {
    "enableModule(address)": (("module-enabled", True),),
    "disableModule(address,address)": (("module-disabled", True),),
    "addOwnerWithThreshold(address,uint256)": (("owner-added", True), ("threshold-changed", False)),
    "removeOwner(address,address,uint256)": (("owner-removed", True), ("threshold-changed", False)),
    "swapOwner(address,address,address)": (("owner-swapped", False),),
    "changeThreshold(uint256)": (("threshold-changed", False),),
    "setGuard(address)": (("guard-set", False),),
    "setFallbackHandler(address)": (("fallback-handler-set", False),),
}


# ---------------------------------------------------------------- the flag
def emit_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the emitter is on. Only ``1`` and ``true`` turn it on; anything else is off."""
    source = os.environ if env is None else env
    return str(source.get(FLAG, "")).strip().lower() in _ON


# ---------------------------------------------------------------- digests
def subject_digest(subject: Mapping[str, Any]) -> str:
    """``digest({chainId, from, calls, typedData, authorizationList})`` with absent members left out."""
    return digest(
        {k: subject[k] for k in ("chainId", "from", "calls", "typedData", "authorizationList") if k in subject}
    )


def context_digest(context: Any) -> str:
    """``digest(context)``, or 32 zero bytes when the request had no context."""
    return ZERO32 if context is None else digest(context)


def report_hash(report: Mapping[str, Any]) -> str:
    """``digest`` of the report without ``signature`` and ``detail``."""
    return digest({k: v for k, v in report.items() if k not in ("signature", "detail")})


def _word(n: int) -> bytes:
    return n.to_bytes(32, "big")


def eip712_digest(subject: str, context: str, report: str, registry_chain_id: int) -> str:
    """The digest the source signs: domain ``Agent Pre-Sign Risk Report`` / ``1`` / the registry's chain id."""
    domain = keccak256(keccak256(_DOMAIN_TYPE) + keccak256(_DOMAIN_NAME) + keccak256(b"1") + _word(registry_chain_id))
    struct = keccak256(
        keccak256(_REPORT_TYPE) + bytes.fromhex(subject[2:]) + bytes.fromhex(context[2:]) + bytes.fromhex(report[2:])
    )
    return "0x" + keccak256(b"\x19\x01" + domain + struct).hex()


# ---------------------------------------------------------------- paths and coverage rules
def path_error(path: Any, request: Mapping[str, Any]) -> Optional[str]:
    """``None``, or the code for a path that does not parse or names a part the request does not have."""
    subject = request["subject"]
    match = _PATH.match(path) if isinstance(path, str) else None
    if not match:
        return "path-syntax"
    if path == "typedData" and "typedData" not in subject:
        return "path-out-of-range"
    if match.group(1) is not None and int(match.group(1)) >= len(subject.get("calls", [])):
        return "path-out-of-range"
    return None


def approval_errors(report: Mapping[str, Any], request: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for approval in report.get("entities", {}).get("approvals", []):
        if "path" in approval:
            error = path_error(approval["path"], request)
            if error:
                errors.append("approval-" + error)
            elif "callIndex" in approval and approval["path"] != f"tx.calls[{approval['callIndex']}]":
                errors.append("approval-path-disagrees-with-callIndex")
    return errors


def coverage_errors(report: Mapping[str, Any], request: Mapping[str, Any]) -> list[str]:
    """Codes for the report's ``coverage``. ``[]`` means it conforms."""
    if "coverage" not in report:
        return ["coverage-absent"]
    coverage = report["coverage"]
    if (
        not isinstance(coverage, dict)
        or set(coverage) != {"status", "gaps"}
        or not isinstance(coverage.get("gaps"), list)
    ):
        return ["coverage-malformed"]
    errors: list[str] = []
    if coverage["status"] not in STATUSES:
        errors.append("status-unknown")
    elif (coverage["status"] == "partial") != (len(coverage["gaps"]) > 0):
        errors.append("status-gaps-disagree")
    seen: set[str] = set()
    for gap in coverage["gaps"]:
        if not isinstance(gap, dict) or set(gap) != {"path", "reason", "nested"}:
            errors.append("gap-malformed")
            continue
        error = path_error(gap["path"], request)
        if error:
            errors.append(error)
        if isinstance(gap["path"], str):
            if gap["path"] in seen:
                errors.append("path-duplicate")
            seen.add(gap["path"])
        if not isinstance(gap["reason"], str) or gap["reason"] not in REASONS:
            errors.append("reason-unknown")
        if not isinstance(gap["nested"], str) or gap["nested"] not in NESTED:
            errors.append("nested-unknown")
        elif gap["reason"] in REASONS and _NESTED_BY_REASON.get(gap["reason"]) not in (None, gap["nested"]):
            errors.append("nested-disagrees-with-reason")
    return errors


def report_errors(report: Mapping[str, Any], request: Mapping[str, Any]) -> list[str]:
    """The draft's coverage and approval-path rules, as its test vectors state them."""
    return coverage_errors(report, request) + approval_errors(report, request)


# ---------------------------------------------------------------- conformance
def _keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _entity_known(entity: Any, report: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    entities = report["entities"]
    if not isinstance(entity, dict):
        return False
    if set(entity) == {"address", "role"}:
        return {"address": entity["address"], "role": entity["role"]} in entities["addresses"]
    if set(entity) == {"approval"}:
        return isinstance(entity["approval"], int) and 0 <= entity["approval"] < len(entities["approvals"])
    if set(entity) == {"token"}:
        return entity["token"] in entities["tokens"]
    if set(entity) == {"call"}:
        return isinstance(entity["call"], int) and 0 <= entity["call"] < len(request["subject"].get("calls", []))
    if set(entity) == {"payment"}:
        return entity["payment"] is True and entities["payment"] is True
    if set(entity) == {"text"}:
        return entity["text"] in [t.get("index") for t in entities["texts"]]
    return False


def _kind_requested(request: Mapping[str, Any], kind: str) -> bool:
    kinds = request.get("kinds")
    if kinds is None:
        return True
    return kind in {k if isinstance(k, str) else k.get("kind") for k in kinds}


def conformance_errors(report: Mapping[str, Any], request: Mapping[str, Any]) -> list[str]:
    """Codes for what the draft requires of a report and this module can check without a registry or a chain.

    The draft's own vector rules come first (``report_errors``). Then: no score, verdict or action anywhere; the
    read / not-read shape; no two findings alike; every finding on a listed entity; every ``at`` in ``blocks``;
    each core address kind present unless the request left it out.
    """
    needed = ("version", "subjectDigest", "contextDigest", "chainId", "signer", "source", "issuedAt", "blocks")
    if not all(k in report for k in needed + ("entities", "findings", "method")):
        return ["report-shape"]
    entities = report["entities"]
    if not isinstance(entities, dict) or not all(
        k in entities for k in ("tokens", "approvals", "addresses", "payment", "texts")
    ):
        return ["report-shape"]
    errors = report_errors(report, request)
    if any(k in _FORBIDDEN_KEYS for k in _keys(report)):
        errors.append("forbidden-field")
    for field in ("subjectDigest", "contextDigest"):
        if not isinstance(report[field], str) or not _HEX32.fullmatch(report[field]):
            errors.append("digest-format")
    if not isinstance(report["issuedAt"], str) or not _TIME.fullmatch(report["issuedAt"]):
        errors.append("issued-at-format")
    blocks = {(b["chainId"], b["block"]) for b in report["blocks"]}
    seen: set[str] = set()
    present: set[tuple[str, str, str]] = set()
    for item in report["findings"]:
        status = item.get("status")
        if status == "read":
            shaped = "value" in item and "reason" not in item
        elif status == "not-read":
            shaped = "value" not in item and item.get("reason") in _UNREAD_REASONS
        else:
            shaped = False
        if not shaped or not isinstance(item.get("evidence"), list):
            errors.append("finding-shape")
        key = jcs([item.get("kind"), item.get("entity"), item.get("param")]).decode("utf-8")
        if key in seen:
            errors.append("finding-duplicate")
        seen.add(key)
        if not _entity_known(item.get("entity"), report, request):
            errors.append("finding-entity-unknown")
        elif "address" in item["entity"]:
            present.add((item["kind"], item["entity"]["address"], item["entity"]["role"]))
        if "at" in item and (item["at"].get("chainId"), item["at"].get("block")) not in blocks:
            errors.append("at-not-in-blocks")
    for entity in entities["addresses"]:
        required = list(CORE_ADDRESS)
        if entity["role"] == "authority":
            required.append(K_DELEGATION)
        if entity["role"] == "account":
            required.append(K_UNSIGNED)
        for kind in required:
            if _kind_requested(request, kind) and (kind, entity["address"], entity["role"]) not in present:
                errors.append("core-kind-missing")
    return errors


# ---------------------------------------------------------------- the signer, and the source
class ReportSigner(Protocol):
    """Whatever signs a report. The caller supplies it; this module never holds a key.

    ``account`` is the CAIP-10 account that signs. ``sign_digest`` returns the 65-byte ``r || s || v``
    signature over the given 32-byte EIP-712 digest.
    """

    account: str

    def sign_digest(self, digest: bytes) -> bytes: ...


@dataclass(frozen=True)
class Source:
    """The report's source: an ERC-8004 identity registry (CAIP-10) and the agent id in it."""

    registry: str
    agent_id: str


def _caip_evm(account: Any, what: str) -> tuple[int, str]:
    match = _CAIP_EVM.fullmatch(account) if isinstance(account, str) else None
    if not match:
        raise ValueError(f"{what} must be a CAIP-10 eip155 account")
    return int(match.group(1)), match.group(2).lower()


def _recover(digest_bytes: bytes, signature: bytes) -> Optional[str]:
    """The 0x address that signed, or ``None``."""
    try:
        v = signature[64]
        if v in (27, 28):
            v -= 27
        if v not in (0, 1):
            return None
        key = PublicKey.from_signature_and_message(signature[:64] + bytes([v]), digest_bytes, hasher=None)
    except Exception:  # coincurve raises its own types for a signature that recovers to nothing
        return None
    return "0x" + keccak256(key.format(compressed=False)[1:])[12:].hex()


def _signature_bytes(report: Mapping[str, Any]) -> Optional[bytes]:
    text = report.get("signature")
    if not isinstance(text, str) or not re.fullmatch(r"0x[0-9a-fA-F]{130}", text):
        return None
    return bytes.fromhex(text[2:])


def _registry_chain(report: Mapping[str, Any]) -> int:
    return _caip_evm(report["source"]["registry"], "source registry")[0]


def _signing_digest(report: Mapping[str, Any]) -> bytes:
    value = eip712_digest(
        report["subjectDigest"], report["contextDigest"], report_hash(report), _registry_chain(report)
    )
    return bytes.fromhex(value[2:])


def recover_signer(report: Mapping[str, Any]) -> Optional[str]:
    """The CAIP-10 account (on the registry's chain) whose key signed this report, or ``None``."""
    signature = _signature_bytes(report)
    if signature is None:
        return None
    address = _recover(_signing_digest(report), signature)
    if address is None:
        return None
    return f"eip155:{_registry_chain(report)}:{eip55(address)}"


def verify_report(report: Mapping[str, Any], request: Mapping[str, Any]) -> list[str]:
    """Steps 1 and 2 of the draft's verification for a signer without code (``ecrecover``).

    The consumer's own digests come from its own request, never from the report. Step 3, that the signer is
    the source's on the registry, needs a chain read and is not done here. ``[]`` means the digests and the
    signature agree.
    """
    errors: list[str] = []
    if report.get("subjectDigest") != subject_digest(request["subject"]):
        errors.append("subject-digest-mismatch")
    if report.get("contextDigest") != context_digest(request.get("context")):
        errors.append("context-digest-mismatch")
    try:
        recovered = recover_signer(report)
        claimed = _caip_evm(report["source"]["signer"], "source signer")[1]
    except (KeyError, TypeError, ValueError):
        return errors + ["signature-invalid"]
    if recovered is None:
        errors.append("signature-invalid")
    elif recovered.split(":")[2].lower() != claimed:
        errors.append("signer-mismatch")
    return errors


# ---------------------------------------------------------------- EIP-7702 authority
def _rlp_bytes(raw: bytes) -> bytes:
    if len(raw) == 1 and raw[0] < 0x80:
        return raw
    return bytes([0x80 + len(raw)]) + raw if len(raw) < 56 else b"\xb8" + bytes([len(raw)]) + raw


def _rlp_int(value: int) -> bytes:
    return _rlp_bytes(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def recover_authority(auth: Mapping[str, Any]) -> Optional[str]:
    """The 0x address that signed an EIP-7702 authorization tuple, or ``None`` if the tuple does not recover."""
    try:
        chain_id, nonce = int(auth["chainId"]), int(auth["nonce"])
        address = auth["address"]
        r_value, s_value, parity = int(auth["r"], 16), int(auth["s"], 16), auth["yParity"]
        if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
            return None
        if parity not in (0, 1) or isinstance(parity, bool):
            return None
        if not (0 < r_value < _SECP256K1_N and 0 < s_value <= _SECP256K1_N // 2):
            return None
        if not (0 <= chain_id < 2**256 and 0 <= nonce < 2**64):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    body = _rlp_int(chain_id) + _rlp_bytes(bytes.fromhex(address[2:])) + _rlp_int(nonce)
    message = b"\x05" + (bytes([0xC0 + len(body)]) + body if len(body) < 56 else b"\xf8" + bytes([len(body)]) + body)
    return _recover(keccak256(message), r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big") + bytes([parity]))


# ---------------------------------------------------------------- building the report
def _time(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise ValueError(f"{name} must be an RFC 3339 UTC time ending in Z")
    try:
        return datetime.strptime(value[:-1].split(".")[0], "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"{name} is not a real time") from exc


def _address(value: Any, chain: str) -> str:
    match = _EVM.fullmatch(value) if isinstance(value, str) else None
    if not match or (":" in value and not value.startswith(chain + ":")):
        raise ValueError("subject has an address that is not an EVM address on its chain")
    return match.group(1).lower()


def _caip(chain: str, address: str) -> str:
    return f"{chain}:{eip55(address)}"


def _path_key(path: str) -> tuple[int, ...]:
    if path == "typedData":
        return (0,)
    return (1,) + tuple(int(i) for i in _PATH_INDEXES.findall(path))


def _check_request(request: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    if request.get("version") != VERSION:
        raise ValueError(f"request version must be {VERSION}")
    subject = request.get("subject")
    if not isinstance(subject, dict) or subject.get("type") != "calls":
        raise ValueError("only a calls subject is supported")
    chain = subject.get("chainId")
    if not isinstance(chain, str) or not _CHAIN.fullmatch(chain):
        raise ValueError("subject chainId must be a CAIP-2 eip155 chain")
    context = request.get("context")
    if context is not None and (
        not isinstance(context, dict) or {"payment", "texts", "tokens", "intent"} & set(context)
    ):
        raise ValueError("context carries payment, texts, tokens or intent, which this source does not read")
    _address(subject.get("from"), chain)
    if not isinstance(subject.get("calls"), list):
        raise ValueError("subject has no calls list")
    return subject, chain


def _check_result(subject: Mapping[str, Any], chain: str, result: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    calls = subject["calls"]
    authorizations = subject.get("authorizationList", [])
    if result is None:
        if calls or authorizations:
            raise ValueError("a check result is needed for a request with calls or an authorization list")
        return {"findings": [], "gaps": [], "wrappers": [], "undecoded": [], "calls": []}
    lists_ = ("findings", "gaps", "wrappers", "undecoded", "calls")
    if not isinstance(result, dict) or not all(isinstance(result.get(k), list) for k in lists_):
        raise ValueError("the check result is not a scan of calls")
    seen = {c.get("path"): c.get("target") for c in result["calls"] if isinstance(c, dict)}
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            raise ValueError("subject call is not an object")
        if call.get("to") is None:
            continue
        if seen.get(f"tx.calls[{index}]") != _address(call["to"], chain):
            raise ValueError("the check result is not about this request's calls")
    return result


def _requested(request: Mapping[str, Any]) -> list[tuple[str, Optional[Any], bool]]:
    """(kind, param, has_param) for each entry of ``kinds``."""
    kinds = request.get("kinds")
    if kinds is None:
        return []
    if not isinstance(kinds, list):
        raise ValueError("kinds must be a list")
    out: list[tuple[str, Optional[Any], bool]] = []
    for entry in kinds:
        if isinstance(entry, str):
            out.append((entry, None, False))
        elif isinstance(entry, dict) and isinstance(entry.get("kind"), str) and "param" in entry:
            out.append((entry["kind"], entry["param"], True))
        else:
            raise ValueError("a kinds entry is a string or {kind, param}")
    return out


def _gaps(
    request: Mapping[str, Any], subject: Mapping[str, Any], result: Mapping[str, Any], bad_tuple: bool
) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}

    def add(path: str, reason: str, nested: str) -> None:
        found.setdefault(path, {"path": path, "reason": reason, "nested": nested})

    for gap in result["gaps"]:
        add(gap["path"], gap["reason"], gap["nested"])
    for path in result["wrappers"]:
        add(path, "unverified-wrapper", "inspected")
    for call in result["undecoded"]:
        add(call["path"], "unsupported-selector", "not-inspected")
    if "typedData" in subject:
        add("typedData", "unsupported-selector", "not-inspected")
    if bad_tuple:
        add("tx", "undecodable-data", "not-inspected")
    gaps = sorted(found.values(), key=lambda g: _path_key(g["path"]))
    return gaps


def _unread(
    kind: str, entity: dict[str, Any], param: Any = None, has_param: bool = False, reason: str = "unsupported"
) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": kind, "entity": entity}
    if has_param:
        item["param"] = param
    item.update(status="not-read", reason=reason, evidence=[], note=_NOTE_UNREAD)
    return item


def _read(kind: str, entity: dict[str, Any], value: Any, param: Any = None, has_param: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": kind, "entity": entity}
    if has_param:
        item["param"] = param
    item.update(status="read", value=value, evidence=[], note=_NOTE_CALLDATA)
    return item


def build_report(
    request: Mapping[str, Any],
    result: Optional[Mapping[str, Any]],
    *,
    source: Source,
    signer_account: str,
    issued_at: str,
    expires_at: Optional[str] = None,
    method: str = METHOD,
    lists: Optional[Mapping[str, Optional[dict[str, Any]]]] = None,
) -> dict[str, Any]:
    """The unsigned report for a request, from the check's answer about it.

    ``result`` is the answer of ``admin_calls.evaluate_admin_calls`` for this request (``None`` is allowed only
    for a request with no calls and no authorization list, such as typed data alone). ``lists`` maps a list uri
    to its parsed document, or to ``None`` when the fetch failed; it answers ``address.on-list`` requests.
    Raises ``ValueError`` for a request or result this module cannot report on honestly, and for a report that
    would not conform.
    """
    subject, chain = _check_request(request)
    scan = _check_result(subject, chain, result)
    _caip_evm(source.registry, "source registry")
    if not isinstance(source.agent_id, str) or not re.fullmatch(r"0|[1-9][0-9]*", source.agent_id):
        raise ValueError("source agent id must be a decimal string")
    _caip_evm(signer_account, "signer account")
    issued = _time(issued_at, "issued_at")
    if expires_at is not None and _time(expires_at, "expires_at") <= issued:
        raise ValueError("expires_at must be after issued_at")

    # Authorities of the EIP-7702 tuples, in order.
    authorities: dict[int, str] = {}
    bad_tuple = False
    for index, auth in enumerate(subject.get("authorizationList", [])):
        recovered = recover_authority(auth) if isinstance(auth, dict) else None
        if recovered is None:
            bad_tuple = True
        else:
            authorities[index] = recovered

    addresses: list[dict[str, str]] = []

    def entity(address: str, role: str) -> dict[str, str]:
        item = {"address": _caip(chain, address), "role": role}
        if item not in addresses:
            addresses.append(item)
        return item

    for call in sorted(scan["calls"], key=lambda c: _path_key(c["path"])):
        entity(call["target"], "target")
        match = re.fullmatch(r"tx\.calls\[(\d+)\]", call["path"])
        if match:
            value = subject["calls"][int(match.group(1))].get("value", "0")
            if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
                raise ValueError("subject call value must be a decimal string")
            if int(value) > 0:
                entity(call["target"], "recipient")
    for index in sorted(authorities):
        entity(authorities[index], "authority")

    custom: list[dict[str, Any]] = []
    power: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def add_power(account: str, change: str, grantee: Optional[str], from_call: bool) -> None:
        ent = entity(account, "account")
        n = counts.get(account, 0)
        counts[account] = n + 1
        if from_call and grantee is not None:
            value = {
                "account": _caip(chain, account),
                "change": change,
                "grantee": _caip(chain, grantee),
                "before": None,
                "after": None,
            }
            power.append(_read(K_POWER, ent, value, n, True))
        else:
            power.append(_unread(K_POWER, ent, n, True))

    for found in sorted(
        scan["findings"], key=lambda f: _path_key(f["path"]) if f["path"].startswith("tx.calls") else (2,)
    ):
        kind, path, target, signature = found["kind"], found["path"], found["target"], found["signature"]
        new_value = found.get("new_value")
        new: Optional[str]
        if kind == "eip7702-delegation":
            match = re.fullmatch(r"tx\.authorizationList\[(\d+)\]", path)
            authority = authorities.get(int(match.group(1))) if match else None
            if authority is None:
                continue  # the tuple did not recover; that is a coverage gap
            on = entity(authority, "authority")
            new = _caip(chain, target)
        else:
            on = entity(target, "target")
            new = _caip(chain, new_value) if isinstance(new_value, str) and _EVM.fullmatch(new_value) else None
        param = {"path": path, "kind": kind}
        custom.append(_read(KIND_ADMIN_CALL, on, {"kind": kind, "signature": signature, "newValue": new}, param, True))
        if kind.startswith("multisig-") and signature in _POWER:
            for change, from_call in _POWER[signature]:
                add_power(target, change, new_value if isinstance(new_value, str) else None, from_call)
        if kind == "delegatecall" and isinstance(found.get("account"), str):
            add_power(found["account"], "code-replaced", target, True)

    requested = _requested(request)
    named = {k for k, _, _ in requested}
    has_kinds = request.get("kinds") is not None

    def core(kind: str) -> bool:
        return not has_kinds or kind in named

    findings: list[dict[str, Any]] = []
    for ent in addresses:
        for kind in CORE_ADDRESS:
            if core(kind):
                findings.append(_unread(kind, ent))
        if ent["role"] == "authority" and core(K_DELEGATION):
            findings.append(_unread(K_DELEGATION, ent))
        if ent["role"] == "account":
            if core(K_UNSIGNED):
                findings.append(_unread(K_UNSIGNED, ent))
            if core(K_POWER):
                findings.extend(f for f in power if f["entity"] == ent)
        for kind, asked, has_param in requested:
            if kind not in _ADDRESS_OPTIONAL:
                continue
            if kind == KIND_ON_LIST and has_param and isinstance(asked, dict):
                outcome = read_on_list(ent["address"], asked, lists or {})
                item: dict[str, Any] = {"kind": kind, "entity": ent, "param": asked}
                item.update(outcome)
                item.update(evidence=[])
                findings.append(item)
            else:
                findings.append(_unread(kind, ent, asked, has_param))
    findings.extend(custom)
    unique: dict[str, dict[str, Any]] = {}
    for item in findings:
        unique.setdefault(jcs([item["kind"], item["entity"], item.get("param")]).decode("utf-8"), item)

    gaps = _gaps(request, subject, scan, bad_tuple)
    report: dict[str, Any] = {
        "version": VERSION,
        "subjectDigest": subject_digest(subject),
        "contextDigest": context_digest(request.get("context")),
        "chainId": chain,
        "signer": subject["from"],
        "source": {"registry": source.registry, "agentId": source.agent_id, "signer": signer_account},
        "issuedAt": issued_at,
        "blocks": [],
        "entities": {"tokens": [], "approvals": [], "addresses": addresses, "payment": False, "texts": []},
        "coverage": {"status": "partial" if gaps else "complete", "gaps": gaps},
        "findings": list(unique.values()),
        "method": method,
    }
    if expires_at is not None:
        report["expiresAt"] = expires_at
    problems = conformance_errors(report, request)
    if problems:
        raise ValueError("report does not conform: " + ", ".join(sorted(set(problems))))
    return report


def emit_report(
    request: Mapping[str, Any],
    result: Optional[Mapping[str, Any]],
    *,
    source: Source,
    signer: ReportSigner,
    issued_at: str,
    expires_at: Optional[str] = None,
    lists: Optional[Mapping[str, Optional[dict[str, Any]]]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[dict[str, Any]]:
    """The signed report, or ``None`` when ``CLAW_GUARD_ERC8481`` is off. Nothing is built or signed when off.

    The signer is asked once, for the EIP-712 digest of the finished report. A signature that is not 65 bytes,
    or that does not recover to the signer's own account, raises: an unsigned report is never returned.
    """
    if not emit_enabled(env):
        return None
    report = build_report(
        request,
        result,
        source=source,
        signer_account=getattr(signer, "account", None),  # type: ignore[arg-type]
        issued_at=issued_at,
        expires_at=expires_at,
        lists=lists,
    )
    raw = signer.sign_digest(_signing_digest(report))
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 65:
        raise ValueError("signature must be 65 bytes")
    signature = bytes(raw)
    if signature[64] in (0, 1):
        signature = signature[:64] + bytes([signature[64] + 27])
    expected = _caip_evm(report["source"]["signer"], "signer account")[1]
    if _recover(_signing_digest(report), signature) != expected:
        raise ValueError("signature does not recover to the signer account")
    signed = dict(report)
    signed["signature"] = "0x" + signature.hex()
    return signed
