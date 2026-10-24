"""Quantum custody haircut. Scores stay estimates, and a missing input is not zero."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from bot.qch.config import load_config
from bot.qch.crawler import crawl
from bot.qch.flag import FLAG, flag_on
from bot.qch.graph import custody_node
from bot.qch.policy import decide
from bot.qch.reading import publish
from bot.qch.sar_bind import flag_bits, scheme_table, timelock_sec
from bot.qch.score import (
    apply_attestation,
    bucket_total,
    is_exposed,
    multisig_factor_bps,
    qes_bps,
    time_weighted,
    weighted_exposed,
)
from bot.qch.sign import MAINNET_CHAIN_IDS, signing_posture

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "bot" / "qch" / "fixtures"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class _Boom:
    """Any read of the signing key is a failure."""

    def get(self, key, default=None):
        raise AssertionError(key)


def _issuer(doc, asset):
    return next(row for row in doc["issuers"] if row["asset"] == asset)


def _walk_scores(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("qes_bps"), int) or isinstance(obj.get("haircut_bps"), int):
            assert obj.get("label") == "estimate"
            assert obj.get("estimate") is True
            assert isinstance(obj.get("method"), str) and obj["method"]
            assert isinstance(obj.get("data_source"), str) and obj["data_source"]
        for value in obj.values():
            _walk_scores(value)
    elif isinstance(obj, list):
        for value in obj:
            _walk_scores(value)


def test_the_flag_is_off_unless_the_value_is_1_or_true():
    assert FLAG == "QCH_ENABLED"
    assert flag_on({}) is False
    assert flag_on({"QCH_ENABLED": ""}) is False
    assert flag_on({"QCH_ENABLED": "yes"}) is False
    assert flag_on({"QCH_ENABLED": "on"}) is False
    assert flag_on({"QCH_ENABLED": "0"}) is False
    assert flag_on({"QCH_ENABLED": "1"}) is True
    assert flag_on({"QCH_ENABLED": " true "}) is True


def test_mainnet_does_not_read_the_signing_key():
    for chain in MAINNET_CHAIN_IDS:
        posture = signing_posture(chain, _Boom())
        assert posture["ok"] is False
        assert posture["reason"] == "mainnet_refused"
        assert posture["key_read"] is False
    absent = signing_posture(84532, {})
    assert absent["reason"] == "keys_absent"
    assert absent["key_read"] is False
    secret = "qch-test-key-material"
    present = signing_posture(11155111, {"QCH_SIGNING_KEYS": secret})
    assert present["ok"] is True
    assert present["key_read"] is True
    assert present["threshold"] == "2-of-3"
    assert secret not in json.dumps(present)
    assert signing_posture(True, _Boom())["reason"] == "chain_unreadable"
    assert signing_posture(10, _Boom())["reason"] == "not_testnet"


def test_the_published_snapshot_matches_the_calculator():
    disk = json.loads((FIXTURES / "published.json").read_text(encoding="utf-8"))
    fresh = publish()
    assert disk == fresh
    assert fresh["signs"] is False
    assert fresh["key_held"] is False
    assert fresh["label"] == "estimate"
    assert "$" not in json.dumps(fresh)
    assert _issuer(fresh, "UBTC")["qes_bps"] == 10000
    assert _issuer(fresh, "WBTC")["qes_bps"] == 9816
    assert _issuer(fresh, "cbBTC")["qes_bps"] == 4089
    assert _issuer(fresh, "LBTC")["qes_bps"] is None
    assert _issuer(fresh, "LBTC")["reason"] == "not_scanned"
    assert _issuer(fresh, "tBTC")["qes_bps"] is None
    assert _issuer(fresh, "WBTC")["gap"]["gap_bps"] == 1
    assert _issuer(fresh, "cbBTC")["gap"]["gap_bps"] == 1
    assert _issuer(fresh, "UBTC")["gap"]["reason"] == "unread"
    assert _issuer(fresh, "UBTC")["gap"]["gap_sats"] is None
    _walk_scores(fresh)


def test_example_rows_are_not_the_reserve_and_classify_exposure():
    rows = json.loads((FIXTURES / "rows.json").read_text(encoding="utf-8"))["rows"]
    snap = json.loads((FIXTURES / "snapshot.json").read_text(encoding="utf-8"))
    totals = {row["asset"]: sum(b["total_sats"] for b in row["buckets"]) for row in snap["issuers"]}
    counts = {row["asset"]: row["address_count"] for row in snap["issuers"]}
    as_of = datetime.fromisoformat(snap["as_of"])
    assert int(as_of.timestamp()) == snap["as_of_unix"]
    differed = False
    for row in rows:
        if counts[row["asset"]] > 1:
            assert row["chain_sats"] != totals[row["asset"]]
            differed = True
        else:
            assert row["chain_sats"] == totals[row["asset"]]
        assert is_exposed(row) is row["expect_exposed"]
        if row["type"] != "P2TR":
            missing = dict(row)
            missing.pop("spent_txo")
            assert is_exposed(missing) is None
    assert differed is True
    for issuer in snap["issuers"]:
        chains = issuer["supply_by_chain"]
        if not chains:
            continue
        assert sum(item["sats"] for item in chains) == issuer["compared_sats"]


def test_an_unset_threshold_keeps_full_weight_and_a_partial_one_withholds():
    snap = json.loads((FIXTURES / "snapshot.json").read_text(encoding="utf-8"))
    buckets = next(row["buckets"] for row in snap["issuers"] if row["asset"] == "WBTC")
    total = bucket_total(buckets)
    full = qes_bps(weighted_exposed(buckets, 10000), total)
    assert full == 9816
    unset = multisig_factor_bps("unset", None, None)
    assert unset["reason"] == "unverified_threshold"
    assert unset["factor_bps"] == 10000
    configured = multisig_factor_bps("value", 2, 3)
    assert configured["reason"] == "configured_m_over_n"
    assert configured["factor_bps"] == 6666
    lower = qes_bps(weighted_exposed(buckets, configured["factor_bps"]), total)
    assert lower is not None and lower < full
    discounted = publish({"QCH_WBTC_MULTISIG_M": "2", "QCH_WBTC_MULTISIG_N": "3"})
    assert _issuer(discounted, "WBTC")["qes_bps"] == lower
    withheld = publish({"QCH_WBTC_MULTISIG_M": "2"})
    assert _issuer(withheld, "WBTC")["qes_bps"] is None
    assert load_config({}).wbtc_multisig_state == "unset"


def test_time_weight_and_attestation_have_both_arms():
    single = time_weighted([{"ts": 10, "qes_bps": 1000}], 20)
    assert single["averaged"] is False
    assert single["reason"] == "single_observation"
    assert single["tw_bps"] == 1000
    open_window = time_weighted(
        [{"ts": 0, "qes_bps": 0}, {"ts": 10, "qes_bps": 10000}],
        10,
    )
    assert open_window["tw_bps"] is None
    assert open_window["reason"] == "window_open"
    averaged = time_weighted(
        [{"ts": 0, "qes_bps": 0}, {"ts": 10, "qes_bps": 10000}],
        30,
    )
    assert averaged["averaged"] is True
    assert averaged["tw_bps"] == 6666
    plain = apply_attestation(1000, None, 0, "placeholder", 100)
    assert plain["qes_bps"] == 1000
    assert plain["applied"] is False
    reduced = apply_attestation(
        1000, {"signed": True, "expires_unix": 200}, 100, "value", 100,
    )
    assert reduced["qes_bps"] == 900
    assert reduced["applied"] is True


def test_every_decision_refuses_liquidation_and_both_actions_exist():
    bits = flag_bits()
    assert bits is not None
    hold = decide(
        qes_bps=100, prev_qes_bps=100, canary=False, jump_bps=50,
        deadline_unix=None, now=1, attested=False, sar_flags=0,
        flag_bits=bits, cap_cut_bps=1000, data_source="fixture",
    )
    cut = decide(
        qes_bps=100, prev_qes_bps=100, canary=True, jump_bps=50,
        deadline_unix=None, now=1, attested=False, sar_flags=bits["canary"],
        flag_bits=bits, cap_cut_bps=1000, data_source="fixture",
    )
    assert hold["action"] == "hold"
    assert cut["action"] == "cut_supply_cap"
    assert hold["liquidate"] is False
    assert cut["liquidate"] is False
    assert cut["haircut_bps"] == 100
    clear = decide(
        qes_bps=100, prev_qes_bps=100, canary=False, jump_bps=50,
        deadline_unix=None, now=1, attested=None, sar_flags=0,
        flag_bits=bits, cap_cut_bps=1000, data_source="fixture",
    )
    unread = decide(
        qes_bps=100, prev_qes_bps=100, canary=False, jump_bps=50,
        deadline_unix=None, now=1, attested=None, sar_flags=None,
        flag_bits=bits, cap_cut_bps=1000, data_source="fixture",
    )
    assert clear["haircut_bps"] == 0
    assert clear["haircut_reason"] == "flags_clear"
    assert unread["haircut_bps"] is None
    assert unread["haircut_reason"] == "flags_unreadable"
    for canary in (True, False, None):
        for prev in (None, 100, 200):
            choice = decide(
                qes_bps=150, prev_qes_bps=prev, canary=canary, jump_bps=50,
                deadline_unix=None, now=1, attested=False, sar_flags=0,
                flag_bits=bits, cap_cut_bps=1000, data_source="fixture",
            )
            assert choice["liquidate"] is False


def test_the_graph_node_uses_the_rxb_kinds():
    node = custody_node("WBTC", 9816, "por", "fixture", "method")
    assert node["node"] == "btc_custody"
    assert node["edge_kind"] == "mint_backing"
    assert node["verif"] == "por"
    assert node["label"] == "estimate"
    unknown = custody_node("LBTC", None, "nope", "none", "method")
    assert unknown["verif"] == "none"
    assert unknown["qes_bps"] is None


def test_scheme_ids_and_the_timelock_come_from_sar():
    table = scheme_table()
    assert table is not None
    verifier = (ROOT / "tools" / "sar-oracle" / "src" / "SaRVerifier.sol").read_text(encoding="utf-8")
    assert f"{table['ecdsa']} ECDSA" in verifier
    assert f"{table['wots']} WOTS" in verifier
    assert table["unavailable"] == frozenset({table["mldsa_sol"], table["mldsa_pre"]})
    bits = flag_bits()
    chains = (ROOT / "tools" / "sar-oracle" / "runtime" / "chains.js").read_text(encoding="utf-8")
    assert f"pegDeviation: {bits['peg']}" in chains
    assert f"quantumCanary: {bits['canary']}" in chains
    assert bits["peg"] == 16
    assert bits["canary"] == 32
    assert timelock_sec() == 604800
    doc = publish()
    assert doc["parameters"]["ltv_timelock_sec"] == 604800
    assert doc["parameters"]["sar_flags"]["peg"] == 16
    assert doc["simulator"]["cases"]["jump"]["action"] == "cut_supply_cap"
    assert doc["simulator"]["cases"]["clear"]["action"] == "hold"
    assert doc["simulator"]["cases"]["canary"]["liquidate"] is False


def test_a_failed_reader_is_not_a_zero_score():
    def boom():
        raise RuntimeError("down")

    failed = crawl(boom)
    assert failed["readable"] is False
    assert failed["reason"] == "reader_failed"
    assert failed["snapshot"] is None
    assert "qes_bps" not in failed


def test_the_contract_job_is_on_ci_and_off_the_local_plan():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = next(item for item in workflow["jobs"].values() if item.get("name") == "QCH contracts (solidity)")
    runs = [(step.get("working-directory"), str(step.get("run") or "")) for step in job["steps"]]
    assert ("contracts/qch", "npm ci --no-audit --no-fund") in runs
    assert ("contracts/qch", "npm test") in runs
    assert any(wd == "contracts/qch" and "audit_gate.mjs ." in cmd for wd, cmd in runs)
    assert "QCH contracts (solidity)" not in preflight.LOCAL_JOBS
    assert "QCH contracts (solidity)" in preflight.uncovered()
    assert job.get("continue-on-error") is not True


def test_placeholder_defaults_are_labelled():
    cfg = load_config({})
    assert cfg.unit_deposit_prefix == "unverified"
    assert "UNVERIFIED" in cfg.unit_deposit_basis
    assert "UNVERIFIED" in cfg.wbtc_multisig_basis
    assert cfg.qes_jump_bps == 50
    assert cfg.qes_jump_state == "placeholder"
    assert cfg.lbtc_scanned is False
    assert cfg.tbtc_scanned is False
    assert cfg.attestation_reduction_bps == 0
    assert load_config({"QCH_QES_JUMP_BPS": "nope"}).qes_jump_state == "unreadable"
    assert load_config({"QCH_LBTC_SCANNED": "1"}).lbtc_scanned is True


@pytest.mark.parametrize("chain", [11155111, 84532, 421614, 998])
def test_each_named_testnet_can_ask_the_posture(chain):
    posture = signing_posture(chain, {})
    assert posture["chain_id"] == chain
    assert posture["key_read"] is False
