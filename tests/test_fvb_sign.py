"""Signatures, the 3-of-5 threshold, and the schemes that are refused."""
from __future__ import annotations

from bot.fvb.chains import MainnetRefused
from bot.fvb.keccak import keccak256
from bot.fvb.packet import scale_price
from bot.fvb.sign import (
    Committee,
    PacketFields,
    SchemeRejected,
    Signer,
    require_scheme,
    symbol_word,
)

_CONTRACT = bytes.fromhex("000000000000000000000000000000000000ffb0")


def _fields(fv: float = 100.0) -> PacketFields:
    scaled = scale_price(fv)
    lo = scale_price(fv * 0.99)
    hi = scale_price(fv * 1.01)
    assert scaled is not None and lo is not None and hi is not None
    return PacketFields(
        symbol=symbol_word("EQ"),
        fv=scaled,
        band_lo=lo,
        band_hi=hi,
        session=0,
        confirmed=True,
        ts=1_700_000_000,
        version=1,
        next_epoch=bytes(32),
    )


def _committee(scheme: str = "secp256k1", chain_id: int | None = 11155111,
               cluster: str | None = None, n: int = 5, k: int = 3) -> Committee:
    signers = [Signer.mint_signer(f"s{i}", scheme) for i in range(n)]
    return Committee(
        signers, threshold=k, verifying_contract=_CONTRACT,
        chain_id=chain_id, cluster=cluster,
    )


def test_keccak_known_vectors():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"


def test_three_of_five_verifies_and_two_does_not():
    committee = _committee()
    fields = _fields()
    signed = committee.sign_packet(fields)
    assert len(signed) == 5
    assert committee.verify_packet(fields, signed[:3]) is True
    assert committee.verify_packet(fields, signed[:2]) is False
    assert committee.verify_packet(fields, [signed[0], signed[0], signed[0]]) is False


def test_a_tampered_price_does_not_verify():
    committee = _committee()
    fields = _fields(100.0)
    signed = committee.sign_packet(fields)
    tampered = _fields(100.01)
    assert committee.verify_packet(tampered, signed[:3]) is False


def test_ed25519_three_of_five_on_devnet():
    committee = _committee(scheme="ed25519", chain_id=None, cluster="devnet")
    fields = _fields()
    signed = committee.sign_packet(fields)
    assert committee.verify_packet(fields, signed[:3]) is True
    assert committee.verify_packet(fields, signed[:2]) is False


def test_mainnet_is_refused_before_a_signature():
    for chain_id in (1, 8453, 42161, 999, 4663):
        try:
            _committee(chain_id=chain_id)
        except MainnetRefused:
            continue
        raise AssertionError(chain_id)
    try:
        _committee(chain_id=None, cluster="mainnet-beta")
    except MainnetRefused:
        return
    raise AssertionError("cluster")


def test_post_quantum_schemes_are_rejected():
    for name in ("ml-dsa", "falcon", "dilithium"):
        try:
            require_scheme(name)
        except SchemeRejected:
            continue
        raise AssertionError(name)
    try:
        Signer.mint_signer("x", "ml-dsa")
    except SchemeRejected:
        return
    raise AssertionError("mint")


def test_rotation_requires_the_commitment():
    committee = _committee()
    fields = _fields()
    newbie = Signer.mint_signer(committee.signers[0].signer_id, "secp256k1")
    try:
        committee.rotate_signer(committee.signers[0].signer_id, newbie)
    except SchemeRejected:
        pass
    else:
        raise AssertionError("uncommitted key was accepted")
    committee.commit_next_key(committee.signers[0].signer_id, newbie.public_key_bytes())
    committee.rotate_signer(committee.signers[0].signer_id, newbie)
    signed = committee.sign_packet(fields)
    assert committee.verify_packet(fields, signed[:3]) is True
