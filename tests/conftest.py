"""Pytest fixtures for RUNECLAW test suite."""
import glob
import os
import pathlib
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


# ── NO TEST REACHES A VENUE ──────────────────────────────────────────
#
# A TEST THAT LOSES ITS STUB BECOMES A VENUE READ, AND THE GATE IS BUILT TO
# FORGIVE THAT EXACT SIGNATURE. `tests/test_scan_reads_the_executors_record.py`
# stubs `ss._closed_trades_file` and asserts about the closed-trade record;
# further into the same `_fetch_live_exchange_data` a real
# `ccxt_sync.bitget({...})` is built and `fetch_balance` is called against
# api.bitget.com, three times with its retries. All eight of that file's tests
# passed, because the one field that read feeds — `equity` — is asserted by
# none of them and the read sits inside a broad `except`. Driven over the whole
# suite, twelve tests in four files reached six hosts: api.bitget.com,
# api.bybit.com and the three news feeds `_refresh_news_radar` pulls.
#
# NOT ONE OF THE TWELVE ASSERTS AGAINST A VENUE — every one is a stub that was
# never made, which is the quiet half. A test that asserts against a live venue
# goes red the first time the venue disagrees; a test that merely reaches one is
# slow, nondeterministic and GREEN. And when it does go red, `ci_test_gate`'s
# flake filter re-runs it alone, the venue answers that time, and the result is
# filed `~ passes alone (flaky/order-dependent)` — the same forgiveness that hid
# the monkeypatch leak above, arriving through the network instead of through
# module state.
#
# SO THE CONNECT IS REFUSED AND THE REFUSAL IS ORDINARY. The code under test
# sees ECONNREFUSED, which is the state every venue reader here is written to
# handle, so the test goes on exercising the path it meant to — deterministically
# and in microseconds rather than a network round trip. The harness records the
# attempt and the TEARDOWN is where it is said, because the broad `except` in
# the code under test would otherwise swallow the only evidence there is. A
# `BaseException` would escape those handlers, and it would also change the
# control flow of the code being tested, which is a different test.
#
# THE READING IS FOUR WORDS AND `not-ip` IS A MEASUREMENT, NOT A PASS. A socket
# whose family is neither AF_INET nor AF_INET6 is not an IP connect at all —
# AF_UNIX, AF_NETLINK — so nothing about a venue can be claimed of it and
# nothing is refused. `unreadable` is an IP connect whose address this reading
# cannot place, and it is REFUSED with a sentence of its own: reading it as
# loopback would be the failed-read-as-allowed shape, on the one gate whose
# whole job is to refuse.
#
# WHAT IT DOES NOT COVER, stated because a gate whose coverage is overstated is
# the failure this repo spends most of its guard tests preventing. DNS is
# untouched — `getaddrinfo` crosses no socket, so a name still resolves and only
# the connect is refused, which is the right chokepoint because a resolution
# that never connects reads nothing. A SUBPROCESS has its own interpreter and
# its own unpatched `socket.socket`, so a test that shells out reaches whatever
# it likes. Connectionless UDP (`sendto` with no connect) is not covered either;
# the tree has no `SOCK_DGRAM` at all today, driven rather than assumed.
#
# THE DOOR IS A WHOLE-RUN DECISION, the shape `_OVERRIDE_ENV` above already
# takes: `RUNECLAW_ALLOW_TEST_NETWORK=1` runs the suite against the network on
# purpose. It SAYS SO at configure, because a containment that is present and
# refusing nothing is a containment reporting success over the leak it exists to
# prevent. There is deliberately no per-test marker: no test in this tree needs
# the network, and a marker nothing uses is a door painted on a wall.
_NETWORK_OVERRIDE_ENV = "RUNECLAW_ALLOW_TEST_NETWORK"

#: Names that mean loopback without a resolver. Resolving inside the guard
#: would be the network read the guard exists to refuse, so the vocabulary is
#: closed and an unknown NAME is refused — which is the loud direction: a false
#: accusation names the host it refused, where a false allow is a venue read.
_LOOPBACK_NAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
})

#: THE PROXY IS THE NETWORK, AND `local` WAS A MEASUREMENT OF THE ADDRESS.
#:
#: This containment shipped reading `127.0.0.1` as loopback and allowing it,
#: which is right for a test's own bound socket and WRONG for a box whose
#: `HTTPS_PROXY` is `http://127.0.0.1:33283` — every venue read there connects
#: to loopback, is allowed, and leaves the machine through the proxy. So the
#: measurement that scoped the slice which introduced this file was taken
#: through a hole in it: driven here it found twelve tests and eighty-five
#: connects (the ones that go by raw IP, bypassing the proxy), and the same
#: commit in CI — no proxy, direct connects — found SEVENTY-FOUR tests.
#:
#: A gate whose coverage depends on the host's proxy configuration is a gate
#: whose coverage is overstated, which is the failure this repo spends its
#: guard tests preventing. The proxy endpoint is read ONCE, from the
#: environment, and a connect to it is `proxy` — refused, with a word of its
#: own, because "you reached the network through 127.0.0.1" and "you talked to
#: your own test server" are different facts and only one of them is allowed.
_PROXY_ENV = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
              "ALL_PROXY", "all_proxy")


def _proxy_endpoints() -> frozenset:
    """Every (host, port) this box's proxy variables name. Read once.

    Malformed values are SKIPPED rather than raising: a proxy variable nobody
    can parse is not a reason to refuse the whole suite, and the connect it
    would have covered is still judged by the address rules below.
    """
    import os
    import urllib.parse

    out = set()
    for name in _PROXY_ENV:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            continue
        if "://" not in raw:
            raw = "http://" + raw
        try:
            u = urllib.parse.urlparse(raw)
            host, port = u.hostname, u.port
        except ValueError:
            continue
        if not host:
            continue
        out.add((host.lower(), port if port else (443 if u.scheme == "https" else 80)))
    return frozenset(out)


_PROXIES = _proxy_endpoints()

#: Test FILES that reach the network today. A RATCHET, not a permission slip:
#: a file NOT listed here that makes an outbound connect is a hard failure.
#:
#: WHY A BACKLOG AND NOT A SWEEP. This containment shipped enforcing hard on a
#: measurement of twelve tests, taken on a box whose `HTTPS_PROXY` is loopback
#: — so it had been reading every proxied venue read as "your own test server"
#: and allowing it. With the proxy read correctly, the true number is what CI
#: had been saying all along, and stubbing that many seams across that many
#: files in one commit is the wholesale conversion CLAUDE.md refuses.
#:
#: WHY BY FILE AND NOT BY NODEID, and this is a stated limit rather than a
#: convenience: driven in CI, 60 of the 74 tests that reached the network
#: PASSED when re-run alone. The set is order-dependent, so a nodeid baseline
#: would churn run to run and its stale half would be a flake generator. A
#: file is the stable unit.
#:
#: WHAT IS ENFORCED, AND WHAT IS NOT. GROWTH is enforced and is the direction
#: that matters: a new file reaching a venue fails, loudly, by name. The STALE
#: direction — a listed file that no longer reaches anything — is NOT enforced
#: here, for the order-dependence above, and `scripts/network_reach_gate.py`
#: is where a human re-measures it deliberately. A gate whose coverage is
#: overstated is the failure this repo is organised around, so the limit is
#: written down rather than left to be discovered.
_REACH_BASELINE_FILE = pathlib.Path(__file__).with_name(
    "network_reach_baseline.txt")


def _reach_baseline() -> frozenset:
    """The baselined files, or an EMPTY set the caller is told about.

    An unreadable baseline is not an empty one: the caller below treats the
    empty set as "everything is new", which fails closed and loudly rather
    than acquitting the whole suite from a file nobody could read.
    """
    try:
        text = _REACH_BASELINE_FILE.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    return frozenset(
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#"))


_REACH_BASELINED = _reach_baseline()


def _reach_file(nodeid) -> str:
    """The file half of a nodeid, which is what the baseline is keyed on."""
    return (nodeid or "").split("::", 1)[0]

#: The three words that mean "this connect leaves the machine". `local` and
#: `not-ip` are allowed; `unreadable` is refused because reading an address
#: nobody could place as loopback is the failed-read-as-allowed shape on the
#: one gate whose whole job is to refuse.
_REFUSED_WORDS = frozenset({"remote", "unreadable", "proxy"})

_NO_VENUE_SHAPE_HINT = (
    "tests/conftest.py refuses every non-loopback connect the suite makes (see "
    "the comment above _refuse_outbound_connections), and the install DRIVES "
    "that refusal in both directions before the first test runs. One of those "
    "two drives did not answer what it must.\n\n"
    "This is NOT a pass: without the refusal, a test that loses a stub reaches "
    "a live venue, asserts against whatever it answered, and the gate's flake "
    "filter files the result as order-dependent."
)


def _outbound_verdict(family, address):
    """One connect, in one of four words, plus what to print.

    ``("not-ip"|"local", host, port)`` is allowed; ``("remote"|"unreadable",
    …)`` is refused. ``port`` is ``-1`` where no port could be read — an
    absence, printed as an absence, never as port zero.
    """
    import ipaddress
    import socket as _socket

    if family not in (_socket.AF_INET, _socket.AF_INET6):
        return "not-ip", "", -1

    seq = address if isinstance(address, (tuple, list)) else ()
    host = seq[0] if len(seq) > 0 else None
    port = -1
    if len(seq) > 1:
        try:
            port = int(seq[1])
        except (TypeError, ValueError):
            port = -1
    if isinstance(host, (bytes, bytearray)):
        try:
            host = bytes(host).decode("ascii")
        except UnicodeDecodeError:
            host = None
    if not isinstance(host, str) or not host:
        return "unreadable", f"<{type(address).__name__}>", port

    # A SCOPE ID IS READ BY `ipaddress` ITSELF, and the line that used to strip
    # it here was a claim that there is a check: driven, `ip_address("::1%lo")`
    # parses and answers `is_loopback` True, so a mutation that removed the
    # strip changed no verdict on any input a socket can produce. It is gone,
    # and the property is driven in the guard instead — so the day that
    # changes, a test fails rather than this quietly starting to refuse `::1`
    # on a machine that spells its loopback with an interface.
    # THE PROXY IS ASKED BEFORE LOOPBACK IS, because on this box the proxy IS
    # loopback and the loopback answer would win. A connect to the endpoint
    # `HTTPS_PROXY` names leaves the machine by construction, whatever its
    # address looks like.
    if (host.lower(), port) in _PROXIES:
        return "proxy", host, port
    if host.lower() in _LOOPBACK_NAMES:
        return "local", host, port
    try:
        if ipaddress.ip_address(host).is_loopback:
            return "local", host, port
    except ValueError:
        pass
    return "remote", host, port


class _OutboundLedger:
    """Every refused connect, stamped with the test that was running.

    The stamp is taken at CONNECT time from `pytest_runtest_logstart` /
    `logfinish`, never at report time. `pytest_runtest_teardown` runs ALONGSIDE
    the hook that invokes the fixture finalizers, so a snapshot taken there is
    taken before them and attributes every artefact to the NEXT test — the
    mis-attribution the monkeypatch probe above hit, and the reason this does
    not ask "what is running now?" when it comes to report.
    """

    def __init__(self):
        self.rows = []
        self.nodeid = None
        self.baselined = {}

    def note(self, word, host, port, call):
        self.rows.append((self.nodeid, word, host, port, call))

    def drain(self, nodeid):
        mine = [r for r in self.rows if r[0] == nodeid]
        if mine:
            self.rows = [r for r in self.rows if r[0] != nodeid]
        return mine

    def drain_all(self):
        rows, self.rows = self.rows, []
        return rows

    def note_baselined(self, nodeid, n):
        """Count a refused connect from a file the baseline already holds."""
        self.baselined[nodeid] = self.baselined.get(nodeid, 0) + n

    def baselined_summary(self):
        """(tests, connects, files), or None when the backlog was untouched."""
        if not self.baselined:
            return None
        return (len(self.baselined), sum(self.baselined.values()),
                len({_reach_file(n) for n in self.baselined}))


_OUTBOUND = _OutboundLedger()


class _RefusedOutbound(ConnectionRefusedError):
    """What the code under test sees.

    An ORDINARY refused connection, deliberately: every venue reader in this
    tree already handles one, so the test keeps exercising its own path and the
    harness — not the exception — is what reports the reach.
    """


def _outbound_row_line(word, host, port, call):
    where = f"{host}:{port}" if port >= 0 else host
    if call != "connect":
        where = f"{where}  ({call})"
    if word == "unreadable":
        return (f"    {where}  — an address this reading could not place, so it "
                f"was refused rather than read as loopback")
    return f"    {where}"


def _reached_the_network_text(nodeid, rows):
    """The whole sentence, pure, so it can be driven without a suite."""
    where = "\n".join(_outbound_row_line(w, h, p, c) for _n, w, h, p, c in rows)
    plural = "" if len(rows) == 1 else "s"
    return (
        f"{nodeid} reached the network.\n\n"
        f"  {len(rows)} refused connect{plural}:\n{where}\n\n"
        "A test that reaches a venue is a test asserting against whatever that\n"
        "venue answered — and when its stub is the thing that went missing, the\n"
        "assertion still passes, so this line is the only evidence there is. The\n"
        "connect was refused: the code under test saw an ordinary ECONNREFUSED,\n"
        "which is the state it is written to handle.\n\n"
        "Stub the seam the test reaches through. To run the suite against the\n"
        f"real network on purpose, set {_NETWORK_OVERRIDE_ENV}=1."
    )


def _refuse_outbound_connections() -> None:
    import errno
    import socket as _socket

    if getattr(_socket.socket.connect, "_runeclaw_no_venue", False):
        return                                  # already installed this session

    real_connect = _socket.socket.connect
    real_connect_ex = _socket.socket.connect_ex

    def _refused(sock, address, call):
        """The errno to answer with, or None to call through."""
        word, host, port = _outbound_verdict(sock.family, address)
        if word not in _REFUSED_WORDS:
            return None, host, port
        _OUTBOUND.note(word, host, port, call)
        return errno.ECONNREFUSED, host, port

    def connect(self, address):
        rc, host, port = _refused(self, address, "connect")
        if rc is not None:
            raise _RefusedOutbound(
                rc, f"refused by tests/conftest.py: no test may reach "
                    f"{host}:{port}")
        return real_connect(self, address)

    def connect_ex(self, address):
        # `connect_ex` ANSWERS an errno where `connect` raises one, and the
        # emulation has to match or a caller that reads the return value gets a
        # traceback from a call that documents itself as never raising.
        rc, _host, _port = _refused(self, address, "connect_ex")
        if rc is not None:
            return rc
        return real_connect_ex(self, address)

    connect._runeclaw_no_venue = True
    connect_ex._runeclaw_no_venue = True
    _socket.socket.connect = connect
    _socket.socket.connect_ex = connect_ex
    _prove_the_refusal_is_honest(_socket)


def _prove_the_refusal_is_honest(_socket) -> None:
    """DRIVE the refusal once, against the socket module that is really there.

    BOTH DIRECTIONS, and the second is the expensive one. A rule that refused
    everything passes the first check and takes down every test that runs a
    local server — including the aiohttp test server `test_networth_gateway`
    stands up — so the loopback connect is made for real and required to land.
    """
    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        # RFC 5737 documentation space. It is never reached: the refusal
        # answers before the real connect is called.
        probe.connect(("198.51.100.1", 9))
    except _RefusedOutbound:
        pass
    except Exception as exc:                      # noqa: BLE001 - reported below
        raise RuntimeError(
            f"a non-loopback connect raised {type(exc).__name__} rather than "
            f"the refusal: {exc}") from exc
    else:
        raise RuntimeError(
            "the refusal is installed and let a non-loopback connect through")
    finally:
        probe.close()
    if not _OUTBOUND.drain_all():
        raise RuntimeError("the refusal raised and recorded nothing")

    server = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    client = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client.settimeout(5)
        client.connect(server.getsockname())
    except _RefusedOutbound as exc:
        raise RuntimeError(f"the refusal refused loopback: {exc}") from exc
    finally:
        client.close()
        server.close()
    if _OUTBOUND.drain_all():
        raise RuntimeError("a loopback connect was recorded as reaching a venue")


def pytest_runtest_logstart(nodeid, location):
    _OUTBOUND.nodeid = nodeid


def pytest_runtest_logfinish(nodeid, location):
    _OUTBOUND.nodeid = None


def pytest_sessionfinish(session, exitstatus):
    """The backstop, and it names the test the way the fixture does.

    Two kinds of row reach here rather than a teardown: one recorded outside any
    test (collection, a session fixture) whose stamp is None, and one recorded
    after `_no_test_reaches_a_venue` has already drained — which happens only if
    a later autouse fixture is declared ABOVE it, since finalizers run in
    reverse declaration order. Neither is a false acquittal: the connect was
    refused either way, and the stamp still says which test was running.
    """
    rows = _OUTBOUND.drain_all()
    fresh = [r for r in rows if _reach_file(r[0]) not in _REACH_BASELINED]
    for nodeid in sorted({r[0] for r in fresh}, key=lambda n: (n is None, n)):
        mine = [r for r in fresh if r[0] == nodeid]
        print("\n" + _reached_the_network_text(
            nodeid or "<outside any test>", mine))
    if fresh:
        session.exitstatus = 1

    # THE BACKLOG IS PRINTED EVERY RUN, because a backlog nobody sees is a
    # backlog nobody clears. Counted, never named one by one: the list is in
    # the baseline file and repeating it here would train the reader to skip
    # the line.
    seen = _OUTBOUND.baselined_summary()
    if seen:
        tests, connects, files = seen
        print(f"\n[no-venue] {tests} baselined test(s) in {files} file(s) made "
              f"{connects} refused connect(s) — see "
              f"tests/network_reach_baseline.txt. Each is a stub that was "
              f"never made; none of them reached a venue.")


def pytest_configure(config):
    _refuse_a_live_store()
    try:
        _install_an_honest_monkeypatch_undo()
    except Exception as exc:
        raise pytest.UsageError(
            f"{_MONKEYPATCH_SHAPE_HINT}\n\n  {type(exc).__name__}: {exc}") from exc
    if os.environ.get(_NETWORK_OVERRIDE_ENV):
        # SAID, not assumed. A containment switched off in silence is a
        # containment reporting success over the leak it exists to prevent.
        print(f"\n[conftest] {_NETWORK_OVERRIDE_ENV} is set: outbound connects "
              f"are NOT refused, and a test that reaches a venue will not be "
              f"reported.")
        return
    try:
        _refuse_outbound_connections()
    except Exception as exc:
        raise pytest.UsageError(
            f"{_NO_VENUE_SHAPE_HINT}\n\n  {type(exc).__name__}: {exc}") from exc


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
def _no_test_reaches_a_venue(request):
    """Say, at teardown, that this test reached the network.

    DECLARED FIRST AMONG THE AUTOUSE FIXTURES ON PURPOSE. Same-scope autouse
    fixtures set up in declaration order and finalize in reverse, so this one
    tears down LAST and sees a connect made in any other fixture's teardown.
    The teardown is also where the report has to be: the venue readers in this
    tree catch broadly, so by the time the test body's assertions run the
    exception is gone and the ledger is the only witness left.
    """
    yield
    rows = _OUTBOUND.drain(request.node.nodeid)
    if not rows:
        return
    if _reach_file(request.node.nodeid) in _REACH_BASELINED:
        # Recorded, counted at session end, and NOT failed: a baselined file is
        # a backlog entry, not a pass. The connect was still refused, so the
        # test drove its own path against ECONNREFUSED rather than a venue.
        _OUTBOUND.note_baselined(request.node.nodeid, len(rows))
        return
    pytest.fail(_reached_the_network_text(request.node.nodeid, rows),
                pytrace=False)


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
