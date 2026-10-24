"""Exit-adjusted NAV: the flag, the RXB node, the placeholders, the signer.

The book walk lives in app/lib/xnav/depth.js and is tested there. This
file checks the Python side, including that a mainnet chain and a flag
that is off are refused before XNAV_SIGNING_KEY is read.
"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

from bot import admin_preview
from bot.fvb.chains import MainnetRefused
from bot.fvb.keccak import keccak256
from bot.fvb.sign import SchemeRejected
from bot.xnav.config import PlaceholdersUnreadable, l1_read_gas, placeholders
from bot.xnav.flag import FLAG, flag_on
from bot.xnav.graph import vault_exit_node
from bot.xnav.reading import SnapshotUnreadable, load_snapshot
from bot.xnav.sign import digest, load_signing_key, parse_address, sign_update, typehash

ROOT = Path(__file__).resolve().parents[1]
VAULT = "0xc179e03922afe8fa9533d3f896338b9fb87ce0c8"
VECTOR = json.loads((ROOT / "bot" / "xnav" / "fixtures" / "digest_vector.json").read_text(encoding="utf-8"))


class Guard(dict):
    """A mapping whose signing-key slot raises if anything reads it."""

    def get(self, key, default=None):
        if key == "XNAV_SIGNING_KEY":
            raise AssertionError("signing key was read")
        return dict.get(self, key, default)


def test_flag_on_accepts_only_one_and_true():
    assert FLAG == "XNAV_ENABLED"
    assert flag_on({}) is False
    assert flag_on({"XNAV_ENABLED": "1"}) is True
    assert flag_on({"XNAV_ENABLED": " true "}) is True
    assert flag_on({"XNAV_ENABLED": "TRUE"}) is True
    assert flag_on({"XNAV_ENABLED": "yes"}) is False
    assert flag_on({"XNAV_ENABLED": "on"}) is False
    assert flag_on({"XNAV_ENABLED": "0"}) is False


def test_admin_preview_lists_the_flag_and_reads_the_same_reader():
    assert admin_preview.public_on("XNAV_ENABLED", env={}) is False
    assert admin_preview.flag_on_for("7", "XNAV_ENABLED", env={}, store={}) is False
    admin = {"ADMIN_TELEGRAM_IDS": "42"}
    assert admin_preview.flag_on_for("42", "XNAV_ENABLED", env=admin, store={}) is True
    assert admin_preview.is_preview("42", "XNAV_ENABLED", env=admin, store={}) is True
    assert admin_preview.flag_on_for("7", "XNAV_ENABLED", env=admin, store={}) is False
    on = {"XNAV_ENABLED": "1"}
    assert admin_preview.public_on("XNAV_ENABLED", env=on) is True
    assert admin_preview.flag_on_for("7", "XNAV_ENABLED", env=on, store={}) is True
    assert admin_preview.is_preview("7", "XNAV_ENABLED", env=on, store={}) is False


def test_placeholders_throw_when_a_key_is_missing(tmp_path: Path):
    table = placeholders()
    assert table["label"].startswith("Placeholder")
    assert table["staleness_seconds"] == 60
    assert table["max_swing_bps"] == 1500
    assert l1_read_gas(2, 1) == 2000 + 65 * 3
    broken = dict(table)
    del broken["staleness_seconds"]
    path = tmp_path / "placeholders.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(PlaceholdersUnreadable):
        placeholders(path)
    missing = tmp_path / "nope.json"
    with pytest.raises(PlaceholdersUnreadable):
        placeholders(missing)


def test_vault_exit_node_reuses_the_rxb_shortfall():
    row = vault_exit_node(VAULT, 4560)
    assert row["readable"] is True
    assert Fraction(row["lts"]) == Fraction(4560, 10000) == Fraction(57, 125)
    assert row["haircut_above_one"] is False
    zero = vault_exit_node(VAULT, 0)
    assert zero["readable"] is True
    assert Fraction(zero["lts"]) == 0
    above = vault_exit_node(VAULT, 15000)
    assert above["readable"] is True
    assert Fraction(above["lts"]) == 1
    assert above["haircut_above_one"] is True
    blank = vault_exit_node(VAULT, None)
    assert blank["readable"] is False
    assert blank["lts"] is None
    assert blank["reason"] == "haircut_unreadable"
    assert vault_exit_node(VAULT, True)["readable"] is False
    bad = vault_exit_node("not-an-address", 4560)
    assert bad["readable"] is False
    assert bad["lts"] is None
    assert bad["reason"] == "invalid_vault"


def test_a_missing_snapshot_is_not_an_empty_ranking(tmp_path: Path):
    doc = load_snapshot()
    assert isinstance(doc["vaults"], list)
    assert len(doc["vaults"]) == 7
    with pytest.raises(SnapshotUnreadable):
        load_snapshot(tmp_path / "missing.json")


def test_the_signer_refuses_before_the_key_is_read():
    off = Guard({"XNAV_ENABLED": "0", "XNAV_CHAIN_ID": "998", "XNAV_SIGN_TESTNET": "1"})
    with pytest.raises(MainnetRefused):
        load_signing_key(off)
    mainnet = Guard({
        "XNAV_ENABLED": "1",
        "XNAV_CHAIN_ID": "999",
        "XNAV_SIGN_TESTNET": "1",
    })
    with pytest.raises(MainnetRefused):
        load_signing_key(mainnet)
    eth = Guard({"XNAV_ENABLED": "1", "XNAV_CHAIN_ID": "1", "XNAV_SIGN_TESTNET": "1"})
    with pytest.raises(MainnetRefused):
        load_signing_key(eth)
    bare = Guard({"XNAV_ENABLED": "true"})
    with pytest.raises(MainnetRefused):
        load_signing_key(bare)
    unarmed = Guard({"XNAV_ENABLED": "1", "XNAV_CHAIN_ID": "998"})
    with pytest.raises(MainnetRefused):
        load_signing_key(unarmed)


def test_post_quantum_schemes_are_rejected_and_the_digest_is_frozen():
    for name in ("ml-dsa", "falcon", "dilithium"):
        with pytest.raises(SchemeRejected):
            sign_update(b"\x11" * 20, 1, 1, 1, 998, b"\x22" * 20, None, scheme=name)
    with pytest.raises(MainnetRefused):
        sign_update(b"\x11" * 20, 1, 1, 1, 999, b"\x22" * 20, None)
    table = placeholders()
    assert typehash() == keccak256(str(table["eip712_type"]).encode("ascii"))
    assert typehash().hex() == VECTOR["typehash"]
    got = digest(
        parse_address(VECTOR["vault"]),
        VECTOR["xnav_bps"],
        VECTOR["stress_bps"],
        VECTOR["ts"],
        VECTOR["chain_id"],
        parse_address(VECTOR["verifying"]),
    )
    assert got.hex() == VECTOR["digest"]


def test_a_testnet_signature_verifies_and_is_low_s():
    key = "11" * 32
    env = {
        "XNAV_ENABLED": "1",
        "XNAV_CHAIN_ID": "998",
        "XNAV_SIGN_TESTNET": "1",
        "XNAV_SIGNING_KEY": key,
    }
    private = load_signing_key(env)
    assert isinstance(private, EllipticCurvePrivateKey)
    vault = parse_address(VECTOR["vault"])
    verifying = parse_address(VECTOR["verifying"])
    sig = sign_update(vault, 4560, 100, VECTOR["ts"], 998, verifying, private)
    assert len(sig) == 65
    assert sig[64] in (27, 28)
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:64], "big")
    n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    assert s <= n // 2
    message = digest(vault, 4560, 100, VECTOR["ts"], 998, verifying)
    private.public_key().verify(
        utils.encode_dss_signature(r, s),
        message,
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )
    with pytest.raises(InvalidSignature):
        private.public_key().verify(
            utils.encode_dss_signature(r, s),
            b"\x00" * 32,
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
