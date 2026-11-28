"""Claw Guard: upgrade and admin-key calls are top risk.

Written before the module. The Atoshi drain (27 Nov 2026) was an EIP-1967
proxy upgrade signed with a phished deployer key: one ``upgradeTo`` call was
all it took. These tests pin what the check must see, wherever the call sits
(direct, in a multicall or a batch, behind a multisig, behind an EIP-7702
delegation) and what the user is told.

The calldata here is built by a small encoder written for this file. It does
not import the decoder under test, so a mistake in one does not hide a
mistake in the other. Selectors are pinned from published ABIs, not from the
module's own table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bot.claw_guard.admin_calls import (
    MAX_CALLS,
    MAX_DEPTH,
    MULTISEND_TARGETS,
    SIGNATURES,
    evaluate_admin_calls,
    scan,
)
from bot.claw_guard.evaluate import evaluate
from bot.claw_guard.verdicts import ALLOW_TEXT, BLOCK_PREFIX, CONFIRM_PREFIX
from bot.hrc.keccak import keccak256
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_700_000_000.0

PROXY = "0x" + "11" * 20
ATTACKER = "0x" + "ba" * 20
OWNER = "0x" + "0a" * 20
TOKEN = "0x" + "22" * 20
SAFE_WALLET = "0x" + "33" * 20
HELPER = "0x" + "44" * 20
ZERO = "0x" + "00" * 20


# ---------------------------------------------------------------- a tiny ABI encoder (tests only)
def sel(signature: str) -> bytes:
    return keccak256(signature.encode())[:4]


def u(n: int) -> bytes:
    return n.to_bytes(32, "big")


def a(address: str) -> bytes:
    return bytes.fromhex(address[2:]).rjust(32, b"\x00")


def pad(b: bytes) -> bytes:
    return b + b"\x00" * (-len(b) % 32)


def enc(head_and_tail: list) -> bytes:
    """ABI-encode a tuple whose items are bytes (static word) or ('dyn', bytes) tails."""
    head_len = 32 * len(head_and_tail)
    heads, tails = b"", b""
    for item in head_and_tail:
        if isinstance(item, tuple):
            heads += u(head_len + len(tails))
            tails += item[1]
        else:
            heads += item
    return heads + tails


def dyn_bytes(b: bytes) -> tuple:
    return ("dyn", u(len(b)) + pad(b))


def bytes_array(items: list[bytes]) -> tuple:
    """bytes[]"""
    return ("dyn", enc_array_of_dyn([u(len(x)) + pad(x) for x in items]))


def enc_array_of_dyn(encoded: list[bytes]) -> bytes:
    n = len(encoded)
    offs, pos = b"", 32 * n
    for e in encoded:
        offs += u(pos)
        pos += len(e)
    return u(n) + offs + b"".join(encoded)


def addr_array(items: list[str]) -> tuple:
    return ("dyn", u(len(items)) + b"".join(a(x) for x in items))


def uint_array(items: list[int]) -> tuple:
    return ("dyn", u(len(items)) + b"".join(u(x) for x in items))


def tuple_array(encoded_tuples: list[bytes]) -> tuple:
    return ("dyn", enc_array_of_dyn(encoded_tuples))


def call_tuple(to: str, value: int, data: bytes) -> bytes:
    """(address,uint256,bytes)"""
    return enc([a(to), u(value), dyn_bytes(data)])


def hexed(b: bytes) -> str:
    return "0x" + b.hex()


# ---------------------------------------------------------------- calldata builders
def upgrade_to(impl: str) -> bytes:
    return sel("upgradeTo(address)") + a(impl)


def upgrade_to_and_call(impl: str, init: bytes = b"") -> bytes:
    return sel("upgradeToAndCall(address,bytes)") + enc([a(impl), dyn_bytes(init)])


def change_admin(new_admin: str) -> bytes:
    return sel("changeAdmin(address)") + a(new_admin)


def transfer_ownership(new_owner: str) -> bytes:
    return sel("transferOwnership(address)") + a(new_owner)


def renounce_ownership() -> bytes:
    return sel("renounceOwnership()")


def erc20_transfer(to: str, amount: int) -> bytes:
    return sel("transfer(address,uint256)") + a(to) + u(amount)


def erc20_approve(spender: str, amount: int) -> bytes:
    return sel("approve(address,uint256)") + a(spender) + u(amount)


def multicall_bytes(inner: list[bytes]) -> bytes:
    return sel("multicall(bytes[])") + enc([bytes_array(inner)])


def multicall_deadline(inner: list[bytes]) -> bytes:
    return sel("multicall(uint256,bytes[])") + enc([u(1_900_000_000), bytes_array(inner)])


def multicall_prev(inner: list[bytes]) -> bytes:
    return sel("multicall(bytes32,bytes[])") + enc([b"\x00" * 32, bytes_array(inner)])


def aggregate(calls: list[tuple[str, bytes]]) -> bytes:
    tuples = [enc([a(t), dyn_bytes(d)]) for t, d in calls]
    return sel("aggregate((address,bytes)[])") + enc([tuple_array(tuples)])


def try_aggregate(calls: list[tuple[str, bytes]]) -> bytes:
    tuples = [enc([a(t), dyn_bytes(d)]) for t, d in calls]
    return sel("tryAggregate(bool,(address,bytes)[])") + enc([u(1), tuple_array(tuples)])


def aggregate3(calls: list[tuple[str, bytes]]) -> bytes:
    tuples = [enc([a(t), u(1), dyn_bytes(d)]) for t, d in calls]
    return sel("aggregate3((address,bool,bytes)[])") + enc([tuple_array(tuples)])


def aggregate3_value(calls: list[tuple[str, bytes]]) -> bytes:
    tuples = [enc([a(t), u(1), u(0), dyn_bytes(d)]) for t, d in calls]
    return sel("aggregate3Value((address,bool,uint256,bytes)[])") + enc([tuple_array(tuples)])


def execute_batch_7821(calls: list[tuple[str, int, bytes]], mode: bytes | None = None) -> bytes:
    mode = mode if mode is not None else b"\x01" + b"\x00" * 31
    tuples = [call_tuple(t, v, d) for t, v, d in calls]
    body = enc([tuple_array(tuples)])
    return sel("execute(bytes32,bytes)") + enc([mode, dyn_bytes(body)])


def execute_single_7579(to: str, value: int, data: bytes, call_type: int = 0x00) -> bytes:
    mode = bytes([call_type]) + b"\x00" * 31
    packed = bytes.fromhex(to[2:]) + u(value) + data
    return sel("execute(bytes32,bytes)") + enc([mode, dyn_bytes(packed)])


def execute_batch_tuples(calls: list[tuple[str, int, bytes]]) -> bytes:
    return sel("executeBatch((address,uint256,bytes)[])") + enc([tuple_array([call_tuple(*c) for c in calls])])


def account_execute(to: str, value: int, data: bytes) -> bytes:
    return sel("execute(address,uint256,bytes)") + enc([a(to), u(value), dyn_bytes(data)])


def account_execute_batch(targets: list[str], datas: list[bytes]) -> bytes:
    return sel("executeBatch(address[],bytes[])") + enc([addr_array(targets), bytes_array(datas)])


def account_execute_batch_values(targets: list[str], values: list[int], datas: list[bytes]) -> bytes:
    return sel("executeBatch(address[],uint256[],bytes[])") + enc(
        [addr_array(targets), uint_array(values), bytes_array(datas)]
    )


def exec_transaction(to: str, data: bytes, operation: int = 0, value: int = 0) -> bytes:
    sig = "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)"
    return sel(sig) + enc(
        [
            a(to),
            u(value),
            dyn_bytes(data),
            u(operation),
            u(0),
            u(0),
            u(0),
            a(ZERO),
            a(ZERO),
            dyn_bytes(b"\x01" * 65),
        ]
    )


def multisend_packed(txs: list[tuple[int, str, int, bytes]]) -> bytes:
    out = b""
    for op, to, value, data in txs:
        out += bytes([op]) + bytes.fromhex(to[2:]) + u(value) + u(len(data)) + data
    return out


def multi_send(txs: list[tuple[int, str, int, bytes]]) -> bytes:
    return sel("multiSend(bytes)") + enc([dyn_bytes(multisend_packed(txs))])


def call(to: str, data: bytes = b"", value: int = 0) -> dict:
    return {"to": to, "value": str(value), "data": hexed(data)}


def request(*calls: dict, authorization_list: list | None = None) -> dict:
    body: dict = {
        "type": "evmCalls",
        "chainId": "eip155:1",
        "from": "eip155:1:" + OWNER,
        "calls": list(calls),
    }
    if authorization_list is not None:
        body["authorizationList"] = authorization_list
    return body


def auth_tuple(address: str, nonce: int = 0, chain_id: int = 1) -> dict:
    return {
        "chainId": str(chain_id),
        "address": address,
        "nonce": str(nonce),
        "yParity": 0,
        "r": "0x" + "01" * 32,
        "s": "0x" + "02" * 32,
    }


def kinds(result) -> list[str]:
    return [f.kind for f in result.findings]


# ---------------------------------------------------------------- selectors
PINNED = {
    "upgradeTo(address)": "0x3659cfe6",
    "upgradeToAndCall(address,bytes)": "0x4f1ef286",
    "changeAdmin(address)": "0x8f283970",
    "transferOwnership(address)": "0xf2fde38b",
    "renounceOwnership()": "0x715018a6",
    "multicall(bytes[])": "0xac9650d8",
    "aggregate3((address,bool,bytes)[])": "0x82ad56cb",
    "execute(bytes32,bytes)": "0xe9ae5c53",
    "addOwnerWithThreshold(address,uint256)": "0x0d582f13",
    "removeOwner(address,address,uint256)": "0xf8dc5dd9",
    "swapOwner(address,address,address)": "0xe318b52b",
    "changeThreshold(uint256)": "0x694e80c3",
    "enableModule(address)": "0x610b5925",
    "setGuard(address)": "0xe19a9dd9",
    "multiSend(bytes)": "0x8d80ff0a",
    "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)": "0x6a761202",
}


def test_the_selector_table_is_pinned_to_published_selectors():
    for signature, expected in PINNED.items():
        assert signature in SIGNATURES, signature
        assert "0x" + sel(signature).hex() == expected, signature
    selectors = [sel(s) for s in SIGNATURES]
    assert len(set(selectors)) == len(selectors), "two signatures share a selector"


# ---------------------------------------------------------------- direct calls
DIRECT = [
    ("upgradeTo", upgrade_to(ATTACKER), "proxy-upgrade", ATTACKER),
    ("upgradeToAndCall", upgrade_to_and_call(ATTACKER, b"\xde\xad\xbe\xef"), "proxy-upgrade", ATTACKER),
    ("upgrade by a proxy admin", sel("upgrade(address,address)") + a(PROXY) + a(ATTACKER), "proxy-upgrade", ATTACKER),
    (
        "upgradeAndCall by a proxy admin",
        sel("upgradeAndCall(address,address,bytes)") + enc([a(PROXY), a(ATTACKER), dyn_bytes(b"")]),
        "proxy-upgrade",
        ATTACKER,
    ),
    (
        "diamondCut",
        sel("diamondCut((address,uint8,bytes4[])[],address,bytes)")
        + enc([tuple_array([]), a(ATTACKER), dyn_bytes(b"")]),
        "proxy-upgrade",
        None,
    ),
    ("setImplementation", sel("setImplementation(address)") + a(ATTACKER), "set-implementation", ATTACKER),
    ("upgrade(address)", sel("upgrade(address)") + a(ATTACKER), "set-implementation", ATTACKER),
    ("changeAdmin", change_admin(ATTACKER), "admin-change", ATTACKER),
    ("changeProxyAdmin", sel("changeProxyAdmin(address,address)") + a(PROXY) + a(ATTACKER), "admin-change", ATTACKER),
    ("setAdmin", sel("setAdmin(address)") + a(ATTACKER), "admin-change", ATTACKER),
    ("transferOwnership", transfer_ownership(ATTACKER), "ownership-transfer", ATTACKER),
    ("setOwner", sel("setOwner(address)") + a(ATTACKER), "ownership-transfer", ATTACKER),
    ("acceptOwnership", sel("acceptOwnership()"), "ownership-transfer", None),
    ("renounceOwnership", renounce_ownership(), "ownership-renounce", None),
    (
        "grantRole of the default admin role",
        sel("grantRole(bytes32,address)") + b"\x00" * 32 + a(ATTACKER),
        "admin-role-grant",
        ATTACKER,
    ),
    (
        "multisig addOwnerWithThreshold",
        sel("addOwnerWithThreshold(address,uint256)") + a(ATTACKER) + u(1),
        "multisig-owner-change",
        ATTACKER,
    ),
    (
        "multisig removeOwner",
        sel("removeOwner(address,address,uint256)") + a(HELPER) + a(OWNER) + u(1),
        "multisig-owner-change",
        OWNER,
    ),
    (
        "multisig swapOwner",
        sel("swapOwner(address,address,address)") + a(HELPER) + a(OWNER) + a(ATTACKER),
        "multisig-owner-change",
        ATTACKER,
    ),
    ("multisig changeThreshold", sel("changeThreshold(uint256)") + u(1), "multisig-threshold-change", None),
    ("multisig enableModule", sel("enableModule(address)") + a(ATTACKER), "multisig-power-change", ATTACKER),
    (
        "multisig disableModule",
        sel("disableModule(address,address)") + a(HELPER) + a(ATTACKER),
        "multisig-power-change",
        ATTACKER,
    ),
    ("multisig setGuard", sel("setGuard(address)") + a(ATTACKER), "multisig-power-change", ATTACKER),
    (
        "multisig setFallbackHandler",
        sel("setFallbackHandler(address)") + a(ATTACKER),
        "multisig-power-change",
        ATTACKER,
    ),
]


@pytest.mark.parametrize(("label", "data", "kind", "new_value"), DIRECT, ids=[d[0] for d in DIRECT])
def test_a_direct_admin_call_is_found_with_its_kind(label, data, kind, new_value):
    found = scan(request(call(PROXY, data)))
    assert kinds(found) == [kind], label
    assert found.gaps == ()
    finding = found.findings[0]
    assert finding.target == PROXY
    assert finding.path == "tx.calls[0]"
    assert finding.new_value == new_value


def test_a_beacon_upgrade_has_the_selector_of_a_proxy_upgrade_and_reads_the_same():
    """UpgradeableBeacon.upgradeTo(address) shares upgradeTo's selector, so the check cannot tell them apart."""
    found = scan(request(call(PROXY, upgrade_to(ATTACKER))))
    assert kinds(found) == ["proxy-upgrade"]


def test_ordinary_calls_are_not_admin_calls():
    ordinary = [
        call(TOKEN, erc20_transfer(ATTACKER, 10**18)),
        call(TOKEN, erc20_approve(ATTACKER, 2**256 - 1)),
        call(ATTACKER, b"", value=10**18),
        call(TOKEN, b""),
        call(TOKEN, sel("grantRole(bytes32,address)") + u(7) + a(ATTACKER)),  # a role other than the default admin
        call(TOKEN, sel("balanceOf(address)") + a(OWNER)),
    ]
    found = scan(request(*ordinary))
    assert found.findings == ()
    assert found.gaps == ()


def test_the_selector_alone_is_enough_but_a_short_call_is_not_read_as_a_match():
    """upgradeTo with no argument is unreadable, not a match and not nothing."""
    found = scan(request(call(PROXY, sel("upgradeTo(address)"))))
    assert found.findings == ()
    assert [g.reason for g in found.gaps] == ["undecodable-data"]
    assert found.gaps[0].path == "tx.calls[0]"


def test_an_address_argument_with_dirty_high_bits_is_unreadable():
    data = sel("upgradeTo(address)") + b"\xff" * 12 + bytes.fromhex(ATTACKER[2:])
    found = scan(request(call(PROXY, data)))
    assert found.findings == ()
    assert [g.reason for g in found.gaps] == ["undecodable-data"]


# ---------------------------------------------------------------- nested in batches
INNER = upgrade_to(ATTACKER)

BATCHES = [
    ("multicall(bytes[])", multicall_bytes([erc20_transfer(OWNER, 1), INNER]), PROXY, "tx.calls[0].inner[1]"),
    ("multicall(uint256,bytes[])", multicall_deadline([INNER]), PROXY, "tx.calls[0].inner[0]"),
    ("multicall(bytes32,bytes[])", multicall_prev([INNER]), PROXY, "tx.calls[0].inner[0]"),
    (
        "Multicall3 aggregate",
        aggregate([(TOKEN, erc20_transfer(OWNER, 1)), (HELPER, INNER)]),
        HELPER,
        "tx.calls[0].inner[1]",
    ),
    ("Multicall3 tryAggregate", try_aggregate([(HELPER, INNER)]), HELPER, "tx.calls[0].inner[0]"),
    ("Multicall3 aggregate3", aggregate3([(HELPER, INNER)]), HELPER, "tx.calls[0].inner[0]"),
    ("Multicall3 aggregate3Value", aggregate3_value([(TOKEN, b""), (HELPER, INNER)]), HELPER, "tx.calls[0].inner[1]"),
    (
        "ERC-7821 batch execute",
        execute_batch_7821([(TOKEN, 0, erc20_transfer(OWNER, 1)), (HELPER, 0, INNER)]),
        HELPER,
        "tx.calls[0].inner[1]",
    ),
    ("ERC-7579 single execute", execute_single_7579(HELPER, 0, INNER), HELPER, "tx.calls[0].inner[0]"),
    ("executeBatch(Call[])", execute_batch_tuples([(HELPER, 0, INNER)]), HELPER, "tx.calls[0].inner[0]"),
    ("account execute", account_execute(HELPER, 0, INNER), HELPER, "tx.calls[0].inner[0]"),
    (
        "account executeBatch(address[],bytes[])",
        account_execute_batch([TOKEN, HELPER], [b"", INNER]),
        HELPER,
        "tx.calls[0].inner[1]",
    ),
    (
        "account executeBatch(address[],uint256[],bytes[])",
        account_execute_batch_values([HELPER], [0], [INNER]),
        HELPER,
        "tx.calls[0].inner[0]",
    ),
    ("multisig execTransaction", exec_transaction(HELPER, INNER), HELPER, "tx.calls[0].inner[0]"),
    (
        "multisig multiSend through a delegatecall to the known MultiSend",
        exec_transaction(MULTISEND_TARGETS[0], multi_send([(0, TOKEN, 0, b""), (0, HELPER, 0, INNER)]), operation=1),
        HELPER,
        "tx.calls[0].inner[0].inner[1]",
    ),
]


@pytest.mark.parametrize(("label", "data", "target", "path"), BATCHES, ids=[b[0] for b in BATCHES])
def test_an_upgrade_inside_a_batch_is_found_where_it_sits(label, data, target, path):
    outer_to = PROXY if label.startswith("multicall(") else SAFE_WALLET
    found = scan(request(call(outer_to, data)))
    assert kinds(found) == ["proxy-upgrade"], label
    assert found.gaps == (), label
    finding = found.findings[0]
    assert finding.target == target
    assert finding.path == path
    assert finding.new_value == ATTACKER


def test_a_multisig_owner_change_inside_multisend_is_found():
    inner = [
        (0, TOKEN, 0, erc20_transfer(OWNER, 1)),
        (0, SAFE_WALLET, 0, sel("addOwnerWithThreshold(address,uint256)") + a(ATTACKER) + u(1)),
        (0, SAFE_WALLET, 0, sel("changeThreshold(uint256)") + u(1)),
    ]
    data = exec_transaction(MULTISEND_TARGETS[1], multi_send(inner), operation=1)
    found = scan(request(call(SAFE_WALLET, data)))
    assert kinds(found) == ["multisig-owner-change", "multisig-threshold-change"]
    assert [f.path for f in found.findings] == [
        "tx.calls[0].inner[0].inner[1]",
        "tx.calls[0].inner[0].inner[2]",
    ]


def test_a_multisig_call_to_itself_for_an_owner_change_is_found():
    data = exec_transaction(SAFE_WALLET, sel("addOwnerWithThreshold(address,uint256)") + a(ATTACKER) + u(1))
    found = scan(request(call(SAFE_WALLET, data)))
    assert kinds(found) == ["multisig-owner-change"]


def test_batches_nest_two_deep_and_report_the_path():
    inner = multicall_bytes([erc20_transfer(OWNER, 1), upgrade_to_and_call(ATTACKER)])
    outer = aggregate3([(TOKEN, b""), (PROXY, inner)])
    found = scan(request(call(HELPER, b""), call(HELPER, outer)))
    assert kinds(found) == ["proxy-upgrade"]
    assert found.findings[0].path == "tx.calls[1].inner[1].inner[1]"
    assert found.findings[0].target == PROXY


def test_the_second_top_level_call_is_found_and_numbered():
    found = scan(request(call(TOKEN, erc20_transfer(OWNER, 1)), call(PROXY, change_admin(ATTACKER))))
    assert [(f.kind, f.path) for f in found.findings] == [("admin-change", "tx.calls[1]")]


def test_a_batch_too_deep_to_read_is_a_gap_not_a_pass():
    data = upgrade_to(ATTACKER)
    for _ in range(MAX_DEPTH + 1):
        data = multicall_bytes([data])
    found = scan(request(call(PROXY, data)))
    assert found.findings == ()
    assert [g.reason for g in found.gaps] == ["depth-limit"]
    assert found.gaps[0].nested == "not-inspected"
    assert found.gaps[0].path.count(".inner[0]") == MAX_DEPTH


def test_a_batch_just_inside_the_depth_limit_is_read_to_the_bottom():
    data = upgrade_to(ATTACKER)
    for _ in range(MAX_DEPTH):
        data = multicall_bytes([data])
    found = scan(request(call(PROXY, data)))
    assert kinds(found) == ["proxy-upgrade"]
    assert found.gaps == ()


def test_too_many_calls_is_a_gap_and_what_was_read_is_kept():
    many = [erc20_transfer(OWNER, 1)] * (MAX_CALLS + 5) + [upgrade_to(ATTACKER)]
    found = scan(request(call(PROXY, multicall_bytes(many))))
    assert [g.reason for g in found.gaps] == ["not-inspected"]
    assert found.gaps[0].nested == "not-inspected"


def test_a_wrapper_that_does_not_decode_is_a_gap():
    data = sel("multicall(bytes[])") + u(0x20) + u(2) + u(0xFFFFFF) + u(0xFFFFFF)  # offsets far outside the data
    found = scan(request(call(PROXY, data)))
    assert found.findings == ()
    assert [g.reason for g in found.gaps] == ["undecodable-data"]
    assert found.gaps[0].path == "tx.calls[0]"


def test_one_unreadable_inner_call_does_not_hide_the_one_next_to_it():
    good = upgrade_to(ATTACKER)
    short = sel("upgradeTo(address)")  # no argument
    found = scan(request(call(PROXY, multicall_bytes([short, good]))))
    assert kinds(found) == ["proxy-upgrade"]
    assert [g.path for g in found.gaps] == ["tx.calls[0].inner[0]"]


# ---------------------------------------------------------------- delegatecall
def test_a_multisig_delegatecall_to_an_unknown_contract_is_a_finding():
    data = exec_transaction(HELPER, b"\x12\x34\x56\x78", operation=1)
    found = scan(request(call(SAFE_WALLET, data)))
    assert kinds(found) == ["delegatecall"]
    assert found.findings[0].target == HELPER


def test_a_delegatecall_to_a_look_alike_multisend_is_a_finding_and_is_not_read_through():
    """A contract that is not a known MultiSend may do anything with the bytes it is given."""
    data = exec_transaction(HELPER, multi_send([(0, PROXY, 0, upgrade_to(ATTACKER))]), operation=1)
    found = scan(request(call(SAFE_WALLET, data)))
    assert kinds(found) == ["delegatecall"]


def test_a_delegatecall_to_a_known_multisend_is_read_through_and_is_not_itself_a_finding():
    data = exec_transaction(MULTISEND_TARGETS[2], multi_send([(0, TOKEN, 0, erc20_transfer(OWNER, 1))]), operation=1)
    found = scan(request(call(SAFE_WALLET, data)))
    assert found.findings == ()
    assert found.gaps == ()


def test_a_delegatecall_inside_multisend_is_a_finding():
    data = exec_transaction(MULTISEND_TARGETS[0], multi_send([(1, HELPER, 0, b"\x01\x02\x03\x04")]), operation=1)
    found = scan(request(call(SAFE_WALLET, data)))
    assert kinds(found) == ["delegatecall"]
    assert found.findings[0].path == "tx.calls[0].inner[0].inner[0]"


def test_an_erc7579_delegatecall_mode_is_a_finding():
    found = scan(request(call(SAFE_WALLET, execute_single_7579(HELPER, 0, b"\x01\x02\x03\x04", call_type=0xFF))))
    assert kinds(found) == ["delegatecall"]
    assert found.findings[0].target == HELPER


def test_an_operation_other_than_call_or_delegatecall_is_unreadable_not_a_call():
    bad_multisend = sel("multiSend(bytes)") + enc([dyn_bytes(multisend_packed([(2, HELPER, 0, upgrade_to(ATTACKER))]))])
    found = scan(request(call(MULTISEND_TARGETS[1], bad_multisend)))
    assert found.findings == ()
    assert [g.reason for g in found.gaps] == ["undecodable-data"]
    found = scan(request(call(SAFE_WALLET, exec_transaction(HELPER, upgrade_to(ATTACKER), operation=2))))
    assert found.findings == ()
    assert [g.reason for g in found.gaps] == ["undecodable-data"]


def test_multisend_targets_are_the_published_deployments():
    assert {t.lower() for t in MULTISEND_TARGETS} == {
        "0xa238cbeb142c10ef7ad8442c6d1f9e89e07e7761",  # MultiSend 1.3.0
        "0x40a2accbd92bca938b02010e17a5b8929b49130d",  # MultiSendCallOnly 1.3.0
        "0x38869bf66a61cf6bdb996a6ae40d5853fd43b526",  # MultiSend 1.4.1
        "0x9641d764fc13c8b624c04430c7356c1c7c8102e2",  # MultiSendCallOnly 1.4.1
    }


# ---------------------------------------------------------------- EIP-7702
def test_an_eip7702_delegation_is_a_finding_with_the_delegate_as_its_target():
    found = scan(request(call(TOKEN, erc20_transfer(OWNER, 1)), authorization_list=[auth_tuple(ATTACKER)]))
    assert kinds(found) == ["eip7702-delegation"]
    finding = found.findings[0]
    assert finding.target == ATTACKER
    assert finding.path == "tx.authorizationList[0]"


def test_clearing_a_delegation_with_the_zero_address_is_not_a_finding():
    found = scan(request(call(TOKEN, b""), authorization_list=[auth_tuple(ZERO)]))
    assert found.findings == ()


def test_every_tuple_of_an_authorization_list_is_read_in_order():
    found = scan(
        request(
            call(TOKEN, b""),
            authorization_list=[
                auth_tuple(HELPER, nonce=1),
                auth_tuple(ZERO, nonce=2),
                auth_tuple(ATTACKER, nonce=3, chain_id=0),
            ],
        )
    )
    assert [(f.path, f.target) for f in found.findings] == [
        ("tx.authorizationList[0]", HELPER),
        ("tx.authorizationList[2]", ATTACKER),
    ]


def test_an_upgrade_through_a_delegated_account_batch_is_found_beside_its_delegation():
    batch = execute_batch_7821([(PROXY, 0, upgrade_to(ATTACKER))])
    found = scan(request(call(OWNER, batch), authorization_list=[auth_tuple(HELPER)]))
    assert sorted(kinds(found)) == ["eip7702-delegation", "proxy-upgrade"]


def test_a_malformed_authorization_tuple_is_a_gap_not_a_skip():
    bad = auth_tuple(ATTACKER)
    bad["address"] = "0x1234"
    found = scan(request(call(TOKEN, b""), authorization_list=[bad]))
    assert found.findings == ()
    assert [(g.path, g.reason) for g in found.gaps] == [("tx.authorizationList[0]", "undecodable-data")]


# ---------------------------------------------------------------- the verdict
def rules_with(**admin_calls) -> dict:
    return {"admin_calls": admin_calls}


def test_an_upgrade_is_blocked_by_default_in_the_users_own_words():
    result = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), None, now=NOW)
    assert result["decision"] == "BLOCK"
    assert result["text"].startswith(BLOCK_PREFIX)
    assert [r["id"] for r in result["rules"]] == ["UPG-1"]
    assert result["rules"][0]["level"] == "block"
    assert result["out_of_scope"] is None


def test_the_answer_has_the_shape_of_every_other_claw_guard_answer():
    result = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), {}, now=NOW)
    reference = evaluate({"type": "evmCalls"}, {}, now=NOW)
    assert set(reference) <= set(result)
    assert result["ts"] == NOW
    assert re.fullmatch(r"[0-9a-f]{64}", result["order_hash"])


def test_a_request_with_no_admin_call_is_allowed_in_the_one_allow_sentence():
    result = evaluate_admin_calls(request(call(TOKEN, erc20_transfer(OWNER, 1))), None, now=NOW)
    assert result["decision"] == "ALLOW"
    assert result["text"] == ALLOW_TEXT
    assert result["rules"] == []


def test_the_users_own_allow_rule_lets_that_call_through():
    allow = {"target": PROXY, "kinds": ["proxy-upgrade"]}
    result = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), rules_with(allow=[allow]), now=NOW)
    assert result["decision"] == "ALLOW"
    assert result["text"] == ALLOW_TEXT


@pytest.mark.parametrize(
    "allow",
    [
        {"target": HELPER, "kinds": ["proxy-upgrade"]},  # another contract
        {"target": PROXY, "kinds": ["admin-change"]},  # another kind of call
        {"target": PROXY, "kinds": ["proxy-upgrade"], "new_value": OWNER},  # another implementation
        {"kinds": ["proxy-upgrade"]},  # no target at all
        {"target": "0xnot-an-address", "kinds": ["proxy-upgrade"]},
        {},
    ],
    ids=["other-target", "other-kind", "other-new-value", "no-target", "bad-target", "empty"],
)
def test_an_allow_rule_that_does_not_match_the_call_does_not_allow_it(allow):
    result = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), rules_with(allow=[allow]), now=NOW)
    assert result["decision"] == "BLOCK"


def test_an_allow_rule_can_pin_the_new_value():
    allow = {"target": PROXY, "kinds": ["proxy-upgrade"], "new_value": ATTACKER.upper().replace("0X", "0x")}
    ok = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), rules_with(allow=[allow]), now=NOW)
    assert ok["decision"] == "ALLOW"
    other = evaluate_admin_calls(request(call(PROXY, upgrade_to(HELPER))), rules_with(allow=[allow]), now=NOW)
    assert other["decision"] == "BLOCK"


def test_an_allow_rule_for_one_call_does_not_allow_a_second_admin_call_in_the_same_request():
    allow = {"target": PROXY, "kinds": ["proxy-upgrade"]}
    both = request(call(PROXY, upgrade_to(ATTACKER)), call(PROXY, change_admin(ATTACKER)))
    result = evaluate_admin_calls(both, rules_with(allow=[allow]), now=NOW)
    assert result["decision"] == "BLOCK"
    assert [r["id"] for r in result["rules"]] == ["ADM-1"]


def test_a_confirm_rule_asks_instead_of_blocking():
    confirm = {"target": PROXY, "kinds": ["proxy-upgrade"]}
    result = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), rules_with(confirm=[confirm]), now=NOW)
    assert result["decision"] == "CONFIRM"
    assert result["text"].startswith(CONFIRM_PREFIX)
    assert result["rules"][0]["level"] == "confirm"


def test_the_default_can_be_set_to_ask_for_every_admin_call():
    result = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), rules_with(default="confirm"), now=NOW)
    assert result["decision"] == "CONFIRM"


@pytest.mark.parametrize("default", ["allow", "off", "", None, 0, ["block"]])
def test_a_default_that_is_not_block_or_confirm_cannot_open_the_gate(default):
    result = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), rules_with(default=default), now=NOW)
    assert result["decision"] == "BLOCK"


def test_a_block_outranks_a_confirm_and_both_are_listed():
    confirm = {"target": PROXY, "kinds": ["proxy-upgrade"]}
    both = request(call(PROXY, upgrade_to(ATTACKER)), call(PROXY, transfer_ownership(ATTACKER)))
    result = evaluate_admin_calls(both, rules_with(confirm=[confirm]), now=NOW)
    assert result["decision"] == "BLOCK"
    assert [(r["id"], r["level"]) for r in result["rules"]] == [("OWN-1", "block"), ("UPG-1", "confirm")]
    assert result["text"] == result["rules"][0]["text"]


def test_an_allow_rule_and_a_confirm_rule_that_both_match_the_stricter_one_wins():
    entry = {"target": PROXY, "kinds": ["proxy-upgrade"]}
    result = evaluate_admin_calls(
        request(call(PROXY, upgrade_to(ATTACKER))), rules_with(allow=[entry], confirm=[entry]), now=NOW
    )
    assert result["decision"] == "CONFIRM"


def test_an_unreadable_part_asks_by_default_and_can_be_set_to_block():
    short = call(PROXY, sel("upgradeTo(address)"))
    asked = evaluate_admin_calls(request(short), None, now=NOW)
    assert asked["decision"] == "CONFIRM"
    assert [r["id"] for r in asked["rules"]] == ["READ-1"]
    blocked_ = evaluate_admin_calls(request(short), rules_with(unreadable="block"), now=NOW)
    assert blocked_["decision"] == "BLOCK"
    ignored = evaluate_admin_calls(request(short), rules_with(unreadable="allow"), now=NOW)
    assert ignored["decision"] == "CONFIRM", "an unreadable part is never waved through by a rule"


def test_an_allow_rule_does_not_cover_an_unreadable_part_beside_the_allowed_call():
    allow = {"target": PROXY, "kinds": ["proxy-upgrade"]}
    data = multicall_bytes([upgrade_to(ATTACKER), sel("upgradeTo(address)")])
    result = evaluate_admin_calls(request(call(PROXY, data)), rules_with(allow=[allow]), now=NOW)
    assert result["decision"] == "CONFIRM"


@pytest.mark.parametrize(
    ("data", "rule_id"),
    [
        (upgrade_to(ATTACKER), "UPG-1"),
        (sel("setImplementation(address)") + a(ATTACKER), "IMPL-1"),
        (change_admin(ATTACKER), "ADM-1"),
        (transfer_ownership(ATTACKER), "OWN-1"),
        (renounce_ownership(), "OWN-2"),
        (sel("grantRole(bytes32,address)") + b"\x00" * 32 + a(ATTACKER), "OWN-3"),
        (sel("addOwnerWithThreshold(address,uint256)") + a(ATTACKER) + u(1), "MSIG-1"),
        (sel("changeThreshold(uint256)") + u(1), "MSIG-2"),
        (sel("enableModule(address)") + a(ATTACKER), "MSIG-3"),
        (exec_transaction(HELPER, b"\x01\x02\x03\x04", operation=1), "DLG-1"),
    ],
)
def test_each_kind_has_its_own_rule_id_and_blocks(data, rule_id):
    outer = SAFE_WALLET if rule_id == "DLG-1" else PROXY
    result = evaluate_admin_calls(request(call(outer, data)), None, now=NOW)
    assert result["decision"] == "BLOCK"
    assert [r["id"] for r in result["rules"]] == [rule_id]


def test_a_delegation_is_blocked_unless_the_users_rule_names_that_delegate():
    req = request(call(TOKEN, b""), authorization_list=[auth_tuple(ATTACKER)])
    assert evaluate_admin_calls(req, None, now=NOW)["decision"] == "BLOCK"
    assert [r["id"] for r in evaluate_admin_calls(req, None, now=NOW)["rules"]] == ["DEL-1"]
    allow = {"target": ATTACKER, "kinds": ["eip7702-delegation"]}
    assert evaluate_admin_calls(req, rules_with(allow=[allow]), now=NOW)["decision"] == "ALLOW"
    wrong = {"target": HELPER, "kinds": ["eip7702-delegation"]}
    assert evaluate_admin_calls(req, rules_with(allow=[wrong]), now=NOW)["decision"] == "BLOCK"


def test_addresses_match_whatever_their_case():
    allow = {"target": PROXY.upper().replace("0X", "0x"), "kinds": ["proxy-upgrade"]}
    mixed = request({"to": PROXY.upper().replace("0X", "0x"), "value": "0", "data": hexed(upgrade_to(ATTACKER))})
    assert evaluate_admin_calls(mixed, rules_with(allow=[allow]), now=NOW)["decision"] == "ALLOW"


# ---------------------------------------------------------------- bad requests fail closed
@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        "x",
        {},
        {"calls": "x"},
        {"calls": [None]},
        {"calls": [{"to": "0x12", "data": "0x"}]},
        {"calls": [{"to": TOKEN, "data": "0xzz"}]},
        {"calls": [{"to": TOKEN, "data": "0x123"}]},
        {"calls": [{"to": TOKEN, "data": "0x", "value": "-1"}]},
        {"calls": [{"to": TOKEN, "data": "0x"}], "authorizationList": "x"},
        {"calls": [{"to": TOKEN, "data": "0x"}] * 600},
        {"calls": [{"to": TOKEN, "data": "0x" + "00" * 300_000}]},
    ],
    ids=lambda x: repr(x)[:40],
)
def test_a_request_that_cannot_be_read_is_blocked_not_allowed(bad):
    result = evaluate_admin_calls(bad, None, now=NOW)
    assert result["decision"] == "BLOCK"
    assert result["text"].startswith(BLOCK_PREFIX)
    assert [r["id"] for r in result["rules"]] == ["REQ-1"]


def test_user_rules_that_are_not_a_mapping_do_not_open_the_gate():
    for junk in (
        "allow",
        7,
        ["allow"],
        {"admin_calls": "allow"},
        {"admin_calls": {"allow": "all"}},
        {"admin_calls": {"allow": [None, 3, "x"]}},
    ):
        result = evaluate_admin_calls(request(call(PROXY, upgrade_to(ATTACKER))), junk, now=NOW)
        assert result["decision"] == "BLOCK", junk


def test_the_check_does_not_change_its_inputs():
    req = request(call(PROXY, upgrade_to(ATTACKER)), authorization_list=[auth_tuple(HELPER)])
    rules = rules_with(allow=[{"target": PROXY, "kinds": ["proxy-upgrade"]}])
    import copy

    before = copy.deepcopy((req, rules))
    evaluate_admin_calls(req, rules, now=NOW)
    assert (req, rules) == before


def test_the_order_hash_follows_the_request_and_only_the_request():
    base = request(call(PROXY, upgrade_to(ATTACKER)))
    first = evaluate_admin_calls(base, None, now=NOW)
    again = evaluate_admin_calls(base, None, now=NOW + 5)
    changed = evaluate_admin_calls(request(call(PROXY, upgrade_to(HELPER))), None, now=NOW)
    with_rules = evaluate_admin_calls(base, rules_with(default="confirm"), now=NOW)
    assert first["order_hash"] == again["order_hash"] == with_rules["order_hash"]
    assert first["order_hash"] != changed["order_hash"]


# ---------------------------------------------------------------- wording and wiring
VERDICT_SWEEP = [d[1] for d in DIRECT] + [b[1] for b in BATCHES]


def test_every_sentence_the_check_says_starts_with_one_of_the_three_and_never_says_safe_or_scam():
    seen = []
    for data in VERDICT_SWEEP:
        for rules in (None, rules_with(default="confirm"), rules_with(unreadable="block")):
            result = evaluate_admin_calls(request(call(PROXY, data)), rules, now=NOW)
            seen.append(result["text"])
            seen.extend(r["text"] for r in result["rules"])
    short = request(call(PROXY, sel("upgradeTo(address)")))
    seen.append(evaluate_admin_calls(short, None, now=NOW)["text"])
    seen.append(evaluate_admin_calls(None, None, now=NOW)["text"])
    seen.append(evaluate_admin_calls(request(call(TOKEN, b"")), None, now=NOW)["text"])
    assert len(seen) > 100
    for text in seen:
        assert text == ALLOW_TEXT or text.startswith((BLOCK_PREFIX, CONFIRM_PREFIX)), text
        assert not re.search(r"safe|scam|trust|legit|malicious|hack|steal|drain", text, re.IGNORECASE), text


def test_the_module_says_nothing_user_facing_with_safe_or_scam_in_it():
    source = (ROOT / "bot" / "claw_guard" / "admin_calls.py").read_text(encoding="utf-8")
    code = code_only(source)
    assert not re.search(r"\b(safe|scam)\b", code, re.IGNORECASE)


def test_the_existing_order_path_still_refuses_an_evm_request_and_signs_nothing():
    from bot.claw_guard.signer import submit_intent

    action = request(call(PROXY, upgrade_to(ATTACKER)))
    account = {"equity_usd": 1.0}
    result = evaluate(action, account, now=NOW)
    assert result["decision"] == "BLOCK"
    signed = submit_intent(action, account, now=NOW, caller_is_admin=True, env={"CLAW_GUARD_ENABLED": "1"})
    assert signed["signed"] is False
    assert signed["sent"] is False


def test_the_check_is_not_reachable_from_the_service_door():
    from bot.claw_guard.service import check_body

    body = {"action": request(call(PROXY, upgrade_to(ATTACKER))), "account": {}}
    result = check_body(body, now=NOW)
    assert result["decision"] == "BLOCK"
    assert [r["id"] for r in result["rules"]][0] == "DENY"


def test_the_module_reads_no_environment_and_makes_no_network_call():
    source = (ROOT / "bot" / "claw_guard" / "admin_calls.py").read_text(encoding="utf-8")
    code = code_only(source)
    for needle in ("os.environ", "getenv", "socket", "urllib", "requests", "http.client", "subprocess", "open("):
        assert needle not in code, needle


# ---------------------------------------------------------------- what was opened and what was not understood
def test_the_scan_lists_the_wrappers_it_opened():
    data = aggregate3([(TOKEN, b""), (PROXY, multicall_bytes([erc20_transfer(OWNER, 1)]))])
    found = scan(request(call(HELPER, b""), call(HELPER, data)))
    assert found.wrappers == ("tx.calls[1]", "tx.calls[1].inner[1]")


def test_the_scan_lists_calls_it_has_no_selector_for_and_never_counts_them_as_findings():
    swap = sel("swap(uint256,uint256)") + u(1) + u(2)
    data = multicall_bytes([swap, erc20_transfer(OWNER, 1)])
    found = scan(request(call(TOKEN, erc20_approve(OWNER, 1)), call(PROXY, data)))
    assert found.findings == ()
    assert [(c.path, c.selector) for c in found.undecoded] == [
        ("tx.calls[0]", "0x095ea7b3"),
        ("tx.calls[1].inner[0]", "0x" + sel("swap(uint256,uint256)").hex()),
        ("tx.calls[1].inner[1]", "0xa9059cbb"),
    ]


def test_a_plain_value_transfer_and_an_admin_call_are_not_undecoded():
    found = scan(request(call(ATTACKER, b"", value=1), call(PROXY, upgrade_to(ATTACKER))))
    assert found.undecoded == ()


def test_a_delegatecall_with_calldata_is_flagged_and_its_calldata_is_listed_as_not_understood():
    found = scan(request(call(SAFE_WALLET, exec_transaction(HELPER, b"\x12\x34\x56\x78", operation=1))))
    assert kinds(found) == ["delegatecall"]
    assert [c.path for c in found.undecoded] == ["tx.calls[0].inner[0]"]


def test_the_verdict_carries_what_was_opened_and_not_understood_without_letting_it_change_the_answer():
    data = multicall_bytes([sel("swap(uint256,uint256)") + u(1) + u(2)])
    result = evaluate_admin_calls(request(call(PROXY, data)), None, now=NOW)
    assert result["decision"] == "ALLOW"
    assert result["text"] == ALLOW_TEXT
    assert result["wrappers"] == ["tx.calls[0]"]
    assert result["undecoded"] == [
        {"path": "tx.calls[0].inner[0]", "selector": "0x" + sel("swap(uint256,uint256)").hex()}
    ]


def test_a_call_may_name_its_target_as_a_caip10_account_like_an_erc8481_request():
    caip = {"to": f"eip155:1:{PROXY}", "value": "0", "data": hexed(upgrade_to(ATTACKER))}
    found = scan(request(caip))
    assert [(f.kind, f.target) for f in found.findings] == [("proxy-upgrade", PROXY)]
    for bad in ("eip155:1:0x12", "eip155::" + PROXY, "eip155:01:" + PROXY, "solana:1:" + PROXY):
        with pytest.raises(ValueError):
            scan(request({"to": bad, "value": "0", "data": "0x"}))


# ---------------------------------------------------------------- every call visited, and who would run a delegatecall
def test_the_scan_lists_every_call_it_visited_with_its_target():
    data = multicall_bytes([erc20_transfer(OWNER, 1), upgrade_to(ATTACKER)])
    found = scan(request(call(TOKEN, b""), call(PROXY, data), {"to": None, "value": "0", "data": "0x6080"}))
    assert [(c.path, c.target) for c in found.calls] == [
        ("tx.calls[0]", TOKEN),
        ("tx.calls[1]", PROXY),
        ("tx.calls[1].inner[0]", PROXY),
        ("tx.calls[1].inner[1]", PROXY),
    ]


def test_a_delegatecall_finding_names_the_account_that_would_run_it():
    direct = scan(request(call(SAFE_WALLET, exec_transaction(HELPER, b"\x12\x34\x56\x78", operation=1))))
    assert [(f.kind, f.account) for f in direct.findings] == [("delegatecall", SAFE_WALLET)]
    inner = exec_transaction(MULTISEND_TARGETS[0], multi_send([(1, HELPER, 0, b"\x01\x02\x03\x04")]), operation=1)
    through = scan(request(call(SAFE_WALLET, inner)))
    assert [(f.kind, f.account) for f in through.findings] == [("delegatecall", SAFE_WALLET)]
    mode = scan(request(call(SAFE_WALLET, execute_single_7579(HELPER, 0, b"\x01\x02\x03\x04", call_type=0xFF))))
    assert [(f.kind, f.account) for f in mode.findings] == [("delegatecall", SAFE_WALLET)]
    plain = scan(request(call(PROXY, upgrade_to(ATTACKER))))
    assert plain.findings[0].account is None


def test_the_verdict_lists_the_calls_and_the_account_of_a_delegatecall():
    result = evaluate_admin_calls(
        request(call(SAFE_WALLET, exec_transaction(HELPER, b"\x12\x34\x56\x78", operation=1))), None, now=NOW
    )
    assert result["calls"] == [
        {"path": "tx.calls[0]", "target": SAFE_WALLET},
        {"path": "tx.calls[0].inner[0]", "target": HELPER},
    ]
    assert result["findings"][0]["account"] == SAFE_WALLET
