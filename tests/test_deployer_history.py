"""Who shipped this contract, and how their last ones ended.

The detective's core question, and the one a scam cannot cheaply answer. A token
can fake its website, its audit badge, its holder count and its volume. It cannot
retroactively give its deployer two years of contracts that still trade.

Which makes the scoring dangerous in a specific way: the WHOLE VALUE is in
"nothing against them", and there are three completely different ways to arrive
at zero recorded rugs.

    nine contracts, none rugged   →  evidence about the deployer
    no prior contracts at all     →  a first-timer: every honest new project,
                                     and every scammer with a fresh wallet
    history unreadable            →  not a measurement at all

Only the first says anything. A scorer that flattens them hands a burner wallet
the same clean sheet as a veteran, which is the single most dangerous output
this module could produce — so `unproven` is its own verdict and sits ABOVE the
evidence test in the ladder.

TWO BUGS THIS FILE EXISTS BECAUSE OF, both found on the module's first run:

1. `survivors = deployments - rugs` is `losses = len(all) - wins` wearing a
   different hat. An indexer that resolves four of nine contracts must not
   report five survivors. The three counts are read independently and the
   remainder is `unresolved` — a first-class number, never absorbed.

2. Nine prior deployments, zero recorded rugs, four confirmed alive scored
   CLEAN — while five contracts had fates nobody had read, any of which could
   be a rug. `prior_rugged == 0` was standing in for "no rugs" when it meant
   "we did not look at five of them".
"""
from __future__ import annotations


from bot.core.deployer_history import (CLEAN, KNOWN_BAD, SUSPECT, UNPROVEN,
                                       assess_deployer, human_readable,
                                       resolve_outcomes)

# Everything benign except the history, so each test varies one axis.
BENIGN = {
    "wallet_age_days": 800, "contract_verified": True,
    "deployer_supply_pct": 0.03, "funded_by_mixer": False,
    "reused_rug_bytecode": False, "concurrent_launches_24h": 1,
}
VETERAN = {**BENIGN, "prior_deployments": 9, "prior_rugged": 0, "prior_alive": 9}


# ── the three zeros ──────────────────────────────────────────────────

def test_a_veteran_with_a_read_record_is_clean():
    assert assess_deployer(VETERAN)["verdict"] == CLEAN


def test_a_first_timer_is_unproven_not_clean():
    """Every honest new project looks exactly like every scammer's fresh
    wallet. `clean` here would be an endorsement of the indistinguishable."""
    first = {**BENIGN, "prior_deployments": 0}
    r = assess_deployer(first)
    assert r["verdict"] == UNPROVEN
    assert r["coverage"]["basis"] in ("broad", "full"), (
        "the check set was readable — this is unproven for lack of HISTORY, "
        "not for lack of coverage, and the two must not be conflated")


def test_an_unreadable_history_is_unproven_too():
    assert assess_deployer({})["verdict"] == UNPROVEN


def test_the_first_timer_and_the_veteran_do_not_print_the_same():
    first = human_readable(assess_deployer({**BENIGN, "prior_deployments": 0}))
    vet = human_readable(assess_deployer(VETERAN))
    assert first != vet
    assert "nothing to judge them on" in first
    assert "0 rugged" not in first, (
        "a first-timer's clean sheet printed as a score reads as a good one")


def test_an_unreadable_history_says_so_rather_than_showing_zero():
    text = human_readable(assess_deployer({}))
    assert "could not be read" in text


# ── the arithmetic ───────────────────────────────────────────────────

def test_unresolved_contracts_are_counted_not_absorbed():
    o = resolve_outcomes({"prior_deployments": 9, "prior_rugged": 0, "prior_alive": 4})
    assert o["unresolved"] == 5, (
        "five contracts with unknown fates were folded into a column — "
        "`survivors = deployments - rugs` by another name")


def test_a_partially_read_record_cannot_be_clean():
    """The bug this module shipped on its first run. Nine deployments, zero
    recorded rugs, four confirmed alive — five unread fates, any of which could
    be a rug."""
    partial = {**BENIGN, "prior_deployments": 9, "prior_rugged": 0, "prior_alive": 4}
    assert assess_deployer(partial)["verdict"] == UNPROVEN


def test_a_mostly_read_record_still_can_be():
    """The other side: requiring perfection would make `clean` unreachable, and
    a verdict nothing can earn is not a verdict."""
    mostly = {**BENIGN, "prior_deployments": 9, "prior_rugged": 0, "prior_alive": 7}
    assert assess_deployer(mostly)["verdict"] == CLEAN


def test_a_missing_rug_count_is_never_treated_as_zero():
    """The column that matters. A source returning deployments and survivors
    but no rug count has said nothing about rugs, and the subtraction would
    otherwise invent a zero."""
    no_rugs_field = {**BENIGN, "prior_deployments": 9, "prior_alive": 9}
    assert assess_deployer(no_rugs_field)["verdict"] == UNPROVEN


def test_an_inconsistent_source_is_not_silently_clamped():
    """More outcomes than deployments means the inputs disagree. Clamping to
    zero would hide a broken feed behind a plausible number."""
    o = resolve_outcomes({"prior_deployments": 2, "prior_rugged": 3, "prior_alive": 4})
    assert o["unresolved"] < 0


def test_absent_counts_stay_none():
    o = resolve_outcomes({})
    assert o["total"] is None and o["rugged"] is None and o["unresolved"] is None


# ── the flags ────────────────────────────────────────────────────────

def test_one_prior_rug_is_disqualifying_on_its_own():
    """Not a weight. Somebody who has taken money once is not offset by four
    contracts that happened to survive, and an averaging model says otherwise."""
    r = assess_deployer({**BENIGN, "prior_deployments": 5,
                         "prior_rugged": 1, "prior_alive": 4})
    assert r["verdict"] == KNOWN_BAD


def test_known_rug_bytecode_is_disqualifying():
    r = assess_deployer({**VETERAN, "reused_rug_bytecode": True})
    assert r["verdict"] == KNOWN_BAD, (
        "a clean history does not launder a contract that IS a known rug "
        "template")


def test_soft_flags_accumulate_to_suspect():
    r = assess_deployer({**VETERAN, "funded_by_mixer": True})
    assert r["verdict"] in (SUSPECT, KNOWN_BAD)
    assert any("mixer" in f for f in r["flags"])


def test_a_burner_wallet_reaches_known_bad_without_any_history():
    """The realistic scam shape: no track record AND every live signal bad.
    `unproven` must not shelter it — the ladder puts hard flags first."""
    r = assess_deployer({
        "prior_deployments": 0, "wallet_age_days": 2, "funded_by_mixer": True,
        "contract_verified": False, "deployer_supply_pct": 0.6,
        "reused_rug_bytecode": False, "concurrent_launches_24h": 5,
    })
    assert r["verdict"] == KNOWN_BAD


def test_coverage_travels_with_the_verdict():
    # 8 checks since `prior_dead_ratio` joined them. Asserted as a computed
    # count rather than a literal: the number is not the property — "every
    # check is accounted for in the coverage line" is.
    report = assess_deployer({})
    text = human_readable(report)
    assert f"0/{len(report['checks'])}" in text and "none" in text


def test_there_is_no_positive_verdict():
    """Detection only, like token_safety. `clean` is the ceiling and it means
    'nothing found against them', never 'endorsed' — so no input may produce a
    word that reads as a recommendation."""
    for facts in (VETERAN, {**BENIGN, "prior_deployments": 50,
                            "prior_rugged": 0, "prior_alive": 50}):
        assert assess_deployer(facts)["verdict"] in (
            CLEAN, UNPROVEN, SUSPECT, KNOWN_BAD)


def test_its_verdicts_do_not_collide_with_token_safetys():
    """Two different judgements — a contract's mechanics and a person's record.
    Sharing vocabulary would invite a caller to compare or merge them."""
    from bot.core import token_safety
    ours = {CLEAN, UNPROVEN, SUSPECT, KNOWN_BAD}
    theirs = {token_safety.SAFE, token_safety.CAUTION, token_safety.DANGER}
    assert not (ours & theirs)
