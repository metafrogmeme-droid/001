"""One equity high-water mark per person, shared across their venues.

The last of Phase 3's person-level caps, and the one that is state rather than
arithmetic. Everything here is about the two ways a peak goes wrong, both of
which this repo has already paid for once:

  * a peak PINNED HIGH by a corrupted or transient reading keeps drawdown near
    100% for ever, so the breaker re-trips on the next evaluation and a manual
    reset never sticks — the "still halted after reset" report;
  * a peak LOST or read as zero reports no drawdown at all, which is a
    confident all-clear assembled from nothing, on the control that decides how
    much real money is lost before the bot stops.

The second is the one that costs money, and it is the one a naive
implementation produces: `(peak - equity) / peak` with `peak` defaulting to 0.
"""
from __future__ import annotations

import pytest

from bot.risk.person_peak import PersonPeakStore


@pytest.fixture
def store(tmp_path):
    return PersonPeakStore(path=str(tmp_path / "peaks.json"))


# ── the peak only rises ──────────────────────────────────────────────────

def test_the_peak_rises_and_never_falls(store):
    """Lowering the peak on a smaller reading would make the drawdown ZERO at
    exactly the moment the account is furthest down — an absent measurement
    scoring healthy, with the number moving in the direction that keeps
    trading."""
    assert store.observe("alice", 1000.0) == 1000.0
    assert store.observe("alice", 1200.0) == 1200.0
    assert store.observe("alice", 600.0) == 1200.0, "the peak followed equity down"
    assert store.peak("alice") == 1200.0


def test_drawdown_is_measured_off_the_peak(store):
    store.observe("alice", 1000.0)
    assert store.drawdown_pct("alice", 900.0) == pytest.approx(10.0)
    assert store.drawdown_pct("alice", 1000.0) == pytest.approx(0.0)


def test_a_new_high_is_not_a_drawdown(store):
    store.observe("alice", 1000.0)
    assert store.drawdown_pct("alice", 1500.0) == pytest.approx(0.0)
    assert store.peak("alice") == 1500.0


def test_one_persons_peak_is_not_anothers(store):
    store.observe("alice", 1000.0)
    store.observe("bob", 50.0)
    assert store.drawdown_pct("bob", 50.0) == pytest.approx(0.0)
    assert store.drawdown_pct("alice", 500.0) == pytest.approx(50.0)


# ── the peak is SHARED across venues, which is the whole point ───────────

def test_the_peak_is_per_person_not_per_venue(store):
    """The reason this is a store and not a field on the risk engine. After
    Phase 2 there is one engine per (user, venue); each keeping its own copy of
    "this person's peak" lets them diverge, and the divergence is invisible —
    every engine reports a plausible drawdown off a peak only it believes in.

    Here the peak is observed from the person's TOTAL equity, so it does not
    matter which venue's engine asks."""
    store.observe("alice", 2000.0)                 # bitget 1000 + bybit 1000
    # bybit collapses to 200; total is now 1200.
    assert store.drawdown_pct("alice", 1200.0) == pytest.approx(40.0)
    # And the answer is the same whichever engine asks, because there is one
    # peak and one total.
    assert store.drawdown_pct("alice", 1200.0) == pytest.approx(40.0)


# ── not measurable is not zero ───────────────────────────────────────────

def test_no_peak_yet_is_none_not_zero_drawdown(store):
    """`None` means "cannot say". `0.0` means "measured, and flat". A gate that
    reads the second when it should read the first prints an all-clear over no
    data — /risk's exact defect, on the control that stops the bot."""
    assert store.peak("fresh") is None
    assert store.drawdown_pct("fresh", None) is None


def test_an_unreadable_equity_does_not_seed_or_move_the_peak(store):
    """Seeding from a bad reading is how the peak gets pinned high. Ignoring it
    costs one observation."""
    store.observe("alice", 1000.0)
    for junk in (None, "", "abc", float("nan"), float("inf"), -5.0, 0.0):
        assert store.observe("alice", junk) == 1000.0, f"{junk!r} moved the peak"
        assert store.drawdown_pct("alice", junk) is None, f"{junk!r} scored a drawdown"
    assert store.peak("alice") == 1000.0


def test_an_absurd_reading_cannot_pin_the_peak(store):
    """A peak of 10^12 pins drawdown at ~100% for ever, so the breaker
    re-trips on every evaluation and a reset never sticks."""
    store.observe("alice", 1000.0)
    assert store.observe("alice", 1e13) == 1000.0
    assert store.drawdown_pct("alice", 900.0) == pytest.approx(10.0)


# ── it survives a restart, and refuses to restore garbage ────────────────

def test_the_peak_survives_a_restart(tmp_path):
    """A high-water mark erased by a restart reports no drawdown on an account
    that is deeply down. CLAUDE.md records that one happening."""
    p = str(tmp_path / "peaks.json")
    PersonPeakStore(path=p).observe("alice", 1000.0)
    assert PersonPeakStore(path=p).peak("alice") == 1000.0


def test_a_corrupt_stored_peak_is_ignored_rather_than_restored(tmp_path):
    import json
    p = tmp_path / "peaks.json"
    p.write_text(json.dumps({"peaks": {"alice": 1e13, "bob": "nonsense",
                                       "carol": 500.0}}))
    s = PersonPeakStore(path=str(p))
    assert s.peak("alice") is None, "an implausible peak was restored"
    assert s.peak("bob") is None
    assert s.peak("carol") == 500.0, "a good peak was discarded with the bad"


def test_an_unreadable_store_is_not_a_crash_and_not_a_fake_peak(tmp_path):
    p = tmp_path / "peaks.json"
    p.write_text("{ not json")
    s = PersonPeakStore(path=str(p))
    assert s.peak("alice") is None
    # And it still works from here — a bad file costs history, not function.
    assert s.observe("alice", 100.0) == 100.0


# ── reset ────────────────────────────────────────────────────────────────

def test_reseed_forgets_the_peak_so_a_manual_reset_sticks(store):
    """An operator resuming after a confirmed transfer needs the peak
    re-measured. Preserving it means the breaker re-trips immediately and the
    reset never takes — the exact bug the engine's own re-seed exists for."""
    store.observe("alice", 1000.0)
    assert store.drawdown_pct("alice", 500.0) == pytest.approx(50.0)
    store.reseed("alice")
    assert store.peak("alice") is None
    assert store.drawdown_pct("alice", 500.0) == pytest.approx(0.0), (
        "after a re-seed the next reading IS the new peak")
    store.reseed("nobody")     # absent user is a no-op, not a crash


def test_the_store_is_process_wide():
    """One owner is the entire point — two stores would be two peaks."""
    from bot.risk.person_peak import get_person_peak_store
    assert get_person_peak_store() is get_person_peak_store()


def test_a_relative_path_is_anchored_to_the_repo_not_the_cwd(tmp_path, monkeypatch):
    """The literal `data/person_equity_peak.json` is baselined in
    tests/durable_path_baseline.txt as "anchored downstream", which means the
    cwd-guard no longer watches it and THIS is the only thing that does.

    The cwd is deliberately not the repo root. Every other test here passes an
    absolute tmp path, and `state_path` returns an absolute path unchanged — so
    they all pass against a store that dropped the anchoring entirely. That is
    the same blind spot the venue-key restore test had, and it is worth one
    test rather than a comment: a peak store that silently reads the wrong
    directory finds no file, and no peak reports NO DRAWDOWN on an account that
    is deeply down.
    """
    root = tmp_path / "repo"
    elsewhere = tmp_path / "launched-from-here"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr("bot.utils.paths.REPO_ROOT", root, raising=False)

    s = PersonPeakStore(path="data/peaks_relative.json")
    s.observe("alice", 1000.0)
    assert (root / "data" / "peaks_relative.json").exists(), (
        "the peak was written relative to the working directory")
    assert not (elsewhere / "data" / "peaks_relative.json").exists()
