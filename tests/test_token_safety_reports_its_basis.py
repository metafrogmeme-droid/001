"""`caution` was the same word for "we looked and it's iffy" and "we couldn't look".

`assess_token` was already honest about the verdict: `_MIN_EVIDENCE_FRAC` stops
a mostly-unknown token reaching `safe`, so 0-of-11 readable returns `caution`
rather than a pass. That half was never broken.

What was missing is the BASIS. `caution` at 0-of-11 and `caution` at 10-of-11
are opposite decisions for whoever is holding the wallet, and they printed as
the same word. `human_readable` mentioned the unknown count in a footnote below
the flags; every programmatic consumer — the research dossiers, the MCP tools,
the Guardian veto — reads `verdict` and loses it entirely.

WHY THIS MATTERS MOST FOR THE TOKENS THIS SCANNER IS AIMED AT

A two-hour-old contract has no holder history, no listing age, and often no
liquidity reading. `unknown` is its NORMAL state, not a failure — so the new-token
case is precisely the one where a bare "caution" invites the reader to hear "we
checked, it's borderline". The verdict and the basis have to travel together or
the honest verdict gets read dishonestly.

    ⚠ TOKEN SAFETY: CAUTION [none basis — 0/11 checks readable]
    ⚠ TOKEN SAFETY: CAUTION [broad basis — 9/11 checks readable]

Same verdict. Nobody confuses them now.

"NONE" IS NOT A BAND. It is `readable == 0` exactly. The first draft of
`coverage()` made it the 0.0 edge of the ladder, so 1-of-11 printed "none
basis" — one reading rendering as no readings, which is the conflation this
field exists to prevent, inside the code adding it. Pinned below.
"""
from __future__ import annotations

import pytest

from bot.core.token_safety import (CAUTION, DANGER, SAFE, assess_token,
                                   coverage, human_readable)

FULLY_CLEAN = {
    "honeypot_cannot_sell": False, "mint_authority_active": False,
    "freeze_authority_active": False, "sell_tax_pct": 1, "top_holder_pct": 0.05,
    "ownership_renounced": True, "lp_locked": True, "buy_tax_pct": 1,
    "liquidity_usd": 500_000, "holder_count": 5_000, "listing_age_hours": 9_000,
}


def _checks(readable: int, total: int = 11) -> list[dict]:
    return ([{"status": "ok"}] * readable
            + [{"status": "unknown"}] * (total - readable))


# ── the band ladder ──────────────────────────────────────────────────

@pytest.mark.parametrize("readable,expected", [
    (0, "none"), (1, "thin"), (3, "thin"),
    (6, "partial"), (9, "broad"), (11, "full"),
])
def test_the_basis_word_matches_what_was_read(readable, expected):
    assert coverage(_checks(readable))["basis"] == expected


def test_one_reading_is_thin_not_none():
    """The bug in the first draft, pinned. `none` must mean zero — a single
    reading rendering as no readings is the founding defect of this repository
    wearing the clothes of the field written to prevent it."""
    assert coverage(_checks(1))["basis"] == "thin"
    assert coverage(_checks(0))["basis"] == "none"


def test_no_checks_at_all_has_no_ratio():
    """Nothing was ASKED, which is different from nothing being readable. A
    0.0 ratio would say the second when the truth is the first."""
    empty = coverage([])
    assert empty["ratio"] is None
    assert empty["total"] == 0


def test_nothing_readable_has_a_ratio_of_zero_not_none():
    """The mirror case: eleven checks were asked and none answered. That IS a
    measurement — of the token's unreadability — and must not render as
    'no checks were run'."""
    got = coverage(_checks(0))
    assert got["ratio"] == 0.0
    assert got["total"] == 11


def test_malformed_entries_do_not_count_as_readable():
    """A check that is not a dict has no status; counting it as readable would
    inflate the basis on exactly the malformed input that should deflate it."""
    assert coverage([{"status": "ok"}, None, "junk"])["readable"] == 1


# ── the verdict now carries it ───────────────────────────────────────

def test_an_unreadable_token_says_so_in_the_report():
    report = assess_token({})
    assert report["verdict"] == CAUTION
    assert report["coverage"]["basis"] == "none"
    assert report["coverage"]["readable"] == 0


def test_a_fully_read_clean_token_says_that_too():
    """The other half: a real pass must still be a pass, and must not be
    downgraded by adding this field."""
    report = assess_token(FULLY_CLEAN)
    assert report["verdict"] == SAFE
    assert report["coverage"]["basis"] == "full"


def test_the_headline_cannot_be_read_without_the_basis():
    """On the SAME LINE as the verdict, not a footnote under the flags. A
    reader who stops at the headline — most readers — must not be able to take
    away 'caution' without taking away how much was read."""
    headline = human_readable(assess_token({})).split("\n")[0]
    assert "CAUTION" in headline
    assert "0/11" in headline and "none" in headline


def test_two_cautions_with_different_bases_do_not_read_the_same(monkeypatch):
    blind = human_readable(assess_token({})).split("\n")[0]
    seeing = human_readable(assess_token(
        {**FULLY_CLEAN, "lp_locked": False, "ownership_renounced": False}
    )).split("\n")[0]
    assert "CAUTION" in blind and "CAUTION" in seeing
    assert blind != seeing, (
        "an unreadable token and a well-read borderline one print identically")


# ── what must NOT change ─────────────────────────────────────────────

def test_a_hard_flag_still_forces_danger_on_thin_coverage():
    """Coverage caps CONFIDENCE IN SAFETY, never confidence in danger. You do
    not need broad coverage to know a token cannot be sold — and requiring it
    would turn the one unambiguous reading into a shrug."""
    report = assess_token({"honeypot_cannot_sell": True})
    assert report["verdict"] == DANGER
    assert report["coverage"]["basis"] == "thin"


def test_coverage_did_not_become_the_verdict():
    """Full coverage of BAD readings is still danger. A field named 'coverage'
    invites a future reader to treat high coverage as reassurance."""
    allbad = {**FULLY_CLEAN, "top_holder_pct": 0.9, "mint_authority_active": True}
    report = assess_token(allbad)
    assert report["verdict"] == DANGER
    assert report["coverage"]["basis"] == "full"


def test_the_existing_counts_are_unchanged():
    """`evidence` and `unknowns` are consumed elsewhere; coverage is additive."""
    report = assess_token({"lp_locked": True})
    assert report["evidence"] == 1
    assert report["unknowns"] == 10
    assert report["coverage"]["readable"] == report["evidence"]
