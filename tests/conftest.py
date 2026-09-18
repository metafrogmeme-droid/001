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
    # Per-user agent profile (risk_pref + watchlist), added 2026-08-21 with
    # bot/core/user_profile_store.py. Listed IN THE SAME COMMIT as the feature
    # rather than the first time a test mysteriously depends on suite order —
    # which is the whole lesson of the glob block below. A durable per-user file
    # that nothing cleans accumulates across RUNS, not just across tests.
    "data/user_profile.json",
    "data/user_profile.json.bak",
    "data/user_profile.json.tmp",
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


def _refuse_a_live_store() -> None:
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


# ── An undo that hands back a different object ────────────────────────────
#
# `monkeypatch.setattr(obj, "m", stub)` records the old value as
# `getattr(obj, name, notset)` — which, for a plain instance, SUCCEEDS through
# the class and hands back a bound method. `undo()` then does
# `setattr(obj, name, that_bound_method)`, so the object it gives back is not
# the object it was given: `m` now sits in the INSTANCE's `__dict__` and
# shadows the class from there on, permanently, for the rest of the session.
#
# PYTEST ASKS EXACTLY THE RIGHT QUESTION ONE LINE ABOVE, FOR CLASSES:
#
#     # avoid class descriptors like staticmethod/classmethod
#     if inspect.isclass(target):
#         oldval = target.__dict__.get(name, notset)
#
# `__dict__.get` rather than `getattr`, because "the type provides it" is not
# "the target had it". An instance needs the same reading for the inheritance
# reason rather than the descriptor one, and never got it.
#
# WHAT IT COST, DRIVEN. `tests/test_cross_venue_funding.py::
# test_funding_command_renders_all_venues` patches `cross_venue.CROSS_VENUE`
# — the module singleton — on `states_for`. Correct code; its undo left a
# bound method in `CROSS_VENUE.__dict__`. From then on every CLASS-level plant
# of that name was silently ignored FOR THE SINGLETON, and
# `test_the_funding_card_says_which_venues_it_read.py::TestTheHandlerIsWired`
# plants exactly that way while `_cmd_funding` reads `CROSS_VENUE` — so the
# plant never ran, the real reader fetched, and two tests spent the rest of
# their life asserting against whatever bybit and hyperliquid answered over
# the network. `ci_test_gate`'s flake filter re-ran each one alone, watched it
# pass, and filed it "passes alone (flaky/order-dependent)" on every run.
#
# Containment in the harness rather than a rule each test remembers — the
# reason the three fixtures below give, and the reason this one is DERIVED
# rather than a list of singletons to scrub after each test. A list is what
# `_STATE_GLOBS` above calls "the failure mode of an allowlist", and the
# singleton somebody patches tomorrow is always the one missing from it.
#
# The correction is narrow on purpose: it fires only when the patch CREATED an
# entry that was not in the target's own `__dict__` before, so a real instance
# attribute is still restored to its old value and a property (whose setter
# writes no instance attribute) is left alone. It carries no `isclass` or
# `ismodule` branch, and the mutation round is why: a class's `__dict__` is a
# mappingproxy, so the reading already declines it; and for a module the
# correction is a no-op — a name absent from `vars(mod)` is absent from
# `getattr` too, so pytest already recorded `notset` — except on a PEP-562
# `__getattr__` module, where deleting is the correct restore and re-setting
# would materialise a lazy attribute for good. Both branches survived every
# mutation, which is the round saying they claimed checks they did not make.
_MONKEYPATCH_SHAPE_HINT = (
    "tests/conftest.py corrects pytest's monkeypatch undo so it cannot leave a "
    "shadowing instance attribute behind (see the comment above "
    "_install_an_honest_monkeypatch_undo). That correction reads _pytest."
    "monkeypatch internals — MonkeyPatch.setattr, MonkeyPatch._setattr, "
    "notset, derive_importpath — and this pytest does not have the shape it "
    "expects.\n\nThis is NOT a pass: without it, a test that patches a module "
    "singleton silently shadows the class for the rest of the session, and the "
    "gate's flake filter files the result as order-dependent and ignores it. "
    "Re-read the correction against this pytest before running the suite."
)


def _own_dict_has(obj, attr):
    """True / False / None. `None` is 'this object has no instance __dict__ to
    read' — `__slots__`, a C type, and (the case that matters here) a CLASS,
    whose `__dict__` is a mappingproxy rather than a dict. Nothing measured,
    so nothing corrected: that is why the correction below carries no
    `inspect.isclass` branch. Driven both ways in the guard, because a branch
    no input can reach is a claim that there is a check."""
    d = getattr(obj, "__dict__", None)
    if not isinstance(d, dict):
        return None
    return attr in d


def _install_an_honest_monkeypatch_undo() -> None:
    import functools

    from _pytest.monkeypatch import MonkeyPatch, derive_importpath, notset

    if getattr(MonkeyPatch.setattr, "_runeclaw_honest_undo", False):
        return                                  # already installed this session

    real_setattr = MonkeyPatch.setattr

    def _target_of(target, name, value, raising):
        """(object, attr) this patch will write to, or (None, None) — which
        covers every argument shape pytest is about to reject itself."""
        try:
            if value is notset:
                if not isinstance(target, str):
                    return None, None           # pytest raises; not ours to pre-empt
                attr, obj = derive_importpath(target, raising)
            else:
                obj, attr = target, name
            return (obj, attr) if isinstance(attr, str) else (None, None)
        except Exception:
            return None, None

    @functools.wraps(real_setattr)
    def setattr_(self, target, name, value=notset, raising=True):
        obj, attr = _target_of(target, name, value, raising)
        had = _own_dict_has(obj, attr) if obj is not None else None
        real_setattr(self, target, name, value, raising)
        # `real_setattr` either raised (we are not here) or appended exactly
        # one entry, for this target — so `[-1]` is ours. The SELF-TEST below
        # proves that against the installed pytest rather than asserting it.
        if had is False and _own_dict_has(obj, attr) is True:
            # It was not on the instance and it is now: this patch created the
            # shadow, so undo must DELETE it rather than write the class's
            # value onto the instance.
            recorded_obj, recorded_attr, _old = self._setattr[-1]
            self._setattr[-1] = (recorded_obj, recorded_attr, notset)

    setattr_._runeclaw_honest_undo = True
    MonkeyPatch.setattr = setattr_
    _prove_the_undo_is_honest(MonkeyPatch)


class _Inherits:
    """A throwaway with its only method on the class — the shape the defect
    needs, and the shape of every module-level singleton in this tree."""

    def m(self):
        return "class"


def _prove_the_undo_is_honest(MonkeyPatch) -> None:
    """DRIVE the correction once, at configure time, against the pytest that
    is actually installed — in BOTH directions.

    The wrapper reads `MonkeyPatch._setattr` and rewrites its last entry, and
    nothing else in this file checks that pytest still keeps its undo stack
    that way. A version that did not would leave the wrapper installed and
    correcting nothing — a containment reporting success over the leak it
    exists to prevent, which is the shape `ruff_gate.check_version` separates
    CANNOT CHECK from PASSED for. So it is measured, not assumed.

    BOTH directions, because one is not a measurement of the other and the
    second is the more expensive to get wrong. An undo that DELETED
    unconditionally would pass the shadow check and quietly destroy every
    real instance attribute a test patches, suite-wide.
    """
    # 1. A name that lives on the CLASS must not be left on the instance.
    probe = _Inherits()
    mp = MonkeyPatch()
    mp.setattr(probe, "m", lambda: "stub")
    if probe.m() != "stub":
        raise RuntimeError("monkeypatch.setattr did not take on a plain object")
    mp.undo()
    if "m" in vars(probe):
        raise RuntimeError(
            "undo left a shadowing instance attribute behind: the correction "
            "is installed and did nothing")
    if probe.m() != "class":
        raise RuntimeError("undo did not restore the class's own attribute")

    # 2. A name that really WAS on the instance must be handed back, not
    #    deleted. The correction is narrow or it is a second defect.
    own = _Inherits()
    own.m = lambda: "the instance's own"
    mp = MonkeyPatch()
    mp.setattr(own, "m", lambda: "stub")
    mp.undo()
    if "m" not in vars(own) or own.m() != "the instance's own":
        raise RuntimeError(
            "undo deleted an instance attribute that was there before it: the "
            "correction is wider than the shadow it exists to remove")


def pytest_configure(config):
    _refuse_a_live_store()
    try:
        _install_an_honest_monkeypatch_undo()
    except Exception as exc:
        raise pytest.UsageError(
            f"{_MONKEYPATCH_SHAPE_HINT}\n\n  {type(exc).__name__}: {exc}") from exc


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


#: BacktestEngine.__init__ forces these OFF process-wide — deliberately, and
#: asserted by tests/test_backtest_flag_restore.py: they are fitted on the
#: bot's whole closed-trade history and are lookahead against a replayed bar.
#: The override is handed back by run(), cleanup(), or __del__.
_LOOKAHEAD_FLAGS = (
    "confidence_calibration_enabled",
    "setup_expectancy_enabled",
    "external_sentiment_enabled",
)


@pytest.fixture(autouse=True)
def _restore_lookahead_flags():
    """Hand the analyzer's learning flags back after every test.

    WHY THIS EXISTS. Around twenty tests construct a BacktestEngine and never
    run(), cleanup() or `with` it — so the ctor's process-wide override is
    released only when the engine is garbage collected. pytest keeps engines
    alive through fixtures and assertion tracebacks, so the release lands
    somewhere unpredictable, or not before the end of the session.

    The visible cost was test_flag_status.py::test_default_on_guards_report_on
    reading CONFIDENCE_CALIBRATION_ENABLED as False in a full run while
    passing on its own. It reproduced on a clean checkout, so the suite has
    been failing its own gate (known_failures.txt is an empty baseline) for a
    reason unrelated to whatever change was being tested.

    This restores the flags rather than asserting they were left alone,
    because a test that leaks one has still tested what it meant to test —
    the leak is a cleanup defect, not a behaviour claim. The behaviour claim
    (that the engine forces them off and gives them back) has its own file,
    tests/test_backtest_flag_restore.py, which this cannot make vacuous: it
    sets and reads the flags WITHIN a single test.
    """
    from bot.config import CONFIG

    saved = {f: getattr(CONFIG.analyzer, f, None) for f in _LOOKAHEAD_FLAGS}
    yield
    for name, val in saved.items():
        if val is not None and getattr(CONFIG.analyzer, name, None) != val:
            object.__setattr__(CONFIG.analyzer, name, val)


@pytest.fixture(autouse=True)
def _contain_vault_writes_to_the_environment():
    """Hand the vault-managed environment variables back after every test.

    THE SAME SHAPE AS THE FIXTURE ABOVE, AND IT COST MORE. `store_secrets`
    writes each secret straight into `os.environ` — deliberately, and its own
    comment says why: "Always update the live environment first — recovery of
    the running process". `seed_and_restore` does the same at boot. Both are
    correct; neither goes through monkeypatch, so monkeypatch's teardown has
    no record of the write and cannot undo it.

    ONE test did it — `test_vault_keeps_what_it_cannot_read.py::
    test_the_recovery_command_does_not_erase_the_rest` calls
    `store_secrets({"WEB_GATEWAY_SECRET": "g" * 48})` — and `WEB_GATEWAY_SECRET`
    then stayed set for the rest of the session. `user_gateway._secret()` reads
    the environment on every request and falls back to the module attribute
    only when it is unset, so every later test that plants a secret by
    monkeypatching `ug._GATEWAY_SECRET` was silently overridden: **40 of
    tests/test_web_gateway.py's 48 failed 403 in a full run and all 48 passed
    alone.** They cover confirm, the live-mode gate, portfolio and authority
    apply/revoke — and `ci_test_gate`'s flake filter re-ran each one alone, saw
    it pass, and counted none of them. The money-facing HTTP surface was in CI
    and gating nothing, for as long as that leak existed.

    Restore rather than assert, for the reason the fixture above gives: the
    leaking test tested exactly what it meant to, and the production write it
    exercises is the behaviour under test. Containment belongs in the harness,
    not in a rule each test has to remember. The behaviour claim that
    `store_secrets` reaches the environment is not made vacuous by this — it is
    asserted WITHIN a single test, before this teardown runs.
    """
    import os as _os
    try:
        from bot.core.secrets_vault import _managed_keys
        keys = tuple(_managed_keys()) + ("RUNECLAW_SECRETS_KEY", "RUNECLAW_VAULT_KEYS")
    except Exception:  # vault unavailable — nothing writes these, nothing to hold
        yield
        return

    saved = {k: _os.environ.get(k) for k in keys}
    yield
    for name, val in saved.items():
        if val is None:
            _os.environ.pop(name, None)
        elif _os.environ.get(name) != val:
            _os.environ[name] = val
