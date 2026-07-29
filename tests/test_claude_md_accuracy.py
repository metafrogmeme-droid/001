"""CLAUDE.md is read before anyone looks at the code, so it must be true.

It exists because scripts/preflight.py was undiscoverable: nothing in the
repo pointed at it, so the next session would have rebuilt the same wrong
habit — running a subset of CI and reporting it as the whole.

A document that is read FIRST is the worst place for a stale claim, because
everything after it is interpreted through it. The runbook demonstrated the
failure mode a day earlier: it described `build` as the answer to "did my fix
land?", which stopped being true when a fix shipped entirely in public/js.
Correct when written, wrong by the time it mattered.

So the checkable claims are pinned. Every command it gives runs, every file it
points at exists, every rule it states is one the suite actually enforces.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


# ── every path it names exists ────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "scripts/preflight.py",
    "scripts/ci_test_gate.py",
    "tests/known_failures.txt",
    "app/test/panel_failure_honesty.test.js",
    "app/test/mcp_public_records.test.js",
    "app/test/dashboard_social.test.js",
    "tests/test_preflight_matches_ci.py",
    "docs/LIVE_HARDENING_RUNBOOK.md",
    "scripts/cloudflared/",
    ".github/workflows/ci.yml",
    "app/lib/version.js",
])
def test_a_referenced_path_exists(rel):
    assert rel in DOC, f"{rel} dropped out of CLAUDE.md"
    assert (ROOT / rel).exists(), f"CLAUDE.md points at a missing path: {rel}"


def test_no_path_it_names_is_missing():
    """The parametrised list above is curated; this catches anything added to
    the doc later without a test."""
    referenced = set(re.findall(r"`((?:app|bot|docs|scripts|tests)/[\w./-]+)`", DOC))
    missing = sorted(p for p in referenced if not (ROOT / p).exists())
    assert missing == [], f"CLAUDE.md points at missing paths: {missing}"


# ── every command it gives actually works ─────────────────────────────────

def test_the_preflight_invocations_are_real():
    for flag in ("--fast", "--list"):
        assert f"scripts/preflight.py {flag}" in DOC
    r = subprocess.run([sys.executable, "scripts/preflight.py", "--help"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0
    for flag in ("--fast", "--list"):
        assert flag in r.stdout, f"{flag} is documented but not accepted"


def test_the_fingerprint_command_prints_both_values():
    m = re.search(r'node -e "(.+?)"', DOC)
    assert m, "the deploy-verification command is gone"
    r = subprocess.run(["node", "-e", m.group(1)], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    parts = r.stdout.split()
    assert len(parts) == 2, f"expected build and assets, got {r.stdout!r}"
    for p in parts:
        assert re.fullmatch(r"[0-9a-f]{12}\+\d+", p), p


def test_the_gate_count_it_quotes_is_the_real_one():
    """"Eight gates" is a number someone will trust rather than count."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight
    m = re.search(r"(\w+) gates:", DOC)
    assert m, "the gate count sentence is gone"
    words = {"six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    claimed = words.get(m.group(1).lower())
    assert claimed is not None, f"unparsed count word: {m.group(1)}"
    assert claimed == len(preflight.steps(fast=False)), (
        f"CLAUDE.md says {claimed} gates, preflight plans "
        f"{len(preflight.steps(fast=False))}")


def test_the_jobs_it_says_are_uncovered_really_are():
    sys.path.insert(0, str(ROOT / "scripts"))
    import preflight
    uncovered = " ".join(preflight.uncovered()).lower()
    for word in ("cargo", "solidity", "gitleaks", "token tooling"):
        assert word in uncovered, f"CLAUDE.md claims {word} is uncovered; it is not"


# ── every rule it states is one the suite enforces ────────────────────────

def test_the_honesty_rule_has_a_guard_behind_it():
    assert "Unreadable is never zero" in DOC
    guard = (ROOT / "app" / "test" / "panel_failure_honesty.test.js").read_text(
        encoding="utf-8")
    assert "renderPanel" in guard and "mustRead" in guard, (
        "the doc cites this file as the structural enforcement — it must "
        "still be that")


def test_the_baseline_gate_behaviour_it_describes_is_real():
    assert "starts *passing* is a hard failure" in DOC
    gate = (ROOT / "scripts" / "ci_test_gate.py").read_text(encoding="utf-8")
    assert "known_failures" in gate
    assert "HARD failure" in gate or "hard failure" in gate


def test_the_comment_stripping_helper_it_recommends_exists():
    assert "code_only()" in DOC
    src = (ROOT / "tests" / "test_preflight_matches_ci.py").read_text(encoding="utf-8")
    assert "def code_only(" in src and "tokenize" in src


def test_it_does_not_tell_anyone_to_run_a_bare_pytest():
    """The trap it exists to close: `pytest` alone skips the baseline gate,
    both ruff passes, mypy, bandit, pip-audit and the web suite."""
    assert "Do not" in DOC and "bare `pytest`" in DOC
    # and it must not contradict itself elsewhere
    assert not re.search(r"^\s*(?:\$ )?python3? -m pytest", DOC, re.M)


# ── F-15 ──────────────────────────────────────────────────────────────────

def test_no_secret_or_address_is_committed_in_it():
    assert re.search(r"0x[a-fA-F0-9]{40}", DOC) is None
    for pat in (r"\bsk-[A-Za-z0-9]{16,}", r"SECRET\s*=\s*\S{8,}",
                r"\b[a-z0-9-]+\.trycloudflare\.com"):
        assert not re.search(pat, DOC), f"secret-shaped text matched {pat}"
