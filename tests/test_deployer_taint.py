"""The last two facts, and why an empty list must answer `unknown`.

    funded_by_mixer        weight 2.0
    reused_rug_bytecode    HARD — one match makes a deployer known_bad alone

Neither is computable; both are lookups against curated reference data, which
is why they stayed unread while every other fact got a source. The mechanism is
small. The *list* is the work, and the list is an accusation.

THE RULE THIS FILE EXISTS FOR

`funded_by_mixer: False` is a PASSING check — it adds to the evidence that lets
a deployer be certified. Returning it from a list with nothing in it would mean
"we checked and found no mixer" when the truth is "we checked nothing". That is
CLAUDE.md's opening sentence wearing a denylist, and it is the direction that
does the quiet damage: a false `True` gets argued with, a false `False` gets
believed.
"""
from __future__ import annotations

import json

import pytest

from bot.core import deployer_taint as dt
from bot.core.deployer_history import assess_deployer

MIXER = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
HASH_A = "a" * 64
HASH_B = "b" * 64


def mixers(*addrs):
    return {"entries": {a: {"address": a, "source": "OFAC"} for a in addrs},
            "source": "test", "retrieved": "2026-01-01", "rejected": []}


def corpus(*hashes):
    return {"entries": {h: {"hash": h, "example": "0xdead", "note": "hidden mint"}
                        for h in hashes},
            "source": "test", "retrieved": "2026-01-01", "rejected": []}


EMPTY = {"entries": {}, "source": None, "retrieved": None, "rejected": []}


# ── the empty-list rule ───────────────────────────────────────────────────

def test_an_empty_mixer_list_answers_unknown_not_clean():
    got = dt.check_funding([OTHER], mixers=EMPTY)
    assert got["value"] is None, (
        "False here means 'we checked and found nothing' — an empty list "
        "checked nothing, and False is the answer that gets believed")


def test_an_empty_corpus_answers_unknown_not_clean():
    got = dt.check_bytecode(HASH_A, corpus=EMPTY)
    assert got["value"] is None


def test_the_shipped_lists_are_empty_and_therefore_silent():
    """What actually ships, asserted rather than assumed.

    Populating these means naming specific addresses as sanctioned mixers and
    specific bytecode as rug templates. That has to come from an authoritative
    source with a retrieval date, so they ship empty and both checks stay
    honestly unknown until somebody does it properly.
    """
    for path, kind in ((dt.MIXER_LIST, "mixer"), (dt.RUG_BYTECODE_LIST, "bytecode")):
        loaded = dt.load_list(path, kind)
        assert loaded["entries"] == {}, f"{path} has entries — update this test"
    assert dt.taint_facts([OTHER], HASH_A) == {}, (
        "with nothing loaded, neither fact may be supplied at all")


def test_a_missing_file_is_an_empty_list_not_a_crash():
    loaded = dt.load_list("/nonexistent/nope.json", "mixer")
    assert loaded["entries"] == {}
    # …and a broken reference file must not take the dossier down with it.
    assert dt.check_funding([MIXER], mixers=loaded)["value"] is None


# ── provenance is mandatory ───────────────────────────────────────────────

def test_a_list_without_provenance_is_refused_whole(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"entries": [{"address": MIXER, "source": "x"}]}))
    loaded = dt.load_list(str(p), "mixer")
    assert loaded["entries"] == {}, (
        "a denylist with no record of where it came from cannot be audited "
        "by the person it accuses")


def test_an_entry_without_its_own_source_is_dropped(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "source": "OFAC", "retrieved": "2026-01-01",
        "entries": [{"address": MIXER}, {"address": OTHER, "source": "OFAC SDN"}]}))
    loaded = dt.load_list(str(p), "mixer")
    assert list(loaded["entries"]) == [OTHER]
    assert loaded["rejected"], "a dropped entry must be reported, not silent"


def test_junk_addresses_never_enter_the_list(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "source": "x", "retrieved": "y",
        "entries": [{"address": "not-an-address", "source": "s"},
                    {"address": "0x123", "source": "s"},
                    "a string", {"address": MIXER.upper(), "source": "s"}]}))
    loaded = dt.load_list(str(p), "mixer")
    # The valid one normalises to lowercase; the rest are refused.
    assert list(loaded["entries"]) == [MIXER]


# ── the hard check has a higher bar, on purpose ───────────────────────────

@pytest.mark.parametrize("missing", ["example", "note"])
def test_a_corpus_entry_without_an_audit_trail_is_refused(missing, tmp_path):
    """One match here is `known_bad` alone, so a human must have vouched.

    Nothing in code can look at a hash and tell whether it is a rug template or
    a stock OpenZeppelin ERC-20 — and the stock template is shared by thousands
    of honest tokens. So the loader demands the reasoning in writing.
    """
    entry = {"hash": HASH_A, "example": "0xdead", "note": "hidden mint"}
    del entry[missing]
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"source": "x", "retrieved": "y", "entries": [entry]}))
    loaded = dt.load_list(str(p), "bytecode")
    assert loaded["entries"] == {}
    assert missing in loaded["rejected"][0] or "no " in loaded["rejected"][0]


def test_a_complete_corpus_entry_is_accepted_and_matches():
    got = dt.check_bytecode(HASH_A, corpus=corpus(HASH_A))
    assert got["value"] is True and got["matched"] == HASH_A
    assert dt.check_bytecode(HASH_B, corpus=corpus(HASH_A))["value"] is False


# ── matching, once a list exists ──────────────────────────────────────────

def test_a_listed_funder_is_found_regardless_of_case():
    assert dt.check_funding([OTHER, MIXER.upper()], mixers=mixers(MIXER))["value"] is True


def test_an_unlisted_funder_over_a_real_list_is_a_genuine_false():
    got = dt.check_funding([OTHER], mixers=mixers(MIXER))
    assert got["value"] is False and got["list_size"] == 1


def test_no_funding_history_is_unknown_not_clean():
    # Every deployer was funded by something, so an empty history is a read we
    # did not get — not a wallet that was never funded.
    for empty in ([], None):
        assert dt.check_funding(empty, mixers=mixers(MIXER))["value"] is None


def test_an_unreadable_code_hash_leaves_the_hard_check_unknown():
    for junk in (None, "", "0x", "not-a-hash", 12345):
        assert dt.check_bytecode(junk, corpus=corpus(HASH_A))["value"] is None


# ── bytecode normalisation ────────────────────────────────────────────────

def test_metadata_is_stripped_so_a_recompile_still_matches():
    """Same logic, different solc path — identical code, different tail.

    Hashing raw bytecode would miss every match that matters.
    """
    body = "60806040" * 4
    # 11 bytes of "metadata" + the 2-byte length suffix (0x000b).
    a = body + "aa" * 11 + "000b"
    b = body + "bb" * 11 + "000b"
    assert dt.strip_metadata(a) == dt.strip_metadata(b) == body
    assert dt.bytecode_hash(a) == dt.bytecode_hash(b)


def test_code_without_a_metadata_blob_is_left_alone():
    # A declared length that cannot fit means there is no blob (vyper, asm).
    code = "60806040ffff"
    assert dt.strip_metadata(code) == code


def test_unreadable_code_hashes_to_nothing_rather_than_to_something():
    # '' would hash to a real digest that could collide with a corpus entry
    # meaning "no code".
    for junk in (None, "", "0x", "zzzz", "abc", 42):
        assert dt.bytecode_hash(junk) is None


def test_the_0x_prefix_and_case_do_not_change_the_hash():
    assert dt.bytecode_hash("0xAABBCCDDffff") == dt.bytecode_hash("aabbccddffff")


# ── what the scorer does with them ────────────────────────────────────────

BASE = {"prior_deployments": 3.0, "prior_alive": 3.0, "prior_dead": 0.0,
        "contract_verified": True, "wallet_age_days": 400.0,
        "deployer_supply_pct": 0.02, "concurrent_launches_24h": 0.0}


def test_an_absent_fact_is_unknown_and_not_a_pass():
    r = assess_deployer(BASE)
    for name in ("funded_by_mixer", "reused_rug_bytecode"):
        c = next(c for c in r["checks"] if c["name"] == name)
        assert c["status"] == "unknown", f"{name} scored as a pass with no list"


def test_a_bytecode_match_is_disqualifying_on_its_own():
    r = assess_deployer({**BASE, "reused_rug_bytecode": True})
    assert r["verdict"] == "known_bad", (
        "this is exactly why the corpus needs an example and a note per entry")


def test_taint_facts_omits_rather_than_guessing():
    # Absent, not present-and-None: the feature map must stay honest about
    # what a source actually supplied.
    assert dt.taint_facts([OTHER], HASH_A, mixers=EMPTY, corpus=EMPTY) == {}
    got = dt.taint_facts([MIXER], HASH_A, mixers=mixers(MIXER), corpus=corpus(HASH_B))
    assert got == {"funded_by_mixer": True, "reused_rug_bytecode": False}
