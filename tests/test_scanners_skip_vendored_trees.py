"""`guard_lint` must not accuse third-party code living inside the repo.

`_route_module_coverage()` walks the WHOLE tree and reports any `.py`/`.js`
module that registers HTTP routes but is named by no guard rule. That sweep is
the right shape — CLAUDE.md records that naming files explicitly is exactly how
`bot/api/auth_routes.py` stayed invisible — but it only skips vendored trees by
matching two hardcoded venv names:

    _COVERAGE_SKIP = (..., "/.venv/", "/venv/")

A virtualenv called anything else is neither ignored by git nor skipped here.
Building one as `.venv-audit/` turned the gate red with nine accusations
against `matplotlib`, `pandas` and `mplfinance` — libraries that do define
`ax.add_patch(...)`, which the aiohttp route pattern `\\.add_(get|post|...)\\(`
cannot tell from `router.add_get("/path", ...)`.

That is the failure this script exists to prevent, pointed the other way. The
module's own docstring for the coverage rule says a guard that is not reached
is not a guard; a guard that fires on library code nobody wrote is worse,
because the fix an engineer reaches for is to stop believing the gate.

So the skip is asserted STRUCTURALLY: a venv is whatever contains a
`pyvenv.cfg`, and an installed package tree is whatever sits under
`site-packages`/`dist-packages` — neither of which depends on what someone
named the directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import guard_lint  # noqa: E402


def _plant_venv(root: Path, name: str) -> Path:
    """Write the smallest thing that is unmistakably a virtualenv.

    A `pyvenv.cfg` at the top, and a package under `site-packages` carrying a
    line the aiohttp route signature matches. `matplotlib.axes._axes` is the
    real precedent: `.add_patch(` is an `add_<verb>(` call on an object that
    has nothing to do with HTTP.
    """
    venv = root / name
    (venv / "lib" / "python3.11" / "site-packages" / "thirdparty").mkdir(
        parents=True, exist_ok=True
    )
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.11.15\n", encoding="utf-8")
    (venv / "lib" / "python3.11" / "site-packages" / "thirdparty" / "plotting.py").write_text(
        "def draw(ax, poly):\n"
        "    ax.add_patch(poly)\n"
        "    ax.add_get(poly)\n"
        "    return ax\n",
        encoding="utf-8",
    )
    return venv


def _accusations_mentioning(paths: list[str], needle: str) -> list[str]:
    return [p for p in paths if needle in p]


def test_a_venv_named_anything_is_not_accused(tmp_path, monkeypatch):
    """The name is not what makes a directory a virtualenv.

    `.venv-audit` is the name that actually broke this, but the rule must not
    be a list of names — the next one will be `.venv311` or `env`.
    """
    for name in (".venv-audit", ".venv311", "env", "venv-3.11", ".tox"):
        root = tmp_path / name.strip(".")
        root.mkdir(parents=True, exist_ok=True)
        (root / "app.py").write_text("routes.add_get('/ok', handler)\n", encoding="utf-8")
        _plant_venv(root, name)

        monkeypatch.setattr(guard_lint, "REPO", root)
        problems = guard_lint._route_module_coverage()

        assert not _accusations_mentioning(problems, name), (
            f"guard_lint accused code inside the virtualenv {name!r}:\n"
            + "\n".join(_accusations_mentioning(problems, name))
        )


def test_site_packages_is_skipped_without_a_pyvenv_cfg(tmp_path, monkeypatch):
    """A vendored install tree need not be a venv to be third-party.

    `--target` installs, Debian's `dist-packages` and a bare `site-packages`
    directory all carry code nobody in this repo wrote and cannot fix.
    """
    root = tmp_path / "repo"
    for leaf in ("site-packages", "dist-packages"):
        pkg = root / "vendor" / "lib" / leaf / "thirdparty"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "plotting.py").write_text("ax.add_get(poly)\n", encoding="utf-8")

    monkeypatch.setattr(guard_lint, "REPO", root)
    problems = guard_lint._route_module_coverage()

    assert not problems, "guard_lint accused a vendored install tree:\n" + "\n".join(problems)


def test_first_party_routes_are_still_accused(tmp_path, monkeypatch):
    """The skip must not become a hole.

    Widening an exclusion is how a checker quietly stops checking, so this
    asserts the POSITIVE: a repo module registering routes that no rule names
    is still reported. Without this, deleting the whole coverage rule would
    pass the two tests above.
    """
    root = tmp_path / "repo"
    (root / "bot" / "web").mkdir(parents=True, exist_ok=True)
    (root / "bot" / "web" / "rogue_server.py").write_text(
        "app.add_get('/secret', handler)\n", encoding="utf-8"
    )
    _plant_venv(root, ".venv-audit")

    monkeypatch.setattr(guard_lint, "REPO", root)
    problems = guard_lint._route_module_coverage()

    assert _accusations_mentioning(problems, "rogue_server.py"), (
        "guard_lint stopped reporting an unguarded first-party route module; "
        "the vendored-tree skip has swallowed real coverage"
    )


def test_gitignore_covers_the_venv_names_guard_lint_skips():
    """A venv the gate tolerates must also be one git refuses to commit.

    These two lists drifted apart once already: `.gitignore` ignored `.venv/`
    and `venv/`, `_COVERAGE_SKIP` skipped the same two, and `.venv-audit/` was
    outside both — so it broke the gate AND was committable.

    Asserted by asking git, not by matching the file's text: a pattern set can
    be read a dozen ways and `git check-ignore` is the only reader that counts.
    """
    import subprocess

    candidates = [".venv/", "venv/", ".venv-audit/", ".venv311/", "venv-3.11/"]
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "-v", *candidates],
        cwd=ROOT, capture_output=True, text=True,
    )
    ignored = {ln.rsplit("\t", 1)[-1] for ln in proc.stdout.splitlines() if ln.strip()}
    missing = [c for c in candidates if c not in ignored]
    assert not missing, (
        ".gitignore does not ignore these virtualenv paths, so they are "
        f"committable: {missing}"
    )


def test_python_comments_cannot_manufacture_a_route(tmp_path, monkeypatch):
    """Prose that quotes a route call is not a route call.

    `_strip_js_comments` exists because a commented-out `router.use(...)` once
    satisfied a rule; its docstring says so. The Python side never got the same
    treatment, so the coverage rule regex-scans docstrings and `#` comments as
    though they were code.

    This is not hypothetical: the fix for the virtualenv blind spot explained
    itself by naming `ax.add_patch(...)` — matplotlib's method, the reason the
    aiohttp signature misfires — and `guard_lint.py` promptly accused ITSELF of
    registering two HTTP routes. CLAUDE.md calls this out directly: "a comment
    that quotes the string it forbids is indistinguishable from the code doing
    it, and this has produced four false failures."
    """
    root = tmp_path / "repo"
    (root / "bot").mkdir(parents=True, exist_ok=True)
    (root / "bot" / "prose.py").write_text(
        '"""Explains a bug.\n'
        "\n"
        "    The aiohttp pattern matches ax.add_patch(poly) as readily as it\n"
        '    matches a real registration.\n'
        '    """\n'
        "\n"
        "# app.add_get('/commented-out', handler)\n"
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(guard_lint, "REPO", root)
    problems = guard_lint._route_module_coverage()

    assert not _accusations_mentioning(problems, "prose.py"), (
        "guard_lint read a docstring and a comment as HTTP route registrations:\n"
        + "\n".join(problems)
    )


def test_real_python_routes_are_still_detected(tmp_path, monkeypatch):
    """The comment strip must not blank out actual code.

    Blanking spans by offset is easy to get wrong in the direction that hides
    real routes, and a coverage rule that stops seeing routes reports a clean
    tree — the quiet failure, not the loud one.
    """
    root = tmp_path / "repo"
    (root / "bot").mkdir(parents=True, exist_ok=True)
    (root / "bot" / "real.py").write_text(
        "# a comment mentioning nothing\n"
        '"""A docstring."""\n'
        "\n"
        "def setup(app):\n"
        "    app.add_get('/live', handler)  # trailing comment\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(guard_lint, "REPO", root)
    problems = guard_lint._route_module_coverage()

    assert _accusations_mentioning(problems, "real.py"), (
        "guard_lint stopped detecting a real aiohttp registration; the comment "
        "strip has blanked live code"
    )


# ── The same blind spot, in a second scanner ──────────────────────────────
#
# `guard_lint` was not alone. `tests/test_no_read_only_fields.py` walks the
# tree with `root.rglob("*.py")` and skips only `__pycache__` and `/.git/`, so
# the audit's own virtualenv put 107 extra `site-packages` symbols into its
# comparison and failed it — on `_pytest.ReprTracebackNative.extraline`, a
# field in pytest itself.
#
# That is the same defect class as the coverage rule's, and it fails the same
# way: an accusation about code this repository does not own and cannot fix.

def test_no_read_only_fields_scanner_skips_vendored_trees(tmp_path):
    """`_py_files` must not hand back third-party sources.

    Asserted against the helper directly rather than by running the whole
    check, because the check's verdict depends on the real tree and this
    property does not.
    """
    import importlib

    mod = importlib.import_module("test_no_read_only_fields")

    root = tmp_path / "repo"
    (root / "bot").mkdir(parents=True, exist_ok=True)
    (root / "bot" / "real.py").write_text("class A:\n    pass\n", encoding="utf-8")
    _plant_venv(root, ".venv-audit")
    vendored = root / "vendor" / "lib" / "site-packages" / "dep"
    vendored.mkdir(parents=True, exist_ok=True)
    (vendored / "mod.py").write_text("class B:\n    pass\n", encoding="utf-8")

    found = [str(p) for p in mod._py_files(root)]

    assert any("bot/real.py" in p for p in found), (
        "the scanner stopped seeing first-party sources"
    )
    leaked = [p for p in found if ".venv-audit" in p or "site-packages" in p]
    assert not leaked, (
        "test_no_read_only_fields scans vendored code, so any in-tree "
        f"virtualenv fails it on third-party symbols: {leaked[:5]}"
    )
