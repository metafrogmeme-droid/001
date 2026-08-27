"""Two honest scorers, and all the danger in the join between them.

`token_safety` scores a contract's mechanics. `deployer_history` scores the
person who shipped it. Each is careful on its own. Composing them into one
answer has three classic ways to go wrong, and this file exists to hold them
shut.

1. AVERAGING TWO VERDICTS. A `known_bad` deployer and a `safe` contract do not
   make a `caution` token. Mechanics and provenance are not commensurable — a
   technically clean contract shipped by somebody with a prior rug is a stand-
   down, and averaging turns the strongest signal available into a middling one.

2. AVERAGING TWO COVERAGES. "60% covered", over a dossier where the contract was
   fully read and the deployer was invisible, describes neither. The reader's
   question is not "how much do you know" but "which part are you blind to".

3. GOOD NEWS CANCELLING BAD. A `clean` deployer cannot lift a `danger` contract
   and a `safe` contract cannot lift a `known_bad` deployer.

AND THE ONE THAT ONLY APPEARS IN THE JOIN

`token_safety` returns `caution` for a token it could not read at all — correct
there, since `caution` is its floor and it must never say `safe` on no evidence.
Mapped straight through, that made a dossier over a brand-new token announce
"CAUTION (on contract)", which reads as "we looked and found something
concerning" when the truth is "nothing answered". Both scorers were honest and
the composition was not. Caught on this module's first run, pinned below.
"""
from __future__ import annotations


from bot.core import deployer_history as dh
from bot.core import token_safety as ts
from bot.core.token_dossier import (CAUTION, STAND_DOWN, UNPROVEN, WATCH,
                                    compose, human_readable)

BENIGN_DEP = {
    "wallet_age_days": 800, "contract_verified": True,
    "deployer_supply_pct": 0.03, "funded_by_mixer": False,
    "reused_rug_bytecode": False, "concurrent_launches_24h": 1,
}
VETERAN = {**BENIGN_DEP, "prior_deployments": 9, "prior_rugged": 0, "prior_alive": 9}
RUGGER = {**VETERAN, "prior_rugged": 1}
CLEAN_TOKEN = {
    "honeypot_cannot_sell": False, "mint_authority_active": False,
    "freeze_authority_active": False, "sell_tax_pct": 1, "top_holder_pct": 0.05,
    "ownership_renounced": True, "lp_locked": True, "buy_tax_pct": 1,
    "liquidity_usd": 500_000, "holder_count": 5_000, "listing_age_hours": 9_000,
}
SOURCES = {"contract": "rpc+explorer", "deployer": "indexer"}


def _dossier(token=None, deployer=None):
    return compose(ts.assess_token(token) if token is not None else None,
                   dh.assess_deployer(deployer) if deployer is not None else None,
                   SOURCES)


# ── the join must not average ────────────────────────────────────────

def test_a_rugger_deployer_stands_the_whole_thing_down():
    """Even with a spotless contract. A technically clean token from somebody
    who has taken money before is not a middling result."""
    d = _dossier(CLEAN_TOKEN, RUGGER)
    assert d["verdict"] == STAND_DOWN
    assert d["driven_by"] == ["deployer"]


def test_a_honeypot_stands_it_down_however_clean_the_deployer():
    d = _dossier({"honeypot_cannot_sell": True}, VETERAN)
    assert d["verdict"] == STAND_DOWN
    assert d["driven_by"] == ["contract"]


def test_good_news_never_cancels_bad():
    """The explicit statement of (3). If either half is a stand-down, so is the
    dossier — a `safe` beside it changes nothing."""
    assert _dossier(CLEAN_TOKEN, RUGGER)["verdict"] == STAND_DOWN
    assert _dossier({"honeypot_cannot_sell": True}, VETERAN)["verdict"] == STAND_DOWN


def test_the_reader_is_told_which_half_did_it():
    """A bare verdict over two sections sends somebody hunting through both."""
    assert _dossier(CLEAN_TOKEN, RUGGER)["driven_by"] == ["deployer"]


def test_both_halves_can_share_the_blame():
    d = _dossier({"honeypot_cannot_sell": True}, RUGGER)
    assert set(d["driven_by"]) == {"contract", "deployer"}


# ── the bug that only exists in the join ─────────────────────────────

def test_an_unreadable_token_is_unproven_not_caution():
    """`token_safety` floors at `caution` on no evidence, which is right there
    and wrong here: a dossier saying CAUTION reads as "we found something"."""
    d = _dossier({}, {})
    assert d["verdict"] == UNPROVEN, (
        "a token where nothing could be read reported as a concern")


def test_but_a_hard_finding_on_thin_coverage_still_stands_down():
    """Coverage caps confidence in safety, never in danger — the scorers' own
    rule, applied again where they meet. One readable check saying HONEYPOT is
    still a honeypot."""
    d = _dossier({"honeypot_cannot_sell": True}, {})
    assert d["verdict"] == STAND_DOWN


def test_a_stand_down_survives_even_a_zero_coverage_section():
    """The guard the test above does NOT reach.

    `{"honeypot_cannot_sell": True}` gives a THIN basis (1 of 11 readable), so
    it never exercised the `basis == "none"` branch — a mutation making coverage
    suppress hard findings too passed the whole file. `compose` takes plain
    dicts, so the shape is constructed directly here.

    Today's two scorers cannot emit `danger` on zero readings, because a hard
    flag needs a reading to raise it. That makes this defensive rather than
    reachable — and defensive is the right posture for a composer that accepts
    reports from scorers not yet written, in the one direction where being
    wrong means suppressing a stand-down.
    """
    synthetic = {"verdict": ts.DANGER, "flags": ["planted"],
                 "coverage": {"readable": 0, "total": 9, "ratio": 0.0,
                              "basis": "none"}}
    d = compose(synthetic, dh.assess_deployer(VETERAN), SOURCES)
    assert d["verdict"] == STAND_DOWN, (
        "a zero-coverage section suppressed a hard finding — coverage must cap "
        "confidence in safety, never in danger")


def test_soft_flags_on_real_coverage_still_reach_caution():
    """The mirror: the fix must not swallow genuine mild concern. This token
    was READ and came back untrustworthy-ish."""
    iffy = {**CLEAN_TOKEN, "lp_locked": False, "ownership_renounced": False}
    d = _dossier(iffy, VETERAN)
    assert d["verdict"] == CAUTION


# ── coverage is never merged ─────────────────────────────────────────

def test_coverage_is_reported_per_section():
    d = _dossier(CLEAN_TOKEN, {})
    assert d["sections"]["contract"]["coverage"]["basis"] == "full"
    assert d["sections"]["deployer"]["coverage"]["basis"] == "none"


def test_there_is_no_single_blended_coverage_number():
    """A blended number over a fully-read contract and an invisible deployer
    describes neither, and invites exactly the averaging this refuses."""
    d = _dossier(CLEAN_TOKEN, {})
    assert "coverage" not in d
    assert "score" not in d


def test_the_blind_spots_are_named():
    d = _dossier(CLEAN_TOKEN, {})
    assert d["blind_spots"] == ["deployer"]
    assert d["unreadable"] == 1


# ── a missing section is a hole, not an omission ─────────────────────

def test_an_absent_section_is_recorded_rather_than_skipped():
    """A dossier that silently dropped the deployer section would read as a
    complete report that happened to have nothing to say about the deployer."""
    d = _dossier(CLEAN_TOKEN, None)
    assert d["sections"]["deployer"]["read"] is False
    assert "deployer" in d["blind_spots"]


def test_an_absent_section_prevents_the_top_verdict():
    """`watch` means both halves were read and neither objected. One unread
    half cannot produce it."""
    assert _dossier(CLEAN_TOKEN, None)["verdict"] == UNPROVEN


def test_the_render_says_a_section_was_not_read():
    text = human_readable(_dossier(CLEAN_TOKEN, None))
    assert "not read" in text


# ── the ceiling is not a recommendation ──────────────────────────────

def test_the_best_case_is_watch_not_safe():
    d = _dossier(CLEAN_TOKEN, VETERAN)
    assert d["verdict"] == WATCH


def test_the_render_refuses_to_let_watch_read_as_approval():
    """Absence of flags implies endorsement unless something says otherwise,
    and for a token with no history that implication is the product risk."""
    text = human_readable(_dossier(CLEAN_TOKEN, VETERAN))
    assert "not proven safe" in text


def test_no_dossier_verdict_reads_as_a_buy():
    for verdict in (WATCH, UNPROVEN, CAUTION, STAND_DOWN):
        assert verdict not in ("safe", "buy", "good", "ok")


def test_the_vocabularies_stay_distinct():
    """Three judgements — contract, deployer, whole token. Shared words would
    let a caller mistake one section's verdict for the dossier's."""
    ours = {WATCH, UNPROVEN, CAUTION, STAND_DOWN}
    tok = {ts.SAFE, ts.DANGER}
    dep = {dh.CLEAN, dh.KNOWN_BAD, dh.SUSPECT}
    assert not (ours & tok)
    assert not (ours & dep)


# ── provenance ───────────────────────────────────────────────────────

def test_every_read_section_names_its_source():
    d = _dossier(CLEAN_TOKEN, VETERAN)
    assert d["sections"]["contract"]["source"] == "rpc+explorer"
    assert d["sections"]["deployer"]["source"] == "indexer"


def test_flags_stay_attributed_to_their_section():
    """Flattened for display, but a reader must still know which half raised
    each one — 'ownership NOT renounced' and 'prior rug' need different
    reactions."""
    d = _dossier({**CLEAN_TOKEN, "lp_locked": False}, RUGGER)
    assert any(f.startswith("contract:") for f in d["flags"])
    assert any(f.startswith("deployer:") for f in d["flags"])


def test_it_survives_being_handed_nothing_at_all():
    d = compose(None, None, None)
    assert d["verdict"] == UNPROVEN
    assert d["unreadable"] == 2
    assert human_readable(d)
