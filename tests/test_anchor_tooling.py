"""MH2 — ERC-8004 anchor tooling (Base root anchor, non-custodial).

The contract under test: the bot NEVER signs or broadcasts — it only plans a
transaction the operator sends from their own wallet, then verifies it
on-chain before anything upgrades to VERIFIED. A recorded anchor that no
longer matches the current identity (rotated key) reads STALE, never
silently VERIFIED. With no recorded anchor, the card's honest UNVERIFIED
plan passes through byte-identical (pinning the existing E4 contract).
"""

import json

import pytest

from bot.proofofpnl import anchor, erc8004

_AGENT = "0x" + "ab" * 20
_PUBKEY = "cd" * 32
_TX = "0x" + "11" * 32


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "anchor_state.json"
    monkeypatch.setenv("ANCHOR_STATE_PATH", str(p))
    monkeypatch.delenv("ANCHOR_REGISTRY_ADDRESS", raising=False)
    monkeypatch.delenv("ANCHOR_CHAIN_ID", raising=False)
    return p


# ── commitment + calldata ────────────────────────────────────────────────────

def test_identity_commitment_deterministic_and_case_insensitive():
    a = anchor.identity_commitment(_AGENT, _PUBKEY)
    b = anchor.identity_commitment(_AGENT.upper(), _PUBKEY.upper())
    assert a == b and len(a) == 64
    assert anchor.identity_commitment(_AGENT, "ee" * 32) != a, \
        "rotating the signing key must change the commitment"


def test_anchor_calldata_carries_magic_and_commitment():
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    data = anchor.anchor_calldata(c)
    assert data == "0x" + "52554e45434c4157" + c
    assert bytes.fromhex(data[2:18]).decode() == "RUNECLAW"


# ── dry-run plan (never signs, never sends) ──────────────────────────────────

def test_build_anchor_tx_is_dry_run_and_estimate_fail_soft(state_path, monkeypatch):
    monkeypatch.setattr(anchor, "_rpc",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    plan = anchor.build_anchor_tx(_AGENT, _PUBKEY)
    assert plan["dry_run"] is True
    assert plan["chain_id"] == anchor.BASE_CHAIN_ID
    assert plan["mode"] == "calldata-commitment"
    assert plan["to"] == _AGENT  # 0-value self-send
    assert plan["value"] == "0"
    assert plan["commitment"] in plan["data"]
    assert plan["estimate"]["available"] is False, "network weather never blocks planning"
    assert any("confirm" in s for s in plan["instructions"])
    assert "Ethereum mainnet" in plan["promotion_note"]
    src = open(anchor.__file__).read()
    for forbidden in ("signTransaction", "sendRawTransaction", "private_key",
                      "eth_sendTransaction", "eth_accounts"):
        assert forbidden not in src, f"anchor tooling must never {forbidden}"


def test_registry_mode_targets_registry(state_path, monkeypatch):
    monkeypatch.setenv("ANCHOR_REGISTRY_ADDRESS", "0x" + "99" * 20)
    monkeypatch.setattr(anchor, "_rpc",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    plan = anchor.build_anchor_tx(_AGENT, _PUBKEY)
    assert plan["mode"] == "registry"
    assert plan["to"] == "0x" + "99" * 20


# ── on-chain confirmation gate ───────────────────────────────────────────────

def _fake_rpc(tx=None, rcpt=None):
    def rpc(method, params):
        if method == "eth_getTransactionByHash":
            return tx
        if method == "eth_getTransactionReceipt":
            return rcpt
        raise AssertionError(method)
    return rpc


def _good_chain(commitment):
    # `to` used to be absent from this fixture and every test still passed,
    # because confirm_anchor never read it. A transaction with no destination
    # at all is a contract creation; the fixture was describing one.
    tx = {"from": _AGENT, "to": _AGENT, "input": anchor.anchor_calldata(commitment)}
    rcpt = {"status": "0x1", "blockNumber": "0x1a2b3c"}
    return tx, rcpt


def test_confirm_rejects_malformed_hash(state_path):
    ok, problems = anchor.confirm_anchor("nothex", _AGENT, _PUBKEY)
    assert not ok and "64 hex" in problems[0]


def test_confirm_records_only_after_all_checks_pass(state_path, monkeypatch):
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    tx, rcpt = _good_chain(c)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(tx, rcpt))
    ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
    assert ok and problems == []
    state = json.loads(state_path.read_text())
    rec = state[str(anchor.BASE_CHAIN_ID)]
    assert rec["tx_hash"] == _TX
    assert rec["block_number"] == 0x1A2B3C
    assert rec["commitment"] == c


@pytest.mark.parametrize("mutate,needle", [
    (lambda tx, rcpt: rcpt.update(status="0x0"), "FAILED"),
    (lambda tx, rcpt: tx.update(input="0xdeadbeef"), "not exactly the anchor payload"),
    (lambda tx, rcpt: tx.update({"from": "0x" + "77" * 20}), "not the agent address"),
    # The destination decides the mode the card publishes, so a destination
    # with no honest name is not recordable.
    (lambda tx, rcpt: tx.update({"to": "0x" + "77" * 20}), "neither the agent address"),
    (lambda tx, rcpt: tx.update({"to": None}), "contract creation"),
])
def test_confirm_rejects_bad_chain_state(state_path, monkeypatch, mutate, needle):
    tx, rcpt = _good_chain(anchor.identity_commitment(_AGENT, _PUBKEY))
    mutate(tx, rcpt)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(tx, rcpt))
    ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
    assert not ok and any(needle in p for p in problems)
    assert not state_path.exists(), "nothing recorded unless every check passes"


def test_confirm_unmined_and_missing(state_path, monkeypatch):
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(None, None))
    ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
    assert not ok and "not found" in problems[0]
    tx, _ = _good_chain(anchor.identity_commitment(_AGENT, _PUBKEY))
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(tx, None))
    ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
    assert not ok and "not yet mined" in problems[0]


# ── the recorder must never be looser than the auditor ───────────────────────

def _reverify(tx, rcpt, pubkey=_PUBKEY, monkeypatch=None):
    """Run verify.py's public check over the same fake chain."""
    import verify as verifier

    def rpc(url, method, params):
        return tx if method == "eth_getTransactionByHash" else rcpt
    monkeypatch.setattr(verifier, "_eth_rpc", rpc)
    return verifier._reverify_anchor(
        {"anchors": [{"tx_hash": _TX, "chain_id": 8453}]}, pubkey, "http://rpc")[0]


@pytest.mark.parametrize("label,calldata_for", [
    ("the real payload", lambda c: anchor.anchor_calldata(c)),
    ("commitment with no RUNECLAW prefix", lambda c: "0xdeadbeef" + c),
    ("payload buried in other arguments", lambda c: anchor.anchor_calldata(c) + "cafe"),
    ("a different commitment entirely", lambda c: anchor.anchor_calldata("9" * 64)),
])
def test_the_recorder_never_accepts_what_the_public_verifier_refuses(
        state_path, monkeypatch, label, calldata_for):
    """`/anchor confirm` writes the state file that `anchor_for_card` publishes
    as VERIFIED. `verify.py --require-identity` is what a skeptic runs against
    that published statement. If the recorder accepts a transaction the
    verifier refuses, the product's own two surfaces disagree about a public
    claim — and the operator only ever sees the permissive one.

    That was live: the recorder asked only whether the commitment appeared
    SOMEWHERE in the calldata, while the verifier has always additionally
    required the RUNECLAW prefix. A transaction with the commitment loose in
    some unrelated call's arguments was recorded and published VERIFIED, and
    verify.py refused the very same transaction hash.

    The property is one-directional on purpose — the recorder is allowed to be
    STRICTER (it now demands exact equality where the verifier still uses
    containment), never looser.
    """
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    tx = {"from": _AGENT, "to": _AGENT, "input": calldata_for(c)}
    rcpt = {"status": "0x1", "blockNumber": "0x1a2b3c"}
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(tx, rcpt))

    recorded = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    audited = _reverify(tx, rcpt, monkeypatch=monkeypatch)
    assert not (recorded and not audited), (
        f"{label}: recorded and published VERIFIED, but verify.py refuses it")


# ── mode is a claim about the destination, so read it off the destination ────

def test_recorded_mode_comes_from_the_transaction_not_the_environment(
        state_path, monkeypatch):
    """A plain self-send, confirmed while ANCHOR_REGISTRY_ADDRESS happens to be
    set — as it would be if the operator configured a registry at any point
    after sending. `mode` was written from that variable, so the identity card
    asserted a third-party contract holds the registration on evidence that
    never left the operator's own wallet."""
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))  # to == _AGENT
    monkeypatch.setenv("ANCHOR_REGISTRY_ADDRESS", "0x" + "99" * 20)

    assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    rec = json.loads(state_path.read_text())[str(anchor.BASE_CHAIN_ID)]
    assert rec["mode"] == "calldata-commitment", \
        "a self-send published as a registry registration"


def test_a_send_to_the_registry_records_registry_mode(state_path, monkeypatch):
    registry = "0x" + "99" * 20
    monkeypatch.setenv("ANCHOR_REGISTRY_ADDRESS", registry)
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    tx, rcpt = _good_chain(c)
    tx["to"] = registry
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(tx, rcpt))

    assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    rec = json.loads(state_path.read_text())[str(anchor.BASE_CHAIN_ID)]
    assert rec["mode"] == "registry"


def test_destination_mode_is_case_insensitive_both_sides(monkeypatch):
    """Wallets and explorers hand back EIP-55 mixed case; the plan lowercases.
    A checksummed destination is the same destination."""
    monkeypatch.setenv("ANCHOR_REGISTRY_ADDRESS", "0x" + "AB" * 20)
    assert anchor.destination_mode("0x" + "ab" * 20, "0x" + "cd" * 20) == "registry"
    assert anchor.destination_mode("0x" + "CD" * 20, "0x" + "cd" * 20) \
        == "calldata-commitment"
    assert anchor.destination_mode("", "0x" + "cd" * 20) is None
    assert anchor.destination_mode(None, "0x" + "cd" * 20) is None


# ── card upgrade: UNVERIFIED → VERIFIED / STALE ──────────────────────────────

def test_no_state_passes_unverified_plan_through(state_path):
    plan = {"status": "UNVERIFIED", "note": "designed anchor"}
    assert anchor.anchor_for_card(_AGENT, _PUBKEY, "h" * 64, plan) is plan


def test_matching_anchor_upgrades_to_verified(state_path, monkeypatch):
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))
    assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    out = anchor.anchor_for_card(_AGENT, _PUBKEY, "h" * 64, {"status": "UNVERIFIED"})
    assert out["status"] == "VERIFIED"
    assert out["commitment"] == c
    assert out["anchors"][0]["explorer"] == f"https://basescan.org/tx/{_TX}"
    assert out["anchors"][0]["block_number"] == 0x1A2B3C


def test_rotated_key_reads_stale_never_verified(state_path, monkeypatch):
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))
    assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    out = anchor.anchor_for_card(_AGENT, "ee" * 32, "h" * 64,
                                 {"status": "UNVERIFIED", "note": "plan"})
    assert out["status"] == "STALE"
    assert "DIFFERENT identity" in out["note"]
    assert out["stale_anchors"][0]["tx_hash"] == _TX


def test_per_chain_records_coexist_base_root_plus_mainnet_mirror(
        state_path, monkeypatch):
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))
    assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
    monkeypatch.setenv("ANCHOR_CHAIN_ID", "1")  # operator later promotes
    tx2 = "0x" + "22" * 32
    assert anchor.confirm_anchor(tx2, _AGENT, _PUBKEY)[0]
    out = anchor.anchor_for_card(_AGENT, _PUBKEY, "h" * 64, {})
    chains = sorted(a["chain_id"] for a in out["anchors"])
    assert chains == [1, anchor.BASE_CHAIN_ID], "mainnet mirror adds, never replaces"
    explorers = {a["chain_id"]: a["explorer"] for a in out["anchors"]}
    assert explorers[1].startswith("https://etherscan.io/tx/")
    assert explorers[anchor.BASE_CHAIN_ID].startswith("https://basescan.org/tx/")


# ── integration: build_identity_card picks the anchor up ─────────────────────

def test_build_identity_card_upgrades_anchor_from_state(state_path, monkeypatch):
    # sign=False → attestation pubkey is "" — the commitment binds exactly that.
    c = anchor.identity_commitment(_AGENT, "")
    monkeypatch.setattr(anchor, "_rpc", _fake_rpc(*_good_chain(c)))
    assert anchor.confirm_anchor(_TX, _AGENT, "")[0]
    card = erc8004.build_identity_card(_AGENT, None, sign=False)
    assert card["anchor"]["status"] == "VERIFIED"
    assert card["anchor"]["anchors"][0]["tx_hash"] == _TX
    # the card itself still verifies — the anchor is reported, not hashed in
    ok, diffs = erc8004.verify_card(card)
    assert ok, diffs


def test_build_identity_card_without_state_stays_unverified(state_path):
    card = erc8004.build_identity_card(_AGENT, None, sign=False)
    assert card["anchor"]["status"] == "UNVERIFIED"
    assert "no on-chain tx" in card["anchor"]["note"]
