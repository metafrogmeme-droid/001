"""Supply-chain pins: exact versions, lockfiles, and a nightly advisory scan.

Ranges are invisible to the pin-agreement test until they become ``==``.

This docstring used to say why the files CI installs carried no hashes: a
hash on one line makes pip require one for every line, and refuse a
transitive the file does not name. That was the reason, and it described a
file that named 29 packages for an install of about a hundred. Since
2026-11-19 the locks name every package and are installed with
``--require-hashes``; ``tests/test_the_lock_is_hash_pinned.py`` holds that.
The Docker base image is digest-pinned, and this test fails if that pin goes
away.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests import pip_installs

ROOT = Path(__file__).resolve().parents[1]

#: Every requirements file, generated and hand-written. An environment marker
#: may compare (`sys_platform != "win32"`); a version may not range.
_REQS = ("requirements-ci.txt", "bot/requirements.txt", "requirements.lock",
         "requirements-xdist.txt", "requirements-piptools.txt",
         "requirements.in", "requirements-ci.in", "requirements-xdist.in",
         "requirements-piptools.in")
_RANGE = re.compile(r"(>=|~=|=>)")


def _code_lines(rel: str):
    for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
        body = line.split("#", 1)[0].strip()
        if body and not body.startswith("-"):
            yield i, body


def test_python_requirements_have_no_version_ranges():
    bad = [
        f"{rel}:{i}: {body}"
        for rel in _REQS
        for i, body in _code_lines(rel)
        if _RANGE.search(body)
    ]
    assert bad == [], bad


def test_pyproject_runtime_deps_have_no_version_ranges():
    """``setuptools`` and ``requires-python`` are the build floor, not a pin."""
    bad = []
    for i, line in enumerate((ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines(), 1):
        if ">=" not in line and "~=" not in line:
            continue
        if "setuptools" in line or "requires-python" in line:
            continue
        bad.append(f"pyproject.toml:{i}: {line.strip()}")
    assert bad == [], bad


def test_image_and_make_installs_are_exact():
    """Both used to name `fastapi==0.141.1` and `uvicorn[standard]==0.52.3`
    on the install line, and this asserted those two strings. They were exact
    and they had no hash, and the extra's packages came in under them at any
    version. The line now installs the lock and nothing else, so the same two
    pins are asserted where they live, with the extra's packages beside them.
    """
    for rel in ("Dockerfile", "Makefile"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "fastapi>=" not in text, rel
        assert "uvicorn[standard]>=" not in text, rel
        found = pip_installs.installs(rel)
        assert found, f"{rel} installs nothing with pip"
        loose = [arguments for arguments, kind in found if kind == "unhashed"]
        assert loose == [], f"{rel} installs without checking a hash: {loose}"
        locked = [arguments for arguments, kind in found if kind == "locked"]
        assert len(locked) == 1 and "-r requirements.lock" in locked[0], (
            f"{rel} must install requirements.lock, once, under --require-hashes: {locked}")

    lock = pip_installs.compile_locks.parse_lock((ROOT / "requirements.lock").read_text(encoding="utf-8"))
    assert lock["fastapi"].version == "0.141.1"
    assert lock["uvicorn"].version == "0.52.3"
    # What `uvicorn[standard]` adds. The lock strips the extra and names its
    # packages, so the extra is these four being present.
    for name in ("httptools", "uvloop", "watchfiles", "pyyaml"):
        assert "uvicorn" in lock[name].via, f"{name} is not in the lock as a requirement of uvicorn"
    declared = (ROOT / "requirements.in").read_text(encoding="utf-8")
    assert re.search(r"^uvicorn\[standard\]==0\.52\.3$", declared, re.M), (
        "requirements.in no longer asks for uvicorn's standard extra")


def test_the_docker_base_image_is_digest_pinned():
    froms = [
        ln for ln in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if ln.startswith("FROM ")
    ]
    assert froms, "the Dockerfile has no FROM"
    for ln in froms:
        assert "@sha256:" in ln, ln


def test_every_package_json_has_a_committed_lockfile():
    missing = []
    for pkg in ROOT.rglob("package.json"):
        if "node_modules" in pkg.parts:
            continue
        if not (pkg.parent / "package-lock.json").exists():
            missing.append(str(pkg.relative_to(ROOT)))
    assert missing == [], missing


def test_workflows_install_node_from_the_lockfile():
    bad = []
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        for i, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\bnpm install\b", code):
                bad.append(f"{wf.name}:{i}")
    assert bad == [], bad


def test_the_nightly_audit_is_scheduled_and_not_a_pull_request_check():
    text = (ROOT / ".github" / "workflows" / "nightly-audit.yml").read_text(encoding="utf-8")
    assert "schedule:" in text
    assert "workflow_dispatch:" in text
    assert "pull_request" not in text
    assert "continue-on-error: true" in text
    assert "contents: read" in text
    assert "pip-audit -r requirements.lock" in text
    assert "pip-audit -r requirements-ci.txt" in text
    assert "npm audit" in text
    assert "npm ci" in text
    assert "ruleset" in text
