"""Supply-chain pins: exact versions, lockfiles, and a nightly advisory scan.

Ranges are invisible to the pin-agreement test until they become ``==``.
A hash on one line of the file CI installs would make pip require hashes
for every line and refuse an unlisted transitive, so this file does not
ask for hashes there. The Docker base image is already digest-pinned, and
this test fails if that pin goes away.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_REQS = ("requirements-ci.txt", "bot/requirements.txt", "requirements.lock")
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
    for rel in ("Dockerfile", "Makefile"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "fastapi>=" not in text, rel
        assert "uvicorn[standard]>=" not in text, rel
        assert "fastapi==0.141.1" in text, rel
        assert "uvicorn[standard]==0.52.3" in text, rel


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
