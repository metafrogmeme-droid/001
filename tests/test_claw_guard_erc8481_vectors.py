"""Claw Guard's ERC-8481 code against the vectors published with the draft.

The vectors are copied from the merged ERC (see ``tests/fixtures/erc8481/README.md``). The ERC's own
checker is not imported: these are Claw Guard's functions, held to the draft's expected values.
The ``on-list`` set is the one the ERC credits to the RUNECLAW team.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bot.claw_guard import erc8481 as r
from bot.claw_guard import erc8481_lists as lists
from bot.hrc.keccak import keccak256

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "erc8481"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


COVERAGE = load("coverage.json")
NORMALIZE = load("normalize.json")
LIST_HASH = load("list-hash.json")
MATCH = load("match.json")


# ---------------------------------------------------------------- hashing primitives
def test_keccak_and_jcs_agree_with_the_drafts_known_answers():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert r.jcs({"b": [1, True, None], "a": "x"}) == b'{"a":"x","b":[1,true,null]}'
    # keys sort by UTF-16 code unit, not by code point: U+FF5E (BMP) sorts after U+1F600 (a surrogate pair)
    assert r.jcs({"\uff5e": 1, "\U0001f600": 2}).decode("utf-8") == '{"\U0001f600":2,"\uff5e":1}'
    assert r.jcs('\u007f\u001f"\\/').decode("utf-8") == '"\u007f\\u001f\\"\\\\/"'


@pytest.mark.parametrize("bad", [1.5, float("nan"), 2**53, -(2**53), {1: "x"}, object(), "\ud800"])
def test_jcs_refuses_what_it_cannot_write_exactly(bad):
    with pytest.raises((ValueError, TypeError)):
        r.jcs(bad)


# ---------------------------------------------------------------- coverage.json
def chain_id_of(report: dict) -> int:
    return int(report["source"]["registry"].split(":")[1])


@pytest.mark.parametrize("vector", COVERAGE["vectors"], ids=lambda v: v["id"])
def test_a_published_report_has_the_digests_and_errors_the_draft_expects(vector):
    report, request, expected = vector["report"], vector["request"], vector["expected"]
    assert r.subject_digest(request["subject"]) == expected["subjectDigest"] == report["subjectDigest"]
    assert r.report_errors(report, request) == expected["errors"]
    assert (r.report_errors(report, request) == []) is expected["conforms"]
    if expected["conforms"]:
        assert r.report_hash(report) == expected["reportHash"]
        digest = r.eip712_digest(
            report["subjectDigest"], report["contextDigest"], r.report_hash(report), chain_id_of(report)
        )
        assert digest == expected["eip712Digest"]
        assert report["coverage"]["status"] == expected["coverageStatus"]
        assert report["coverage"]["gaps"] == expected["gaps"]


def test_a_context_free_request_has_the_zero_context_digest():
    for vector in COVERAGE["vectors"]:
        assert vector["report"]["contextDigest"] == r.context_digest(vector["request"].get("context"))
    assert r.context_digest(None) == "0x" + "00" * 32
    assert r.context_digest({}) != "0x" + "00" * 32


def test_rewriting_coverage_changes_the_report_hash_so_the_signature_would_not_verify():
    by_id = {v["id"]: v for v in COVERAGE["vectors"]}
    lie = by_id["coverage-is-signed"]
    honest = by_id["wrapped-approve-unsupported-batch"]
    assert r.report_hash(lie["report"]) != r.report_hash(honest["report"])
    assert r.report_hash(honest["report"]) == lie["expected"]["differsFrom"]["reportHash"]


def test_signature_and_detail_are_outside_the_report_hash_and_nothing_else_is():
    report = json.loads(json.dumps(COVERAGE["vectors"][0]["report"]))
    base = r.report_hash(report)
    report["signature"] = "0x" + "11" * 65
    report["detail"] = "https://example.org/fuller-report"
    assert r.report_hash(report) == base
    report["method"] = "something-else"
    assert r.report_hash(report) != base


CONFORMANCE = COVERAGE["conformance"]
WRAPPED_REQUEST = COVERAGE["vectors"][0]["request"]
DECODED_REPORT = COVERAGE["vectors"][2]["report"]
DECODED_REQUEST = COVERAGE["vectors"][2]["request"]


@pytest.mark.parametrize("case", [c for c in CONFORMANCE if "coverage" in c], ids=lambda c: c["id"])
def test_coverage_conformance_cases(case):
    report: dict[str, Any] = {}
    if case["coverage"] != "<absent>":
        report["coverage"] = case["coverage"]
    errors = r.coverage_errors(report, WRAPPED_REQUEST)
    assert errors == case["expected"]["errors"]
    assert (errors == []) is case["expected"]["conforms"]


@pytest.mark.parametrize("case", [c for c in CONFORMANCE if "approval" in c], ids=lambda c: c["id"])
def test_approval_path_conformance_cases(case):
    report = json.loads(json.dumps(DECODED_REPORT))
    report["entities"]["approvals"] = [case["approval"]]
    errors = r.approval_errors(report, DECODED_REQUEST)
    assert errors == case["expected"]["errors"]
    assert (errors == []) is case["expected"]["conforms"]


def test_every_coverage_vector_and_case_was_run():
    assert len(COVERAGE["vectors"]) == 4
    assert len(CONFORMANCE) == 27
    assert COVERAGE["open"] == []


# ---------------------------------------------------------------- on-list: account normalization
@pytest.mark.parametrize("vector", NORMALIZE["vectors"], ids=lambda v: v["id"])
def test_account_normalization(vector):
    got = lists.normalize_account(vector["input"])
    expected = vector["expected"]
    assert got["ok"] is expected["ok"], vector["note"]
    if expected["ok"]:
        assert got["account"] == expected["account"]
        assert got["encoding"] == expected["encoding"]


@pytest.mark.parametrize("vector", NORMALIZE["classification"], ids=lambda v: v["id"])
def test_bip122_encoding_classification(vector):
    assert lists.bip122_encoding(vector["address"]) == vector["expected"]["encoding"]


def test_eip55_matches_the_published_checksum_examples():
    for address in (
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ):
        assert lists.eip55(address.lower()) == address


def test_a_wildcard_chain_is_only_valid_in_a_list_entry():
    assert lists.normalize_account("eip155:*:0x" + "ab" * 20)["ok"] is True
    assert lists.normalize_account("eip155:*:0x" + "ab" * 20, allow_wildcard=False)["ok"] is False


# ---------------------------------------------------------------- on-list: list hash
def hash_of(document):
    value, error = lists.list_hash(document)
    return value, error


@pytest.mark.parametrize("vector", [v for v in LIST_HASH["vectors"] if "documents" in v], ids=lambda v: v["id"])
def test_list_document_hash(vector):
    expected = vector["expected"]
    results = [hash_of(d) for d in vector["documents"]]
    if "hashes" in expected:
        assert [h for h, _ in results] == expected["hashes"]
        assert (results[0][0] == results[1][0]) is expected["sameHash"]
        canon = [lists.canonical_list(d)[0] for d in vector["documents"]]
        assert [r.jcs(c).decode("utf-8") for c in canon] == expected["canonical"]
    else:
        document = vector["documents"][0]
        if expected["conforms"] is False:
            assert lists.conforms(document)[0] is False
            assert hash_of(document)[0] is expected["hash"]
        else:
            assert lists.conforms(document)[0] is True
            assert results[0][0] == expected["hash"]
            if "entries" in expected:
                assert len(lists.canonical_list(document)[0]["entries"]) == expected["entries"]
            if "canonical" in expected:
                assert r.jcs(lists.canonical_list(document)[0]).decode("utf-8") == expected["canonical"]


def test_the_hash_is_over_the_parsed_value_not_the_published_bytes():
    vector = next(v for v in LIST_HASH["vectors"] if v["id"] == "published-bytes-vs-jcs")
    for text, raw in zip(vector["publishedBytes"], vector["expected"]["rawKeccak"]):
        assert "0x" + keccak256(text.encode("utf-8")).hex() == raw
        assert hash_of(json.loads(text))[0] == vector["expected"]["digest"]


def test_the_fbi_list_is_one_attested_document_with_the_published_hash():
    vector = next(v for v in LIST_HASH["vectors"] if v["id"] == "fbi-tradertraitor-2026-10-31")
    document = load("fbi-tradertraitor-2026-10-31.list.json")
    expected = vector["expected"]
    assert lists.conforms(document) == (True, None)
    assert hash_of(document)[0] == expected["hash"]
    entries = lists.canonical_list(document)[0]["entries"]
    assert len(entries) == expected["entries"]
    by_namespace: dict[str, int] = {}
    for entry in entries:
        namespace = entry["account"].split(":")[0]
        by_namespace[namespace] = by_namespace.get(namespace, 0) + 1
    assert by_namespace == expected["byNamespace"]
    assert "attestation" in document and all("evidence" not in e for e in document["entries"])


def test_every_list_hash_vector_was_run():
    assert len(LIST_HASH["vectors"]) == 15
    assert LIST_HASH["open"] == []


# ---------------------------------------------------------------- on-list: matching and findings
@pytest.mark.parametrize("vector", MATCH["vectors"], ids=lambda v: v["id"])
def test_matching(vector):
    document = MATCH["lists"][vector["list"]]
    assert lists.match_value(document, vector["candidate"]) == vector["expected"]["value"]


def test_the_list_hashes_in_the_fixture_are_the_hashes_of_the_lists():
    for name in ("L", "L2", "L3"):
        assert hash_of(MATCH["lists"][name])[0] == MATCH["lists"][name + "_hash"]


@pytest.mark.parametrize("case", MATCH["conformance"], ids=lambda c: c["id"])
def test_list_conformance(case):
    assert lists.conforms(case["document"])[0] is case["expected"]["conforms"]


@pytest.mark.parametrize("case", MATCH["findings"], ids=lambda c: c["id"])
def test_on_list_findings(case):
    scenario = case["scenario"]
    expected = case["expected"]
    sources: dict[str, Any] = {}
    for uri, state in scenario["lists"].items():
        if "published" in state:
            sources[uri] = state["published"]
        elif state.get("fetch") == "fails":
            sources[uri] = None  # the fetch failed
        # a list the source does not read at all is simply absent
    got = []
    for entity in scenario["entities"]:
        for param in scenario["params"]:
            got.append(lists.read_on_list(entity, param, sources))
    assert len(got) == len(expected)
    for finding, want in zip(got, expected):
        assert finding["status"] == want["status"]
        if want["status"] == "read":
            assert finding["value"] == want["value"]
            assert "reason" not in finding
        else:
            assert finding["reason"] == want["reason"]
            assert "value" not in finding


def test_every_on_list_vector_was_run():
    assert len(MATCH["vectors"]) == 15
    assert len(MATCH["findings"]) == 7
    assert len(MATCH["conformance"]) == 7
    assert len(NORMALIZE["vectors"]) == 25
    assert len(NORMALIZE["classification"]) == 10
    assert MATCH["open"] == NORMALIZE["open"] == []
