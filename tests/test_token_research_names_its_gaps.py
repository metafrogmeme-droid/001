"""One address in, one dossier out — and every gap named rather than filled.

`token_dossier` and `presale_claims` were imported by zero non-test modules:
four scorers, seventy-seven tests, and nothing that could answer a question
about a real token. This is the thin orchestration that closes that, and the
tests below are about what it REFUSES to do.

TWO BUGS FOUND BY DRIVING IT, NEITHER VISIBLE BY READING

1. A contract section reading `caution` off ONE readable check out of eleven,
   with `flags: []`, made the dossier announce "⚠ CAUTION (on contract)".
   Nothing had been found — the single check that could be read had passed.
   `token_safety`'s floor is caution-on-no-evidence, and the dossier only
   downgraded a `none` basis, so a `thin` one sailed through wearing a verdict
   that reads as a finding. Coverage was the wrong discriminator; whether the
   verdict is BACKED BY A FLAG is the right one.

2. A source supplying `honeypot` where the scorer reads `honeypot_cannot_sell`
   answered successfully, was credited in the provenance map, and contributed
   nothing. "1 of 1 answered" — a misconfigured integration indistinguishable
   from a working one. My own fixture did it, which is how it surfaced.

RED HERRING, planted in test_a_thin_clean_read_is_not_a_finding: a source that
answers, is credited, and reports a genuinely fine liquidity figure. Everything
about that row looks like a successful check, and it supports no verdict at all.
"""
from __future__ import annotations

import pytest

from bot.core.token_dossier import STAND_DOWN, UNPROVEN
from bot.core.token_research import human_readable, investigate


class _Src:
    def __init__(self, name, features=None, avail=True):
        self.name = name
        self._f = features or {}
        self._a = avail

    def available(self):
        return self._a

    async def fetch(self, chain, address):
        return self._f


# ── a caution nobody backed is not a finding ─────────────────────────

@pytest.mark.asyncio
async def test_a_thin_clean_read_is_not_a_finding():
    """RED HERRING and headline bug. One readable check, it passed, no flags —
    the dossier must not lead with CAUTION."""
    r = await investigate("0x1", sources=[_Src("dex", {"liquidity_usd": 90000.0})])
    assert r["contract"]["verdict"] == "caution", "the scorer's floor is unchanged"
    assert r["contract"]["flags"] == [], "nothing was actually found"
    assert r["dossier"]["verdict"] == UNPROVEN, (
        "an unbacked caution reads as 'we looked and found something'")
    assert "contract" in r["dossier"]["blind_spots"]


@pytest.mark.asyncio
async def test_a_hard_flag_on_a_thin_basis_still_stands_down():
    """The half that keeps the other half honest. A honeypot found on one
    readable check is still a honeypot: coverage caps confidence in safety,
    never in danger."""
    r = await investigate("0x2", sources=[
        _Src("dex", {"liquidity_usd": 90000.0}),
        _Src("hp", {"honeypot_cannot_sell": True})])
    assert r["dossier"]["verdict"] == STAND_DOWN
    assert any("HONEYPOT" in f.upper() for f in r["dossier"]["flags"])


@pytest.mark.asyncio
async def test_the_render_does_not_lead_with_a_warning_it_cannot_support():
    r = await investigate("0x1", sources=[_Src("dex", {"liquidity_usd": 90000.0})])
    text = human_readable(r)
    assert "UNPROVEN" in text
    assert "CAUTION" not in text
    # ...but the basis is still shown, so nobody reads UNPROVEN as "checked".
    assert "1/11" in text


# ── a missing section is a hole, not an omission ─────────────────────

@pytest.mark.asyncio
async def test_the_deployer_section_is_reported_as_unread():
    """No deployer source exists yet. Omitting the section would make a
    two-section report look like a complete one-section report."""
    r = await investigate("0x1", sources=[_Src("dex", {"liquidity_usd": 1.0})])
    assert r["dossier"]["sections"]["deployer"]["read"] is False
    assert "deployer" in r["dossier"]["blind_spots"]
    assert "not read" in human_readable(r)


@pytest.mark.asyncio
async def test_a_healthy_token_does_not_reach_watch_while_a_section_is_unread():
    """`watch` is the ceiling and it means both sections were read. Reaching it
    with the deployer never looked at would claim the deployer checked out."""
    r = await investigate("0x1", sources=[_Src("dex", {"liquidity_usd": 5_000_000.0})])
    assert r["dossier"]["verdict"] != "watch"


# ── provenance and the sources that did not answer ───────────────────

@pytest.mark.asyncio
async def test_a_section_is_only_attributed_when_something_was_read():
    """An attribution on an unread section is a citation for a claim nobody
    made."""
    r = await investigate("0x1", sources=[_Src("paid", avail=False)])
    assert r["dossier"]["sections"]["contract"]["source"] is None


@pytest.mark.asyncio
async def test_an_unconfigured_source_is_named_not_hidden():
    r = await investigate("0x1", sources=[
        _Src("paid", avail=False), _Src("dex", {"liquidity_usd": 1.0})])
    text = human_readable(r)
    assert "paid" in text and "unavailable" in text
    assert "1 of 2 answered" in text


@pytest.mark.asyncio
async def test_nothing_readable_anywhere_is_unproven_not_safe():
    r = await investigate("0x1", sources=[_Src("paid", avail=False)])
    assert r["dossier"]["verdict"] == UNPROVEN
    assert r["contract"]["coverage"]["basis"] == "none"


# ── a field nobody consumes is a misconfiguration, not a success ─────

@pytest.mark.asyncio
async def test_a_supplied_field_no_check_reads_is_surfaced():
    """The second bug, and my own fixture made it. `honeypot` is not a feature
    name — the scorer reads `honeypot_cannot_sell` — so the source answered,
    was credited, and changed nothing."""
    r = await investigate("0x1", sources=[_Src("hp", {"honeypot": True})])
    assert "honeypot" in r["unused_fields"]
    assert "supplied but unused" in human_readable(r)


@pytest.mark.asyncio
async def test_a_correctly_named_field_is_not_flagged_as_unused():
    r = await investigate("0x1", sources=[_Src("hp", {"honeypot_cannot_sell": True})])
    assert r["unused_fields"] == []
    assert "supplied but unused" not in human_readable(r)


@pytest.mark.asyncio
async def test_an_unused_field_does_not_count_toward_coverage():
    """It must not buy credit for a check it never fed."""
    r = await investigate("0x1", sources=[_Src("hp", {"honeypot": True})])
    assert r["contract"]["coverage"]["readable"] == 0
    assert r["contract"]["coverage"]["basis"] == "none"


# ── junk ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_sources_does_not_crash_or_reassure():
    r = await investigate("0x1", sources=[])
    assert r["dossier"]["verdict"] == UNPROVEN
    assert human_readable(r)


def test_render_survives_junk():
    assert human_readable(None)
    assert human_readable({})


# ── the deployer section, once something actually feeds it ───────────
#
# This file previously asserted that the section is ALWAYS unread, because no
# deployer source existed. That is now a statement about configuration rather
# than about the code, and both halves of it need pinning: unconfigured must
# still read as "we never asked", and configured must actually reach the
# scorer instead of quietly staying None.


@pytest.mark.asyncio
async def test_an_unconfigured_deployer_source_is_named_not_silent():
    """No key is "we never asked", which is a fact about our diligence."""
    r = await investigate("0x1", sources=[_Src("dex", {"liquidity_usd": 1.0})],
                          deployer_sources=[_Src("etherscan", avail=False)])
    assert r["deployer"] is None
    assert r["dossier"]["sections"]["deployer"]["read"] is False
    assert "deployer" in r["dossier"]["blind_spots"]
    names = [x["source"] for x in r["deployer_sources"]["results"]]
    assert "etherscan" in names, "an unasked source must still be listed"
    assert r["deployer_sources"]["unavailable"] == 1


@pytest.mark.asyncio
async def test_a_deployer_source_that_answers_reaches_the_scorer():
    """The half-built case: the scorer existed and nothing ever called it."""
    r = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 5_000_000.0})],
        deployer_sources=[_Src("etherscan", {
            "deployer_address": "0xabc", "wallet_age_days": 400.0,
            "prior_deployments": 3.0, "contract_verified": True,
            "deployer_supply_pct": 0.02, "concurrent_launches_24h": 0.0})])
    assert r["deployer"] is not None
    assert r["dossier"]["sections"]["deployer"]["read"] is True
    assert "deployer" not in r["dossier"]["blind_spots"]
    assert r["dossier"]["sections"]["deployer"]["source"] == "etherscan", (
        "a section that was read must carry who read it")


@pytest.mark.asyncio
async def test_an_answered_deployer_still_cannot_certify_a_record():
    """Five facts read and every one benign is still not a clean record.

    `prior_rugged` is unreadable from an explorer, so the fates of those three
    prior contracts are unknown — and unknown fates must not become survivors.
    """
    r = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 5_000_000.0})],
        deployer_sources=[_Src("etherscan", {
            "deployer_address": "0xabc", "wallet_age_days": 400.0,
            "prior_deployments": 3.0, "contract_verified": True,
            "deployer_supply_pct": 0.02, "concurrent_launches_24h": 0.0})])
    assert r["deployer"]["verdict"] == "unproven"
    assert r["deployer"]["outcomes"]["unresolved"] == 3
    # And the render says so rather than printing "3 prior deployments" beside
    # a benign verdict, which reads as three survivors.
    text = human_readable(r)
    assert "0xabc" in text, "a provenance verdict needs its subject"
    assert "unknown fate" in text


@pytest.mark.asyncio
async def test_deployer_output_fields_are_not_flagged_as_unused():
    """`unused_fields` catches a source answering in the wrong dialect.

    `deployer_address` and the outcome counts are real output that no named
    check reads, so a naive set difference reports the working source as
    misconfigured — and a false warning trains the reader to ignore the true
    ones.
    """
    r = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 1.0})],
        deployer_sources=[_Src("etherscan", {
            "deployer_address": "0xabc", "prior_deployments": 2.0,
            "prior_alive": 1.0, "deployments_truncated": True})])
    assert r["unused_fields"] == []
    # …but a genuinely misspelled field is still caught, on this half too.
    r2 = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 1.0})],
        deployer_sources=[_Src("etherscan", {"deployer_address": "0xabc",
                                             "wallet_age": 400.0})])
    assert r2["unused_fields"] == ["wallet_age"]


# ── the fate pass: the column nothing could fill ─────────────────────

class _Feed:
    """A price feed keyed by contract address."""

    def __init__(self, table):
        self.table = table

    async def fetch(self, chain, address):
        return self.table.get(address, {})


_OLD_MS = (1_700_000_000 - 200 * 86400) * 1000.0
_LIVE = {"liquidity_usd": 90_000.0, "volume_24h_usd": 500.0,
         "pair_created_at_ms": _OLD_MS}
_GONE = {"liquidity_usd": 3.0, "volume_24h_usd": 0.0,
         "pair_created_at_ms": _OLD_MS}


def _deployer_src(contracts):
    return _Src("etherscan", {
        "deployer_address": "0xabc", "wallet_age_days": 400.0,
        "prior_deployments": float(len(contracts)), "prior_contracts": contracts,
        "contract_verified": True, "deployer_supply_pct": 0.02,
        "concurrent_launches_24h": 0.0, "funded_by_mixer": False,
        "reused_rug_bytecode": False})


@pytest.mark.asyncio
async def test_a_record_of_survivors_finally_reaches_clean():
    """The first input that could ever produce it.

    Before this, `prior_rugged` was unfillable and `_outcomes_resolved` treated
    its absence as fatal, so the deployer section's ceiling was `unproven` no
    matter how good the record was.
    """
    r = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 5_000_000.0})],
        deployer_sources=[_deployer_src(["0xa", "0xb", "0xc"])],
        fate_source=_Feed({"0xa": _LIVE, "0xb": _LIVE, "0xc": _LIVE}))
    assert r["deployer"]["verdict"] == "clean"
    assert r["deployer"]["outcomes"]["alive"] == 3
    assert r["deployer"]["outcomes"]["unresolved"] == 0


@pytest.mark.asyncio
async def test_contracts_the_feed_never_heard_of_stay_unresolved():
    """A history full of proxies and NFT drops is not a history of failures."""
    r = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 5_000_000.0})],
        deployer_sources=[_deployer_src(["0xa", "0xb", "0xc", "0xd"])],
        fate_source=_Feed({"0xa": _LIVE}))
    o = r["deployer"]["outcomes"]
    assert o["dead"] == 0, "an unindexed contract was scored as a dead market"
    assert o["unresolved"] == 3
    # …and one survivor out of four determined fates cannot certify anybody.
    assert r["deployer"]["verdict"] == "unproven"


@pytest.mark.asyncio
async def test_dead_markets_never_reach_known_bad_through_this_path():
    """A price feed proves a market ended, never that somebody took it."""
    r = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 5_000_000.0})],
        deployer_sources=[_deployer_src(["0xa", "0xb", "0xc"])],
        fate_source=_Feed({"0xa": _GONE, "0xb": _GONE, "0xc": _GONE}))
    assert r["deployer"]["outcomes"]["dead"] == 3
    assert r["deployer"]["verdict"] != "known_bad", (
        "an honest failure was published as theft")
    assert "prior_rugged" not in (r["deployer_sources"]["features"])


@pytest.mark.asyncio
async def test_the_render_names_which_contracts_and_how_many_it_could_not_read():
    r = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 5_000_000.0})],
        deployer_sources=[_deployer_src(["0xa", "0xb", "0xc"])],
        fate_source=_Feed({"0xa": _LIVE, "0xb": _GONE}))
    text = human_readable(r)
    assert "0xa" in text and "alive" in text
    assert "dead" in text
    assert "1 of 3 could not be determined" in text, (
        "a list of determined fates reads as the whole record without this")


@pytest.mark.asyncio
async def test_no_addresses_means_no_fate_pass_and_no_invented_counts():
    r = await investigate(
        "0x1", sources=[_Src("dex", {"liquidity_usd": 1.0})],
        deployer_sources=[_Src("etherscan", {"deployer_address": "0xabc"})])
    assert r["fates"] is None
    assert r["deployer"]["outcomes"]["alive"] is None
    assert r["deployer"]["outcomes"]["dead"] is None


# ── the integrity veto, in shadow ────────────────────────────────────
#
# It was tested, documented as the thing token_safety "unblocks", and imported
# by nothing. Its intended consumer (meme_executor) is unwired too, so
# ENFORCEMENT is a product decision — this is the reading, observe-only.


@pytest.mark.asyncio
async def test_a_clear_verdict_over_nothing_readable_is_never_shown():
    """The trap wiring this naively would have introduced.

    `assess({})` returns the word `clear` — correctly, on its own terms:
    nothing flagged because nothing could be. But `clear` is what a reader
    takes as a clean bill of health, and printing it over zero readable
    features is a confident all-clear manufactured from no data.
    """
    r = await investigate("0x1", sources=[_Src("dex", {})])
    assert r["integrity"]["verdict"] == "clear", "the scorer's own answer is unchanged"
    assert r["integrity"]["checked"] == 0
    text = human_readable(r)
    assert "integrity" not in text.lower(), (
        "a verdict resting on nothing was rendered as a reading")


@pytest.mark.asyncio
async def test_a_verdict_backed_by_a_real_feature_is_shown_with_its_basis():
    r = await investigate("0x1", sources=[
        _Src("dex", {"liquidity_usd": 90000.0}),
        _Src("chain", {"top_holder_pct": 0.92})])
    assert r["integrity"]["checked"] >= 1
    text = human_readable(r)
    assert "integrity (shadow)" in text
    # The coverage travels with it, as everywhere else.
    assert "shapes readable" in text


@pytest.mark.asyncio
async def test_the_veto_never_changes_the_dossier_verdict():
    """Shadow means shadow: it computes, nothing acts on it."""
    clean = [_Src("dex", {"liquidity_usd": 5_000_000.0})]
    baseline = await investigate("0x1", sources=clean)
    flagged = await investigate("0x1", sources=clean + [
        _Src("chain", {"top_holder_pct": 0.99})])
    assert flagged["integrity"]["verdict"] in ("caution", "veto")
    # The contract scorer may react to top_holder_pct on its own — that is its
    # job. What must NOT happen is the veto adding a section or a flag of its
    # own to the composed dossier.
    assert set(flagged["dossier"]["sections"]) == set(baseline["dossier"]["sections"])
    assert "integrity" not in flagged["dossier"]["sections"]


@pytest.mark.asyncio
async def test_the_veto_is_carried_in_shadow_mode():
    r = await investigate("0x1", sources=[_Src("dex", {"liquidity_usd": 1.0})])
    assert r["integrity"]["mode"] == "shadow"
