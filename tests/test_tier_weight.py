"""The tier weight, and the two things the design document got backwards.

`docs/TIER_MODEL.md` specifies `√(staked) × lock_multiplier × standing` and a
five-phase plan to ship it. Implementing the plan turned up two claims that a
reader would act on and that are not true, and both are pinned here rather than
only corrected in prose — a document is not a gate.

1. PHASE A SHIPS NOTHING. The plan said to "replace threshold comparison with
   `√(staked)`, keep absolute bands initially… Zero migration," under a heading
   promising each phase ships value alone, and §11 said A+B "already fixes
   plutocracy". `√` is monotonic, so `√x ≥ √t` is the same predicate as `x ≥ t`
   at every holding — the verdicts are identical everywhere. That identity is
   genuinely useful, because it is what makes the flag safe to switch on, but it
   is not a fix for plutocracy. Plutocracy is a claim about SHARES OF A TOTAL,
   and no absolute band computes a share; only the relative bands of Phase D do.

2. PHASE B IS MOSTLY ALREADY SHIPPED, AT DIFFERENT OFFSETS. It said to add
   `lock_until: i64` keeping `amount@72` and appending at 89. The real layout has
   `amount@73` and already carries `unlock_at@89`. Following the instruction
   would have moved `amount` by one byte and broken the reader — the exact
   failure the instruction congratulated itself on avoiding. Asserted here
   against the constants the Rust source and the Python gate share.

The concavity test is the one worth reading twice. `√` is sub-additive, which is
the sybil defence ACROSS accounts and a trap WITHIN one: a wallet with three
stake records must be scored `√(total)`, never `√(r1)+√(r2)+√(r3)`, or holding
your stake in more pieces manufactures weight out of nothing.
"""

from __future__ import annotations

import math

import pytest

from bot.token import tier_gate as tg
from bot.token.tier_weight import (LOCK_MAX_MULTIPLIER, SECONDS_PER_MONTH,
                                   STANDING_MAX, STANDING_MIN, STANDING_NEUTRAL,
                                   clamp_standing, lock_multiplier,
                                   stake_weight_inputs, tier_for_weight,
                                   tier_weight, weight_of_threshold)

MONTH = SECONDS_PER_MONTH


# ── the claim the plan made, and the arithmetic ──────────────────────────

class TestSqrtWithAbsoluteBandsIsTheIdentity:
    @pytest.mark.parametrize("staked", [0, 1, 9_999, 10_000, 10_001, 99_999,
                                        100_000, 250_000, 2_000_000])
    def test_it_returns_exactly_what_the_linear_bands_return(self, staked, monkeypatch):
        monkeypatch.setenv("RCLAW_TIER_WEIGHT_ENABLED", "true")
        linear = tg.tier_for_balance(staked)
        weighted = tg.tier_for_stake(staked)          # neutral lock, neutral standing
        assert weighted == linear, (
            f"at {staked} tokens the weight path says {weighted} and the linear "
            f"path says {linear}. They must agree at neutral inputs — that is "
            "what makes RCLAW_TIER_WEIGHT_ENABLED safe to turn on, and it is "
            "also why Phase A on its own fixes nothing.")

    def test_so_the_flag_alone_changes_no_verdict(self, monkeypatch):
        # Stated as the property rather than the table: flipping the flag with
        # no lock and no standing must be invisible.
        for staked in (5_000, 25_000, 250_000):
            monkeypatch.delenv("RCLAW_TIER_WEIGHT_ENABLED", raising=False)
            off = tg.tier_for_stake(staked)
            monkeypatch.setenv("RCLAW_TIER_WEIGHT_ENABLED", "true")
            on = tg.tier_for_stake(staked)
            assert off == on, f"{staked}: {off} -> {on}"

    def test_a_LOCK_is_what_actually_moves_a_verdict(self, monkeypatch):
        monkeypatch.setenv("RCLAW_TIER_WEIGHT_ENABLED", "true")
        # 9,000 tokens is below the 10,000 pro floor...
        assert tg.tier_for_stake(9_000) == "basic"
        # ...and a long lock is what buys the difference, not the root.
        assert tg.tier_for_stake(9_000, lock_seconds_remaining=24 * MONTH) == "pro"

    def test_and_standing_is_the_other_one(self, monkeypatch):
        monkeypatch.setenv("RCLAW_TIER_WEIGHT_ENABLED", "true")
        assert tg.tier_for_stake(9_000, standing=STANDING_MAX) == "pro"
        # Symmetrically, poor standing demotes a holding that would otherwise pass.
        assert tg.tier_for_stake(12_000, standing=STANDING_MIN) == "basic"


# ── concavity, in both directions ────────────────────────────────────────

class TestConcavity:
    def test_splitting_ACROSS_accounts_lowers_each_ones_weight(self):
        # The sybil defence in TIER_MODEL.md §10, as arithmetic.
        whole = tier_weight(100_000)
        half = tier_weight(50_000)
        assert half < whole
        assert math.isclose(whole, 316.227766, rel_tol=1e-6)
        assert math.isclose(half, 223.606797, rel_tol=1e-6)

    def test_and_the_SUM_across_them_rises_which_is_why_weights_never_merge(self):
        # The caveat the document states plainly, kept honest here: 2·√50k >
        # √100k. Harmless only because nothing ever adds weight across accounts.
        assert 2 * tier_weight(50_000) > tier_weight(100_000)

    def test_WITHIN_one_wallet_the_total_is_formed_before_the_root(self):
        # The trap the other direction. Three records of 10k must score as
        # √30,000 = 173.2, not √10,000 × 3 = 300 — otherwise opening extra stake
        # records against the same program manufactures weight from nothing.
        folded = stake_weight_inputs([(10_000, 0), (10_000, 0), (10_000, 0)])
        assert folded is not None
        total, lock = folded
        assert total == 30_000
        assert lock == 0
        assert math.isclose(tier_weight(total), math.sqrt(30_000), rel_tol=1e-9)
        assert tier_weight(total) < 3 * tier_weight(10_000)

    def test_the_folded_lock_is_weighted_by_AMOUNT_not_by_record_count(self):
        # 1,000 locked for a day and 1,000,000 locked for a year are not two
        # equal opinions about how committed this wallet is.
        folded = stake_weight_inputs([(1_000, 1 * 86400), (1_000_000, 365 * 86400)])
        assert folded is not None
        total, lock = folded
        assert total == 1_001_000
        assert lock > 300 * 86400, f"the tiny short record dominated: {lock / 86400:.1f}d"


# ── the lock multiplier ──────────────────────────────────────────────────

class TestLockMultiplier:
    @pytest.mark.parametrize("months,expected", [
        (0, 1.000), (3, 1.1875), (6, 1.375), (12, 1.750), (24, 2.500),
    ])
    def test_it_matches_the_published_table(self, months, expected):
        assert math.isclose(lock_multiplier(months * MONTH), expected, rel_tol=1e-9)

    def test_it_is_capped_so_a_longer_lock_cannot_run_away(self):
        assert lock_multiplier(120 * MONTH) == LOCK_MAX_MULTIPLIER

    @pytest.mark.parametrize("bad", [None, -1, -99999, "soon", float("nan"), float("inf")])
    def test_an_absent_expired_or_unreadable_lock_buys_NO_premium(self, bad):
        # The safe direction: a lock nobody could read is not a lock.
        assert lock_multiplier(bad) == 1.0

    def test_the_default_30_day_lock_buys_almost_nothing(self):
        # A plain `stake` still writes LOCKUP_SECONDS, so the default position
        # sits at the bottom of the range. This used to be the ONLY reachable
        # value, and the 2.5x in the design table was decoration.
        assert lock_multiplier(1 * MONTH) < 1.07

    def test_the_ceiling_IS_reachable_now_and_agrees_with_the_program(self):
        # `stake_for` accepts a caller-chosen duration bounded by
        # MAX_LOCK_SECONDS. The two constants have to be the same number, or the
        # model advertises a multiplier the chain refuses to mint — which is the
        # state this replaced, one level up.
        from pathlib import Path
        src = Path("programs/rclaw_staking/src/lib.rs").read_text(encoding="utf-8")
        assert "pub const MAX_LOCK_SECONDS: i64 = 24 * 30 * 24 * 60 * 60;" in src, (
            "the program's lock ceiling moved or vanished; LOCK_CEILING_MONTHS "
            "in tier_weight.py is what it has to match")
        assert lock_multiplier(24 * MONTH) == LOCK_MAX_MULTIPLIER
        assert 24 * MONTH == 24 * 30 * 24 * 60 * 60


# ── standing ─────────────────────────────────────────────────────────────

class TestStanding:
    def test_no_history_is_the_neutral_midpoint_not_zero(self):
        assert clamp_standing(None) == STANDING_NEUTRAL == 1.0

    @pytest.mark.parametrize("raw,expected", [
        (-5, STANDING_MIN), (0.0, STANDING_MIN), (0.2, 0.2),
        (1.4, 1.4), (2.0, 2.0), (99, STANDING_MAX),
    ])
    def test_it_is_clamped_to_the_published_range(self, raw, expected):
        assert clamp_standing(raw) == expected

    def test_a_measured_ZERO_clamps_to_the_FLOOR_and_does_not_zero_the_weight(self):
        # 0.0 is a real reading of an account with no discipline at all. It must
        # land on the floor, not annihilate the stake — a zero weight would rank
        # a bad actor identically to someone holding nothing.
        assert tier_weight(100_000, standing=0.0) > 0
        assert math.isclose(tier_weight(100_000, standing=0.0),
                            math.sqrt(100_000) * STANDING_MIN, rel_tol=1e-9)


# ── unreadable is never zero ─────────────────────────────────────────────

class TestUnreadableIsNeverZero:
    @pytest.mark.parametrize("bad", [None, "lots", float("nan"), float("inf"), -1])
    def test_an_unreadable_stake_has_NO_weight_rather_than_a_weight_of_zero(self, bad):
        assert tier_weight(bad) is None, (
            "a stake we could not read came back as a number. Zero is a real "
            "verdict about an empty position; returning it for a failed read "
            "demotes a holder on evidence nobody has")

    def test_a_measured_zero_IS_zero(self):
        assert tier_weight(0) == 0.0
        assert tier_weight(0.0) == 0.0

    def test_an_unreadable_weight_or_band_is_not_reported_as_basic(self):
        assert tier_for_weight(None, 1.0, 2.0) is None
        assert tier_for_weight(1.5, None, 2.0) is None
        assert tier_for_weight(1.5, 1.0, None) is None
        assert tier_for_weight(0.0, 1.0, 2.0) == "basic"   # a real, low weight

    def test_weight_of_threshold_rejects_nonsense_rather_than_guessing(self):
        assert weight_of_threshold(None) is None
        assert weight_of_threshold(-1) is None
        assert math.isclose(weight_of_threshold(10_000), 100.0, rel_tol=1e-9)

    def test_an_unreadable_record_fails_the_whole_fold(self):
        # Dropping the record we could not parse would under-report the stake
        # and deny a tier the holder actually has. A partial total is a wrong
        # total, so say so instead.
        assert stake_weight_inputs([(10_000, 0), (None, 0)]) is None
        assert stake_weight_inputs([(10_000, 0), ("x", 0)]) is None
        assert stake_weight_inputs([(-5, 0)]) is None
        assert stake_weight_inputs([]) is None
        assert stake_weight_inputs([(10_000,)]) is None


# ── the gate wiring ──────────────────────────────────────────────────────

class TestItIsActuallyWiredIn:
    def test_the_gate_imports_and_calls_the_weight_module(self):
        # #58: a scorer nothing calls is indistinguishable from one that does
        # not work, and this repo has a ratchet for it. Asked of the module
        # object rather than the source text.
        assert tg.tier_weight_mod.tier_weight is tier_weight
        assert callable(tg.staked_profile)
        assert callable(tg.tier_for_stake)

    def test_the_flag_is_OFF_by_default(self, monkeypatch):
        monkeypatch.delenv("RCLAW_TIER_WEIGHT_ENABLED", raising=False)
        assert tg.weight_enabled() is False

    def test_with_the_flag_off_the_weight_cannot_change_a_verdict(self, monkeypatch):
        # Even a 24-month lock and perfect standing must not promote anyone
        # while the flag is off, or "default off" means nothing.
        monkeypatch.delenv("RCLAW_TIER_WEIGHT_ENABLED", raising=False)
        assert tg.tier_for_stake(
            9_000, lock_seconds_remaining=24 * MONTH, standing=STANDING_MAX) == "basic"

    def test_staked_profile_and_staked_of_use_the_SAME_account_filters(self):
        # They diverged once already in this file's sibling (the leverage rest,
        # keyed two ways). One builder, so a mint filter cannot be present on
        # one path and absent on the other.
        import inspect
        for fn in (tg.staked_of, tg.staked_profile):
            src = inspect.getsource(fn)
            assert "_stake_filters(wallet)" in src, (
                f"{fn.__name__} builds its own filters — that is how two readers "
                "of one map come to disagree about what they are reading")


# ── the layout the plan got wrong ────────────────────────────────────────

class TestThePlansOffsetsWouldHaveBrokenTheReader:
    def test_amount_is_at_73_not_72(self):
        assert tg.STAKE_AMOUNT_OFFSET == 73, (
            "TIER_MODEL.md Phase B said to keep amount@72 while appending the "
            "lock field. It is at 73, behind a version byte, and moving it "
            "would break every stake read")

    def test_the_lock_field_already_exists_at_89(self):
        assert tg.STAKE_UNLOCK_AT_OFFSET == 89
        assert tg.STAKE_ACCOUNT_SPACE == 90, (
            "the plan proposed SPACE 81 -> 89; it is already 90")

    def test_the_rust_source_is_the_authority_for_all_of_them(self):
        # Not a fixture: read the constants out of the program itself, so this
        # test cannot pass against a stale copy of the layout.
        from pathlib import Path
        src = Path("programs/rclaw_staking/src/lib.rs").read_text(encoding="utf-8")
        for name, value in (("AMOUNT_OFFSET", tg.STAKE_AMOUNT_OFFSET),
                            ("UNLOCK_AT_OFFSET", tg.STAKE_UNLOCK_AT_OFFSET),
                            ("OWNER_OFFSET", tg.STAKE_OWNER_OFFSET),
                            ("MINT_OFFSET", tg.STAKE_MINT_OFFSET)):
            assert f"pub const {name}: usize = {value};" in src, (
                f"{name} in tier_gate.py disagrees with the Rust layout module")
