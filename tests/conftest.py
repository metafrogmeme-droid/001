"""Pytest fixtures for RUNECLAW test suite."""
import glob
import os
import shutil

import pytest

# Runtime state files written under data/ during a test run. Several tests
# construct a LiveExecutor / RuneClawEngine / PortfolioTracker that persists
# positions, closed trades, risk-breaker state, and learning records to these
# paths. Without per-test cleanup they leak across tests — e.g. a leftover
# data/live_positions.json makes a later test see an "already open" position and
# fail (the root cause of the historical test-isolation failures). The data/
# files are all gitignored runtime artifacts (never committed fixtures), so it is
# safe to remove them between tests.
_STATE_FILES = (
    "data/combined_state.json",
    "data/combined_state.json.bak",
    "data/combined_state.json.tmp",
    "data/live_positions.json",
    "data/live_positions.json.bak",
    "data/live_positions.json.tmp",
    "data/closed_trades.json",
    "data/risk_state.json",
    "data/risk_state.json.bak",
    "data/risk_state.json.tmp",
    # Roadmap P0: the persisted user store leaked across tests. A stale
    # last_seen on a seeded admin tripped the 24h sensitive-command staleness
    # check (user_store.has_permission), so /pause, /resume, emergency-stop and
    # /llmreset tests passed only in suite order (an earlier test refreshed
    # last_seen) and failed in isolation. Cleaning it makes the user store
    # fresh and order-independent.
    "data/users.json",
    "data/users.json.bak",
    "data/users.json.tmp",
)
_STATE_GLOBS = (
    "data/portfolio_*.json",
    # AN ENUMERATED LIST THAT A FEATURE OUTGREW. `data/risk_state.json` is on
    # the list above — the SHARED operator engine's file, correct when it was
    # the only one. Per-user risk engines came later and persist to
    # `data/risk_state_{user}.json`, which matches nothing here, so their state
    # survived every test and accumulated across RUNS.
    #
    # `test_win_does_not_trip_other_user` is the test that noticed: alice's
    # consecutive_losses reached 38, `_bust(a, 4)` stopped leaving 4, and the
    # gate's flake filter re-ran it against a data/ that had moved again and
    # filed it "passes alone". Green build, real failure.
    #
    # Note `portfolio_*.json` directly above: per-user PORTFOLIOS were added
    # and somebody remembered the glob. Per-user RISK ENGINES were added and
    # nobody did. That is the failure mode of an allowlist, not an oversight
    # by anyone in particular.
    "data/risk_state_*.json",
    "data/risk_state_*.json.bak",
    "data/risk_state_*.json.tmp",
)
_STATE_DIRS = (
    "data/learning",
)


# ── Refuse to run against a LIVE store ────────────────────────────────────
#
# _clean_runtime_state() below deletes live_positions.json, closed_trades.json,
# combined_state.json, risk_state.json, users.json, portfolio_*.json and the
# whole learning/ tree — before AND after every test, ~6000 times a run. Its
# comment says that is safe because "the data/ files are all gitignored runtime
# artifacts". True where data/ is scratch. On a deployed box data/ is a SYMLINK
# into ~/runeclaw-persist/, and those same paths resolve to the production
# store: the assumption is correct and the environment invalidates it.
#
# That is not hypothetical. On 2026-08-07 the operator's box was found with
# exactly this fixture's delete-list missing — closed_trades.json,
# live_positions.json, risk_state.json, users.json, the lot — every file NOT on
# the list intact, and data/learning/ present but emptied, which is the
# signature of rmtree followed by the store recreating it. No backup existed.
#
# So the guard is the same question state_guard.py asks at startup, asked here:
# am I somewhere it is safe to write? A vault or a symlink means no.
_LIVE_MARKERS = ("data/secrets_vault.enc", "data/runeclaw.db",
                 "data/exchange_creds.json", "data/attestation_key.bin")
_OVERRIDE_ENV = "RUNECLAW_ALLOW_LIVE_STATE_TESTS"


def _live_store_reasons() -> list:
    """Evidence that data/ points at a store OUTSIDE this working tree.

    The discriminator is deliberately the PATH, not the presence of a vault.
    The first version of this guard refused whenever data/secrets_vault.enc or
    data/runeclaw.db existed — and then refused to run in a plain CI checkout,
    because the suite itself creates those files: ~6000 tests construct
    LiveExecutor / SecretsVault at their default paths. A guard that fires on
    its own side effects gets overridden as a matter of routine, and an
    override everyone sets is not a guard.

    `data/` resolving outside the repo is exactly the deployed shape
    (deploy.sh symlinks it into ~/runeclaw-persist/) and is never the scratch
    shape. No false positives, and it is the precise condition under which
    _clean_runtime_state() reaches state it does not own.
    """
    reasons = []
    try:
        # Anchored to the repo root via __file__, never to the CWD: the guard
        # has to give the same answer wherever pytest was invoked from.
        here = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
        expect = os.path.join(here, "data")
        if not os.path.exists(expect):
            return []
        # realpath on ONE side only. Resolving both collapses the symlink at
        # both ends and the comparison can never differ — the first version did
        # exactly that and could not detect the case it exists for.
        real = os.path.realpath(expect)
        if real != expect:
            reasons.append(f"data/ resolves outside the working tree -> {real}")
            if os.path.islink(expect):
                reasons.append(f"  (data/ is a symlink -> {os.readlink(expect)})")
            for marker in _LIVE_MARKERS:
                if os.path.exists(os.path.join(here, marker)):
                    reasons.append(f"  and {marker} exists there")
    except OSError:
        pass
    return reasons


def pytest_configure(config):
    """Abort the whole run before a single test can delete anything.

    Checked once at configure time, not per-test: by the time an autouse
    fixture runs, the deletion it guards has already been scheduled, and a
    per-test skip would still leave the run half-destructive.
    """
    reasons = _live_store_reasons()
    if not reasons:
        return
    if os.environ.get(_OVERRIDE_ENV) == "1":
        print(f"\n[conftest] {_OVERRIDE_ENV}=1 — running against what looks "
              f"like a LIVE store anyway:\n  " + "\n  ".join(reasons) +
              "\n  runtime state under data/ WILL be deleted.\n")
        return
    raise pytest.UsageError(
        "refusing to run: data/ looks like a live store, and this suite "
        "deletes runtime state under it before and after every test.\n  "
        + "\n  ".join(reasons)
        + "\n\nRun the suite from a clean checkout instead. If you are certain "
          f"this data/ is disposable, set {_OVERRIDE_ENV}=1."
    )


def _clean_runtime_state() -> None:
    for f in _STATE_FILES:
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
    for pattern in _STATE_GLOBS:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
    for d in _STATE_DIRS:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_combined_state():
    """Remove runtime state files before and after each test, preventing
    cross-test state contamination (originally C2-34 for combined_state.json;
    extended to live_positions / closed_trades / risk_state / portfolio_* /
    learning to fix the broader test-isolation failures)."""
    _clean_runtime_state()
    yield
    _clean_runtime_state()
