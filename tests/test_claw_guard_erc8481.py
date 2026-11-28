"""The ERC-8481 report emitter: Claw Guard check results in, a signed, request-bound report out.

The emitter is off by default, signs only through an injected signer, and never carries a score, a verdict
or an action. ``FixtureSigner`` below is the test signer: its key is derived here at run time and exists
nowhere else.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from coincurve import PrivateKey, PublicKey

from bot.claw_guard import erc8481 as r
from bot.claw_guard import erc8481_lists as lists
from bot.hrc.keccak import keccak256

ROOT = Path(__file__).resolve().parent.parent
CHAIN = "eip155:1"
ZERO32 = "0x" + "00" * 32

OWNER = "0x" + "0a" * 20
PROXY = "0x" + "1b" * 20
TOKEN = "0x" + "2c" * 20
SAFE_WALLET = "0x" + "3d" * 20
ATTACKER = "0x" + "4e" * 20
HELPER = "0x" + "5f" * 20
REGISTRY = "0x" + "66" * 20
NOW = "2026-11-28T09:00:00Z"


def caip(address: str, chain: str = CHAIN) -> str:
    return f"{chain}:{lists.eip55(address)}"


def pad(address: str) -> bytes:
    return b"\x00" * 12 + bytes.fromhex(address[2:])


def word(n: int) -> bytes:
    return n.to_bytes(32, "big")


UPGRADE_TO = bytes.fromhex("3659cfe6") + pad(ATTACKER)
ADD_OWNER = bytes.fromhex("0d582f13") + pad(ATTACKER) + word(1)
ENABLE_MODULE = bytes.fromhex("610b5925") + pad(ATTACKER)
CHANGE_THRESHOLD = bytes.fromhex("694e80c3") + word(1)
UNKNOWN_CALL = bytes.fromhex("a9059cbb") + pad(ATTACKER) + word(5)


def request_for(*calls: tuple[str, int, bytes], authorizations: list | None = None, **extra: Any) -> dict:
    subject: dict[str, Any] = {
        "type": "calls",
        "chainId": CHAIN,
        "from": caip(OWNER),
        "calls": [{"to": caip(to), "value": str(value), "data": "0x" + data.hex()} for to, value, data in calls],
    }
    if authorizations is not None:
        subject["authorizationList"] = authorizations
    return {"version": "erc-presign/1", "subject": subject, **extra}


def finding(kind: str, path: str, target: str, signature: str, new_value=None, account=None) -> dict:
    return {
        "kind": kind,
        "path": path,
        "target": target,
        "signature": signature,
        "new_value": new_value,
        "account": account,
    }


def result_for(request: dict, *, findings=(), gaps=(), wrappers=(), undecoded=(), nested=()) -> dict:
    """The shape of ``admin_calls.evaluate_admin_calls``. Hand-built here so this PR does not import that one."""
    calls = [
        {"path": f"tx.calls[{i}]", "target": c["to"].split(":")[-1].lower()}
        for i, c in enumerate(request["subject"]["calls"])
    ] + [{"path": p, "target": t} for p, t in nested]
    return {
        "decision": "DENY",
        "rules": ["UPG-1"],
        "findings": list(findings),
        "gaps": list(gaps),
        "wrappers": list(wrappers),
        "undecoded": list(undecoded),
        "calls": calls,
    }


# ---------------------------------------------------------------- the test signer
def eth_address(public: PublicKey) -> str:
    return "0x" + keccak256(public.format(compressed=False)[1:])[12:].hex()


class FixtureSigner:
    """Signs with a key derived from a tag at run time. Counts and records what it was asked to sign."""

    def __init__(self, tag: str, chain: str = "eip155:4663") -> None:
        self._key = PrivateKey(keccak256(f"claw-guard erc8481 fixture {tag}".encode()))
        self.address = eth_address(self._key.public_key)
        self.account = f"{chain}:{lists.eip55(self.address)}"
        self.asked: list[bytes] = []

    def sign_digest(self, digest: bytes) -> bytes:
        self.asked.append(digest)
        raw = self._key.sign_recoverable(digest, hasher=None)
        return raw[:64] + bytes([raw[64] + 27])


SOURCE_SIGNER = FixtureSigner("source")
SOURCE = r.Source(registry=f"eip155:4663:{lists.eip55(REGISTRY)}", agent_id="20030")


def rlp_int(n: int) -> bytes:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return rlp_bytes(raw)


def rlp_bytes(b: bytes) -> bytes:
    if len(b) == 1 and b[0] < 0x80:
        return b
    return bytes([0x80 + len(b)]) + b if len(b) < 56 else b"\xb8" + bytes([len(b)]) + b


def rlp_list(items: list[bytes]) -> bytes:
    body = b"".join(items)
    return bytes([0xC0 + len(body)]) + body if len(body) < 56 else b"\xf8" + bytes([len(body)]) + body


def signed_authorization(signer: FixtureSigner, delegate: str, nonce: int = 0, chain_id: int = 1) -> dict:
    payload = b"\x05" + rlp_list([rlp_int(chain_id), rlp_bytes(bytes.fromhex(delegate[2:])), rlp_int(nonce)])
    sig = signer.sign_digest(keccak256(payload))
    return {
        "chainId": str(chain_id),
        "address": delegate,
        "nonce": str(nonce),
        "yParity": sig[64] - 27,
        "r": "0x" + sig[:32].hex(),
        "s": "0x" + sig[32:64].hex(),
    }


def build(request: dict, result: dict | None, **kw: Any) -> dict:
    kw.setdefault("source", SOURCE)
    kw.setdefault("signer_account", SOURCE_SIGNER.account)
    kw.setdefault("issued_at", NOW)
    return r.build_report(request, result, **kw)


def emit(request: dict, result: dict | None, signer=SOURCE_SIGNER, **kw: Any) -> dict:
    kw.setdefault("source", SOURCE)
    kw.setdefault("issued_at", NOW)
    kw.setdefault("env", {r.FLAG: "1"})
    out = r.emit_report(request, result, signer=signer, **kw)
    assert out is not None
    return out


def by_kind(report: dict, kind: str) -> list[dict]:
    return [f for f in report["findings"] if f["kind"] == kind]


UPGRADE_REQUEST = request_for((PROXY, 0, UPGRADE_TO))
UPGRADE_RESULT = result_for(
    UPGRADE_REQUEST,
    findings=[finding("proxy-upgrade", "tx.calls[0]", PROXY, "upgradeTo(address)", ATTACKER)],
)


# ---------------------------------------------------------------- off by default
@pytest.mark.parametrize("value", [None, "", "0", "false", "yes", "on", "TRUE1", "2"])
def test_the_emitter_is_off_unless_the_flag_is_exactly_one_or_true(value):
    env = {} if value is None else {r.FLAG: value}
    assert r.emit_enabled(env) is False


@pytest.mark.parametrize("value", ["1", "true", "True", " 1 "])
def test_the_flag_turns_it_on(value):
    assert r.emit_enabled({r.FLAG: value}) is True


def test_the_flag_is_named_for_the_emitter_and_is_not_the_checks_own_flag():
    assert r.FLAG == "CLAW_GUARD_ERC8481"


def test_when_off_nothing_is_built_and_the_signer_is_never_called():
    signer = FixtureSigner("spy")
    assert r.emit_report(UPGRADE_REQUEST, UPGRADE_RESULT, source=SOURCE, signer=signer, issued_at=NOW, env={}) is None
    assert signer.asked == []


def test_the_default_environment_is_the_process_environment(monkeypatch):
    monkeypatch.delenv(r.FLAG, raising=False)
    signer = FixtureSigner("spy2")
    assert r.emit_report(UPGRADE_REQUEST, UPGRADE_RESULT, source=SOURCE, signer=signer, issued_at=NOW) is None
    monkeypatch.setenv(r.FLAG, "1")
    assert r.emit_report(UPGRADE_REQUEST, UPGRADE_RESULT, source=SOURCE, signer=signer, issued_at=NOW) is not None


# ---------------------------------------------------------------- binding
def test_the_report_is_bound_to_the_exact_request():
    report = build(UPGRADE_REQUEST, UPGRADE_RESULT)
    assert report["subjectDigest"] == r.subject_digest(UPGRADE_REQUEST["subject"])
    assert report["contextDigest"] == ZERO32
    assert report["chainId"] == CHAIN
    assert report["signer"] == UPGRADE_REQUEST["subject"]["from"]
    assert report["version"] == "erc-presign/1"


def test_one_changed_byte_of_calldata_is_another_request():
    other = request_for((PROXY, 0, UPGRADE_TO[:-1] + b"\x00"))
    assert r.subject_digest(other["subject"]) != r.subject_digest(UPGRADE_REQUEST["subject"])
    report = emit(UPGRADE_REQUEST, UPGRADE_RESULT)
    assert r.verify_report(report, other) == ["subject-digest-mismatch"]


def test_the_order_of_the_authorization_list_is_part_of_the_binding():
    a = signed_authorization(FixtureSigner("a"), HELPER, nonce=0)
    b = signed_authorization(FixtureSigner("b"), ATTACKER, nonce=0)
    one = request_for((PROXY, 0, b""), authorizations=[a, b])
    two = request_for((PROXY, 0, b""), authorizations=[b, a])
    assert r.subject_digest(one["subject"]) != r.subject_digest(two["subject"])


def test_the_digests_come_from_the_request_and_not_from_the_check_result():
    result = copy.deepcopy(UPGRADE_RESULT)
    result["subjectDigest"] = "0x" + "ab" * 32
    assert build(UPGRADE_REQUEST, result)["subjectDigest"] == r.subject_digest(UPGRADE_REQUEST["subject"])


def test_a_check_result_that_is_not_about_this_request_is_refused():
    other = request_for((TOKEN, 0, UNKNOWN_CALL))
    with pytest.raises(ValueError, match="check result"):
        build(UPGRADE_REQUEST, result_for(other))
    with pytest.raises(ValueError, match="check result"):
        build(UPGRADE_REQUEST, {"decision": "DENY", "rules": ["REQ-1"]})
    with pytest.raises(ValueError, match="check result"):
        build(UPGRADE_REQUEST, None)


def test_a_typed_data_only_request_needs_no_check_result_and_is_a_gap():
    request = {
        "version": "erc-presign/1",
        "subject": {"type": "calls", "chainId": CHAIN, "from": caip(OWNER), "calls": [], "typedData": {"x": 1}},
    }
    report = build(request, None)
    assert report["coverage"] == {
        "status": "partial",
        "gaps": [{"path": "typedData", "reason": "unsupported-selector", "nested": "not-inspected"}],
    }
    assert r.conformance_errors(report, request) == []


def test_a_context_the_emitter_cannot_read_is_refused_not_ignored():
    for context in ({"texts": [{"field": "tool-output", "text": "x"}]}, {"payment": {"requirements": {}}}):
        with pytest.raises(ValueError, match="context"):
            build({**UPGRADE_REQUEST, "context": context}, UPGRADE_RESULT)
    with pytest.raises(ValueError, match="context"):
        build({**UPGRADE_REQUEST, "context": {"intent": {"action": "swap"}}}, UPGRADE_RESULT)
    with pytest.raises(ValueError, match="context"):
        build({**UPGRADE_REQUEST, "context": {"tokens": [caip(TOKEN)]}}, UPGRADE_RESULT)
    empty = build({**UPGRADE_REQUEST, "context": {}}, UPGRADE_RESULT)
    assert empty["contextDigest"] == r.context_digest({}) != ZERO32


def test_an_envelope_subject_is_refused():
    with pytest.raises(ValueError, match="subject"):
        build({"version": "erc-presign/1", "subject": {"type": "prepared-tx-envelope", "envelope": {}}}, None)


def test_a_request_of_another_version_is_refused():
    with pytest.raises(ValueError, match="version"):
        build({**UPGRADE_REQUEST, "version": "erc-presign/2"}, UPGRADE_RESULT)


# ---------------------------------------------------------------- no score, no verdict
FORBIDDEN = {"score", "verdict", "severity", "action", "recommendation", "decision", "risk", "level", "rating", "text"}
VERDICT_WORDS = ("Blocked by your rule", "Allowed: within", "Needs your OK", "DENY", "ALLOW", "CONFIRM", "UPG-1")


def walk(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)
    elif isinstance(value, str):
        yield value


def test_the_report_has_no_score_no_verdict_and_no_action():
    report = emit(UPGRADE_REQUEST, UPGRADE_RESULT)
    seen = list(walk(report))
    assert not FORBIDDEN & {s for s in seen if isinstance(s, str)}
    blob = json.dumps(report)
    for word_ in VERDICT_WORDS:
        assert word_ not in blob


def test_the_decision_and_the_rule_ids_of_the_check_stay_out_of_the_report():
    result = copy.deepcopy(UPGRADE_RESULT)
    result.update({"decision": "DENY", "rules": ["UPG-1"], "text": "Blocked by your rule: x", "order_hash": "0x1"})
    assert build(UPGRADE_REQUEST, result) == build(UPGRADE_REQUEST, UPGRADE_RESULT)


def test_a_report_that_carries_a_verdict_does_not_conform():
    report = build(UPGRADE_REQUEST, UPGRADE_RESULT)
    report["findings"][0]["severity"] = "high"
    assert "forbidden-field" in r.conformance_errors(report, UPGRADE_REQUEST)
    report = build(UPGRADE_REQUEST, UPGRADE_RESULT)
    report["verdict"] = "block"
    assert "forbidden-field" in r.conformance_errors(report, UPGRADE_REQUEST)


def test_the_report_does_not_reproduce_calldata():
    blob = json.dumps(build(UPGRADE_REQUEST, UPGRADE_RESULT))
    assert UPGRADE_TO.hex() not in blob
    assert "3659cfe6" not in blob


# ---------------------------------------------------------------- entities and core findings
def test_every_call_target_is_an_address_entity_once_per_role():
    request = request_for((PROXY, 0, UPGRADE_TO), (PROXY, 0, b""), (TOKEN, 0, UNKNOWN_CALL))
    result = result_for(request, undecoded=[{"path": "tx.calls[2]", "selector": "0xa9059cbb"}])
    report = build(request, result)
    assert report["entities"] == {
        "tokens": [],
        "approvals": [],
        "addresses": [{"address": caip(PROXY), "role": "target"}, {"address": caip(TOKEN), "role": "target"}],
        "payment": False,
        "texts": [],
    }


def test_a_nested_call_is_listed_as_a_target_too():
    request = request_for((SAFE_WALLET, 0, UNKNOWN_CALL))
    result = result_for(
        request,
        wrappers=["tx.calls[0]"],
        nested=[("tx.calls[0].inner[0]", PROXY), ("tx.calls[0].inner[1]", SAFE_WALLET)],
    )
    roles = [(a["address"], a["role"]) for a in build(request, result)["entities"]["addresses"]]
    assert roles == [(caip(SAFE_WALLET), "target"), (caip(PROXY), "target")]


def test_native_value_makes_the_receiver_a_recipient_as_well():
    request = request_for((HELPER, 7, b""))
    roles = [(a["address"], a["role"]) for a in build(request, result_for(request))["entities"]["addresses"]]
    assert roles == [(caip(HELPER), "target"), (caip(HELPER), "recipient")]


def test_a_zero_value_call_makes_no_recipient():
    roles = [a["role"] for a in build(UPGRADE_REQUEST, UPGRADE_RESULT)["entities"]["addresses"]]
    assert roles == ["target"]


def test_addresses_are_written_as_caip_10_with_the_eip_55_checksum():
    address = build(UPGRADE_REQUEST, UPGRADE_RESULT)["entities"]["addresses"][0]["address"]
    assert address == f"eip155:1:{lists.eip55(PROXY)}"


def test_every_listed_address_carries_each_core_kind_not_read_because_no_chain_state_is_read():
    report = build(UPGRADE_REQUEST, UPGRADE_RESULT)
    entity = {"address": caip(PROXY), "role": "target"}
    for kind in ("address.code", "address.first-seen-block", "address.prior-transfers"):
        (item,) = [f for f in report["findings"] if f["kind"] == f"erc.presign.{kind}" and f["entity"] == entity]
        assert item["status"] == "not-read"
        assert item["reason"] == "unsupported"
        assert item["evidence"] == []
        assert "value" not in item and "at" not in item
    assert report["blocks"] == []


def test_a_read_finding_has_a_value_and_no_reason_and_a_not_read_one_the_reverse():
    request = request_for((SAFE_WALLET, 0, ADD_OWNER))
    result = result_for(
        request,
        findings=[
            finding(
                "multisig-owner-change", "tx.calls[0]", SAFE_WALLET, "addOwnerWithThreshold(address,uint256)", ATTACKER
            )
        ],
    )
    statuses = set()
    for item in build(request, result)["findings"]:
        statuses.add(item["status"])
        if item["status"] == "read":
            assert "value" in item and "reason" not in item
        else:
            assert "value" not in item and "reason" in item
        assert isinstance(item["evidence"], list)
    assert statuses == {"read", "not-read"}


def test_no_two_findings_share_a_kind_an_entity_and_a_param():
    request = request_for((SAFE_WALLET, 0, ADD_OWNER), (SAFE_WALLET, 0, ENABLE_MODULE))
    result = result_for(
        request,
        findings=[
            finding(
                "multisig-owner-change", "tx.calls[0]", SAFE_WALLET, "addOwnerWithThreshold(address,uint256)", ATTACKER
            ),
            finding("multisig-power-change", "tx.calls[1]", SAFE_WALLET, "enableModule(address)", ATTACKER),
        ],
    )
    report = build(request, result)
    keys = [json.dumps([f["kind"], f["entity"], f.get("param")], sort_keys=True) for f in report["findings"]]
    assert len(keys) == len(set(keys))
    assert r.conformance_errors(report, request) == []


# ---------------------------------------------------------------- the admin-call finding
def test_an_admin_call_is_a_finding_under_a_kind_the_project_controls():
    report = build(UPGRADE_REQUEST, UPGRADE_RESULT)
    (item,) = by_kind(report, r.KIND_ADMIN_CALL)
    assert not r.KIND_ADMIN_CALL.startswith("erc.presign.")
    assert r.KIND_ADMIN_CALL == "com.humanoid-traders.claw-guard.admin-call"
    assert item["entity"] == {"address": caip(PROXY), "role": "target"}
    assert item["param"] == {"path": "tx.calls[0]", "kind": "proxy-upgrade"}
    assert item["status"] == "read"
    assert item["value"] == {"kind": "proxy-upgrade", "signature": "upgradeTo(address)", "newValue": caip(ATTACKER)}
    assert "at" not in item
    assert item["evidence"] == []


def test_an_admin_call_with_no_readable_new_value_says_null_not_a_made_up_address():
    request = request_for((PROXY, 0, bytes.fromhex("715018a6")))
    result = result_for(
        request, findings=[finding("ownership-renounce", "tx.calls[0]", PROXY, "renounceOwnership()", None)]
    )
    (item,) = by_kind(build(request, result), r.KIND_ADMIN_CALL)
    assert item["value"]["newValue"] is None


def test_an_admin_call_nested_in_a_batch_sits_on_the_inner_target():
    request = request_for((SAFE_WALLET, 0, UNKNOWN_CALL))
    result = result_for(
        request,
        findings=[finding("proxy-upgrade", "tx.calls[0].inner[0]", PROXY, "upgradeTo(address)", ATTACKER)],
        wrappers=["tx.calls[0]"],
        nested=[("tx.calls[0].inner[0]", PROXY)],
    )
    (item,) = by_kind(build(request, result), r.KIND_ADMIN_CALL)
    assert item["entity"] == {"address": caip(PROXY), "role": "target"}
    assert item["param"]["path"] == "tx.calls[0].inner[0]"


# ---------------------------------------------------------------- account power changes
def power(report: dict, n: int | None = None) -> list[dict]:
    items = by_kind(report, "erc.presign.account.power-change")
    return items if n is None else [i for i in items if i["param"] == n]


def test_a_module_enable_is_one_power_change_on_the_account_entity():
    request = request_for((SAFE_WALLET, 0, ENABLE_MODULE))
    result = result_for(
        request,
        findings=[finding("multisig-power-change", "tx.calls[0]", SAFE_WALLET, "enableModule(address)", ATTACKER)],
    )
    report = build(request, result)
    assert {"address": caip(SAFE_WALLET), "role": "account"} in [a for a in report["entities"]["addresses"]]
    (item,) = power(report)
    assert item["entity"] == {"address": caip(SAFE_WALLET), "role": "account"}
    assert item["param"] == 0
    assert item["status"] == "read"
    assert item["value"] == {
        "account": caip(SAFE_WALLET),
        "change": "module-enabled",
        "grantee": caip(ATTACKER),
        "before": None,
        "after": None,
    }


def test_adding_an_owner_is_two_changes_and_the_threshold_one_is_not_read():
    request = request_for((SAFE_WALLET, 0, ADD_OWNER))
    result = result_for(
        request,
        findings=[
            finding(
                "multisig-owner-change", "tx.calls[0]", SAFE_WALLET, "addOwnerWithThreshold(address,uint256)", ATTACKER
            )
        ],
    )
    report = build(request, result)
    first, second = power(report, 0)[0], power(report, 1)[0]
    assert first["value"]["change"] == "owner-added" and first["value"]["grantee"] == caip(ATTACKER)
    assert second["status"] == "not-read" and second["reason"] == "unsupported"
    assert "value" not in second


def test_changes_to_one_account_count_up_in_the_order_they_would_run():
    request = request_for(
        (SAFE_WALLET, 0, ENABLE_MODULE), (SAFE_WALLET, 0, CHANGE_THRESHOLD), (SAFE_WALLET, 0, ENABLE_MODULE)
    )
    result = result_for(
        request,
        findings=[
            finding("multisig-power-change", "tx.calls[0]", SAFE_WALLET, "enableModule(address)", ATTACKER),
            finding("multisig-threshold-change", "tx.calls[1]", SAFE_WALLET, "changeThreshold(uint256)", None),
            finding("multisig-power-change", "tx.calls[2]", SAFE_WALLET, "enableModule(address)", HELPER),
        ],
    )
    report = build(request, result)
    assert [(p["param"], p["status"]) for p in power(report)] == [(0, "read"), (1, "not-read"), (2, "read")]
    assert power(report, 2)[0]["value"]["grantee"] == caip(HELPER)


def test_changes_to_two_accounts_are_counted_apart():
    other = "0x" + "77" * 20
    request = request_for((SAFE_WALLET, 0, ENABLE_MODULE), (other, 0, ENABLE_MODULE))
    result = result_for(
        request,
        findings=[
            finding("multisig-power-change", "tx.calls[0]", SAFE_WALLET, "enableModule(address)", ATTACKER),
            finding("multisig-power-change", "tx.calls[1]", other, "enableModule(address)", ATTACKER),
        ],
    )
    report = build(request, result)
    assert sorted(p["param"] for p in power(report)) == [0, 0]
    assert {p["entity"]["address"] for p in power(report)} == {caip(SAFE_WALLET), caip(other)}


@pytest.mark.parametrize(
    ("signature", "change"),
    [
        ("disableModule(address,address)", "module-disabled"),
        ("removeOwner(address,address,uint256)", "owner-removed"),
    ],
)
def test_other_decoded_changes_are_read_with_the_address_that_loses_the_power(signature, change):
    request = request_for((SAFE_WALLET, 0, ENABLE_MODULE))
    result = result_for(
        request, findings=[finding("multisig-power-change", "tx.calls[0]", SAFE_WALLET, signature, ATTACKER)]
    )
    item = power(build(request, result), 0)[0]
    assert item["value"]["change"] == change
    assert item["value"]["grantee"] == caip(ATTACKER)


@pytest.mark.parametrize(
    "signature",
    [
        "swapOwner(address,address,address)",
        "changeThreshold(uint256)",
        "setGuard(address)",
        "setFallbackHandler(address)",
    ],
)
def test_a_change_whose_before_and_after_need_chain_state_is_counted_and_not_read(signature):
    request = request_for((SAFE_WALLET, 0, ENABLE_MODULE))
    result = result_for(
        request, findings=[finding("multisig-power-change", "tx.calls[0]", SAFE_WALLET, signature, ATTACKER)]
    )
    (item,) = power(build(request, result))
    assert item["status"] == "not-read" and item["reason"] == "unsupported" and "value" not in item


def test_the_account_entity_carries_unsigned_actors_not_read():
    request = request_for((SAFE_WALLET, 0, ENABLE_MODULE))
    result = result_for(
        request,
        findings=[finding("multisig-power-change", "tx.calls[0]", SAFE_WALLET, "enableModule(address)", ATTACKER)],
    )
    (item,) = by_kind(build(request, result), "erc.presign.account.unsigned-actors")
    assert item["status"] == "not-read" and item["reason"] == "unsupported"
    assert item["entity"] == {"address": caip(SAFE_WALLET), "role": "account"}


def test_a_delegatecall_made_by_an_account_is_a_code_replaced_change_with_the_target_as_grantee():
    request = request_for((SAFE_WALLET, 0, UNKNOWN_CALL))
    result = result_for(
        request,
        findings=[finding("delegatecall", "tx.calls[0].inner[0]", HELPER, "delegatecall", None, SAFE_WALLET)],
        wrappers=["tx.calls[0]"],
        nested=[("tx.calls[0].inner[0]", HELPER)],
    )
    report = build(request, result)
    (item,) = power(report)
    assert item["entity"] == {"address": caip(SAFE_WALLET), "role": "account"}
    assert item["value"] == {
        "account": caip(SAFE_WALLET),
        "change": "code-replaced",
        "grantee": caip(HELPER),
        "before": None,
        "after": None,
    }
    (custom,) = by_kind(report, r.KIND_ADMIN_CALL)
    assert custom["value"]["kind"] == "delegatecall"


def test_a_plain_proxy_upgrade_is_not_an_account_power_change():
    report = build(UPGRADE_REQUEST, UPGRADE_RESULT)
    assert power(report) == []
    assert by_kind(report, "erc.presign.account.unsigned-actors") == []


# ---------------------------------------------------------------- EIP-7702
def test_an_authorization_makes_its_recovered_authority_an_entity_with_delegation_not_read():
    authority = FixtureSigner("authority")
    auth = signed_authorization(authority, HELPER)
    assert r.recover_authority(auth) == authority.address
    request = request_for((PROXY, 0, b""), authorizations=[auth])
    result = result_for(
        request, findings=[finding("eip7702-delegation", "tx.authorizationList[0]", HELPER, "authorizationList")]
    )
    report = build(request, result)
    entity = {"address": f"eip155:1:{lists.eip55(authority.address)}", "role": "authority"}
    assert entity in report["entities"]["addresses"]
    (delegation,) = by_kind(report, "erc.presign.address.delegation")
    assert delegation["entity"] == entity
    assert delegation["status"] == "not-read" and delegation["reason"] == "unsupported"
    (custom,) = by_kind(report, r.KIND_ADMIN_CALL)
    assert custom["entity"] == entity
    assert custom["param"] == {"path": "tx.authorizationList[0]", "kind": "eip7702-delegation"}
    assert custom["value"] == {"kind": "eip7702-delegation", "signature": "authorizationList", "newValue": caip(HELPER)}
    assert report["coverage"]["status"] == "complete"
    assert r.conformance_errors(report, request) == []


def test_only_an_authority_has_the_delegation_kind():
    report = build(UPGRADE_REQUEST, UPGRADE_RESULT)
    assert by_kind(report, "erc.presign.address.delegation") == []


def test_a_tuple_whose_signature_does_not_recover_is_a_gap_on_the_whole_transaction():
    bad = {"chainId": "1", "address": HELPER, "nonce": "0", "yParity": 0, "r": "0x" + "00" * 32, "s": "0x" + "00" * 32}
    assert r.recover_authority(bad) is None
    request = request_for((PROXY, 0, b""), authorizations=[bad])
    result = result_for(
        request, findings=[finding("eip7702-delegation", "tx.authorizationList[0]", HELPER, "authorizationList")]
    )
    report = build(request, result)
    assert report["coverage"]["gaps"] == [{"path": "tx", "reason": "undecodable-data", "nested": "not-inspected"}]
    assert not [a for a in report["entities"]["addresses"] if a["role"] == "authority"]
    assert r.conformance_errors(report, request) == []


def test_the_recovered_authority_matches_an_independent_derivation():
    authority = FixtureSigner("independent")
    auth = signed_authorization(authority, HELPER, nonce=3, chain_id=0)
    assert r.recover_authority(auth) == authority.address
    assert r.recover_authority({**auth, "nonce": "4"}) != authority.address


# ---------------------------------------------------------------- coverage
def test_a_request_read_to_the_end_is_complete():
    assert build(UPGRADE_REQUEST, UPGRADE_RESULT)["coverage"] == {"status": "complete", "gaps": []}


def test_a_call_with_a_selector_nobody_decoded_is_an_unsupported_selector_gap():
    request = request_for((TOKEN, 0, UNKNOWN_CALL))
    result = result_for(request, undecoded=[{"path": "tx.calls[0]", "selector": "0xa9059cbb"}])
    assert build(request, result)["coverage"] == {
        "status": "partial",
        "gaps": [{"path": "tx.calls[0]", "reason": "unsupported-selector", "nested": "not-inspected"}],
    }


def test_a_decoded_wrapper_is_an_unverified_wrapper_gap_because_no_code_was_read():
    request = request_for((SAFE_WALLET, 0, UNKNOWN_CALL))
    result = result_for(request, wrappers=["tx.calls[0]"], nested=[("tx.calls[0].inner[0]", PROXY)])
    assert build(request, result)["coverage"]["gaps"] == [
        {"path": "tx.calls[0]", "reason": "unverified-wrapper", "nested": "inspected"}
    ]


def test_the_gaps_the_check_reported_are_kept_with_their_reason():
    request = request_for((SAFE_WALLET, 0, UNKNOWN_CALL))
    result = result_for(
        request,
        gaps=[
            {"path": "tx.calls[0]", "reason": "depth-limit", "nested": "not-inspected"},
            {"path": "tx", "reason": "not-inspected", "nested": "not-inspected"},
        ],
    )
    gaps = build(request, result)["coverage"]["gaps"]
    assert {"path": "tx.calls[0]", "reason": "depth-limit", "nested": "not-inspected"} in gaps
    assert {"path": "tx", "reason": "not-inspected", "nested": "not-inspected"} in gaps


def test_two_gaps_on_one_path_collapse_to_the_one_the_check_reported():
    request = request_for((SAFE_WALLET, 0, UNKNOWN_CALL))
    result = result_for(
        request,
        gaps=[{"path": "tx.calls[0]", "reason": "undecodable-data", "nested": "not-inspected"}],
        wrappers=["tx.calls[0]"],
        undecoded=[{"path": "tx.calls[0]", "selector": "0x12345678"}],
    )
    assert build(request, result)["coverage"]["gaps"] == [
        {"path": "tx.calls[0]", "reason": "undecodable-data", "nested": "not-inspected"}
    ]


def test_a_gap_on_a_part_the_request_does_not_have_is_refused():
    result = copy.deepcopy(UPGRADE_RESULT)
    result["gaps"] = [{"path": "tx.calls[9]", "reason": "not-inspected", "nested": "not-inspected"}]
    with pytest.raises(ValueError, match="path"):
        build(UPGRADE_REQUEST, result)


def test_gaps_come_out_in_the_order_of_the_request():
    request = request_for((TOKEN, 0, UNKNOWN_CALL), (SAFE_WALLET, 0, UNKNOWN_CALL), (TOKEN, 0, UNKNOWN_CALL))
    result = result_for(
        request,
        wrappers=["tx.calls[1]"],
        undecoded=[
            {"path": "tx.calls[2]", "selector": "0xa9059cbb"},
            {"path": "tx.calls[0]", "selector": "0xa9059cbb"},
        ],
    )
    assert [g["path"] for g in build(request, result)["coverage"]["gaps"]] == [
        "tx.calls[0]",
        "tx.calls[1]",
        "tx.calls[2]",
    ]


# ---------------------------------------------------------------- kinds the request asks for
def test_when_the_request_names_kinds_only_those_core_kinds_are_listed():
    request = {**UPGRADE_REQUEST, "kinds": ["erc.presign.address.code"]}
    report = build(request, UPGRADE_RESULT)
    core = [f["kind"] for f in report["findings"] if f["kind"].startswith("erc.presign.")]
    assert core == ["erc.presign.address.code"]
    assert r.conformance_errors(report, request) == []


def test_an_optional_kind_the_request_names_is_not_read_for_each_address():
    request = {**UPGRADE_REQUEST, "kinds": ["erc.presign.address.lookalike"]}
    report = build(request, UPGRADE_RESULT)
    (item,) = by_kind(report, "erc.presign.address.lookalike")
    assert item["status"] == "not-read" and item["reason"] == "unsupported" and "param" not in item


def test_a_kind_with_a_param_the_source_will_not_read_keeps_the_consumers_param():
    param = {"prefix": 4, "suffix": 4}
    request = {**UPGRADE_REQUEST, "kinds": [{"kind": "erc.presign.address.lookalike", "param": param}]}
    (item,) = by_kind(build(request, UPGRADE_RESULT), "erc.presign.address.lookalike")
    assert item["param"] == param and item["reason"] == "unsupported"


def test_an_on_list_kind_is_read_against_the_list_the_consumer_chose():
    document = {
        "version": "erc-presign-list/1",
        "source": "https://example.org/advisory",
        "criteria": "Test list.",
        "attestation": "Covers every entry.",
        "entries": [{"account": f"eip155:*:{ATTACKER}"}, {"account": f"eip155:*:{PROXY}"}],
    }
    uri = "https://example.org/lists/test.json"
    digest, _ = lists.list_hash(document)
    param = {"uri": uri, "hash": digest}
    request = {**UPGRADE_REQUEST, "kinds": [{"kind": "erc.presign.address.on-list", "param": param}]}
    report = build(request, UPGRADE_RESULT, lists={uri: document})
    (item,) = by_kind(report, "erc.presign.address.on-list")
    assert item["status"] == "read"
    assert item["value"] == {"account": f"eip155:*:{PROXY}"}
    assert item["param"] == param
    assert r.conformance_errors(report, request) == []
    unread = build(request, UPGRADE_RESULT, lists={})
    assert by_kind(unread, "erc.presign.address.on-list")[0]["reason"] == "unsupported"
    failed = build(request, UPGRADE_RESULT, lists={uri: None})
    assert by_kind(failed, "erc.presign.address.on-list")[0]["reason"] == "unavailable"


def test_an_on_list_list_with_the_wrong_hash_is_unavailable_not_a_negative():
    uri = "https://example.org/lists/test.json"
    document = {"version": "erc-presign-list/1", "source": "s", "criteria": "c", "attestation": "a", "entries": []}
    param = {"uri": uri, "hash": "0x" + "00" * 32}
    request = {**UPGRADE_REQUEST, "kinds": [{"kind": "erc.presign.address.on-list", "param": param}]}
    (item,) = by_kind(build(request, UPGRADE_RESULT, lists={uri: document}), "erc.presign.address.on-list")
    assert item["status"] == "not-read" and item["reason"] == "unavailable"


def test_a_kind_that_applies_to_nothing_this_report_lists_adds_nothing():
    request = {**UPGRADE_REQUEST, "kinds": ["erc.presign.token.observation", "erc.presign.text.injection", "x.unknown"]}
    report = build(request, UPGRADE_RESULT)
    assert not [f for f in report["findings"] if f["kind"].startswith(("erc.presign.token", "erc.presign.text"))]
    assert r.conformance_errors(report, request) == []


# ---------------------------------------------------------------- structure
def conforming_cases():
    owner = request_for((SAFE_WALLET, 0, ADD_OWNER))
    owner_result = result_for(
        owner,
        findings=[
            finding(
                "multisig-owner-change", "tx.calls[0]", SAFE_WALLET, "addOwnerWithThreshold(address,uint256)", ATTACKER
            )
        ],
    )
    value = request_for((HELPER, 5, b""))
    typed = {
        "version": "erc-presign/1",
        "subject": {"type": "calls", "chainId": CHAIN, "from": caip(OWNER), "calls": [], "typedData": {"x": 1}},
    }
    return [
        ("upgrade", UPGRADE_REQUEST, UPGRADE_RESULT),
        ("owner", owner, owner_result),
        ("value", value, result_for(value)),
        ("typed-data", typed, None),
    ]


@pytest.mark.parametrize(("label", "request_", "result"), conforming_cases(), ids=[c[0] for c in conforming_cases()])
def test_a_built_report_conforms(label, request_, result):
    assert r.conformance_errors(build(request_, result), request_) == []


def test_conformance_catches_a_missing_core_kind_a_bad_entity_and_a_duplicate():
    request = UPGRADE_REQUEST
    report = build(request, UPGRADE_RESULT)
    missing = copy.deepcopy(report)
    missing["findings"] = [f for f in missing["findings"] if f["kind"] != "erc.presign.address.code"]
    assert "core-kind-missing" in r.conformance_errors(missing, request)
    ghost = copy.deepcopy(report)
    ghost["findings"][0]["entity"] = {"address": caip(HELPER), "role": "target"}
    assert "finding-entity-unknown" in r.conformance_errors(ghost, request)
    twice = copy.deepcopy(report)
    twice["findings"].append(copy.deepcopy(twice["findings"][0]))
    assert "finding-duplicate" in r.conformance_errors(twice, request)
    shape = copy.deepcopy(report)
    shape["findings"][0]["status"] = "read"
    assert "finding-shape" in r.conformance_errors(shape, request)
    at = copy.deepcopy(report)
    at["findings"][0]["at"] = {"chainId": CHAIN, "block": "5"}
    assert "at-not-in-blocks" in r.conformance_errors(at, request)


def test_the_report_names_a_method_and_issue_time_and_optional_expiry():
    report = build(UPGRADE_REQUEST, UPGRADE_RESULT, expires_at="2026-11-28T09:05:00Z")
    assert report["method"] == r.METHOD
    assert report["issuedAt"] == NOW and report["expiresAt"] == "2026-11-28T09:05:00Z"
    assert "expiresAt" not in build(UPGRADE_REQUEST, UPGRADE_RESULT)
    assert report["source"] == {
        "registry": SOURCE.registry,
        "agentId": "20030",
        "signer": SOURCE_SIGNER.account,
    }


@pytest.mark.parametrize("bad", ["2026-11-28", "2026-11-28T09:00:00+01:00", "2026-11-28 09:00:00Z", "yesterday", 5])
def test_a_time_that_is_not_rfc_3339_utc_is_refused(bad):
    with pytest.raises(ValueError, match="issued_at"):
        build(UPGRADE_REQUEST, UPGRADE_RESULT, issued_at=bad)


def test_an_expiry_before_issue_is_refused():
    with pytest.raises(ValueError, match="expires_at"):
        build(UPGRADE_REQUEST, UPGRADE_RESULT, expires_at="2026-11-28T08:59:59Z")


def test_the_same_inputs_give_the_same_report_and_hash():
    one, two = (
        build(UPGRADE_REQUEST, UPGRADE_RESULT),
        build(copy.deepcopy(UPGRADE_REQUEST), copy.deepcopy(UPGRADE_RESULT)),
    )
    assert one == two and r.report_hash(one) == r.report_hash(two)


def test_the_source_registry_must_be_a_caip_10_account():
    with pytest.raises(ValueError, match="registry"):
        build(UPGRADE_REQUEST, UPGRADE_RESULT, source=r.Source(registry="0x" + "66" * 20, agent_id="1"))
    with pytest.raises(ValueError, match="agent"):
        build(UPGRADE_REQUEST, UPGRADE_RESULT, source=r.Source(registry=SOURCE.registry, agent_id="-1"))


# ---------------------------------------------------------------- signing
def test_the_signer_is_asked_once_for_the_eip_712_digest_of_this_report():
    signer = FixtureSigner("once")
    report = emit(UPGRADE_REQUEST, UPGRADE_RESULT, signer=signer)
    unsigned = {k: v for k, v in report.items() if k != "signature"}
    expected = r.eip712_digest(unsigned["subjectDigest"], unsigned["contextDigest"], r.report_hash(unsigned), 4663)
    assert signer.asked == [bytes.fromhex(expected[2:])]
    assert re.fullmatch(r"0x[0-9a-f]{130}", report["signature"])
    assert report["source"]["signer"] == signer.account


def test_the_signature_verifies_for_the_source_signer_and_no_other():
    report = emit(UPGRADE_REQUEST, UPGRADE_RESULT)
    assert r.recover_signer(report) == SOURCE_SIGNER.account
    assert r.verify_report(report, UPGRADE_REQUEST) == []
    forged = copy.deepcopy(report)
    forged["source"]["signer"] = FixtureSigner("someone else").account
    assert r.verify_report(forged, UPGRADE_REQUEST) == ["signer-mismatch"]


def test_rewriting_any_signed_part_breaks_the_signature():
    report = emit(UPGRADE_REQUEST, UPGRADE_RESULT)
    lie = copy.deepcopy(report)
    lie["coverage"] = {"status": "complete", "gaps": []}
    lie["findings"][0]["reason"] = "unavailable"
    assert r.verify_report(lie, UPGRADE_REQUEST) == ["signer-mismatch"]
    assert r.recover_signer(lie) != SOURCE_SIGNER.account


def test_detail_and_signature_are_the_only_unsigned_members():
    report = emit(UPGRADE_REQUEST, UPGRADE_RESULT)
    report["detail"] = "https://example.org/more"
    assert r.verify_report(report, UPGRADE_REQUEST) == []


def test_the_signature_is_checked_against_the_domain_of_the_registry_chain():
    report = emit(UPGRADE_REQUEST, UPGRADE_RESULT)
    moved = copy.deepcopy(report)
    moved["source"]["registry"] = f"eip155:8453:{lists.eip55(REGISTRY)}"
    assert r.verify_report(moved, UPGRADE_REQUEST) != []


@pytest.mark.parametrize("bad", [b"", b"\x00" * 64, b"\x00" * 66, "0x" + "00" * 65, None])
def test_a_signer_that_returns_anything_but_65_bytes_yields_no_report(bad):
    class Broken:
        account = SOURCE_SIGNER.account

        def sign_digest(self, digest: bytes):
            return bad

    with pytest.raises(ValueError, match="signature"):
        emit(UPGRADE_REQUEST, UPGRADE_RESULT, signer=Broken())


def test_a_signature_that_does_not_recover_to_the_signer_yields_no_report():
    other = FixtureSigner("other key")

    class Wrong:
        account = SOURCE_SIGNER.account

        def sign_digest(self, digest: bytes) -> bytes:
            return other.sign_digest(digest)

    with pytest.raises(ValueError, match="signature"):
        emit(UPGRADE_REQUEST, UPGRADE_RESULT, signer=Wrong())


def test_a_signer_that_raises_yields_no_report_and_no_unsigned_one():
    class Raises:
        account = SOURCE_SIGNER.account

        def sign_digest(self, digest: bytes) -> bytes:
            raise RuntimeError("key offline")

    with pytest.raises(RuntimeError):
        emit(UPGRADE_REQUEST, UPGRADE_RESULT, signer=Raises())


def test_a_report_that_does_not_conform_is_never_signed():
    signer = FixtureSigner("never")
    bad = copy.deepcopy(UPGRADE_RESULT)
    bad["gaps"] = [{"path": "tx.calls[9]", "reason": "not-inspected", "nested": "not-inspected"}]
    with pytest.raises(ValueError):
        emit(UPGRADE_REQUEST, bad, signer=signer)
    assert signer.asked == []


def test_the_signer_account_must_be_a_caip_10_account():
    class Plain:
        account = "0x" + "11" * 20

        def sign_digest(self, digest: bytes) -> bytes:
            return b"\x00" * 65

    with pytest.raises(ValueError, match="account"):
        emit(UPGRADE_REQUEST, UPGRADE_RESULT, signer=Plain())


# ---------------------------------------------------------------- what the module is not
MODULES = [ROOT / "bot" / "claw_guard" / "erc8481.py", ROOT / "bot" / "claw_guard" / "erc8481_lists.py"]


def code_of(path: Path) -> str:
    """Source with comments and docstrings dropped, so a scan reads code and not prose."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                body[0].value = ast.Constant("")
    return ast.unparse(tree)


def test_the_module_holds_no_private_key_and_cannot_sign_on_its_own():
    for path in MODULES:
        code = code_of(path)
        for needle in ("PrivateKey", "sign_recoverable", "mnemonic", "os.environ", "getenv", "open(", "seed"):
            assert needle not in code, (path.name, needle)


def test_the_test_signer_exists_only_in_tests():
    for path in (ROOT / "bot").rglob("*.py"):
        assert "FixtureSigner" not in path.read_text(encoding="utf-8"), path


def test_the_wording_never_says_safe_or_scam():
    for path in MODULES:
        assert not re.search(r"\b(safe|scam)\b", code_of(path).lower()), path.name
    request = request_for((SAFE_WALLET, 0, ENABLE_MODULE))
    result = result_for(
        request,
        findings=[finding("multisig-power-change", "tx.calls[0]", SAFE_WALLET, "enableModule(address)", ATTACKER)],
    )
    assert not re.search(r"\b(safe|scam)\b", json.dumps(build(request, result)).lower())


def test_the_emitter_is_not_wired_into_the_order_path_the_signer_or_the_service():
    for name in ("evaluate.py", "signer.py", "service.py", "door.py", "payload.py"):
        text = (ROOT / "bot" / "claw_guard" / name).read_text(encoding="utf-8")
        assert "erc8481" not in text, name


# ---------------------------------------------------------------- with the real admin-call check
def real_result(request: dict) -> dict:
    admin_calls = pytest.importorskip("bot.claw_guard.admin_calls")
    body = {"type": "evmCalls", **{k: v for k, v in request["subject"].items() if k != "type"}}
    return admin_calls.evaluate_admin_calls(body, None, now=0.0)


REAL = [
    ("upgrade", request_for((PROXY, 0, UPGRADE_TO))),
    ("owner-added", request_for((SAFE_WALLET, 0, ADD_OWNER))),
    ("module-then-unknown", request_for((SAFE_WALLET, 0, ENABLE_MODULE), (TOKEN, 0, UNKNOWN_CALL))),
    ("plain-value", request_for((HELPER, 9, b""))),
]


@pytest.mark.parametrize(("label", "request_"), REAL, ids=[x[0] for x in REAL])
def test_a_report_from_the_real_check_conforms_and_verifies(label, request_):
    report = emit(request_, real_result(request_))
    assert r.conformance_errors(report, request_) == []
    assert r.verify_report(report, request_) == []


def test_a_real_nested_admin_call_is_found_through_a_multicall_and_the_wrapper_is_a_gap():
    pytest.importorskip("bot.claw_guard.admin_calls")
    inner = [UPGRADE_TO]
    head = bytes.fromhex("ac9650d8")  # multicall(bytes[])
    blob = (
        word(0x20)
        + word(len(inner))
        + word(0x20)
        + word(len(UPGRADE_TO))
        + UPGRADE_TO
        + b"\x00" * (-len(UPGRADE_TO) % 32)
    )
    request_ = request_for((PROXY, 0, head + blob))
    report = emit(request_, real_result(request_))
    assert [g["reason"] for g in report["coverage"]["gaps"]] == ["unverified-wrapper"]
    (item,) = by_kind(report, r.KIND_ADMIN_CALL)
    assert item["param"]["path"] == "tx.calls[0].inner[0]"
    assert r.conformance_errors(report, request_) == []
