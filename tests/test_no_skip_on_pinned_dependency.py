"""A test may not skip itself over a dependency the lockfile guarantees.

2026-08-17. Signal cards stopped rendering because Pillow was unpinned. The
fix pinned it. Charts were still dead the same evening for the same reason one
layer down — matplotlib, mplfinance and pandas — and the reason nobody noticed
for months is not in `bot/`, it is here:

    needs_charts = pytest.mark.skipif(
        not cr.charts_available(), reason="charting libs not installed")

Twenty tests behind that mark. With the libraries absent the file reported
twenty skips, the suite was green, and CI agreed the charting feature was
fine. The libraries were installed in NO environment, so those twenty tests
had never executed once. They all pass on the first run against real libs —
they were correct and unreachable, which is `test_no_new_unreachable_modules`'s
subject arriving through pytest instead of through imports.

THE RULE. `pytest.importorskip("x")` says "x is optional". That is a claim,
and this repo already keeps the answer in one place:

    pinned in requirements.lock  →  the claim is FALSE. A missing x means the
                                    environment is broken; skipping publishes
                                    a green run that established nothing.
    declared optional            →  the claim is TRUE. Skip away.

`tests/test_web3_signer.py` skips on `eth_account`, which is declared optional
on purpose, and stays exactly as it is — that case is the control. If this
guard flagged it too it would be measuring "uses importorskip" rather than
"skips over something guaranteed present", and the fix would be to delete the
guard.

WHY A SOURCE SCAN. The behaviour cannot be reached from inside a test run: by
the time a skip has happened there is nothing left to assert against, and a
run where every dependency IS present exercises none of these branches. The
property is "no call site names a pinned module", which is a property of the
call sites. Comments are stripped first — a docstring naming a module is not
a call, and this file's own prose would otherwise match it.
"""

import re
from pathlib import Path

from tests.dep_policy import optional_modules, pinned_modules
from tests.source_scan import code_only

TESTS = Path(__file__).resolve().parent
SELF = Path(__file__).name

#: `pytest.importorskip("name")` / `importorskip('name', reason=…)`.
_IMPORTORSKIP = re.compile(r"""importorskip\(\s*["']([A-Za-z0-9_.]+)["']""")


def _test_files():
    for f in sorted(TESTS.glob("test_*.py")):
        if f.name != SELF:
            yield f


def test_no_test_skips_itself_over_a_pinned_dependency():
    pinned = pinned_modules()
    offenders = []
    for f in _test_files():
        for mod in _IMPORTORSKIP.findall(code_only(f.read_text(encoding="utf-8"))):
            if mod.split(".")[0].lower() in pinned:
                offenders.append(f"{f.name}: importorskip({mod!r})")
    assert offenders == [], (
        "these tests skip themselves over a module requirements.lock "
        "guarantees is installed:\n  " + "\n  ".join(offenders)
        + "\n\nA missing pinned module is a broken environment, not an "
          "optional feature. Use tests.dep_policy.require(), which fails "
          "for a pinned module and skips for one declared optional.")


def test_the_honest_skips_are_still_allowed():
    """The control. If this stops finding anything, the guard above has become
    a ban on `importorskip` rather than a check on what it names, and the two
    are not the same test."""
    optional = optional_modules()
    honest = []
    for f in _test_files():
        for mod in _IMPORTORSKIP.findall(code_only(f.read_text(encoding="utf-8"))):
            if mod.split(".")[0] in optional:
                honest.append((f.name, mod))
    assert honest, (
        "no test skips over a declared-optional module any more. Either the "
        "optional list emptied out, or somebody 'fixed' the honest skips too.")


def test_chart_libraries_are_pinned_because_charts_are_not_optional():
    """The specific regression, by name — the Pillow test's sibling.

    Guarded at their import site and returning None, which is what made them
    look expendable for months. Charts are a shipped feature: `/chart`, the
    signal cards' companion image, and every `send_idea_charts_multi` caller.
    """
    pinned = pinned_modules()
    for mod in ("matplotlib", "mplfinance", "pandas"):
        assert mod in pinned, (
            f"{mod} left requirements.lock — charts go back to silently "
            "sending text, exactly as they did on 2026-08-17")
        assert mod not in optional_modules(), (
            f"{mod} is declared optional again. The chart renderer survives "
            "its absence; the feature does not.")


def test_require_fails_for_pinned_and_skips_for_optional(monkeypatch):
    """The helper's discrimination, exercised rather than described.

    Everything above checks that no call site NAMES a pinned module. None of
    it would notice if `require` had been written to skip in both cases — the
    call sites would be immaculate and the behaviour unchanged from the bug.
    So drive both branches with a module that cannot exist either way.
    """
    import pytest as _pytest

    from tests import dep_policy

    ghost = "definitely_not_a_real_module_xyz"

    monkeypatch.setattr(dep_policy, "pinned_modules", lambda: {ghost})
    # BaseException, not Exception: pytest's Failed/Skipped derive from
    # OutcomeException(BaseException) precisely so a bare `except Exception`
    # in a test cannot swallow them. `raises(Exception)` does not catch them.
    with _pytest.raises(BaseException) as pinned_exc:
        dep_policy.require(ghost)
    assert pinned_exc.typename == "Failed", (
        "a pinned module that will not import must FAIL the run, not skip it "
        f"— got {pinned_exc.typename}")
    assert "requirements.lock" in str(pinned_exc.value)

    monkeypatch.setattr(dep_policy, "pinned_modules", lambda: set())
    monkeypatch.setattr(dep_policy, "optional_modules", lambda: {ghost})
    with _pytest.raises(BaseException) as opt_exc:
        dep_policy.require(ghost)
    assert opt_exc.typename == "Skipped", (
        "a module declared optional is allowed to be absent — skipping is the "
        f"honest outcome there, got {opt_exc.typename}")


def test_the_scan_actually_matches_something():
    """A derived guard whose derivation stops matching passes vacuously."""
    total = sum(len(_IMPORTORSKIP.findall(code_only(f.read_text(encoding="utf-8"))))
                for f in _test_files())
    assert total >= 3, (
        f"only {total} importorskip call sites parsed across tests/ — the "
        "regex has drifted and this file is no longer checking anything")
