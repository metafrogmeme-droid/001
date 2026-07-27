"""The signature proves the arithmetic. The anchor proves whose arithmetic it is.

THE ATTACK THIS CLOSES

`verify.py` checks 1-7 re-derive everything from the fills: hashes, Merkle root,
metrics, reconciliation, and an Ed25519 signature over the commitment. All of it
is sound, and none of it says WHO signed.

So the forgery is trivial and needs no exploit at all. Generate a keypair.
Invent a set of internally-consistent fills. Sign the commitment. Every check
passes, because every check is a statement about arithmetic, and the arithmetic
is honest — the numbers are simply not anyone's.

The identity anchor is the only thing that closes it: a transaction sent FROM
the agent's own wallet whose calldata carries sha256({agent_address,
ed25519_pubkey}). Check 8 re-derives that from the chain.

WHY IT RE-DERIVES RATHER THAN READS

`anchor_for_card` builds the published `anchor.status` from the operator's LOCAL
state file. A published "VERIFIED" is the operator vouching for themselves,
which is precisely what a skeptic must not accept. And the agent address is
taken from the transaction's own `from` field, never from the statement — a
statement naming an address it does not control cannot survive, because the
commitment would then be computed over the real sender and would not match the
calldata.
"""

import json

import pytest

import verify as verifier
from bot.proofofpnl.anchor import identity_commitment

MAGIC = "52554e45434c4157"
AGENT = "0x00000000000000000000000000000000deadbeef"
ATTACKER = "0x00000000000000000000000000000000badc0de0"
PUBKEY = "ab" * 32
OTHER_PUBKEY = "cd" * 32


def _chain(txs):
    """A fake public RPC serving a fixed set of transactions."""
    def rpc(url, method, params):
        txh = params[0]
        entry = txs.get(txh)
        if entry is None:
            return None
        if method == "eth_getTransactionByHash":
            return entry["tx"]
        if method == "eth_getTransactionReceipt":
            return entry["rcpt"]
        raise AssertionError(f"unexpected RPC {method}")
    return rpc


def _anchor_tx(sender: str, pubkey: str, *, status="0x1", magic=MAGIC):
    commit = identity_commitment(sender, pubkey)
    return {
        "tx": {"from": sender, "input": "0x" + magic + commit},
        "rcpt": {"status": status},
    }


def _anchor_block(tx_hash="0x" + "11" * 32):
    return {"status": "VERIFIED", "anchors": [{"tx_hash": tx_hash, "chain_id": 8453}]}


@pytest.fixture
def patched_rpc(monkeypatch):
    def install(txs):
        monkeypatch.setattr(verifier, "_eth_rpc", _chain(txs))
    return install


def test_a_genuine_anchor_confirms_the_signing_key(patched_rpc):
    txh = "0x" + "11" * 32
    patched_rpc({txh: _anchor_tx(AGENT, PUBKEY)})
    ok, notes = verifier._reverify_anchor(_anchor_block(txh), PUBKEY, "http://rpc")
    assert ok, notes


def test_an_anchor_binding_a_DIFFERENT_key_is_refused(patched_rpc):
    """The forgery: a real anchor from a real wallet, reused to bless someone
    else's signing key. The wallet did anchor an identity — just not this one."""
    txh = "0x" + "11" * 32
    patched_rpc({txh: _anchor_tx(AGENT, OTHER_PUBKEY)})
    ok, notes = verifier._reverify_anchor(_anchor_block(txh), PUBKEY, "http://rpc")
    assert not ok
    assert any("NOT the anchored one" in n for n in notes), notes


def test_claiming_someone_elses_wallet_cannot_survive(patched_rpc):
    """The agent address is read from the chain, not from the statement.

    An attacker points at a famous wallet's transaction. The commitment is then
    recomputed over THAT wallet plus the attacker's key, which is not what the
    famous wallet ever committed to.
    """
    txh = "0x" + "22" * 32
    patched_rpc({txh: _anchor_tx(AGENT, PUBKEY)})       # the real agent's anchor
    ok, notes = verifier._reverify_anchor(_anchor_block(txh), OTHER_PUBKEY, "http://rpc")
    assert not ok
    assert any("NOT the anchored one" in n for n in notes), notes


def test_a_failed_or_unmined_anchor_tx_is_not_evidence(patched_rpc):
    txh = "0x" + "33" * 32
    patched_rpc({txh: _anchor_tx(AGENT, PUBKEY, status="0x0")})
    ok, notes = verifier._reverify_anchor(_anchor_block(txh), PUBKEY, "http://rpc")
    assert not ok and any("FAILED on-chain" in n for n in notes), notes

    patched_rpc({})  # tx not found at all
    ok2, notes2 = verifier._reverify_anchor(_anchor_block(txh), PUBKEY, "http://rpc")
    assert not ok2, notes2


def test_calldata_without_the_anchor_prefix_is_refused(patched_rpc):
    """Any transaction could incidentally contain 32 bytes; the prefix is what
    marks it as an intentional RUNECLAW identity anchor."""
    txh = "0x" + "44" * 32
    patched_rpc({txh: _anchor_tx(AGENT, PUBKEY, magic="deadbeefdeadbeef")})
    ok, notes = verifier._reverify_anchor(_anchor_block(txh), PUBKEY, "http://rpc")
    assert not ok and any("anchor prefix" in n for n in notes), notes


def test_an_unreachable_rpc_reports_unverified_and_never_passes(patched_rpc):
    def boom(url, method, params):
        raise RuntimeError("connection refused")
    import verify as v
    v._eth_rpc = boom
    try:
        ok, notes = v._reverify_anchor(_anchor_block(), PUBKEY, "http://rpc")
        assert not ok
        assert any("UNVERIFIED" in n for n in notes), notes
    finally:
        import importlib
        importlib.reload(v)


def test_a_statement_with_no_anchor_passes_but_is_labelled_unattributed():
    """A bare statement has no anchor by construction — it lives one layer up.

    Failing it would be failing it for lacking something its own layer never
    carries. What must not happen is an unqualified PASS, which a stranger reads
    as attribution.
    """
    from tests.test_proof_of_pnl import _complete_fills, _snap  # reuse the fixtures
    from bot.proofofpnl import statement
    st = statement.build_epoch(_complete_fills(), account_ids=["acct"],
                               open_snapshot=_snap("1000", 1000),
                               close_snapshot=_snap("1009.79", 2000),
                               range_start=1000, range_end=2000, sign=True)
    if (st.get("attestation") or {}).get("type") != "ed25519":
        pytest.skip("Ed25519 signing unavailable")
    ok, diffs, identity = verifier.verify_statement_full(st)
    assert ok, diffs
    assert identity == "unattributed"


def test_require_identity_turns_absence_into_an_error():
    """For anyone gating a publish step on attribution rather than arithmetic."""
    from tests.test_proof_of_pnl import _complete_fills, _snap
    from bot.proofofpnl import statement
    st = statement.build_epoch(_complete_fills(), account_ids=["acct"],
                               open_snapshot=_snap("1000", 1000),
                               close_snapshot=_snap("1009.79", 2000),
                               range_start=1000, range_end=2000, sign=True)
    if (st.get("attestation") or {}).get("type") != "ed25519":
        pytest.skip("Ed25519 signing unavailable")
    ok, diffs, identity = verifier.verify_statement_full(st, require_identity=True)
    assert not ok
    assert any("require-identity" in d for d in diffs), diffs


def test_the_legacy_two_tuple_api_still_works():
    """7 tests and assemble.py unpack (ok, diffs); adding a third element for
    one CLI line would have broken all of them."""
    from tests.test_proof_of_pnl import _complete_fills, _snap
    from bot.proofofpnl import statement
    st = statement.build_epoch(_complete_fills(), account_ids=["acct"],
                               open_snapshot=_snap("1000", 1000),
                               close_snapshot=_snap("1009.79", 2000),
                               range_start=1000, range_end=2000, sign=True)
    result = verifier.verify_statement(st)
    assert len(result) == 2


def test_a_statement_supplied_agent_address_is_IGNORED(patched_rpc):
    """The address must come from the chain, and this proves it rather than
    asserting it in a comment.

    Added after a mutation test embarrassed the suite: swapping the derivation
    to `entry.get("agent_address") or sender` passed all nine tests, because not
    one of them supplied an `agent_address` for the code to wrongly prefer. The
    mutation was a no-op against the fixtures, so the suite "proved" a property
    it never exercised — the exact vacuous-test shape this audit keeps finding.

    Here the anchor block names a wallet the attacker does not control. If the
    verifier believed it, the commitment would be computed over that wallet and
    would match the calldata it planted. Reading `from` off the chain is what
    makes the claim unforgeable.
    """
    txh = "0x" + "55" * 32
    # On chain: ATTACKER anchored ATTACKER's own key. Perfectly valid, for them.
    patched_rpc({txh: _anchor_tx(ATTACKER, PUBKEY)})
    # In the statement: a block claiming this is the famous AGENT's anchor.
    block = {"status": "VERIFIED",
             "anchors": [{"tx_hash": txh, "chain_id": 8453, "agent_address": AGENT}]}

    ok, notes = verifier._reverify_anchor(block, PUBKEY, "http://rpc")
    # It IS a real anchor by ATTACKER, so it confirms — but as ATTACKER, and the
    # claim to be AGENT buys nothing. What must never happen is the reverse:
    # accepting AGENT because the statement said so.
    assert ok, notes

    # Now the case that must fail: the statement names AGENT and the on-chain
    # sender committed to a DIFFERENT key. Trusting the statement's address
    # would make this pass.
    txh2 = "0x" + "66" * 32
    patched_rpc({txh2: _anchor_tx(ATTACKER, OTHER_PUBKEY)})
    block2 = {"status": "VERIFIED",
              "anchors": [{"tx_hash": txh2, "chain_id": 8453, "agent_address": AGENT}]}
    ok2, notes2 = verifier._reverify_anchor(block2, PUBKEY, "http://rpc")
    assert not ok2, "a statement-supplied agent_address was trusted over the chain"
    assert any("NOT the anchored one" in n for n in notes2), notes2
