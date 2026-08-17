"""CI must install everything `requirements.lock` claims is guaranteed present.

2026-08-17, the third layer of one incident.

  Layer 1 — Pillow was not in requirements.lock. Signal cards rendered as
            nothing. Fixed by pinning it.
  Layer 2 — matplotlib/mplfinance/pandas were not in requirements.lock either,
            and `/chart` sent text. Fixed by pinning them, and by making a
            missing pinned module FAIL a test instead of skipping it
            (tests/dep_policy.py, tests/test_no_skip_on_pinned_dependency.py).
  Layer 3 — this file. The moment those skips became failures, CI went red,
            because CI does not install requirements.lock at all. It installs
            `requirements-ci.txt`, which said in its own header:

                Optional, heavy, or import-guarded extras (anthropic,
                mplfinance/matplotlib/pandas) are intentionally omitted —
                their absence is part of the baseline.

Every clause of that was true and the conclusion was backwards. `requirements.lock`
is a statement that a module is present wherever the code runs. A CI environment
that omits one is testing a system nobody deploys — and the omission cannot be
noticed from inside, because the only tests that would have complained are the
ones that skip when it is missing. That is a closed loop: the absence justifies
the skip, and the skip hides the absence.

So the loop gets cut from outside, by comparing the two files. This is a
property of the pair, not of either one, which is why no test inside the suite
could have caught it.

WHY "heavy" IS NOT A REASON. It was the honest half of the old header —
matplotlib and pandas are ~25MB and they do slow the run. But heaviness is an
argument for caching the install (`actions/setup-python` already has
`cache: pip`), never for declining to test a shipped feature. The cost of the
alternative was twenty tests that had never executed once.

WHY "import-guarded" IS NOT A REASON EITHER, and this is the trap that did the
damage: Pillow is guarded at all eleven of its import sites and is not optional
in the slightest. A guarded import means the code SURVIVES the absence, not that
the feature is expendable. Reading "guarded" as "optional" is the single
conflation behind all three layers.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements.lock"
CI = ROOT / "requirements-ci.txt"

#: Modules pinned in requirements.lock that CI deliberately does NOT install.
#: Empty, and it should stay that way. An entry here is a claim that production
#: and CI may legitimately differ on this module — write down who decided and
#: why, the way tests/optional_imports.json does. The test below refuses a
#: placeholder, and refuses an entry that CI turns out to install after all.
CI_EXEMPT: dict[str, str] = {}


def _dists(path: Path) -> set:
    """Distribution names in a requirements file, lowercased, extras stripped."""
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(re.split(r"[=<>!~\[]", line)[0].strip().lower())
    return out


def test_ci_installs_everything_the_lock_guarantees():
    missing = sorted(_dists(LOCK) - _dists(CI) - set(CI_EXEMPT))
    assert missing == [], (
        "requirements.lock pins these, and CI does not install them:\n  "
        + "\n  ".join(missing)
        + "\n\nThe lock is a claim that a module is present wherever the code "
          "runs. CI omitting one means the suite is exercising a system nobody "
          "deploys, and the tests that would notice are exactly the ones that "
          "skip when it is absent. Add it to requirements-ci.txt, or add it to "
          "CI_EXEMPT with a real reason.")


def test_no_stale_exemptions():
    """The ratchet's other half — the known_failures.txt rule, same reasoning.
    An exemption that CI installs anyway has stopped describing anything, and a
    list that only grows stops being read."""
    stale = sorted(m for m in CI_EXEMPT if m in _dists(CI))
    assert stale == [], (
        f"{stale} is installed by CI now — delete it from CI_EXEMPT in this "
        "commit, or the list stops meaning what it says")


def test_every_exemption_carries_a_reason_someone_can_argue_with():
    for mod, why in CI_EXEMPT.items():
        assert isinstance(why, str) and len(why) >= 40, (
            f"{mod}'s reason is too short to be a reason: {why!r}")
        assert not re.fullmatch(r"(n/a|tbd|todo|heavy|optional|guarded)\.?",
                                why.strip(), re.I), (
            f"{mod} is excused by a placeholder, not a reason: {why!r}. "
            "'heavy' and 'guarded' in particular are the two that caused this "
            "— heaviness argues for caching, and a guarded import means the "
            "code survives the absence, not that the feature is expendable.")


def test_the_chart_libraries_are_in_both():
    """The specific regression, by name, on the CI side.

    tests/test_no_skip_on_pinned_dependency.py pins them in requirements.lock.
    That is necessary and was not sufficient: they were in the lock and still
    absent from every CI run, which is precisely how this PR went red.
    """
    lock, ci = _dists(LOCK), _dists(CI)
    for mod in ("matplotlib", "mplfinance", "pandas"):
        assert mod in lock, f"{mod} left requirements.lock"
        assert mod in ci, (
            f"{mod} left requirements-ci.txt — the 20 tests in "
            "tests/test_chart_renderer.py stop running, and this time they "
            "fail loudly instead of skipping, which is the only reason anyone "
            "found out")


def test_the_parse_actually_finds_something():
    """A derived guard whose derivation stops matching passes vacuously."""
    lock, ci = _dists(LOCK), _dists(CI)
    assert len(lock) >= 14, f"only {len(lock)} dists parsed from the lock"
    assert len(ci) >= 20, f"only {len(ci)} dists parsed from requirements-ci.txt"
    for known in ("ccxt", "pillow", "numpy"):
        assert known in lock and known in ci, f"{known} should be in both"


def test_ci_workflow_still_installs_the_file_this_test_checks():
    """The assumption underneath everything above.

    If ci.yml is ever changed to install something else, this file keeps
    comparing requirements-ci.txt to the lock and keeps passing while checking
    a file CI no longer reads — a guard measuring the wrong artifact, which is
    the failure mode `tests/test_preflight_matches_ci.py` exists to prevent one
    level up.
    """
    from tests.source_scan import code_only

    workflow = code_only((ROOT / ".github" / "workflows" / "ci.yml")
                         .read_text(encoding="utf-8"))
    assert "requirements-ci.txt" in workflow, (
        "ci.yml no longer installs requirements-ci.txt — this whole file is "
        "comparing the lock against an artifact CI does not use")
