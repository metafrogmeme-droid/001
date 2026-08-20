"""255 commits stale, and every check passed.

2026-08-20. A deploy ran

    git fetch origin && git reset --hard origin/main

and reported "Deploy-pull completed successfully". It had reset to a commit
255 COMMITS BEHIND. `origin` on that box points at a GitLab mirror; the real
repository is a remote named `backup` on GitHub.

Nothing in the deploy noticed, because every check it ran was TRUE of the stale
tree: the pull worked, `deploy.sh` re-linked, `data/ -> persist` resolved, the
user store loaded, 18 users were present. The only thing wrong was WHICH CODE,
and no step asked. Seven merged fixes were about to be "deployed" by restarting
a binary that contained none of them, with the operator's own verification
steps agreeing that all was well.

WHY "USE THE RIGHT REMOTE NAME" IS NOT THE FIX. A remote name is a local
nickname — per-machine, silently editable, meaningless off the box. Advice is
what failed here. So `verify_deploy_source.sh` never reads a remote name: it
asks the URL directly with `git ls-remote`, which consults no local ref, so the
stale-ref path does not exist for it. That also steps around the trap CLAUDE.md
already records — `git fetch origin main` updates FETCH_HEAD and leaves
refs/remotes/origin/main untouched.

THE EXIT CODE THAT MATTERS MOST IS 3. "Could not check" is not "passed" and is
not "failed". A deploy gate that collapses an unreachable network into either
verdict is the same defect one level up, so `test_an_unreachable_url_is_not_a_pass`
is the test to keep if any of these must go.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "verify_deploy_source.sh"

OK, MISMATCH, USAGE, UNKNOWN = 0, 1, 2, 3


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True)


def _run(cwd, *args):
    p = subprocess.run([str(GUARD), *args], cwd=str(cwd),
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


@pytest.fixture
def world(tmp_path):
    """An upstream with three commits, and a checkout sitting on the first."""
    up = tmp_path / "up.git"
    _git("init", "-q", "--bare", "-b", "main", str(up), cwd=tmp_path)

    work = tmp_path / "work"
    _git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "f").write_text("v1")
    _git("add", "f", cwd=work)
    _git("commit", "-qm", "v1", cwd=work)
    _git("push", "-q", str(up), "main", cwd=work)

    box = tmp_path / "box"
    _git("clone", "-q", str(up), str(box), cwd=tmp_path)

    for v in ("v2", "v3"):
        (work / "f").write_text(v)
        _git("commit", "-qam", v, cwd=work)
    _git("push", "-q", str(up), "main", cwd=work)

    return {"up": up, "box": box, "work": work}


# ── the incident ────────────────────────────────────────────────────────────

def test_a_stale_checkout_fails_the_gate(world):
    """THE ONE THIS EXISTS FOR. The box was 255 behind; here it is 2."""
    code, out = _run(world["box"], "--url", str(world["up"]))
    assert code == MISMATCH, out
    assert "SOURCE MISMATCH" in out
    assert "BEHIND" in out and "2 commit" in out
    assert "STALE" in out


def test_the_failure_names_the_fix_that_cannot_go_wrong(world):
    """An operator reading this at 3am needs the command, not the theory —
    and the command must be the URL form, because the name form is what
    failed."""
    _, out = _run(world["box"], "--url", str(world["up"]))
    assert "git reset --hard FETCH_HEAD" in out
    assert f"git fetch {world['up']} main" in out
    assert "origin/main" not in out.split("Reset to the URL")[-1], (
        "the remedy suggests a remote-tracking ref, which is the stale path")


def test_an_up_to_date_checkout_passes(world):
    _git("fetch", "-q", str(world["up"]), "main", cwd=world["box"])
    _git("reset", "-q", "--hard", "FETCH_HEAD", cwd=world["box"])
    code, out = _run(world["box"], "--url", str(world["up"]))
    assert code == OK, out
    assert "SOURCE OK" in out


# ── could not check is not passed ───────────────────────────────────────────

def test_an_unreachable_url_is_not_a_pass(world, tmp_path):
    """THE MOST IMPORTANT EXIT CODE. A gate that reads a network failure as
    "up to date" deploys stale code on the one day the network is down; a gate
    that reads it as "stale" blocks a correct deploy for the same reason.
    Neither is a measurement."""
    code, out = _run(world["box"], "--url", str(tmp_path / "does-not-exist.git"))
    assert code == UNKNOWN, out
    assert "SOURCE UNKNOWN" in out
    assert "NOTHING WAS CHECKED" in out
    assert "SOURCE OK" not in out and "MISMATCH" not in out


def test_a_missing_branch_is_unknown_not_a_mismatch(world):
    """A branch that does not exist at the URL means the question was not
    answered — not that this checkout is wrong."""
    code, out = _run(world["box"], "--url", str(world["up"]), "--branch", "nope")
    assert code == UNKNOWN, out
    assert "NOTHING WAS CHECKED" in out


def test_a_directory_that_is_not_a_repo_is_unknown(tmp_path):
    code, out = _run(tmp_path, "--url", "https://example.invalid/x.git")
    assert code == UNKNOWN, out
    assert "not a git repository" in out


# ── usage errors check nothing, and say so ──────────────────────────────────

@pytest.mark.parametrize("args", [("--url",), ("--branch",), ("--bogus",)])
def test_a_usage_error_says_nothing_was_checked(world, args):
    """Distinct from both verdicts, and worded so `||` cannot read it as one.
    verify_bot_alive.sh learned this the hard way."""
    code, out = _run(world["box"], *args)
    assert code == USAGE, out
    assert "Nothing was checked" in out


def test_a_missing_value_does_not_hang(world):
    """`shift 2` with one token left shifts nothing and spins forever. A deploy
    gate that HANGS is worse than one that is wrong: the deploy never finishes
    and never says why. The timeout in _run is the assertion."""
    code, _ = _run(world["box"], "--url")   # would time out if it spun
    assert code == USAGE


# ── ahead, for a dev box ────────────────────────────────────────────────────

def test_local_commits_fail_by_default(world):
    """A box with unpushed commits is not running reviewed code either."""
    _git("fetch", "-q", str(world["up"]), "main", cwd=world["box"])
    _git("reset", "-q", "--hard", "FETCH_HEAD", cwd=world["box"])
    _git("config", "user.email", "t@t", cwd=world["box"])
    _git("config", "user.name", "t", cwd=world["box"])
    (world["box"] / "f").write_text("local")
    _git("commit", "-qam", "local", cwd=world["box"])

    code, out = _run(world["box"], "--url", str(world["up"]))
    assert code == MISMATCH
    assert "AHEAD" in out


def test_allow_ahead_is_an_explicit_opt_in(world):
    _git("fetch", "-q", str(world["up"]), "main", cwd=world["box"])
    _git("reset", "-q", "--hard", "FETCH_HEAD", cwd=world["box"])
    _git("config", "user.email", "t@t", cwd=world["box"])
    _git("config", "user.name", "t", cwd=world["box"])
    (world["box"] / "f").write_text("local")
    _git("commit", "-qam", "local", cwd=world["box"])

    code, out = _run(world["box"], "--url", str(world["up"]), "--allow-ahead")
    assert code == OK, out
    assert "ahead" in out.lower()


# ── it reads no remote name at all ──────────────────────────────────────────

def test_the_guard_never_consults_a_remote_name(world):
    """THE PROPERTY, not the implementation. A remote called `origin` pointing
    somewhere else entirely must not change the answer — that is the whole
    incident."""
    other = world["work"].parent / "decoy.git"
    _git("init", "-q", "--bare", "-b", "main", str(other), cwd=world["work"].parent)
    # `origin` now points at an EMPTY repo, exactly the shape of a stale mirror.
    _git("remote", "set-url", "origin", str(other), cwd=world["box"])

    code, out = _run(world["box"], "--url", str(world["up"]))
    assert code == MISMATCH, out
    assert "BEHIND" in out, (
        "the answer changed when `origin` was repointed — the guard is reading "
        "a remote name somewhere")


def test_the_script_does_not_reference_origin_in_code():
    """Source-checked because the behavioural test above can only prove it for
    the paths it exercises."""
    from tests.source_scan import code_only  # noqa: F401  (parity with py scans)

    import re

    src = GUARD.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    # The WORD may appear — the failure message quotes the incident, and
    # forbidding that would forbid explaining the bug. What must not appear is
    # a remote name passed to git. CLAUDE.md: strip comments first, then test
    # the property rather than the substring.
    bad = re.findall(r'git\s+(?:fetch|pull|ls-remote|reset|rev-parse)[^\n]*'
                     r'(?<![\w/-])(origin|upstream|backup)(?![\w-])', code)
    assert not bad, (
        f"the guard passes a remote NAME to git: {bad}. A remote name is a "
        "local nickname and naming one is the defect this file exists to "
        "remove — every git call must take the URL.")
    assert "ls-remote" in code
    # And every real fetch/ls-remote takes "$URL".
    #
    # `echo` lines are excluded, and that is not a convenience: the remedy this
    # script PRINTS is itself a `git fetch <url> <branch>` command, so a scan
    # that cannot tell a message from an invocation fails on the advice. This
    # repo counts five prior false failures from exactly that confusion.
    for line in code.splitlines():
        if "echo" in line:
            continue
        for call in re.findall(r'git\s+(?:fetch|ls-remote)[^\n]*', line):
            assert '"$URL"' in call, f"git call not addressed by URL: {call.strip()}"


def test_the_guard_is_executable_and_self_documenting():
    import os
    assert os.access(GUARD, os.X_OK), "not executable — a launcher cannot call it"
    out = subprocess.run([str(GUARD), "--help"], capture_output=True, text=True)
    assert out.returncode == 0
    assert "255 COMMITS STALE" in out.stdout
